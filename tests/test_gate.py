import pytest
from conftest import CONTACT, OWNER, Platform, make_event

from growthx_ea import gate, owner


class RecordingStore:
    def __init__(self):
        self.ingested = []
        self.lids = []

    def ingest(self, event, owner=None):
        self.ingested.append(event)
        return True

    def remember_owner_lid(self, chat_id):
        self.lids.append(chat_id)


def run(event, env):
    store = RecordingStore()
    result = gate.make_hook(lambda: store, env=env)(event=event, gateway=None, session_store=None)
    return result, store


def test_owner_self_chat_is_allowed_with_prefix_stripped(safe_env):
    result, store = run(make_event(f"{OWNER}@s.whatsapp.net", from_owner=True, text="/new"), safe_env)
    assert result == {"action": "rewrite", "text": "/new"}
    assert store.ingested == []


def test_owner_self_chat_with_device_suffix(safe_env):
    result, _ = run(make_event(f"{OWNER}:12@s.whatsapp.net", from_owner=True), safe_env)
    assert result["action"] == "rewrite"


def test_owner_self_chat_without_prefix_is_plain_allow(safe_env):
    event = make_event(f"{OWNER}@s.whatsapp.net", from_owner=True)
    event.text = "hello"
    assert run(event, safe_env)[0] == {"action": "allow"}


def test_owner_lid_self_chat_is_allowed_and_remembered(safe_env):
    result, store = run(make_event("123456789012345@lid", from_owner=True), safe_env)
    assert result["action"] == "rewrite"
    assert store.lids == ["123456789012345@lid"]
    assert store.ingested == []


def test_owner_message_in_contact_dm_is_stored_and_skipped(safe_env):
    result, store = run(make_event(f"{CONTACT}@s.whatsapp.net", from_owner=True), safe_env)
    assert result["action"] == "skip"
    assert len(store.ingested) == 1


def test_contact_dm_is_stored_and_skipped(safe_env):
    result, store = run(make_event(f"{CONTACT}@s.whatsapp.net"), safe_env)
    assert result["action"] == "skip"
    assert len(store.ingested) == 1


def test_contact_lid_dm_is_skipped(safe_env):
    result, store = run(make_event("998877665544332@lid"), safe_env)
    assert result["action"] == "skip"
    assert len(store.ingested) == 1


def test_group_message_is_stored_and_skipped(safe_env):
    result, store = run(make_event("120363041234567890@g.us", chat_type="group",
                                   sender=f"{CONTACT}@s.whatsapp.net"), safe_env)
    assert result["action"] == "skip"
    assert len(store.ingested) == 1


def test_owner_flag_in_group_is_skipped(safe_env):
    result, _ = run(make_event("120363041234567890@g.us", chat_type="group", from_owner=True), safe_env)
    assert result["action"] == "skip"


def test_owner_group_id_with_owner_digits_is_skipped(safe_env):
    result, _ = run(make_event(f"{OWNER}@g.us", chat_type="dm", from_owner=True), safe_env)
    assert result["action"] == "skip"


def test_self_chat_without_owner_flag_is_skipped(safe_env):
    result, store = run(make_event(f"{OWNER}@s.whatsapp.net"), safe_env)
    assert result["action"] == "skip"
    assert len(store.ingested) == 1


def test_non_whatsapp_platform_is_untouched(safe_env):
    result, store = run(make_event("12345", platform=Platform.TELEGRAM), safe_env)
    assert result is None
    assert store.ingested == []


def test_platform_as_plain_string(safe_env):
    assert run(make_event("12345", platform="Telegram"), safe_env)[0] is None
    assert run(make_event(f"{CONTACT}@s.whatsapp.net", platform="WhatsApp"), safe_env)[0]["action"] == "skip"


@pytest.mark.parametrize("env_patch", [
    {"WHATSAPP_ALLOWED_USERS": "*"},
    {"WHATSAPP_ALLOWED_USERS": ""},
    {"WHATSAPP_ALLOWED_USERS": None},
    {"WHATSAPP_ALLOWED_USERS": f"{OWNER},{CONTACT}"},
    {"WHATSAPP_ALLOWED_USERS": f"{OWNER}, *"},
    {"WHATSAPP_ALLOWED_USERS": "not-a-number"},
    {"WHATSAPP_ALLOW_ALL_USERS": "true"},
    {"WHATSAPP_ALLOW_ALL_USERS": "1"},
])
def test_lockdown_stores_and_skips_everything_including_owner(safe_env, env_patch):
    env = dict(safe_env)
    for k, v in env_patch.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    assert owner.check(env).lockdown
    for event in (make_event(f"{OWNER}@s.whatsapp.net", from_owner=True),
                  make_event("123456789012345@lid", from_owner=True),
                  make_event(f"{CONTACT}@s.whatsapp.net")):
        result, store = run(event, env)
        assert result["action"] == "skip"
        assert len(store.ingested) == 1


def test_lockdown_warns_once(safe_env, caplog):
    env = dict(safe_env, WHATSAPP_ALLOWED_USERS="*")
    gate._warned.clear()
    with caplog.at_level("WARNING"):
        run(make_event(f"{CONTACT}@s.whatsapp.net"), env)
        run(make_event(f"{CONTACT}@s.whatsapp.net"), env)
    assert sum("LOCKDOWN" in r.getMessage() for r in caplog.records) == 1


def test_yaml_allow_from_override_triggers_lockdown(safe_env, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("whatsapp:\n  allow_from: ['*']\n")
    assert owner.check(safe_env).lockdown
    (home / "config.yaml").write_text(f"platforms:\n  whatsapp:\n    allow_from: [{OWNER}]\n")
    assert not owner.check(safe_env).lockdown


def test_exception_inside_gate_returns_skip(safe_env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(gate, "decide", boom)
    result, _ = run(make_event(f"{OWNER}@s.whatsapp.net", from_owner=True), safe_env)
    assert result["action"] == "skip"


def test_exception_in_precheck_returns_skip(safe_env, monkeypatch):
    monkeypatch.setattr(owner, "check", lambda env: (_ for _ in ()).throw(ValueError("bad")))
    result, _ = run(make_event(f"{OWNER}@s.whatsapp.net", from_owner=True), safe_env)
    assert result["action"] == "skip"


def test_broken_event_returns_skip(safe_env):
    class Weird:
        @property
        def source(self):
            raise RuntimeError("nope")

    result, _ = run(Weird(), safe_env)
    assert result["action"] == "skip"


def test_store_failure_still_skips(safe_env):
    class Broken:
        def ingest(self, *a, **k):
            raise OSError("disk full")

    hook = gate.make_hook(lambda: Broken(), env=safe_env)
    assert hook(event=make_event(f"{CONTACT}@s.whatsapp.net"))["action"] == "skip"


def test_reads_os_environ_at_dispatch_time(monkeypatch):
    store = RecordingStore()
    hook = gate.make_hook(lambda: store)
    event = make_event(f"{OWNER}@s.whatsapp.net", from_owner=True)
    assert hook(event=event)["action"] == "skip"
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", OWNER)
    assert hook(event=make_event(f"{OWNER}@s.whatsapp.net", from_owner=True))["action"] == "rewrite"


def test_selftest_cases_match_decisions(safe_env):
    from growthx_ea import cli

    pre = owner.check(safe_env)
    for _, event, expected in cli.selftest_cases(pre.owner):
        assert gate.decide(event, pre)[0] == expected
