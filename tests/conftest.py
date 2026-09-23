import importlib.util
import os
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent

if "growthx_ea" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "growthx_ea", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["growthx_ea"] = module
    spec.loader.exec_module(module)

OWNER = "919876543210"
CONTACT = "14155550123"


class Platform(Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"


class MessageType(Enum):
    TEXT = "text"
    PHOTO = "photo"
    VOICE = "voice"


def make_event(chat_id, chat_type="dm", from_owner=False, platform=Platform.WHATSAPP, text="hi",
               message_id=None, sender=None, sender_name=None, chat_name=None, ts=None,
               message_type=MessageType.TEXT, raw=None, reply_to_own=False):
    metadata = {"whatsapp_from_owner": True} if from_owner else {}
    if from_owner:
        text = "[owner reply] " + text
    return SimpleNamespace(
        text=text, message_type=message_type, message_id=message_id, metadata=metadata,
        timestamp=ts or datetime.now(), internal=False, raw_message=raw,
        reply_to_is_own_message=reply_to_own,
        source=SimpleNamespace(platform=platform, chat_id=chat_id, chat_type=chat_type, chat_name=chat_name,
                               user_id=sender or chat_id, user_name=sender_name),
    )


@pytest.fixture
def safe_env(tmp_path):
    return {
        "HERMES_HOME": str(tmp_path / "home"),
        "WHATSAPP_ALLOWED_USERS": OWNER,
        "WHATSAPP_HOME_CHANNEL": OWNER,
    }


@pytest.fixture(autouse=True)
def isolated_env(tmp_path):
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith("WHATSAPP_"):
            del os.environ[key]
    os.environ["HERMES_HOME"] = str(tmp_path / "home")
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def store(tmp_path):
    from growthx_ea.store import Store

    s = Store(path=tmp_path / "inbox.db")
    yield s
    s.close()
