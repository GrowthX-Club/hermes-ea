import json
import time
from datetime import datetime, timedelta

from conftest import CONTACT, OWNER, MessageType, make_event

from growthx_ea import tools
from growthx_ea.store import Store

GROUP = "120363041234567890@g.us"


def call(store, name, **args):
    return json.loads(tools.make_handler(name, lambda: store)(args))


def dm(text, mid, sender=CONTACT, name="Asha", ts=None, from_owner=False):
    return make_event(f"{sender}@s.whatsapp.net", text=text, message_id=mid, sender_name=name,
                      chat_name=name, ts=ts, from_owner=from_owner)


def test_ingest_dedupes_by_message_id(store):
    assert store.ingest(dm("hello", "m1"), owner=OWNER)
    assert not store.ingest(dm("hello", "m1"), owner=OWNER)
    assert store.stats()["messages"] == 1


def test_owner_prefix_stripped_and_flagged(store):
    store.ingest(dm("on my way", "m1", from_owner=True), owner=OWNER)
    row = store.messages_since(0)[0]
    assert row["text"] == "on my way"
    assert row["from_owner"] == 1


def test_media_stored_as_marker_plus_caption(store):
    ev = make_event(f"{CONTACT}@s.whatsapp.net", text="look at this", message_id="m1",
                    message_type=MessageType.PHOTO)
    store.ingest(ev, owner=OWNER)
    assert store.messages_since(0)[0]["text"] == "[photo] look at this"
    ev = make_event(f"{CONTACT}@s.whatsapp.net", text="", message_id="m2", message_type=MessageType.VOICE)
    store.ingest(ev, owner=OWNER)
    assert store.messages_since(0)[1]["text"] == "[voice]"


def test_missing_message_id_gets_stable_hash(store):
    ts = datetime.now()
    assert store.ingest(dm("x", None, ts=ts), owner=OWNER)
    assert not store.ingest(dm("x", None, ts=ts), owner=OWNER)


def test_exclude_deletes_and_stops_storing(store):
    store.ingest(dm("a", "m1"), owner=OWNER)
    store.ingest(dm("b", "m2"), owner=OWNER)
    assert store.exclude(f"{CONTACT}@s.whatsapp.net") == 2
    assert not store.ingest(dm("c", "m3"), owner=OWNER)
    assert store.stats()["messages"] == 0


def test_retention_prune(tmp_path):
    s = Store(path=tmp_path / "r.db", retention_days=14)
    s.ingest(dm("new", "m2"), owner=OWNER)
    s.ingest(dm("old", "m1", ts=datetime.now() - timedelta(days=20)), owner=OWNER)
    assert s.stats()["messages"] == 2
    assert s.prune() == 1
    assert [m["text"] for m in s.messages_since(0)] == ["new"]
    s.close()


def test_forget_wipes_everything(store):
    store.ingest(dm("a", "m1"), owner=OWNER)
    store.remember_owner_lid("123@lid")
    store.forget()
    assert store.stats()["messages"] == 0
    assert store.chats() == []
    assert store.owner_lids() == set()


def test_mentions_owner_from_raw_mentions_and_replies(store):
    raw = {"mentionedIds": [f"{OWNER}@s.whatsapp.net"], "botIds": []}
    store.ingest(make_event(GROUP, chat_type="group", message_id="g1", raw=raw, chat_name="Founders"), owner=OWNER)
    store.ingest(make_event(GROUP, chat_type="group", message_id="g2", reply_to_own=True), owner=OWNER)
    store.ingest(make_event(GROUP, chat_type="group", message_id="g3", raw={"mentionedIds": ["55@lid"],
                                                                           "botIds": ["55:3@lid"]}), owner=OWNER)
    store.ingest(make_event(GROUP, chat_type="group", message_id="g4", text="lunch?"), owner=OWNER)
    flags = [m["mentions_owner"] for m in store.messages_since(0)]
    assert flags == [1, 1, 1, 0]


def test_resolve_chat_by_id_number_and_name(store):
    store.ingest(dm("a", "m1", name="Asha Rao"), owner=OWNER)
    store.ingest(dm("b", "m2", sender="14155550999", name="Asha Mehta"), owner=OWNER)
    store.ingest(make_event(GROUP, chat_type="group", message_id="g1", chat_name="Asha Rao"), owner=OWNER)
    assert store.resolve_chat(GROUP)[0]["chat_id"] == GROUP
    assert store.resolve_chat("+1 415 555 0999")[0]["chat_name"] == "Asha Mehta"
    assert store.resolve_chat("mehta")[0]["chat_name"] == "Asha Mehta"
    chat, candidates = store.resolve_chat("asha")
    assert chat is None and len(candidates) == 3
    chat, candidates = store.resolve_chat("asha rao")
    assert chat is None and len(candidates) == 2
    assert store.resolve_chat("nobody") == (None, [])


def test_wa_inbox_rollup_and_category_filter(store):
    for i in range(7):
        store.ingest(dm(f"msg {i} " + "x" * 300, f"m{i}"), owner=OWNER)
    store.ingest(make_event(GROUP, chat_type="group", message_id="g1", chat_name="Founders"), owner=OWNER)
    out = call(store, "wa_inbox")
    assert out["total_messages"] == 8
    asha = next(c for c in out["chats"] if c["chat"] == "Asha")
    assert asha["count"] == 7
    assert len(asha["recent"]) == tools.RECENT_PER_CHAT
    assert len(asha["recent"][0]["text"]) <= tools.PREVIEW_CHARS
    assert "note" in out
    call(store, "wa_set_category", chat="Asha", category="work")
    only_work = call(store, "wa_inbox", category="work")
    assert [c["chat"] for c in only_work["chats"]] == ["Asha"]
    assert [c["chat"] for c in call(store, "wa_inbox", category="uncategorised")["chats"]] == ["Founders"]


def test_wa_inbox_since_hours(store):
    store.ingest(dm("old", "m1", ts=datetime.now() - timedelta(hours=30)), owner=OWNER)
    store.ingest(dm("new", "m2"), owner=OWNER)
    assert call(store, "wa_inbox", since_hours=24)["total_messages"] == 1
    assert call(store, "wa_inbox", since_hours=48)["total_messages"] == 2


def test_wa_read_and_ambiguity(store):
    store.ingest(dm("first", "m1", name="Asha Rao", ts=datetime.now() - timedelta(minutes=5)), owner=OWNER)
    store.ingest(dm("second", "m2", name="Asha Rao"), owner=OWNER)
    store.ingest(dm("x", "m3", sender="14155550999", name="Asha Mehta"), owner=OWNER)
    out = call(store, "wa_read", chat="rao")
    assert [m["text"] for m in out["messages"]] == ["first", "second"]
    assert [m["text"] for m in call(store, "wa_read", chat="rao", limit=1)["messages"]] == ["second"]
    amb = call(store, "wa_read", chat="asha")
    assert "error" in amb and len(amb["candidates"]) == 2
    assert "error" in call(store, "wa_read", chat="zzz")


def test_wa_waiting_on_me(store, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", OWNER)
    now = datetime.now()
    store.ingest(dm("can you call?", "a1", ts=now - timedelta(hours=2)), owner=OWNER)
    store.ingest(dm("ok", "b1", sender="14155550999", name="Ravi", ts=now - timedelta(hours=3)), owner=OWNER)
    store.ingest(dm("done", "b2", sender="14155550999", name="Ravi", ts=now - timedelta(hours=2),
                    from_owner=True), owner=OWNER)
    raw = {"mentionedIds": [f"{OWNER}@s.whatsapp.net"]}
    store.ingest(make_event(GROUP, chat_type="group", message_id="g1", raw=raw, chat_name="Founders",
                            text=f"@{OWNER} thoughts?"), owner=OWNER)
    out = call(store, "wa_waiting_on_me")
    assert [w["chat"] for w in out["dms_waiting"]] == ["Asha"]
    assert [g["chat"] for g in out["group_mentions"]] == ["Founders"]
    assert "cannot see replies" in out["caveat"]
    call(store, "wa_mark_handled", chat="Asha")
    call(store, "wa_mark_handled", chat="Founders")
    out = call(store, "wa_waiting_on_me")
    assert out["dms_waiting"] == [] and out["group_mentions"] == []
    time.sleep(0.01)
    store.ingest(dm("hello again?", "a2"), owner=OWNER)
    assert [w["chat"] for w in call(store, "wa_waiting_on_me")["dms_waiting"]] == ["Asha"]


def test_set_category_validates_and_persists(store, tmp_path):
    store.ingest(dm("a", "m1"), owner=OWNER)
    assert "error" in call(store, "wa_set_category", chat="Asha", category="enemies")
    assert call(store, "wa_set_category", chat="Asha", category="Family")["category"] == "family"
    reopened = Store(path=store.path)
    assert reopened.chats()[0]["category"] == "family"
    reopened.close()


def test_wa_exclude_tool(store):
    store.ingest(dm("a", "m1"), owner=OWNER)
    out = call(store, "wa_exclude", chat="Asha")
    assert out["deleted_messages"] == 1
    assert call(store, "wa_inbox")["chats"] == []


def test_handlers_always_return_json_even_on_error():
    def broken():
        raise RuntimeError("db gone")

    for name in tools.SCHEMAS:
        out = json.loads(tools.make_handler(name, broken)({"chat": "x", "category": "work"}))
        assert "error" in out


def test_inbox_description_points_to_skill():
    assert "growthx-ea:morning-brief" in tools.SCHEMAS["wa_inbox"]["description"]
