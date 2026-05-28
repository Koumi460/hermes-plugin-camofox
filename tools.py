"""Tool schemas and handlers for the Camofox browser plugin."""

from __future__ import annotations

import base64
import uuid
from typing import Any, Dict, Optional

from hermes_constants import get_hermes_home
from tools.registry import tool_error, tool_result

from . import client
from .snapshot import coerce_bool, coerce_float, coerce_int, shape_snapshot


def _task_id(kw: Dict[str, Any]) -> Optional[str]:
    value = kw.get("task_id")
    return str(value) if value else None


def _tool_exception(exc: Exception) -> str:
    extra: Dict[str, Any] = {"success": False}
    if isinstance(exc, client.CamofoxHTTPError):
        extra["status_code"] = exc.status_code
        if exc.code:
            extra["code"] = exc.code
    return tool_error(str(exc), **extra)


def _check_camofox_available() -> bool:
    return client.is_available()


def _handle_navigate(args: dict, **kw) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        return tool_error("url is required", success=False)
    try:
        result = client.navigate(
            url,
            task_id=_task_id(kw),
            delay_s=coerce_float(args.get("delay_s"), 0.0, 0.0, 300.0),
        )
        shaped = shape_snapshot(result)
        return tool_result({**result, **shaped, "success": True})
    except Exception as exc:
        return _tool_exception(exc)


def _handle_snapshot(args: dict, **kw) -> str:
    try:
        data = client.snapshot(
            task_id=_task_id(kw),
            offset=coerce_int(args.get("offset"), 0, 0, 10_000_000),
            include_screenshot=coerce_bool(args.get("include_screenshot"), False),
            full=coerce_bool(args.get("full"), False),
        )
        result = shape_snapshot(
            data,
            line_offset=(
                coerce_int(args.get("line_offset"), 1, 1, 10_000_000)
                if args.get("line_offset") is not None
                else None
            ),
            line_limit=(
                coerce_int(args.get("line_limit"), 500, 1, 5000)
                if args.get("line_limit") is not None
                else None
            ),
            pattern=str(args.get("pattern") or ""),
            case_sensitive=coerce_bool(args.get("case_sensitive"), False),
            context=coerce_int(args.get("context"), 0, 0, 20),
        )
        if coerce_bool(args.get("summarize"), False):
            from tools.browser_tool import _extract_relevant_content

            task = str(args.get("user_task") or "")
            result["snapshot"] = _extract_relevant_content(result["snapshot"], task or None)
            result["summarized"] = True
        return tool_result({"success": True, **result})
    except Exception as exc:
        return _tool_exception(exc)


def _handle_click(args: dict, **kw) -> str:
    ref = str(args.get("ref") or "").strip().lstrip("@")
    selector = str(args.get("selector") or "").strip()
    if not ref and not selector:
        return tool_error("ref or selector is required", success=False)
    try:
        body = {"ref": ref} if ref else {"selector": selector}
        data = client.interact(
            task_id=_task_id(kw),
            path="/click",
            body=body,
            safe_to_replay_after_new_tab=False,
        )
        return tool_result({"success": True, "clicked": ref or selector, **data})
    except Exception as exc:
        return _tool_exception(exc)


def _handle_type(args: dict, **kw) -> str:
    ref = str(args.get("ref") or "").strip().lstrip("@")
    selector = str(args.get("selector") or "").strip()
    text = str(args.get("text") or "")
    if not ref and not selector:
        return tool_error("ref or selector is required", success=False)
    try:
        body = {"text": text}
        if ref:
            body["ref"] = ref
        else:
            body["selector"] = selector
        if coerce_bool(args.get("press_enter"), False) or coerce_bool(args.get("submit"), False):
            body["pressEnter"] = True
        mode = str(args.get("mode") or "").strip().lower()
        if mode in {"fill", "keyboard"}:
            body["mode"] = mode
        data = client.interact(
            task_id=_task_id(kw),
            path="/type",
            body=body,
            safe_to_replay_after_new_tab=False,
        )
        return tool_result({"success": True, "typed": text, "element": ref or selector, **data})
    except Exception as exc:
        return _tool_exception(exc)


def _handle_scroll(args: dict, **kw) -> str:
    direction = str(args.get("direction") or "down").strip().lower()
    if direction not in {"up", "down", "left", "right"}:
        return tool_error("direction must be one of: up, down, left, right", success=False)
    try:
        data = client.interact(
            task_id=_task_id(kw),
            path="/scroll",
            body={"direction": direction},
            safe_to_replay_after_new_tab=False,
        )
        return tool_result({"success": True, "scrolled": direction, **data})
    except Exception as exc:
        return _tool_exception(exc)


def _handle_back(args: dict, **kw) -> str:
    try:
        data = client.interact(
            task_id=_task_id(kw),
            path="/back",
            body={},
            safe_to_replay_after_new_tab=False,
        )
        return tool_result({"success": True, **data})
    except Exception as exc:
        return _tool_exception(exc)


def _handle_press(args: dict, **kw) -> str:
    key = str(args.get("key") or "").strip()
    if not key:
        return tool_error("key is required", success=False)
    try:
        data = client.interact(
            task_id=_task_id(kw),
            path="/press",
            body={"key": key},
            safe_to_replay_after_new_tab=False,
        )
        return tool_result({"success": True, "pressed": key, **data})
    except Exception as exc:
        return _tool_exception(exc)


def _handle_screenshot(args: dict, **kw) -> str:
    try:
        resp = client.screenshot(task_id=_task_id(kw))
        screenshots_dir = get_hermes_home() / "browser_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        path = screenshots_dir / f"camofox_screenshot_{uuid.uuid4().hex[:8]}.png"
        path.write_bytes(resp.content)
        return tool_result({
            "success": True,
            "screenshot_path": str(path),
            "mime_type": resp.headers.get("Content-Type", "image/png"),
        })
    except Exception as exc:
        return _tool_exception(exc)


def _handle_vision(args: dict, **kw) -> str:
    question = str(args.get("question") or "").strip()
    if not question:
        return tool_error("question is required", success=False)
    try:
        resp = client.screenshot(task_id=_task_id(kw))
        image_bytes = resp.content

        screenshots_dir = get_hermes_home() / "browser_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        path = screenshots_dir / f"camofox_vision_{uuid.uuid4().hex[:8]}.png"
        path.write_bytes(image_bytes)

        annotation_context = ""
        if coerce_bool(args.get("annotate"), False):
            try:
                snap = client.snapshot(task_id=_task_id(kw))
                snapshot_text = str(snap.get("snapshot") or "")
                if snapshot_text:
                    annotation_context = (
                        "\n\nAccessibility snapshot with Camofox refs/selectors context:\n"
                        f"{snapshot_text[:4000]}"
                    )
            except Exception:
                annotation_context = ""

        from agent.redact import redact_sensitive_text

        annotation_context = redact_sensitive_text(annotation_context)
        prompt = f"Analyze this Camofox browser screenshot and answer: {question}{annotation_context}"
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")

        from agent.auxiliary_client import call_llm

        response = call_llm(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                    ],
                }
            ],
            task="vision",
            temperature=0.1,
        )
        analysis = (response.choices[0].message.content or "").strip() if response.choices else ""
        analysis = redact_sensitive_text(analysis)

        result = {
            "success": True,
            "analysis": analysis,
            "screenshot_path": str(path),
            "mime_type": resp.headers.get("Content-Type", "image/png"),
        }
        vnc = client.get_vnc_url()
        if vnc:
            result["vnc_url"] = vnc
        return tool_result(result)
    except Exception as exc:
        return _tool_exception(exc)


def _handle_list_tabs(args: dict, **kw) -> str:
    try:
        session = client.get_session(_task_id(kw))
        tabs = client.list_tabs(session)
        return tool_result({
            "success": True,
            "user_id": session.user_id,
            "session_key": session.session_key,
            "active_tab_id": session.tab_id,
            "tabs": tabs,
            "count": len(tabs),
        })
    except Exception as exc:
        return _tool_exception(exc)


def _handle_get_images(args: dict, **kw) -> str:
    try:
        data = client.get_images(task_id=_task_id(kw))
        return tool_result({"success": True, **data})
    except Exception as exc:
        return _tool_exception(exc)


def _handle_close(args: dict, **kw) -> str:
    try:
        return tool_result(client.close(task_id=_task_id(kw)))
    except Exception as exc:
        return _tool_exception(exc)


def _handle_import_cookies(args: dict, **kw) -> str:
    try:
        cookies = args.get("cookies")
        if not isinstance(cookies, list):
            return tool_error("cookies must be a list of cookie objects", success=False)
        return tool_result({"success": True, **client.import_cookies(task_id=_task_id(kw), cookies=cookies)})
    except Exception as exc:
        return _tool_exception(exc)


CAMOFOX_NAVIGATE_SCHEMA = {
    "name": "camofox_navigate",
    "description": (
        "Navigate using the Camofox anti-detection browser. Use this for bot-protected "
        "or Firefox-stealth browsing while browser_* remains available for Chrome/CDP. "
        "Set delay_s when a page lazy-loads records after navigation; the tool will "
        "recapture a fresh snapshot after the delay and return only the later snapshot."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to open."},
            "delay_s": {
                "type": "number",
                "description": "Optional seconds to wait after the first snapshot before returning a fresh second snapshot.",
                "default": 0,
            },
        },
        "required": ["url"],
    },
}

CAMOFOX_SNAPSHOT_SCHEMA = {
    "name": "camofox_snapshot",
    "description": (
        "Get the current Camofox accessibility snapshot. Supports Camofox offset pagination, "
        "line ranges, and regex search so large snapshots do not require terminal curl/grep. "
        "Use offset=next_offset, line_offset/line_limit, or pattern to inspect more content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "offset": {"type": "integer", "description": "Character offset from a prior next_offset.", "default": 0},
            "full": {"type": "boolean", "description": "Fetch multiple Camofox pages within configured safety caps.", "default": False},
            "line_offset": {"type": "integer", "description": "1-indexed line offset for a windowed view."},
            "line_limit": {"type": "integer", "description": "Maximum lines to return for line window or pattern results."},
            "pattern": {"type": "string", "description": "Regex pattern to search across the snapshot."},
            "case_sensitive": {"type": "boolean", "description": "Whether pattern search is case-sensitive.", "default": False},
            "context": {"type": "integer", "description": "Context lines around pattern matches.", "default": 0},
            "include_screenshot": {"type": "boolean", "description": "Save a screenshot from the snapshot response and return its path.", "default": False},
            "summarize": {"type": "boolean", "description": "Opt-in LLM summarization after snapshot retrieval.", "default": False},
            "user_task": {"type": "string", "description": "Task hint for optional summarization."},
        },
    },
}

_REF_PROP = {"type": "string", "description": "Element ref from a camofox_snapshot, with or without @ prefix."}
_SELECTOR_PROP = {
    "type": "string",
    "description": (
        "CSS selector alternative when Camofox snapshot omits a ref. Useful for "
        "combobox/search inputs that Camofox intentionally does not annotate, "
        "for example textarea[name='q'], input[name='q'], or input[type='search']."
    ),
}

CAMOFOX_CLICK_SCHEMA = {
    "name": "camofox_click",
    "description": (
        "Click a Camofox element by ref or CSS selector. If the snapshot shows an "
        "interactive element without a ref, use selector. If recovery recreates a "
        "tab, the click is not replayed; refresh refs first."
    ),
    "parameters": {"type": "object", "properties": {"ref": _REF_PROP, "selector": _SELECTOR_PROP}},
}

CAMOFOX_TYPE_SCHEMA = {
    "name": "camofox_type",
    "description": (
        "Type text into a Camofox element by ref or CSS selector. Use selector when "
        "Camofox snapshot omits a ref for a combobox/search input; common Google "
        "selectors are textarea[name='q'] or input[name='q']. If recovery recreates "
        "a tab, typing is not replayed; refresh refs first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": _REF_PROP,
            "selector": _SELECTOR_PROP,
            "text": {"type": "string", "description": "Text to type."},
            "press_enter": {"type": "boolean", "description": "Press Enter after typing.", "default": False},
            "submit": {"type": "boolean", "description": "Alias for press_enter.", "default": False},
            "mode": {
                "type": "string",
                "enum": ["fill", "keyboard"],
                "description": "Use fill for inputs, keyboard for focused/contenteditable fields.",
                "default": "fill",
            },
        },
        "required": ["text"],
    },
}

CAMOFOX_SCROLL_SCHEMA = {
    "name": "camofox_scroll",
    "description": "Scroll the Camofox page.",
    "parameters": {
        "type": "object",
        "properties": {"direction": {"type": "string", "enum": ["up", "down", "left", "right"], "default": "down"}},
    },
}

CAMOFOX_BACK_SCHEMA = {
    "name": "camofox_back",
    "description": "Go back in the active Camofox tab.",
    "parameters": {"type": "object", "properties": {}},
}

CAMOFOX_PRESS_SCHEMA = {
    "name": "camofox_press",
    "description": "Press a keyboard key in the active Camofox tab.",
    "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
}

CAMOFOX_SCREENSHOT_SCHEMA = {
    "name": "camofox_screenshot",
    "description": "Take a Camofox screenshot and return a local PNG path, not inline base64.",
    "parameters": {"type": "object", "properties": {}},
}

CAMOFOX_VISION_SCHEMA = {
    "name": "camofox_vision",
    "description": (
        "Take a screenshot of the active Camofox page and analyze it with the "
        "configured vision model. Use this like browser_vision when the text "
        "snapshot misses visual layout, CAPTCHA-like screens, canvas content, "
        "charts, or when you need to see what is on screen. Returns analysis "
        "and a local screenshot_path. If configured, also returns vnc_url."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Question to answer about the current screen."},
            "annotate": {
                "type": "boolean",
                "description": "Include a short accessibility snapshot excerpt as context for the vision model.",
                "default": False,
            },
        },
        "required": ["question"],
    },
}

CAMOFOX_LIST_TABS_SCHEMA = {
    "name": "camofox_list_tabs",
    "description": "List live Camofox tabs for the plugin's current user identity and active task.",
    "parameters": {"type": "object", "properties": {}},
}

CAMOFOX_GET_IMAGES_SCHEMA = {
    "name": "camofox_get_images",
    "description": "List images on the active Camofox page using the native Camofox /images endpoint.",
    "parameters": {"type": "object", "properties": {}},
}

CAMOFOX_CLOSE_SCHEMA = {
    "name": "camofox_close",
    "description": "Close or release the Camofox session. Persistent/external identities use non-destructive soft cleanup.",
    "parameters": {"type": "object", "properties": {}},
}

CAMOFOX_IMPORT_COOKIES_SCHEMA = {
    "name": "camofox_import_cookies",
    "description": "Import Playwright-format cookies into the current Camofox user session. Requires plugin api_key.",
    "parameters": {
        "type": "object",
        "properties": {
            "cookies": {
                "type": "array",
                "description": "Cookie objects with at least name, value, and domain.",
                "items": {"type": "object"},
            }
        },
        "required": ["cookies"],
    },
}


TOOLS = (
    ("camofox_navigate", CAMOFOX_NAVIGATE_SCHEMA, _handle_navigate, ""),
    ("camofox_snapshot", CAMOFOX_SNAPSHOT_SCHEMA, _handle_snapshot, ""),
    ("camofox_click", CAMOFOX_CLICK_SCHEMA, _handle_click, ""),
    ("camofox_type", CAMOFOX_TYPE_SCHEMA, _handle_type, ""),
    ("camofox_scroll", CAMOFOX_SCROLL_SCHEMA, _handle_scroll, ""),
    ("camofox_back", CAMOFOX_BACK_SCHEMA, _handle_back, ""),
    ("camofox_press", CAMOFOX_PRESS_SCHEMA, _handle_press, ""),
    ("camofox_screenshot", CAMOFOX_SCREENSHOT_SCHEMA, _handle_screenshot, ""),
    ("camofox_vision", CAMOFOX_VISION_SCHEMA, _handle_vision, ""),
    ("camofox_list_tabs", CAMOFOX_LIST_TABS_SCHEMA, _handle_list_tabs, ""),
    ("camofox_get_images", CAMOFOX_GET_IMAGES_SCHEMA, _handle_get_images, ""),
    ("camofox_close", CAMOFOX_CLOSE_SCHEMA, _handle_close, ""),
    ("camofox_import_cookies", CAMOFOX_IMPORT_COOKIES_SCHEMA, _handle_import_cookies, ""),
)
