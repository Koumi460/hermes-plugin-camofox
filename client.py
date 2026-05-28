"""REST client and session recovery for the Camofox browser plugin."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import requests

from .config import CamofoxConfig, get_config, is_configured
from .snapshot import merge_snapshot_pages
from .state import get_identity

logger = logging.getLogger(__name__)


class CamofoxError(RuntimeError):
    """Base Camofox plugin error."""


class CamofoxHTTPError(CamofoxError):
    """HTTP error with parsed Camofox payload."""

    def __init__(self, status_code: int, message: str, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload

    @property
    def code(self) -> str:
        if isinstance(self.payload, dict):
            return str(self.payload.get("code") or "")
        return ""


@dataclass
class RecoveryInfo:
    recovered: bool = False
    recovery_reason: str = ""
    adopted_existing_tab: bool = False
    recreated_tab: bool = False
    old_tab_id: str = ""
    tab_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        if not self.recovered:
            return {}
        return {
            "recovered": True,
            "recovery_reason": self.recovery_reason,
            "adopted_existing_tab": self.adopted_existing_tab,
            "recreated_tab": self.recreated_tab,
            "old_tab_id": self.old_tab_id,
            "tab_id": self.tab_id,
        }


@dataclass
class CamofoxSession:
    user_id: str
    session_key: str
    tab_id: Optional[str] = None
    managed: bool = False
    adopt_existing_tab: bool = False
    last_url: str = ""
    last_navigation_url: str = ""
    snapshot_token: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)


_sessions: Dict[str, CamofoxSession] = {}
_sessions_lock = threading.RLock()
_vnc_url: Optional[str] = None
_vnc_checked = False


def _task_key(task_id: Optional[str]) -> str:
    return task_id or "default"


def _headers(cfg: CamofoxConfig) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    return headers


def _parse_response(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _request(
    method: str,
    path: str,
    *,
    cfg: Optional[CamofoxConfig] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
    raw: bool = False,
) -> Any:
    cfg = cfg or get_config()
    if not cfg.url:
        raise CamofoxError("Camofox plugin URL is not configured. Set plugins.entries.camofox.url.")
    resp = requests.request(
        method,
        f"{cfg.url}{path}",
        params=params,
        json=json_body,
        headers=_headers(cfg),
        timeout=timeout if timeout is not None else cfg.request_timeout_s,
    )
    if resp.status_code >= 400:
        payload = _parse_response(resp)
        message = payload.get("error") if isinstance(payload, dict) else str(payload)
        raise CamofoxHTTPError(resp.status_code, message or f"HTTP {resp.status_code}", payload)
    return resp if raw else _parse_response(resp)


def health(timeout: float = 5.0) -> Dict[str, Any]:
    """Return Camofox health, allowing 503 recovery payloads through."""
    global _vnc_checked, _vnc_url
    cfg = get_config()
    if not cfg.url:
        return {"ok": False, "error": "Camofox plugin URL is not configured."}
    try:
        resp = requests.get(f"{cfg.url}/health", headers=_headers(cfg), timeout=timeout)
        data = _parse_response(resp)
        if not isinstance(data, dict):
            data = {"ok": resp.ok, "raw": data}
        data["status_code"] = resp.status_code
        if resp.status_code == 200 and not _vnc_checked:
            vnc_port = data.get("vncPort")
            if isinstance(vnc_port, int) and 1 <= vnc_port <= 65535:
                parsed = urlparse(cfg.url)
                _vnc_url = f"http://{parsed.hostname or 'localhost'}:{vnc_port}"
            _vnc_checked = True
        return data
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "connection_error": True}


def get_vnc_url() -> Optional[str]:
    cfg_vnc_url = get_config().vnc_url
    if cfg_vnc_url:
        return cfg_vnc_url
    if not _vnc_checked:
        health()
    return _vnc_url


def is_available() -> bool:
    if not is_configured():
        return False
    data = health(timeout=3)
    return int(data.get("status_code") or 0) == 200


def _identity(task_id: Optional[str], cfg: CamofoxConfig) -> Dict[str, str]:
    if cfg.user_id:
        return {
            "user_id": cfg.user_id,
            "session_key": cfg.session_key or f"task_{_task_key(task_id)[:16]}",
        }
    if cfg.managed_persistence:
        identity = get_identity(task_id)
        if cfg.session_key:
            identity["session_key"] = cfg.session_key
        return identity
    return {
        "user_id": f"hermes_{uuid.uuid4().hex[:10]}",
        "session_key": cfg.session_key or f"task_{_task_key(task_id)[:16]}",
    }


def get_session(task_id: Optional[str]) -> CamofoxSession:
    key = _task_key(task_id)
    cfg = get_config()
    with _sessions_lock:
        existing = _sessions.get(key)
        if existing:
            return existing
        identity = _identity(task_id, cfg)
        session = CamofoxSession(
            user_id=identity["user_id"],
            session_key=identity["session_key"],
            managed=bool(cfg.managed_persistence or cfg.user_id),
            adopt_existing_tab=cfg.adopt_existing_tab,
        )
        _sessions[key] = session
        return session


def drop_session(task_id: Optional[str]) -> Optional[CamofoxSession]:
    with _sessions_lock:
        return _sessions.pop(_task_key(task_id), None)


def list_tabs(session: CamofoxSession) -> list[Dict[str, Any]]:
    data = _request("GET", "/tabs", params={"userId": session.user_id}, timeout=5)
    tabs = data.get("tabs", []) if isinstance(data, dict) else []
    return [tab for tab in tabs if isinstance(tab, dict)]


def adopt_existing_tab(session: CamofoxSession) -> bool:
    tabs = list_tabs(session)
    if not tabs:
        return False
    matching = [tab for tab in tabs if tab.get("listItemId") == session.session_key]
    latest = (matching or tabs)[-1]
    tab_id = latest.get("tabId") or latest.get("targetId")
    if not isinstance(tab_id, str) or not tab_id:
        return False
    session.tab_id = tab_id
    session.last_url = str(latest.get("url") or session.last_url or "")
    return True


def ensure_tab(session: CamofoxSession, url: str = "about:blank") -> CamofoxSession:
    if session.tab_id:
        return session
    if session.adopt_existing_tab and adopt_existing_tab(session):
        return session
    cfg = get_config()
    try:
        data = _request(
            "POST",
            "/tabs",
            json_body={
                "userId": session.user_id,
                "sessionKey": session.session_key,
                "url": url,
            },
            timeout=max(30.0, cfg.request_timeout_s),
        )
    except Exception as exc:
        if not _is_recoverable_error(exc):
            raise
        _wait_for_healthy_browser(cfg)
        if session.adopt_existing_tab and adopt_existing_tab(session):
            return session
        data = _request(
            "POST",
            "/tabs",
            json_body={
                "userId": session.user_id,
                "sessionKey": session.session_key,
                "url": url,
            },
            timeout=max(30.0, cfg.request_timeout_s),
        )
    if not isinstance(data, dict) or not data.get("tabId"):
        raise CamofoxError("Camofox did not return a tabId when creating a tab.")
    session.tab_id = str(data["tabId"])
    session.last_url = str(data.get("url") or url)
    return session


def _wait_for_healthy_browser(cfg: CamofoxConfig) -> None:
    deadline = time.monotonic() + cfg.recovery.health_timeout_s
    while time.monotonic() < deadline:
        data = health(timeout=min(5.0, cfg.recovery.health_timeout_s))
        if data.get("connection_error"):
            raise CamofoxError(
                "Cannot connect to the Camofox HTTP server. This plugin only "
                "connects to an existing Camofox instance; start or fix the "
                "Camofox service and retry."
            )
        elif data.get("recovering"):
            pass
        elif data.get("ok") and data.get("browserRunning"):
            return
        elif data.get("ok") and not data.get("browserRunning"):
            _request("POST", "/start", cfg=cfg, timeout=cfg.request_timeout_s)
        time.sleep(cfg.recovery.health_poll_s)
    raise CamofoxError("Camofox browser did not become healthy before recovery timed out.")


def _is_recoverable_error(exc: Exception) -> bool:
    if isinstance(exc, requests.ConnectionError):
        return True
    if isinstance(exc, requests.RequestException):
        return True
    if not isinstance(exc, CamofoxHTTPError):
        return False
    if exc.status_code in {404, 410, 503}:
        return True
    return 500 <= exc.status_code < 600


def _is_auth_or_validation_error(exc: Exception) -> bool:
    return isinstance(exc, CamofoxHTTPError) and exc.status_code in {400, 401, 403}


def recover_session(
    session: CamofoxSession,
    *,
    reason: str,
    allow_new_tab: bool,
    fallback_url: str = "about:blank",
) -> RecoveryInfo:
    cfg = get_config()
    if not cfg.recovery.enabled:
        raise CamofoxError(f"Camofox recovery is disabled after: {reason}")

    old_tab_id = session.tab_id or ""
    session.tab_id = None
    _wait_for_healthy_browser(cfg)

    adopted = adopt_existing_tab(session)
    recreated = False
    if not adopted:
        if not allow_new_tab:
            raise CamofoxError(
                "The Camofox tab was lost. Refreshed browser state, but the original "
                "action was not replayed because it may be unsafe. Call camofox_snapshot "
                "and retry with current refs."
            )
        ensure_tab(session, fallback_url or session.last_navigation_url or session.last_url or "about:blank")
        recreated = True

    return RecoveryInfo(
        recovered=True,
        recovery_reason=reason,
        adopted_existing_tab=adopted,
        recreated_tab=recreated,
        old_tab_id=old_tab_id,
        tab_id=session.tab_id or "",
    )


def with_recovery(
    session: CamofoxSession,
    operation: Callable[[], Dict[str, Any]],
    *,
    safe_to_replay_after_new_tab: bool,
    fallback_url: str = "about:blank",
) -> tuple[Dict[str, Any], RecoveryInfo]:
    cfg = get_config()
    attempts = max(1, cfg.recovery.max_attempts)
    last_error: Optional[Exception] = None
    recovery_info = RecoveryInfo()
    for attempt in range(attempts):
        try:
            return operation(), recovery_info
        except Exception as exc:
            last_error = exc
            if _is_auth_or_validation_error(exc) or not _is_recoverable_error(exc) or attempt >= attempts - 1:
                raise
            reason = getattr(exc, "code", "") or str(exc) or type(exc).__name__
            recovery_info = recover_session(
                session,
                reason=reason,
                allow_new_tab=safe_to_replay_after_new_tab,
                fallback_url=fallback_url,
            )
    assert last_error is not None
    raise last_error


def navigate(url: str, *, task_id: Optional[str], delay_s: float = 0.0) -> Dict[str, Any]:
    cfg = get_config()
    session = get_session(task_id)
    with session.lock:
        ensure_tab(session, url)

        def _nav() -> Dict[str, Any]:
            assert session.tab_id is not None
            return _request(
                "POST",
                f"/tabs/{session.tab_id}/navigate",
                json_body={"userId": session.user_id, "url": url, "sessionKey": session.session_key},
                timeout=max(60.0, cfg.request_timeout_s),
            )

        nav_data, recovery = with_recovery(
            session,
            _nav,
            safe_to_replay_after_new_tab=True,
            fallback_url=url,
        )
        session.last_url = str(nav_data.get("url") or url) if isinstance(nav_data, dict) else url
        session.last_navigation_url = url

        delay_s = max(0.0, min(float(delay_s or 0.0), cfg.navigate_delay_max_s))
        initial_snapshot_chars = 0
        try:
            first = snapshot(task_id=task_id, offset=0, _session=session)
            initial_snapshot_chars = len(str(first.get("snapshot") or ""))
        except Exception:
            first = {}

        if delay_s > 0:
            time.sleep(delay_s)
            snap = snapshot(task_id=task_id, offset=0, _session=session)
            snap["delayed_recapture"] = True
            snap["delay_s"] = delay_s
            snap["initial_snapshot_chars"] = initial_snapshot_chars
        else:
            snap = first if first else snapshot(task_id=task_id, offset=0, _session=session)
            snap["delayed_recapture"] = False

        result = {
            "success": True,
            "url": session.last_url,
            "tab_id": session.tab_id,
            **snap,
        }
        result.update(recovery.to_dict())
        vnc = get_vnc_url()
        if vnc:
            result["vnc_url"] = vnc
        return result


def _snapshot_page(session: CamofoxSession, *, offset: int, include_screenshot: bool) -> Dict[str, Any]:
    assert session.tab_id is not None
    params: Dict[str, Any] = {"userId": session.user_id}
    if offset > 0:
        params["offset"] = offset
    if include_screenshot:
        params["includeScreenshot"] = "true"
    return _request("GET", f"/tabs/{session.tab_id}/snapshot", params=params)


def snapshot(
    *,
    task_id: Optional[str],
    offset: int = 0,
    include_screenshot: bool = False,
    full: bool = False,
    _session: Optional[CamofoxSession] = None,
) -> Dict[str, Any]:
    cfg = get_config()
    session = _session or get_session(task_id)
    with session.lock:
        ensure_tab(session, session.last_navigation_url or session.last_url or "about:blank")

        def _read_pages() -> Dict[str, Any]:
            pages = []
            current_offset = max(0, int(offset or 0))
            seen_offsets: set[int] = set()
            total_chars = 0
            for _ in range(cfg.snapshot_max_pages if full else 1):
                if current_offset in seen_offsets:
                    break
                seen_offsets.add(current_offset)
                page = _snapshot_page(session, offset=current_offset, include_screenshot=include_screenshot)
                pages.append(page)
                total_chars += len(str(page.get("snapshot") or ""))
                if not full or total_chars >= cfg.snapshot_max_chars:
                    break
                if not page.get("hasMore"):
                    break
                try:
                    next_offset = int(page.get("nextOffset"))
                except (TypeError, ValueError):
                    break
                if next_offset <= current_offset:
                    break
                current_offset = next_offset
            merged = merge_snapshot_pages(pages)
            if pages:
                merged["page_count"] = len(pages)
                merged["truncated_by_client"] = bool(
                    full and (merged.get("has_more") or total_chars >= cfg.snapshot_max_chars)
                )
            session.snapshot_token += 1
            merged["snapshot_token"] = session.snapshot_token
            session.last_url = str(merged.get("url") or session.last_url or "")
            return merged

        data, recovery = with_recovery(
            session,
            _read_pages,
            safe_to_replay_after_new_tab=True,
            fallback_url=session.last_navigation_url or session.last_url or "about:blank",
        )
        data.update(recovery.to_dict())
        return data


def interact(
    *,
    task_id: Optional[str],
    path: str,
    body: Dict[str, Any],
    safe_to_replay_after_new_tab: bool = False,
) -> Dict[str, Any]:
    session = get_session(task_id)
    with session.lock:
        ensure_tab(session, session.last_navigation_url or session.last_url or "about:blank")

        def _op() -> Dict[str, Any]:
            assert session.tab_id is not None
            data = _request(
                "POST",
                f"/tabs/{session.tab_id}{path}",
                json_body={"userId": session.user_id, **body},
            )
            if isinstance(data, dict):
                session.last_url = str(data.get("url") or session.last_url or "")
            return data if isinstance(data, dict) else {"result": data}

        data, recovery = with_recovery(
            session,
            _op,
            safe_to_replay_after_new_tab=safe_to_replay_after_new_tab,
            fallback_url=session.last_navigation_url or session.last_url or "about:blank",
        )
        data.update(recovery.to_dict())
        return data


def screenshot(*, task_id: Optional[str], full_page: bool = False) -> requests.Response:
    session = get_session(task_id)
    with session.lock:
        ensure_tab(session, session.last_navigation_url or session.last_url or "about:blank")

        def _op() -> Dict[str, Any]:
            assert session.tab_id is not None
            resp = _request(
                "GET",
                f"/tabs/{session.tab_id}/screenshot",
                params={"userId": session.user_id, "fullPage": "true" if full_page else "false"},
                raw=True,
            )
            return {"response": resp}

        data, _recovery = with_recovery(
            session,
            _op,
            safe_to_replay_after_new_tab=True,
            fallback_url=session.last_navigation_url or session.last_url or "about:blank",
        )
        return data["response"]


def get_images(*, task_id: Optional[str]) -> Dict[str, Any]:
    session = get_session(task_id)
    with session.lock:
        ensure_tab(session, session.last_navigation_url or session.last_url or "about:blank")

        def _op() -> Dict[str, Any]:
            assert session.tab_id is not None
            data = _request(
                "GET",
                f"/tabs/{session.tab_id}/images",
                params={"userId": session.user_id, "includeData": "false"},
            )
            return data if isinstance(data, dict) else {"images": data}

        data, recovery = with_recovery(
            session,
            _op,
            safe_to_replay_after_new_tab=True,
            fallback_url=session.last_navigation_url or session.last_url or "about:blank",
        )
        data.update(recovery.to_dict())
        return data


def close(*, task_id: Optional[str]) -> Dict[str, Any]:
    session = drop_session(task_id)
    if session is None:
        return {"success": True, "closed": True}
    if session.managed:
        return {"success": True, "closed": True, "soft_cleanup": True}
    try:
        _request("DELETE", f"/sessions/{session.user_id}")
    except Exception as exc:
        return {"success": True, "closed": True, "warning": str(exc)}
    return {"success": True, "closed": True}


def import_cookies(*, task_id: Optional[str], cookies: list[dict]) -> Dict[str, Any]:
    cfg = get_config()
    if not cfg.api_key:
        raise CamofoxError("camofox_import_cookies requires plugins.entries.camofox.api_key.")
    if not isinstance(cookies, list):
        raise CamofoxError("cookies must be a list of cookie objects.")
    if len(cookies) > 500:
        raise CamofoxError("Too many cookies. Maximum is 500.")
    session = get_session(task_id)
    data = _request(
        "POST",
        f"/sessions/{session.user_id}/cookies",
        json_body={"cookies": cookies},
    )
    return data if isinstance(data, dict) else {"result": data}
