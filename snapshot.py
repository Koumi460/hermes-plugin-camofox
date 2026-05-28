"""Snapshot shaping helpers for the Camofox plugin."""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from hermes_constants import get_hermes_home


def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def coerce_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_snapshot_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Camofox snapshot field names for Hermes tool results."""
    snapshot = str(data.get("snapshot") or "")
    return {
        "url": data.get("url") or "",
        "snapshot": snapshot,
        "element_count": int(data.get("refsCount") or data.get("element_count") or 0),
        "truncated": bool(data.get("truncated") or False),
        "has_more": bool(data.get("hasMore") or False),
        "next_offset": data.get("nextOffset"),
        "total_chars": int(data.get("totalChars") or len(snapshot)),
    }


def save_screenshot_payload(screenshot: Any) -> Optional[str]:
    """Persist a Camofox inline screenshot payload and return its path."""
    if not isinstance(screenshot, dict):
        return None
    raw_data = screenshot.get("data")
    if not isinstance(raw_data, str) or not raw_data:
        return None
    try:
        image_bytes = base64.b64decode(raw_data, validate=True)
    except Exception:
        return None

    screenshots_dir = get_hermes_home() / "browser_screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    path = screenshots_dir / f"camofox_screenshot_{uuid.uuid4().hex[:8]}.png"
    path.write_bytes(image_bytes)
    return str(path)


def apply_line_window(
    text: str,
    *,
    line_offset: int = 1,
    line_limit: int = 500,
) -> Dict[str, Any]:
    """Return a 1-indexed line window with metadata."""
    lines = text.splitlines()
    total = len(lines)
    start = max(1, line_offset)
    limit = max(1, line_limit)
    end = min(total, start + limit - 1)
    selected = lines[start - 1:end] if total else []
    return {
        "snapshot": "\n".join(selected),
        "line_count": total,
        "line_start": start if total else 0,
        "line_end": end if total else 0,
        "line_has_more": total > end,
    }


def search_lines(
    text: str,
    *,
    pattern: str,
    case_sensitive: bool = False,
    context: int = 0,
    limit: int = 200,
) -> Dict[str, Any]:
    """Search snapshot lines and return matching lines plus context."""
    flags = 0 if case_sensitive else re.IGNORECASE
    rx = re.compile(pattern, flags)
    lines = text.splitlines()
    context = max(0, context)
    limit = max(1, limit)

    selected_indexes: set[int] = set()
    match_count = 0
    for idx, line in enumerate(lines):
        if rx.search(line):
            match_count += 1
            if len(selected_indexes) >= limit + (2 * context * limit):
                continue
            for nearby in range(max(0, idx - context), min(len(lines), idx + context + 1)):
                selected_indexes.add(nearby)

    selected = [f"{idx + 1}|{lines[idx]}" for idx in sorted(selected_indexes)]
    return {
        "snapshot": "\n".join(selected),
        "line_count": len(lines),
        "match_count": match_count,
        "searched_chars": len(text),
        "truncated_search": match_count > limit,
    }


def merge_snapshot_pages(pages: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge paginated Camofox snapshot pages into one normalized payload."""
    normalized: List[Dict[str, Any]] = [normalize_snapshot_response(page) for page in pages]
    if not normalized:
        return normalize_snapshot_response({})
    first = normalized[0]
    last = normalized[-1]
    text = "\n".join(page["snapshot"] for page in normalized if page["snapshot"])
    return {
        **last,
        "url": last.get("url") or first.get("url", ""),
        "snapshot": text,
        "element_count": max(page.get("element_count", 0) for page in normalized),
        "truncated": bool(last.get("truncated")),
        "has_more": bool(last.get("has_more")),
        "next_offset": last.get("next_offset"),
        "total_chars": int(last.get("total_chars") or len(text)),
    }


def shape_snapshot(
    data: Dict[str, Any],
    *,
    line_offset: Optional[int] = None,
    line_limit: Optional[int] = None,
    pattern: str = "",
    case_sensitive: bool = False,
    context: int = 0,
) -> Dict[str, Any]:
    """Apply optional search/range shaping to a normalized snapshot payload."""
    result = {**data, **normalize_snapshot_response(data)}
    screenshot_path = save_screenshot_payload(data.get("screenshot"))
    if screenshot_path:
        result["screenshot_path"] = screenshot_path

    text = result["snapshot"]
    if pattern:
        shaped = search_lines(
            text,
            pattern=pattern,
            case_sensitive=case_sensitive,
            context=context,
            limit=line_limit or 200,
        )
        result.update(shaped)
        result["filtered_by_pattern"] = pattern
    elif line_offset is not None or line_limit is not None:
        shaped = apply_line_window(
            text,
            line_offset=line_offset or 1,
            line_limit=line_limit or 500,
        )
        result.update(shaped)
    else:
        result["line_count"] = len(text.splitlines())

    if result.get("has_more") or result.get("line_has_more"):
        result["hint"] = (
            "Use camofox_snapshot with offset=next_offset, "
            "line_offset/line_limit, or pattern to inspect more content."
        )
    return result
