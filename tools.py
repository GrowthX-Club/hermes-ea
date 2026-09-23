"""Agent-facing tools over the WhatsApp store. Every handler returns a JSON string."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Callable

from . import owner as owner_mod
from .store import CATEGORIES, Store

TOOLSET = "growthx_ea"
PREVIEW_CHARS = 200
RECENT_PER_CHAT = 5
MAX_CHATS = 60

NO_OWNER_REPLIES = (
    "This plugin only sees messages other people send. It cannot see replies the owner typed on their "
    "phone, so a chat can look unanswered when it is not."
)


def _fmt(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def _trim(text, n=PREVIEW_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _label(chat: dict) -> str:
    return chat.get("chat_name") or owner_mod.digits(chat.get("chat_id")) or chat.get("chat_id")


def _chat_out(chat: dict) -> dict:
    return {"chat": _label(chat), "chat_id": chat["chat_id"], "type": chat.get("chat_type"),
            "category": chat.get("category")}


def _num(args: dict, key: str, default, lo, hi):
    try:
        value = float(args.get(key, default) if args.get(key) is not None else default)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _resolve(store: Store, args: dict):
    chat, candidates = store.resolve_chat(args.get("chat", ""))
    if chat:
        return chat, None
    if not candidates:
        return None, {"error": f"No chat matches {args.get('chat')!r}. Use wa_inbox to see chat names."}
    return None, {"error": "More than one chat matches. Call again with one of these chat_id values.",
                  "candidates": [_chat_out(c) for c in candidates[:15]]}


def _data_note(store: Store) -> str | None:
    st = store.stats()
    if not st["first_ts"]:
        return "No WhatsApp messages stored yet. The plugin only records messages that arrive after it was installed."
    days = (time.time() - st["first_ts"]) / 86400
    if days < 3:
        return f"Only {days:.1f} days of WhatsApp history so far (since {_fmt(st['first_ts'])}). Say so in any summary."
    return None


def wa_inbox(store: Store, args: dict) -> dict:
    hours = _num(args, "since_hours", 24, 1, 24 * 30)
    category = (args.get("category") or "").strip().lower() or None
    msgs = store.messages_since(time.time() - hours * 3600)
    chats = {c["chat_id"]: c for c in store.chats(include_excluded=False)}
    grouped: dict[str, list] = {}
    for m in msgs:
        grouped.setdefault(m["chat_id"], []).append(m)
    rollup = []
    for chat_id, items in grouped.items():
        chat = chats.get(chat_id) or {"chat_id": chat_id, "chat_name": items[-1]["chat_name"],
                                      "chat_type": items[-1]["chat_type"]}
        if category and (chat.get("category") or "uncategorised") != category:
            continue
        rollup.append({
            **_chat_out(chat),
            "count": len(items),
            "mentions_you": sum(m["mentions_owner"] for m in items),
            "last": _fmt(items[-1]["ts"]),
            "_last_ts": items[-1]["ts"],
            "recent": [{"at": _fmt(m["ts"]), "from": m["sender_name"] or owner_mod.digits(m["sender_id"]),
                        "text": _trim(m["text"])} for m in items[-RECENT_PER_CHAT:]],
        })
    rollup.sort(key=lambda r: r.pop("_last_ts"), reverse=True)
    out = {"since_hours": hours, "total_messages": sum(r["count"] for r in rollup), "chats": rollup[:MAX_CHATS]}
    if len(rollup) > MAX_CHATS:
        out["more_chats"] = len(rollup) - MAX_CHATS
    note = _data_note(store)
    if note:
        out["note"] = note
    return out


def wa_read(store: Store, args: dict) -> dict:
    chat, err = _resolve(store, args)
    if err:
        return err
    hours = _num(args, "since_hours", 72, 1, 24 * 30)
    limit = int(_num(args, "limit", 100, 1, 500))
    msgs = store.messages_since(time.time() - hours * 3600, chat_id=chat["chat_id"], limit=limit, newest_first=True)
    msgs.reverse()
    return {**_chat_out(chat), "since_hours": hours, "count": len(msgs),
            "messages": [{"at": _fmt(m["ts"]), "from": m["sender_name"] or owner_mod.digits(m["sender_id"]),
                          "type": m["msg_type"], "text": m["text"]} for m in msgs]}


def wa_waiting_on_me(store: Store, args: dict) -> dict:
    hours = _num(args, "since_hours", 72, 1, 24 * 30)
    owner = owner_mod.check().owner
    msgs = store.messages_since(time.time() - hours * 3600)
    chats = {c["chat_id"]: c for c in store.chats(include_excluded=False)}
    dms: dict[str, list] = {}
    mentions = []
    for m in msgs:
        chat = chats.get(m["chat_id"], {})
        handled = chat.get("handled_at") or 0
        if m["chat_type"] == "dm":
            if owner and owner_mod.digits(m["chat_id"]) == owner:
                continue
            dms.setdefault(m["chat_id"], []).append(m)
        elif m["mentions_owner"] and m["ts"] > handled:
            mentions.append({**_chat_out(chat or m), "at": _fmt(m["ts"]),
                             "from": m["sender_name"] or owner_mod.digits(m["sender_id"]), "text": _trim(m["text"])})
    waiting = []
    for chat_id, items in dms.items():
        chat = chats.get(chat_id, items[-1])
        handled = chat.get("handled_at") or 0
        latest = items[-1]
        if latest["from_owner"] or latest["ts"] <= handled:
            continue
        tail = []
        for m in reversed(items):
            if m["from_owner"] or m["ts"] <= handled:
                break
            tail.append(m)
        waiting.append({**_chat_out(chat), "waiting_since": _fmt(tail[-1]["ts"]), "messages": len(tail),
                        "latest": _trim(latest["text"]), "_ts": latest["ts"]})
    waiting.sort(key=lambda w: w.pop("_ts"), reverse=True)
    out = {"since_hours": hours, "dms_waiting": waiting, "group_mentions": mentions,
           "caveat": NO_OWNER_REPLIES + " Treat this list as a best guess and ask the owner. "
                     "Call wa_mark_handled when the owner says a chat is dealt with."}
    note = _data_note(store)
    if note:
        out["note"] = note
    return out


def wa_set_category(store: Store, args: dict) -> dict:
    category = str(args.get("category") or "").strip().lower()
    if category not in CATEGORIES:
        return {"error": f"category must be one of: {', '.join(CATEGORIES)}"}
    chat, err = _resolve(store, args)
    if err:
        return err
    store.set_category(chat["chat_id"], category)
    return {"ok": True, **_chat_out({**chat, "category": category})}


def wa_mark_handled(store: Store, args: dict) -> dict:
    chat, err = _resolve(store, args)
    if err:
        return err
    store.mark_handled(chat["chat_id"])
    return {"ok": True, **_chat_out(chat), "handled_at": _fmt(time.time())}


def wa_exclude(store: Store, args: dict) -> dict:
    chat, err = _resolve(store, args)
    if err:
        return err
    deleted = store.exclude(chat["chat_id"], chat.get("chat_name"))
    return {"ok": True, **_chat_out(chat), "deleted_messages": deleted,
            "note": "This chat is no longer recorded. Its stored history is deleted."}


_CHAT_PARAM = {"type": "string",
               "description": "Chat name (case-insensitive, partial is fine), phone number, or exact chat_id."}

SCHEMAS = {
    "wa_inbox": {
        "name": "wa_inbox",
        "description": (
            "Summary of the owner's recent incoming WhatsApp messages (DMs and groups), one entry per chat with "
            "message count, last message time, category, how often the owner was @mentioned, and the latest few "
            "messages (truncated). Read-only: nothing is sent to anyone. For a morning brief or 'what did I miss', "
            "first load the recipe with skill_view('growthx-ea:morning-brief'). Use wa_read for a full chat."
        ),
        "parameters": {"type": "object", "properties": {
            "since_hours": {"type": "number", "description": "Look-back window in hours. Default 24."},
            "category": {"type": "string", "enum": [*CATEGORIES, "uncategorised"],
                         "description": "Only chats in this category."},
        }},
    },
    "wa_read": {
        "name": "wa_read",
        "description": (
            "Full stored messages of one WhatsApp chat, oldest first. If the name matches several chats you get "
            "candidates back; call again with the chat_id. Read-only."
        ),
        "parameters": {"type": "object", "properties": {
            "chat": _CHAT_PARAM,
            "since_hours": {"type": "number", "description": "Look-back window in hours. Default 72."},
            "limit": {"type": "integer", "description": "Max messages (newest kept). Default 100."},
        }, "required": ["chat"]},
    },
    "wa_waiting_on_me": {
        "name": "wa_waiting_on_me",
        "description": (
            "Best guess at WhatsApp chats waiting on the owner: DMs whose latest message is from the other person "
            "and not marked handled, plus group messages that @mention the owner or reply to them. The plugin "
            "cannot see replies the owner sent from their phone, so present this as 'might be waiting' and let "
            "the owner correct it."
        ),
        "parameters": {"type": "object", "properties": {
            "since_hours": {"type": "number", "description": "Look-back window in hours. Default 72."},
        }},
    },
    "wa_set_category": {
        "name": "wa_set_category",
        "description": (
            "Save a category for a WhatsApp chat so future briefs can group it: family, personal, work or other. "
            "Use what memory says about the owner's people."
        ),
        "parameters": {"type": "object", "properties": {
            "chat": _CHAT_PARAM,
            "category": {"type": "string", "enum": list(CATEGORIES)},
        }, "required": ["chat", "category"]},
    },
    "wa_mark_handled": {
        "name": "wa_mark_handled",
        "description": ("Mark a WhatsApp chat as dealt with right now, so wa_waiting_on_me stops listing it until "
                        "a new message arrives. Use when the owner says they replied or it needs nothing."),
        "parameters": {"type": "object", "properties": {"chat": _CHAT_PARAM}, "required": ["chat"]},
    },
    "wa_exclude": {
        "name": "wa_exclude",
        "description": ("Stop recording a WhatsApp chat and delete everything stored from it. Only when the owner "
                        "asks (e.g. 'stop reading my family group'). Cannot be undone."),
        "parameters": {"type": "object", "properties": {"chat": _CHAT_PARAM}, "required": ["chat"]},
    },
}

_IMPLS = {"wa_inbox": wa_inbox, "wa_read": wa_read, "wa_waiting_on_me": wa_waiting_on_me,
          "wa_set_category": wa_set_category, "wa_mark_handled": wa_mark_handled, "wa_exclude": wa_exclude}


def make_handler(name: str, get_store: Callable[[], Store]):
    impl = _IMPLS[name]

    def handler(args: dict = None, **kwargs) -> str:
        try:
            return json.dumps(impl(get_store(), args or {}), ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": f"{name} failed: {type(exc).__name__}: {exc}"})

    handler.__name__ = name
    return handler
