import argparse
import os

import pytest
import yaml
from conftest import OWNER

from growthx_ea import cli, owner, setup_cfg

ENV_BEFORE = """# my keys
OPENAI_API_KEY=sk-abc
export WHATSAPP_MODE=self-chat
WHATSAPP_ALLOWED_USERS=*
WHATSAPP_ALLOW_ALL_USERS=true
TELEGRAM_BOT_TOKEN=123:abc

WHATSAPP_ALLOWED_USERS=duplicate
"""

CONFIG_BEFORE = """# Hermes config
model:
  default: some-model  # keep this comment
whatsapp:
  require_mention: false
  unauthorized_dm_behavior: pair
plugins:
  enabled:
    - growthx-ea
"""


@pytest.mark.parametrize("raw,expected", [
    ("919876543210", "919876543210"), ("+91 98765 43210", "919876543210"), ("0091-98765-43210", "919876543210"),
    ("+1 (415) 555-0123", "14155550123"),
])
def test_normalize_number_ok(raw, expected):
    assert setup_cfg.normalize_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "*", "98765", "09876543210", "91987654321012345", "abc", "9198765432@x"])
def test_normalize_number_rejects(raw):
    with pytest.raises(ValueError):
        setup_cfg.normalize_number(raw)


def test_env_update_preserves_backs_up_and_is_idempotent(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV_BEFORE)
    os.chmod(path, 0o600)
    backup = setup_cfg.update_env(path, OWNER)
    assert backup and backup.read_text() == ENV_BEFORE
    text = path.read_text()
    assert "# my keys\nOPENAI_API_KEY=sk-abc\n" in text
    assert "TELEGRAM_BOT_TOKEN=123:abc" in text
    assert "WHATSAPP_MODE=bot" in text and "self-chat" not in text
    assert text.count("WHATSAPP_ALLOWED_USERS=") == 1 and f"WHATSAPP_ALLOWED_USERS={OWNER}" in text
    assert "duplicate" not in text
    assert "# WHATSAPP_ALLOW_ALL_USERS=true" in text
    for key, value in setup_cfg.env_values(OWNER).items():
        assert f"\n{key}={value}\n" in "\n" + text
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    assert setup_cfg.update_env(path, OWNER) is None
    assert path.read_text() == text
    assert len(list(tmp_path.glob(".env.bak-*"))) == 1


def test_env_created_when_missing(tmp_path):
    path = tmp_path / "home" / ".env"
    assert setup_cfg.update_env(path, OWNER) is None
    assert f"WHATSAPP_ALLOWED_USERS={OWNER}" in path.read_text()
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_config_update_preserves_comments_and_is_idempotent(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_BEFORE)
    backup = setup_cfg.update_config(path)
    assert backup and backup.read_text() == CONFIG_BEFORE
    text = path.read_text()
    assert "# keep this comment" in text and "# Hermes config" in text
    data = yaml.safe_load(text)
    assert data["whatsapp"] == {"require_mention": False, "unauthorized_dm_behavior": "ignore"}
    assert data["plugins"]["enabled"] == ["growthx-ea"]
    assert setup_cfg.update_config(path) is None
    assert path.read_text() == text


@pytest.mark.parametrize("before", [
    "", "model:\n  default: x\n", "whatsapp:\nplugins:\n  enabled: []\n",
    "whatsapp:\n    # nested comment\n    dm_policy: pairing\n", "whatsapp: {require_mention: true}\n",
])
def test_config_update_shapes(tmp_path, before):
    path = tmp_path / "config.yaml"
    if before:
        path.write_text(before)
    setup_cfg.update_config(path)
    data = yaml.safe_load(path.read_text())
    assert data["whatsapp"]["unauthorized_dm_behavior"] == "ignore"
    if "require_mention: true" in before:
        assert data["whatsapp"]["require_mention"] is True
    if "dm_policy" in before:
        assert data["whatsapp"]["dm_policy"] == "pairing"


def test_setup_command_end_to_end(tmp_path, monkeypatch, capsys, store):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / ".env").write_text(ENV_BEFORE)
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert cli.cmd_setup(store, "+91 98765 43210") == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("Restart the gateway: hermes gateway restart")
    assert not owner.check().lockdown
    assert yaml.safe_load((home / "config.yaml").read_text())["whatsapp"]["unauthorized_dm_behavior"] == "ignore"
    assert cli.cmd_setup(store, "12") == 1


def test_setup_prompts_until_valid(tmp_path, monkeypatch, store):
    answers = iter(["hello", "919876543210"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert cli.cmd_setup(store, None) == 0


def test_cli_parser_and_commands(store, monkeypatch, capsys):
    parser = argparse.ArgumentParser()
    cli.setup_argparse(parser)
    handle = cli.make_handler(lambda: store)
    assert handle(parser.parse_args(["status"])) == 1
    assert "Lockdown:      YES" in capsys.readouterr().out
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", OWNER)
    assert handle(parser.parse_args(["status"])) == 0
    assert handle(parser.parse_args(["selftest"])) == 0
    assert "All good." in capsys.readouterr().out
    assert handle(parser.parse_args(["chats"])) == 0
    assert handle(parser.parse_args(["exclude", "120363041234567890@g.us"])) == 0
    assert store.chats()[0]["excluded"] == 1
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert handle(parser.parse_args(["forget"])) == 1
    assert store.chats()
    assert handle(parser.parse_args(["forget", "--yes"])) == 0
    assert store.chats() == []


def test_selftest_in_lockdown_passes(capsys):
    assert cli.cmd_selftest() == 0
    assert "LOCKDOWN" in capsys.readouterr().out
