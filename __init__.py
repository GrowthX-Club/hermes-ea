"""growthx-ea: listen-only WhatsApp inbox for a personal Executive Assistant on Hermes."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from . import cli, gate, guard, tools
from .store import Store

logger = logging.getLogger(__name__)

_store = None
_store_lock = threading.Lock()
_retention_days = 14


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store(retention_days=_retention_days)
        return _store


def _owner_lids():
    return get_store().owner_lids()


def register(ctx) -> None:
    global _retention_days
    try:
        _retention_days = int(ctx.get_config("retention_days", default=14) or 14)
    except Exception:
        _retention_days = 14

    ctx.register_hook("pre_gateway_dispatch", gate.make_hook(get_store))
    ctx.register_hook("pre_tool_call", guard.make_hook(_owner_lids))

    for name, schema in tools.SCHEMAS.items():
        ctx.register_tool(name=name, toolset=tools.TOOLSET, schema=schema,
                          handler=tools.make_handler(name, get_store))

    skill_md = Path(__file__).parent / "skills" / "morning-brief" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill("morning-brief", skill_md)

    ctx.register_cli_command(name="ea", help="GrowthX EA: WhatsApp inbox setup and status",
                             setup_fn=cli.setup_argparse, handler_fn=cli.make_handler(get_store))
