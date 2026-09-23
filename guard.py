"""pre_tool_call guard: the agent may only message the owner on WhatsApp."""

from __future__ import annotations

import logging
import os
import re
from typing import Callable, Iterable, Mapping, Optional

from . import owner as owner_mod

logger = logging.getLogger(__name__)

SEND_TOOLS = {"send_message"}
CRON_TOOLS = {"cronjob_manage", "cronjob"}
SHELL_TOOLS = {"terminal": "command", "execute_code": "code"}

DRAFT_HINT = ("Do not message anyone on WhatsApp except the owner. Write the draft in your reply to the owner "
              "in their self-chat so they can copy it and send it themselves.")

_HERMES_SEND_RE = re.compile(r"\bhermes\b[^\n;|&]*\bsend\b", re.IGNORECASE)
_BRIDGE_SEND_RE = re.compile(r"(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?/send", re.IGNORECASE)


def is_owner_ref(ref: str, owner: Optional[str], owner_lids: Iterable[str] = ()) -> bool:
    ref = str(ref or "").strip()
    if not owner or not ref or owner_mod.is_group_id(ref):
        return False
    if owner_mod.is_lid(ref):
        return owner_mod.digits(ref) in set(owner_lids)
    if "@" in ref and not ref.lower().endswith("@s.whatsapp.net"):
        return False
    if not re.fullmatch(r"\+?[\d\s\-]+(:\d+)?(@s\.whatsapp\.net)?", ref, re.IGNORECASE):
        return False
    return owner_mod.digits(ref) == owner


def _home_problem(owner: Optional[str], env: Mapping[str, str], lids) -> Optional[str]:
    home = owner_mod.home_channel(env)
    if not home:
        return ("the WhatsApp home channel is not set in WHATSAPP_HOME_CHANNEL, so this plugin cannot confirm it is "
                "the owner's own chat. Run `hermes ea setup` to point it at the owner's number")
    if not is_owner_ref(home, owner, lids):
        return "the WhatsApp home channel (WHATSAPP_HOME_CHANNEL) is not the owner's own chat"
    return None


def _whatsapp_target_problem(target: str, owner, env, lids) -> Optional[str]:
    """None if ``target`` is safe. ``target`` is one send/deliver element, e.g. ``whatsapp:9198...``."""
    platform, _, ref = str(target or "").strip().partition(":")
    platform = platform.strip().lower()
    if platform == "all":
        return _home_problem(owner, env, lids)
    if platform != "whatsapp":
        return None
    if not owner:
        return "the plugin is in lockdown (WHATSAPP_ALLOWED_USERS is not exactly one number), so no WhatsApp sends"
    if not ref.strip():
        return _home_problem(owner, env, lids)
    if is_owner_ref(ref, owner, lids):
        return None
    return f"{ref.strip()!r} is not the owner's own WhatsApp chat"


def _check_send(args: dict, owner, env, lids) -> Optional[str]:
    action = str(args.get("action") or "send").strip().lower()
    if action == "list":
        return None
    return _whatsapp_target_problem(args.get("target", ""), owner, env, lids)


def _check_cron(args: dict, owner, env, lids) -> Optional[str]:
    action = str(args.get("action") or "").strip().lower()
    if action not in ("create", "update"):
        return None
    for key in ("deliver", "failure_deliver"):
        value = args.get(key)
        if value is None:
            if action == "create" and key == "deliver":
                value = "origin"
            else:
                continue
        parts = value if isinstance(value, (list, tuple)) else str(value).split(",")
        for part in (str(p).strip() for p in parts):
            if not part:
                continue
            if part.lower() == "origin":
                home = owner_mod.home_channel(env)
                if home and not is_owner_ref(home, owner, lids):
                    return (f"{key}='origin' can fall back to the WhatsApp home channel, "
                            "which is not the owner's own chat")
                continue
            problem = _whatsapp_target_problem(part, owner, env, lids)
            if problem:
                return f"{key} target {part!r}: {problem}"
    return None


def _check_shell(tool_name: str, args: dict) -> Optional[str]:
    text = str(args.get(SHELL_TOOLS[tool_name]) or "")
    if _HERMES_SEND_RE.search(text) and "whatsapp" in text.lower():
        return "sending WhatsApp messages through `hermes send` from a tool is not allowed"
    if _BRIDGE_SEND_RE.search(text):
        return "calling the WhatsApp bridge's /send endpoint directly is not allowed"
    return None


def check_tool_call(tool_name: str, args: dict, env: Mapping[str, str], owner_lids: Iterable[str] = ()) -> Optional[str]:
    """A block message, or None to let the call through."""
    args = args if isinstance(args, dict) else {}
    lids = set(owner_lids)
    if tool_name in SEND_TOOLS:
        problem = _check_send(args, owner_mod.check(env).owner, env, lids)
    elif tool_name in CRON_TOOLS:
        problem = _check_cron(args, owner_mod.check(env).owner, env, lids)
    elif tool_name in SHELL_TOOLS:
        problem = _check_shell(tool_name, args)
    else:
        return None
    return f"Blocked by growthx-ea: {problem}. {DRAFT_HINT}" if problem else None


def make_hook(get_owner_lids: Callable[[], Iterable[str]], env: Optional[Mapping[str, str]] = None):
    guarded = SEND_TOOLS | CRON_TOOLS | set(SHELL_TOOLS)

    def pre_tool_call(tool_name=None, args=None, task_id=None, **kwargs):
        if tool_name not in guarded:
            return None
        try:
            try:
                lids = get_owner_lids()
            except Exception:
                lids = ()
            message = check_tool_call(tool_name, args or {}, os.environ if env is None else env, lids)
            return {"action": "block", "message": message} if message else None
        except Exception as exc:
            logger.exception("growthx-ea: outbound guard error")
            return {"action": "block", "message": f"Blocked by growthx-ea: guard error ({type(exc).__name__}). {DRAFT_HINT}"}

    return pre_tool_call
