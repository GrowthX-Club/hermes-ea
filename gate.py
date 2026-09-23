"""pre_gateway_dispatch gate: the owner's self-chat reaches the agent, everything else is stored and dropped."""

from __future__ import annotations

import logging
import os
from typing import Callable, Mapping, Optional

from . import owner as owner_mod

logger = logging.getLogger(__name__)

ALLOW = "allow"
STORE_SKIP = "store_skip"
PASS = "pass"

OWNER_PREFIX = "[owner reply] "

_warned: set[str] = set()


def is_whatsapp(event) -> bool:
    platform = getattr(getattr(event, "source", None), "platform", None)
    return str(getattr(platform, "value", platform) or "").strip().lower() == "whatsapp"


def decide(event, pre: owner_mod.Precheck) -> tuple[str, str]:
    """``(PASS|ALLOW|STORE_SKIP, reason)`` for one event. Pure: no I/O."""
    if not is_whatsapp(event):
        return PASS, "not whatsapp"
    if pre.lockdown:
        return STORE_SKIP, "lockdown"
    src = event.source
    metadata = getattr(event, "metadata", None) or {}
    if not metadata.get("whatsapp_from_owner"):
        return STORE_SKIP, "not from owner"
    if getattr(src, "chat_type", None) != "dm":
        return STORE_SKIP, "owner message outside a dm"
    chat_id = str(getattr(src, "chat_id", "") or "")
    if owner_mod.digits(chat_id) == pre.owner and not owner_mod.is_group_id(chat_id):
        return ALLOW, "owner self-chat"
    # Only safe because the precondition passed: the bridge forwards owner-typed
    # messages solely from chats matching the one-number allowlist.
    if owner_mod.is_lid(chat_id):
        return ALLOW, "owner self-chat (lid)"
    return STORE_SKIP, "owner message in someone else's chat"


def _warn_lockdown(pre: owner_mod.Precheck) -> None:
    key = "|".join(pre.problems) or "no owner"
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(
        "\n%s\ngrowthx-ea LOCKDOWN: WhatsApp messages are being stored but the agent will not answer ANY of them, "
        "including your own self-chat.\nReason: %s\nFix it with `hermes ea setup`, then restart the gateway.\n%s",
        "!" * 72, "; ".join(pre.problems) or "owner number unknown", "!" * 72,
    )


def make_hook(get_store: Callable, env: Optional[Mapping[str, str]] = None):
    def pre_gateway_dispatch(event=None, **kwargs):
        try:
            if not is_whatsapp(event):
                return None
            pre = owner_mod.check(os.environ if env is None else env)
            if pre.lockdown:
                _warn_lockdown(pre)
            action, reason = decide(event, pre)
            if action == ALLOW:
                chat_id = str(event.source.chat_id or "")
                if owner_mod.is_lid(chat_id):
                    try:
                        get_store().remember_owner_lid(chat_id)
                    except Exception:
                        logger.exception("growthx-ea: could not record owner lid")
                text = getattr(event, "text", None)
                if isinstance(text, str) and text.startswith(OWNER_PREFIX):
                    return {"action": "rewrite", "text": text[len(OWNER_PREFIX):]}
                return {"action": "allow"}
            try:
                get_store().ingest(event, owner=pre.owner)
            except Exception:
                logger.exception("growthx-ea: failed to store a WhatsApp message")
            return {"action": "skip", "reason": f"growthx-ea listen-only: {reason}"}
        except Exception:
            logger.exception("growthx-ea: gate error, dropping message")
            return {"action": "skip", "reason": "growthx-ea: gate error"}

    return pre_gateway_dispatch
