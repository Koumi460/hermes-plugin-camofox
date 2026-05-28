"""Configuration resolution for the Camofox browser plugin."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from hermes_cli.config import load_config


@dataclass(frozen=True)
class RecoveryConfig:
    enabled: bool = True
    max_attempts: int = 2
    health_poll_s: float = 0.5
    health_timeout_s: float = 10.0


@dataclass(frozen=True)
class CamofoxConfig:
    url: str = ""
    api_key: str = ""
    vnc_url: str = ""
    managed_persistence: bool = False
    user_id: str = ""
    session_key: str = ""
    adopt_existing_tab: bool = False
    request_timeout_s: float = 30.0
    snapshot_max_pages: int = 20
    snapshot_max_chars: int = 1_000_000
    navigate_delay_max_s: float = 30.0
    recovery: RecoveryConfig = RecoveryConfig()


def _env_flag(name: str) -> Optional[bool]:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _entry_config() -> Dict[str, Any]:
    try:
        cfg = load_config()
    except Exception:
        return {}

    plugins = cfg.get("plugins", {})
    if not isinstance(plugins, dict):
        return {}
    entries = plugins.get("entries", {})
    if not isinstance(entries, dict):
        return {}

    for key in ("camofox", "browser-camofox", "browser/camofox"):
        value = entries.get(key)
        if isinstance(value, dict):
            return value
    return {}


def get_config() -> CamofoxConfig:
    """Resolve plugin config without touching legacy ``CAMOFOX_URL``."""
    raw = _entry_config()
    recovery_raw = raw.get("recovery", {})
    if not isinstance(recovery_raw, dict):
        recovery_raw = {}

    adopt_env = _env_flag("CAMOFOX_ADOPT_EXISTING_TAB")
    recovery = RecoveryConfig(
        enabled=_as_bool(recovery_raw.get("enabled"), True),
        max_attempts=_as_int(recovery_raw.get("max_attempts"), 2, 1, 5),
        health_poll_s=_as_float(recovery_raw.get("health_poll_s"), 0.5, 0.1, 5.0),
        health_timeout_s=_as_float(recovery_raw.get("health_timeout_s"), 10.0, 1.0, 60.0),
    )

    return CamofoxConfig(
        url=(str(raw.get("url") or "").strip() or os.getenv("CAMOFOX_TOOL_URL", "").strip()).rstrip("/"),
        api_key=str(raw.get("api_key") or os.getenv("CAMOFOX_TOOL_API_KEY", "")).strip(),
        vnc_url=(str(raw.get("vnc_url") or "").strip() or os.getenv("CAMOFOX_TOOL_VNC_URL", "").strip()).rstrip("/"),
        managed_persistence=_as_bool(raw.get("managed_persistence"), False),
        user_id=str(raw.get("user_id") or os.getenv("CAMOFOX_USER_ID", "")).strip(),
        session_key=str(raw.get("session_key") or os.getenv("CAMOFOX_SESSION_KEY", "")).strip(),
        adopt_existing_tab=adopt_env if adopt_env is not None else _as_bool(raw.get("adopt_existing_tab"), False),
        request_timeout_s=_as_float(raw.get("request_timeout_s"), 30.0, 1.0, 120.0),
        snapshot_max_pages=_as_int(raw.get("snapshot_max_pages"), 20, 1, 200),
        snapshot_max_chars=_as_int(raw.get("snapshot_max_chars"), 1_000_000, 10_000, 10_000_000),
        navigate_delay_max_s=_as_float(raw.get("navigate_delay_max_s"), 30.0, 0.0, 300.0),
        recovery=recovery,
    )


def is_configured() -> bool:
    """Return True when a non-legacy Camofox plugin URL is configured."""
    return bool(get_config().url)
