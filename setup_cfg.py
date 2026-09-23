"""`hermes ea setup`: write the safe WhatsApp config into .env and config.yaml."""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

ENV_REMOVE = ("WHATSAPP_ALLOW_ALL_USERS",)


def env_values(number: str) -> dict[str, str]:
    return {
        "WHATSAPP_ENABLED": "true",
        "WHATSAPP_MODE": "bot",
        "WHATSAPP_ALLOWED_USERS": number,
        "WHATSAPP_FORWARD_OWNER_MESSAGES": "true",
        "WHATSAPP_DM_POLICY": "pairing",
        "WHATSAPP_GROUP_POLICY": "open",
        "WHATSAPP_HOME_CHANNEL": number,
    }


def normalize_number(raw: str) -> str:
    """Digits-only international number, or ValueError."""
    text = str(raw or "").strip()
    if re.search(r"[^\d\s\-+().]", text):
        raise ValueError("use digits only, with the country code, e.g. 919876543210")
    number = re.sub(r"\D", "", text)
    if number.startswith("00"):
        number = number[2:]
    if not 8 <= len(number) <= 15:
        raise ValueError("that is not a full international number (country code + number, 8 to 15 digits)")
    if number.startswith("0"):
        raise ValueError("start with the country code, not 0 (e.g. 91 for India)")
    return number


def _backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    n = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    shutil.copy2(path, backup)
    return backup


def _write(path: Path, text: str, default_mode: int) -> Optional[Path]:
    """Write ``text`` if it differs; back up the old file first. Returns the backup path, if any."""
    backup = None
    if path.exists():
        if path.read_text(encoding="utf-8") == text:
            return None
        backup = _backup(path)
        mode = path.stat().st_mode & 0o777
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = default_mode
    tmp = path.with_name(path.name + ".tmp-ea")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return backup


def _env_key(line: str) -> Optional[str]:
    m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
    return m.group(1) if m else None


def render_env(original: str, values: dict[str, str], remove=ENV_REMOVE) -> str:
    out: list[str] = []
    written: set[str] = set()
    for line in original.splitlines():
        key = _env_key(line)
        if key in values:
            if key not in written:
                out.append(f"{key}={values[key]}")
                written.add(key)
            continue
        if key in remove:
            out.append(f"# {line.strip()}  # disabled by hermes ea setup")
            continue
        out.append(line)
    missing = [k for k in values if k not in written]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append("# WhatsApp, set by hermes ea setup")
        out += [f"{k}={values[k]}" for k in missing]
    return "\n".join(out) + "\n"


def update_env(path: Path, number: str) -> Optional[Path]:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    return _write(path, render_env(original, env_values(number)), 0o600)


_TOP_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")


def render_config(original: str) -> str:
    """Set ``whatsapp.unauthorized_dm_behavior: ignore``, editing text in place so comments survive."""
    import yaml

    lines = original.splitlines()
    start = None
    for i, line in enumerate(lines):
        m = _TOP_KEY_RE.match(line)
        if m and m.group(1) == "whatsapp":
            start = i
            break

    if start is None:
        body = lines + ([""] if lines and lines[-1].strip() else []) + ["whatsapp:", "  unauthorized_dm_behavior: ignore"]
        text = "\n".join(body) + "\n"
    elif _TOP_KEY_RE.match(lines[start]).group(2).split("#", 1)[0].strip():
        text = None
    else:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].strip() and not lines[j][0].isspace() and not lines[j].lstrip().startswith("#"):
                end = j
                break
        block = lines[start + 1:end]
        indent = next((re.match(r"^(\s+)", l).group(1) for l in block
                       if l.strip() and not l.lstrip().startswith("#") and re.match(r"^\s+", l)), "  ")
        key_re = re.compile(rf"^{re.escape(indent)}unauthorized_dm_behavior\s*:")
        hits = [k for k, l in enumerate(block) if key_re.match(l)]
        if hits:
            block[hits[0]] = f"{indent}unauthorized_dm_behavior: ignore"
        else:
            block.insert(0, f"{indent}unauthorized_dm_behavior: ignore")
        text = "\n".join(lines[:start + 1] + block + lines[end:]) + "\n"

    try:
        ok = text is not None and (yaml.safe_load(text) or {}).get("whatsapp", {}).get("unauthorized_dm_behavior") == "ignore"
    except Exception:
        ok = False
    if not ok:
        data = yaml.safe_load(original) or {}
        wa = data.get("whatsapp")
        if not isinstance(wa, dict):
            wa = data["whatsapp"] = {}
        wa["unauthorized_dm_behavior"] = "ignore"
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return text


def update_config(path: Path) -> Optional[Path]:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    return _write(path, render_config(original), 0o600)
