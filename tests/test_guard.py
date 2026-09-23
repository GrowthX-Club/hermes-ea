import pytest
from conftest import CONTACT, OWNER

from growthx_ea import guard

LID = "123456789012345"


def check(tool, args, env, lids=(LID,)):
    return guard.check_tool_call(tool, args, env, lids)


@pytest.mark.parametrize("target", [
    f"whatsapp:{OWNER}", f"whatsapp:+{OWNER}", f"whatsapp:{OWNER}@s.whatsapp.net",
    f"whatsapp:{OWNER}:7@s.whatsapp.net", f"whatsapp:{LID}@lid", "WhatsApp", "whatsapp",
    "telegram:12345", "slack:#general",
])
def test_send_allowed(safe_env, target):
    assert check("send_message", {"action": "send", "target": target, "message": "x"}, safe_env) is None


@pytest.mark.parametrize("target", [
    f"whatsapp:{CONTACT}", f"whatsapp:{CONTACT}@s.whatsapp.net", "whatsapp:120363041234567890@g.us",
    f"whatsapp:{OWNER}@g.us", "whatsapp:999999999999999@lid", "whatsapp:#Family", "whatsapp:Mom",
    f"whatsapp:{OWNER}9", "all",
])
@pytest.mark.parametrize("action", ["send", "react", "unreact", None])
def test_send_blocked(safe_env, target, action):
    args = {"target": target, "message": "x"}
    if action:
        args["action"] = action
    if target == "all":
        safe_env = dict(safe_env, WHATSAPP_HOME_CHANNEL=CONTACT)
    msg = check("send_message", args, safe_env)
    assert msg and "self-chat" in msg and "themselves" in msg


def test_list_is_allowed(safe_env):
    assert check("send_message", {"action": "list"}, safe_env) is None


def test_home_channel_rules(safe_env):
    args = {"target": "whatsapp", "message": "x"}
    assert check("send_message", args, dict(safe_env, WHATSAPP_HOME_CHANNEL=f"{OWNER}@s.whatsapp.net")) is None
    assert "not the owner's" in check("send_message", args, dict(safe_env, WHATSAPP_HOME_CHANNEL=CONTACT))
    env = dict(safe_env)
    env.pop("WHATSAPP_HOME_CHANNEL")
    assert "WHATSAPP_HOME_CHANNEL" in check("send_message", args, env)


def test_lockdown_blocks_all_whatsapp_sends(safe_env):
    env = dict(safe_env, WHATSAPP_ALLOWED_USERS="*")
    assert "lockdown" in check("send_message", {"target": f"whatsapp:{OWNER}", "message": "x"}, env)
    assert check("send_message", {"target": "telegram:1", "message": "x"}, env) is None


def test_lid_unknown_without_recorded_owner_lids(safe_env):
    assert check("send_message", {"target": f"whatsapp:{LID}@lid", "message": "x"}, safe_env, lids=())


@pytest.mark.parametrize("tool", ["cronjob_manage", "cronjob"])
def test_cron_delivery(safe_env, tool):
    ok = [None, "origin", "local", "whatsapp", f"whatsapp:{OWNER}", "telegram:1,local", ["whatsapp", "local"],
          "bot-chat", "all"]
    for deliver in ok:
        args = {"action": "create", "schedule": "every day at 8am", "prompt": "brief"}
        if deliver is not None:
            args["deliver"] = deliver
        assert check(tool, args, safe_env) is None, deliver
    bad = [f"whatsapp:{CONTACT}", f"local,whatsapp:{CONTACT}", "whatsapp:120363041234567890@g.us",
           ["local", f"whatsapp:{CONTACT}"]]
    for deliver in bad:
        for action in ("create", "update"):
            msg = check(tool, {"action": action, "job_id": "j", "deliver": deliver}, safe_env)
            assert msg and "Blocked by growthx-ea" in msg, (deliver, action)
    assert check(tool, {"action": "update", "job_id": "j", "failure_deliver": f"whatsapp:{CONTACT}"}, safe_env)
    assert check(tool, {"action": "update", "job_id": "j", "prompt": "x"}, safe_env) is None
    assert check(tool, {"action": "list"}, safe_env) is None
    assert check(tool, {"action": "remove", "job_id": "j"}, safe_env) is None


def test_cron_origin_blocked_when_home_is_someone_else(safe_env):
    env = dict(safe_env, WHATSAPP_HOME_CHANNEL=CONTACT)
    assert check("cronjob_manage", {"action": "create", "prompt": "x", "schedule": "1h"}, env)
    assert check("cronjob_manage", {"action": "create", "prompt": "x", "schedule": "1h", "deliver": "local"},
                 env) is None
    no_home = dict(safe_env)
    no_home.pop("WHATSAPP_HOME_CHANNEL")
    assert check("cronjob_manage", {"action": "create", "prompt": "x", "schedule": "1h"}, no_home) is None
    assert check("cronjob_manage", {"action": "create", "prompt": "x", "schedule": "1h", "deliver": "all"}, no_home)


@pytest.mark.parametrize("command,blocked", [
    (f"hermes send whatsapp:{CONTACT} 'hi'", True),
    (f"cd /tmp && hermes send --to whatsapp:{CONTACT} hi", True),
    ("curl -X POST http://localhost:3000/send -d '{}'", True),
    ("curl http://127.0.0.1:3000/send-media", True),
    ("hermes send telegram:1 hi", False),
    ("ls -la", False),
    ("echo whatsapp", False),
])
def test_shell_heuristics(safe_env, command, blocked):
    assert bool(check("terminal", {"command": command}, safe_env)) is blocked
    assert bool(check("execute_code", {"code": f"import os; os.system({command!r})"}, safe_env)) is blocked


def test_other_tools_untouched(safe_env):
    assert check("web_search", {"query": "whatsapp:123"}, safe_env) is None


def test_hook_blocks_and_fails_closed(safe_env, monkeypatch):
    hook = guard.make_hook(lambda: [LID], env=safe_env)
    assert hook(tool_name="send_message", args={"target": f"whatsapp:{CONTACT}", "message": "x"},
                task_id="")["action"] == "block"
    assert hook(tool_name="send_message", args={"target": f"whatsapp:{OWNER}", "message": "x"}, task_id="") is None
    assert hook(tool_name="read_file", args={"path": "x"}, task_id="") is None

    monkeypatch.setattr(guard, "check_tool_call", lambda *a: 1 / 0)
    assert hook(tool_name="send_message", args={}, task_id="")["action"] == "block"
    assert hook(tool_name="read_file", args={}, task_id="") is None


def test_hook_survives_store_failure(safe_env):
    def broken():
        raise OSError("db")

    hook = guard.make_hook(broken, env=safe_env)
    assert hook(tool_name="send_message", args={"target": f"whatsapp:{LID}@lid", "message": "x"},
                task_id="")["action"] == "block"
