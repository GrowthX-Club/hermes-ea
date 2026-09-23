"""Who the owner is, and whether the config is safe enough to let anything through."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

_PHONE_RE = re.compile(r"^\+?\d{7,15}$")
_TRUTHY = {"1", "true", "yes", "on"}
_GROUP_SUFFIXES = ("@g.us", "@broadcast", "@newsletter")


def digits(value) -> str:
    """Bare user part of a WhatsApp id or phone number, digits only.

    ``919876543210:12@s.whatsapp.net`` -> ``919876543210``; ``+91 98765 43210`` -> ``919876543210``.
    """
    user = str(value or "").strip().split("@", 1)[0].split(":", 1)[0]
    return re.sub(r"\D", "", user)


def is_lid(value) -> bool:
    return str(value or "").strip().lower().endswith("@lid")


def is_group_id(value) -> bool:
    return str(value or "").strip().lower().endswith(_GROUP_SUFFIXES)


def hermes_home(env: Mapping[str, str] = os.environ) -> Path:
    return Path(env.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


@dataclass
class Precheck:
    owner: Optional[str]
    problems: list[str] = field(default_factory=list)

    @property
    def lockdown(self) -> bool:
        return bool(self.problems) or not self.owner


_yaml_cache: dict = {}


def _yaml_allow_from(path: Path):
    """``(present, values)`` for a DM allowlist set in config.yaml; config.yaml wins over .env in Hermes."""
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return False, None
    cached = _yaml_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result = (False, None)
    blocks = [data.get("whatsapp")]
    platforms = data.get("platforms")
    if isinstance(platforms, dict):
        blocks.append(platforms.get("whatsapp"))
    sections = []
    for block in blocks:
        if isinstance(block, dict):
            sections += [block, block.get("extra")]
    for section in sections:
        if not isinstance(section, dict):
            continue
        for key in ("allow_from", "allowFrom"):
            if key in section:
                result = (True, section[key])
    _yaml_cache[path] = (mtime, result)
    return result


def _split_allowlist(raw) -> list[str]:
    parts = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    return [str(p).strip() for p in parts if str(p).strip()]


def check(env: Mapping[str, str] = os.environ) -> Precheck:
    """The owner's number if WHATSAPP_ALLOWED_USERS is exactly one concrete number; otherwise lockdown."""
    problems: list[str] = []
    if str(env.get("WHATSAPP_ALLOW_ALL_USERS", "")).strip().lower() in _TRUTHY:
        problems.append("WHATSAPP_ALLOW_ALL_USERS is on; it must be off")

    entries = _split_allowlist(env.get("WHATSAPP_ALLOWED_USERS", ""))
    owner = None
    if not entries:
        problems.append("WHATSAPP_ALLOWED_USERS is empty; set it to your own number")
    elif len(entries) > 1:
        problems.append("WHATSAPP_ALLOWED_USERS lists more than one number; it must be only yours")
    elif entries[0] == "*":
        problems.append("WHATSAPP_ALLOWED_USERS is '*'; it must be your own number")
    elif not _PHONE_RE.match(entries[0].replace(" ", "").replace("-", "")):
        problems.append("WHATSAPP_ALLOWED_USERS is not a phone number; use digits only, e.g. 919876543210")
    else:
        owner = digits(entries[0])

    try:
        present, raw = _yaml_allow_from(hermes_home(env) / "config.yaml")
    except Exception as exc:
        problems.append(f"could not read config.yaml to check whatsapp.allow_from ({type(exc).__name__})")
    else:
        if present:
            yaml_entries = _split_allowlist(raw)
            if len(yaml_entries) != 1 or yaml_entries[0] == "*" or digits(yaml_entries[0]) != owner:
                problems.append("config.yaml sets whatsapp.allow_from to something other than your one number; "
                                "remove it or make it match WHATSAPP_ALLOWED_USERS")

    return Precheck(owner=None if problems else owner, problems=problems)


def home_channel(env: Mapping[str, str] = os.environ) -> str:
    return str(env.get("WHATSAPP_HOME_CHANNEL", "") or "").strip()
