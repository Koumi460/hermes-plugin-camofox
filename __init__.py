"""Camofox local anti-detection browser plugin."""

from __future__ import annotations

from .tools import TOOLS, _check_camofox_available


def register(ctx) -> None:
    """Register the separate camofox_* toolset."""
    for name, schema, handler, emoji in TOOLS:
        ctx.register_tool(
            name=name,
            toolset="camofox",
            schema=schema,
            handler=handler,
            check_fn=_check_camofox_available,
            emoji=emoji,
        )
