"""Profile-scoped identity helpers for the Camofox plugin."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, Optional

from hermes_constants import get_hermes_home

STATE_DIR_NAME = "browser_auth"
STATE_SUBDIR = "camofox_plugin"


def get_state_dir() -> Path:
    """Return the profile-scoped root used to derive stable identities."""
    return get_hermes_home() / STATE_DIR_NAME / STATE_SUBDIR


def get_identity(task_id: Optional[str] = None) -> Dict[str, str]:
    """Return a deterministic Camofox user/session identity for this profile."""
    scope_root = str(get_state_dir())
    logical_scope = task_id or "default"
    user_digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"camofox-plugin-user:{scope_root}",
    ).hex[:10]
    session_digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"camofox-plugin-session:{scope_root}:{logical_scope}",
    ).hex[:16]
    return {
        "user_id": f"hermes_{user_digest}",
        "session_key": f"task_{session_digest}",
    }
