"""Local SQLite store of incoming WhatsApp messages."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import owner as owner_mod
from .gate import OWNER_PREFIX

PLUGIN_NAME = "growthx-ea"
DB_FILE = "inbox.db"
CATEGORIES = ("family", "personal", "work", "other")
MAX_TEXT = 4000
PRUNE_EVERY_S = 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    chat_name TEXT,
    chat_type TEXT,
    sender_id TEXT,
    sender_name TEXT,
    text TEXT,
    msg_type TEXT,
    ts REAL NOT NULL,
    received_at REAL NOT NULL,
    from_owner INTEGER NOT NULL DEFAULT 0,
    mentions_owner INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS messages_chat_ts ON messages (chat_id, ts);
CREATE INDEX IF NOT EXISTS messages_ts ON messages (ts);
CREATE TABLE IF NOT EXISTS chats (
    chat_id TEXT PRIMARY KEY,
    chat_name TEXT,
    chat_type TEXT,
    category TEXT,
    excluded INTEGER NOT NULL DEFAULT 0,
    handled_at REAL,
    last_ts REAL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir

        return Path(plugin_data_dir(PLUGIN_NAME))
    except Exception:
        path = owner_mod.hermes_home() / "plugin-data" / PLUGIN_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path


def _epoch(value) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    return time.time()


def _type_name(message_type) -> str:
    return str(getattr(message_type, "value", message_type) or "text").lower()


def _mentions_owner(event, owner: Optional[str], text: str) -> bool:
    if getattr(event, "reply_to_is_own_message", False):
        return True
    raw = getattr(event, "raw_message", None)
    if isinstance(raw, dict):
        bot_ids = {owner_mod.digits(b) for b in raw.get("botIds") or [] if owner_mod.digits(b)}
        if owner:
            bot_ids.add(owner)
        for mentioned in raw.get("mentionedIds") or []:
            if owner_mod.digits(mentioned) in bot_ids:
                return True
    return bool(owner) and f"@{owner}" in text


class Store:
    def __init__(self, path: Optional[Path] = None, retention_days: int = 14):
        self.path = Path(path) if path else data_dir() / DB_FILE
        self.retention_days = max(1, int(retention_days or 14))
        self._lock = threading.RLock()
        self._last_prune = 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _q(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _x(self, sql: str, params=()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    # --- ingest ---------------------------------------------------------

    def ingest(self, event, owner: Optional[str] = None) -> bool:
        """Store one WhatsApp MessageEvent. False when excluded or a duplicate."""
        src = event.source
        chat_id = str(getattr(src, "chat_id", "") or "")
        if not chat_id:
            return False
        metadata = getattr(event, "metadata", None) or {}
        from_owner = bool(metadata.get("whatsapp_from_owner"))
        text = str(getattr(event, "text", "") or "")
        if from_owner and text.startswith(OWNER_PREFIX):
            text = text[len(OWNER_PREFIX):]
        msg_type = _type_name(getattr(event, "message_type", "text"))
        if msg_type != "text":
            text = f"[{msg_type}] {text}".strip()
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + " …[truncated]"
        ts = _epoch(getattr(event, "timestamp", None))
        now = time.time()
        sender_id = getattr(src, "user_id", None) or getattr(event, "user_id", None)
        msg_id = getattr(event, "message_id", None) or hashlib.sha1(
            f"{chat_id}|{sender_id}|{ts}|{text}".encode()).hexdigest()
        chat_name = getattr(src, "chat_name", None)
        chat_type = getattr(src, "chat_type", None) or "dm"

        with self._lock:
            row = self._conn.execute("SELECT excluded FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
            if row and row["excluded"]:
                return False
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO messages (id, chat_id, chat_name, chat_type, sender_id, sender_name, text,"
                " msg_type, ts, received_at, from_owner, mentions_owner) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(msg_id), chat_id, chat_name, chat_type, sender_id,
                 getattr(src, "user_name", None) or getattr(event, "user_name", None),
                 text, msg_type, ts, now, int(from_owner), int(_mentions_owner(event, owner, text))),
            )
            if cur.rowcount == 0:
                return False
            self._conn.execute(
                "INSERT INTO chats (chat_id, chat_name, chat_type, last_ts) VALUES (?,?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET chat_name=COALESCE(excluded.chat_name, chats.chat_name),"
                " chat_type=excluded.chat_type, last_ts=MAX(COALESCE(chats.last_ts, 0), excluded.last_ts)",
                (chat_id, chat_name, chat_type, ts),
            )
        if now - self._last_prune > PRUNE_EVERY_S:
            self.prune()
        return True

    def prune(self) -> int:
        self._last_prune = time.time()
        cutoff = time.time() - self.retention_days * 86400
        return self._x("DELETE FROM messages WHERE ts < ?", (cutoff,))

    # --- chats ----------------------------------------------------------

    def chats(self, include_excluded: bool = True) -> list[dict]:
        sql = ("SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.chat_id=c.chat_id) AS n FROM chats c"
               + ("" if include_excluded else " WHERE c.excluded=0") + " ORDER BY c.last_ts DESC")
        return [dict(r) for r in self._q(sql)]

    def resolve_chat(self, query: str) -> tuple[Optional[dict], list[dict]]:
        """``(chat, [])`` on a unique match, else ``(None, candidates)``. Id, number, or name substring."""
        q = str(query or "").strip()
        if not q:
            return None, []
        rows = [dict(r) for r in self._q("SELECT * FROM chats ORDER BY last_ts DESC")]
        exact = [r for r in rows if r["chat_id"] == q]
        if exact:
            return exact[0], []
        qd = owner_mod.digits(q)
        if len(qd) >= 7 and not any(c.isalpha() for c in q.split("@", 1)[0]):
            by_number = [r for r in rows if owner_mod.digits(r["chat_id"]) == qd]
            if len(by_number) == 1:
                return by_number[0], []
            if by_number:
                return None, by_number
        ql = q.lower()
        by_name = [r for r in rows if ql in (r["chat_name"] or "").lower()]
        if len(by_name) == 1:
            return by_name[0], []
        same = [r for r in by_name if (r["chat_name"] or "").lower() == ql]
        if len(same) == 1:
            return same[0], []
        return None, by_name

    def set_category(self, chat_id: str, category: Optional[str]) -> None:
        self._x("UPDATE chats SET category=? WHERE chat_id=?", (category, chat_id))

    def mark_handled(self, chat_id: str, at: Optional[float] = None) -> None:
        self._x("UPDATE chats SET handled_at=? WHERE chat_id=?", (at or time.time(), chat_id))

    def exclude(self, chat_id: str, chat_name: Optional[str] = None) -> int:
        with self._lock:
            self._conn.execute(
                "INSERT INTO chats (chat_id, chat_name, excluded) VALUES (?,?,1) "
                "ON CONFLICT(chat_id) DO UPDATE SET excluded=1", (chat_id, chat_name))
            return self._conn.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,)).rowcount

    # --- queries --------------------------------------------------------

    def messages_since(self, since_ts: float, chat_id: Optional[str] = None, limit: Optional[int] = None,
                       newest_first: bool = False) -> list[dict]:
        sql = ("SELECT m.* FROM messages m JOIN chats c ON c.chat_id=m.chat_id WHERE c.excluded=0 AND m.ts>=?"
               + (" AND m.chat_id=?" if chat_id else "")
               + (" ORDER BY m.ts DESC" if newest_first else " ORDER BY m.ts ASC")
               + (" LIMIT ?" if limit else ""))
        params: list = [since_ts]
        if chat_id:
            params.append(chat_id)
        if limit:
            params.append(int(limit))
        return [dict(r) for r in self._q(sql, params)]

    def stats(self) -> dict:
        row = self._q("SELECT COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last FROM messages")[0]
        chats = self._q("SELECT COUNT(*) AS n, SUM(excluded) AS ex FROM chats")[0]
        return {"messages": row["n"], "first_ts": row["first"], "last_ts": row["last"],
                "chats": chats["n"], "excluded": chats["ex"] or 0}

    # --- meta -----------------------------------------------------------

    def owner_lids(self) -> set[str]:
        row = self._q("SELECT value FROM meta WHERE key='owner_lids'")
        return set(json.loads(row[0]["value"])) if row else set()

    def remember_owner_lid(self, chat_id: str) -> None:
        lid = owner_mod.digits(chat_id)
        if not lid:
            return
        with self._lock:
            lids = self.owner_lids()
            if lid in lids:
                return
            lids.add(lid)
            self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('owner_lids', ?)",
                               (json.dumps(sorted(lids)),))

    def forget(self) -> None:
        with self._lock:
            self._conn.executescript("DELETE FROM messages; DELETE FROM chats; DELETE FROM meta;")
            self._conn.execute("VACUUM")
