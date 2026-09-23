"""`hermes ea ...` commands."""

from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from typing import Callable

from . import gate, owner as owner_mod, setup_cfg
from .store import Store

RESTART_HINT = "Restart the gateway: hermes gateway restart"


def _fmt(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "never"


def _env_status(pre: owner_mod.Precheck) -> list[str]:
    lines = []
    if pre.lockdown:
        lines.append("Lockdown:      YES. Messages are stored, but the agent answers nobody, not even you.")
        for p in pre.problems or ["owner number unknown"]:
            lines.append(f"  - {p}")
    else:
        lines.append(f"Owner number:  {pre.owner}")
        lines.append("Lockdown:      no. Only your self-chat reaches the agent.")
    env = os.environ
    expected = {"WHATSAPP_MODE": "bot", "WHATSAPP_FORWARD_OWNER_MESSAGES": "true",
                "WHATSAPP_DM_POLICY": "pairing", "WHATSAPP_GROUP_POLICY": "open"}
    for key, want in expected.items():
        have = str(env.get(key, "")).strip().lower()
        if have != want:
            lines.append(f"  ! {key} is {have or 'unset'!r}, expected {want!r}")
    if str(env.get("WHATSAPP_REQUIRE_MENTION", "")).strip().lower() in {"1", "true", "yes", "on"}:
        lines.append("  ! WHATSAPP_REQUIRE_MENTION is on; group messages that don't mention you are never stored")
    home = owner_mod.home_channel(env)
    if not home:
        lines.append("  ! WHATSAPP_HOME_CHANNEL is unset; scheduled briefs to WhatsApp will be blocked")
    elif pre.owner and owner_mod.digits(home) != pre.owner:
        lines.append("  ! WHATSAPP_HOME_CHANNEL is not your number; sends there will be blocked")
    return lines


def cmd_status(store: Store) -> int:
    pre = owner_mod.check()
    for line in _env_status(pre):
        print(line)
    st = store.stats()
    print(f"Stored:        {st['messages']} messages in {st['chats']} chats")
    print(f"First / last:  {_fmt(st['first_ts'])} / {_fmt(st['last_ts'])}")
    print(f"Retention:     {store.retention_days} days")
    print(f"Database:      {store.path}")
    excluded = [c for c in store.chats() if c["excluded"]]
    print("Excluded:      " + (", ".join(c["chat_name"] or c["chat_id"] for c in excluded) if excluded else "none"))
    return 1 if pre.lockdown else 0


def cmd_chats(store: Store) -> int:
    rows = store.chats()
    if not rows:
        print("No chats stored yet.")
        return 0
    for c in rows:
        flags = " [excluded]" if c["excluded"] else ""
        print(f"{_fmt(c['last_ts'])}  {c['chat_type'] or '?':5}  {(c['category'] or '-'):8}  {c['n']:5}  "
              f"{c['chat_name'] or '-'}  ({c['chat_id']}){flags}")
    return 0


def cmd_exclude(store: Store, query: str) -> int:
    chat, candidates = store.resolve_chat(query)
    if not chat:
        if not candidates:
            print(f"No stored chat matches {query!r}. Use a chat id to exclude a chat that has no messages yet.")
            if "@" not in query:
                return 1
            chat = {"chat_id": query, "chat_name": None}
        else:
            print("More than one chat matches. Use the chat id:")
            for c in candidates:
                print(f"  {c['chat_id']}  {c['chat_name'] or ''}")
            return 1
    deleted = store.exclude(chat["chat_id"], chat.get("chat_name"))
    print(f"Excluded {chat.get('chat_name') or chat['chat_id']}. Deleted {deleted} stored messages.")
    return 0


def cmd_forget(store: Store, yes: bool) -> int:
    if not yes:
        answer = input(f"Delete ALL stored WhatsApp messages and chat settings in {store.path}? Type 'yes': ")
        if answer.strip().lower() != "yes":
            print("Nothing deleted.")
            return 1
    store.forget()
    print("Done. Every stored WhatsApp message, category and exclusion is gone.")
    return 0


def _event(chat_id, chat_type="dm", from_owner=False, platform="whatsapp"):
    return SimpleNamespace(
        text="selftest", metadata={"whatsapp_from_owner": True} if from_owner else {},
        source=SimpleNamespace(platform=platform, chat_id=chat_id, chat_type=chat_type,
                               chat_name="selftest", user_id=chat_id, user_name="selftest"),
    )


def selftest_cases(owner: str):
    contact = "15550001111" if owner != "15550001111" else "15550002222"
    allow = gate.ALLOW
    skip = gate.STORE_SKIP
    return [
        ("You, in your self-chat", _event(f"{owner}@s.whatsapp.net", from_owner=True), allow),
        ("You, in your self-chat (LID form)", _event("123456789012345@lid", from_owner=True), allow),
        ("You, typing in a contact's DM", _event(f"{contact}@s.whatsapp.net", from_owner=True), skip),
        ("A contact's DM to you", _event(f"{contact}@s.whatsapp.net"), skip),
        ("A group message", _event("120363000000000000@g.us", chat_type="group"), skip),
        ("Your own number, but not marked as typed by you", _event(f"{owner}@s.whatsapp.net"), skip),
        ("A Telegram message (not WhatsApp)", _event("12345", platform="telegram"), gate.PASS),
    ]


def cmd_selftest() -> int:
    pre = owner_mod.check()
    owner = pre.owner or "919999999999"
    print("Running synthetic messages through the gate with your current settings.")
    if pre.lockdown:
        print("LOCKDOWN is on, so everything on WhatsApp should be stored and dropped:")
        for p in pre.problems:
            print(f"  - {p}")
    words = {gate.ALLOW: "AGENT SEES IT", gate.STORE_SKIP: "stored, no reply", gate.PASS: "not handled by this plugin"}
    failures = 0
    for label, event, expected in selftest_cases(owner):
        if pre.lockdown and expected == gate.ALLOW:
            expected = gate.STORE_SKIP
        action, reason = gate.decide(event, pre)
        ok = action == expected
        failures += not ok
        print(f"  [{'ok' if ok else 'FAIL'}] {label:48} -> {words[action]} ({reason})")
    print("All good." if not failures else f"{failures} case(s) did not match. Do not use the plugin until fixed.")
    return 1 if failures else 0


def cmd_setup(store: Store, number_arg: str | None) -> int:
    number = None
    raw = number_arg
    while number is None:
        if raw is None:
            raw = input("Your WhatsApp number, with country code, digits only (e.g. 919876543210): ")
        try:
            number = setup_cfg.normalize_number(raw)
        except ValueError as exc:
            print(f"That number doesn't look right: {exc}.")
            if number_arg is not None:
                return 1
            raw = None
    home = owner_mod.hermes_home()
    env_backup = setup_cfg.update_env(home / ".env", number)
    try:
        cfg_backup = setup_cfg.update_config(home / "config.yaml")
    except Exception as exc:
        print(f"Could not update {home / 'config.yaml'}: {exc}")
        print("Add this by hand:\n  whatsapp:\n    unauthorized_dm_behavior: ignore")
        cfg_backup = None
    print(f"Saved your WhatsApp settings to {home / '.env'} and {home / 'config.yaml'}.")
    for b in (env_backup, cfg_backup):
        if b:
            print(f"  Backup of the old file: {b}")
    for key, value in setup_cfg.env_values(number).items():
        os.environ[key] = value
    os.environ.pop("WHATSAPP_ALLOW_ALL_USERS", None)
    print()
    cmd_status(store)
    print()
    pre = owner_mod.check()
    if pre.lockdown:
        print("Something still overrides your settings (see above). The agent will stay silent on WhatsApp until "
              "that is fixed.")
    else:
        print(f"Done. Only your own chat with yourself ({number}) can talk to the agent. Everyone else's messages "
              "are saved on this computer for your briefs, and nobody gets a reply.")
    print(RESTART_HINT)
    return 1 if pre.lockdown else 0


def setup_argparse(subparser) -> None:
    subs = subparser.add_subparsers(dest="ea_command")
    p = subs.add_parser("setup", help="Configure WhatsApp safely for your own number")
    p.add_argument("number", nargs="?", help="Your WhatsApp number with country code, digits only")
    subs.add_parser("status", help="Show safety checks and what is stored")
    subs.add_parser("chats", help="List stored chats")
    p = subs.add_parser("exclude", help="Stop recording a chat and delete its history")
    p.add_argument("chat", help="Chat name, number or id")
    p = subs.add_parser("forget", help="Delete every stored message")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    subs.add_parser("selftest", help="Check the listen-only gate against your current settings")


def make_handler(get_store: Callable[[], Store]):
    def handle(args) -> int:
        sub = getattr(args, "ea_command", None)
        if sub == "selftest":
            return cmd_selftest()
        if sub == "setup":
            return cmd_setup(get_store(), getattr(args, "number", None))
        if sub == "status":
            return cmd_status(get_store())
        if sub == "chats":
            return cmd_chats(get_store())
        if sub == "exclude":
            return cmd_exclude(get_store(), args.chat)
        if sub == "forget":
            return cmd_forget(get_store(), getattr(args, "yes", False))
        print("Usage: hermes ea {setup|status|chats|exclude <chat>|forget|selftest}")
        return 1

    return handle
