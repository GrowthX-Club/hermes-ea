# GrowthX EA for Hermes

A Hermes Agent plugin that turns your WhatsApp into a quiet inbox for your Executive Assistant.

- It **reads** the WhatsApp messages people send you (DMs and groups) and saves them on your computer.
- Your agent uses them for a **morning brief**: what needs you, who might be waiting, what's safe to ignore, and drafts you can send.
- It **never replies to anyone**. The only chat that can talk to the agent is your own chat with yourself ("Message yourself" in WhatsApp).
- The agent **can't message anyone but you** on WhatsApp. When it writes a reply for someone, it gives you the text to copy and send yourself.

## Install

```bash
hermes plugins install GrowthX-Club/hermes-ea --enable
hermes ea setup 919876543210      # your number: country code + number, digits only
hermes gateway restart
hermes ea selftest
```

While this repo is private, installing needs a GitHub account with access to GrowthX-Club (run `gh auth login` first).

`hermes ea setup` writes the safe WhatsApp settings for you and makes a backup of each file first. `selftest` shows how different kinds of messages would be handled. Every line should say `ok`.

Then open WhatsApp, go to your chat with yourself, and say "brief me".

## The settings `hermes ea setup` writes

In `~/.hermes/.env`:

```env
WHATSAPP_ENABLED=true
WHATSAPP_MODE=bot
WHATSAPP_ALLOWED_USERS=<your number, digits only>
WHATSAPP_FORWARD_OWNER_MESSAGES=true
WHATSAPP_DM_POLICY=pairing
WHATSAPP_GROUP_POLICY=open
WHATSAPP_HOME_CHANNEL=<your number, digits only>
```

It also comments out any `WHATSAPP_ALLOW_ALL_USERS` line.

In `~/.hermes/config.yaml`:

```yaml
whatsapp:
  unauthorized_dm_behavior: ignore
```

If `WHATSAPP_ALLOWED_USERS` is anything other than exactly your one number (empty, `*`, or a list), or `WHATSAPP_ALLOW_ALL_USERS` is on, the plugin goes into **lockdown**. It keeps saving messages, but the agent answers nobody, not even you. `hermes ea status` tells you why.

## What it can and can't see

- It sees messages that arrive **after** you install it. It has no older history.
- It **can't see the replies you type on your phone**. So "waiting on you" is a best guess. Tell the agent "I've replied to Asha" and it will stop listing her.
- Photos, voice notes and documents are saved as a label (like `[photo]`) plus any caption. Files themselves aren't kept by the plugin.
- Messages are kept for 14 days, then deleted.

## Privacy

- Messages stay on your computer, in a file at `~/.hermes/plugin-data/growthx-ea/inbox.db`.
- When the agent summarises them, the messages it reads are sent to your AI model provider, like everything else your agent reads.
- To stop recording one chat and delete its history: tell the agent "stop reading <chat>", or run `hermes ea exclude "<chat name>"`.

## Risk: WhatsApp may ban your number

Hermes connects to WhatsApp as a linked device using an unofficial client. This is against WhatsApp's terms and runs on your personal number. WhatsApp can restrict or ban numbers that use unofficial clients, and the risk goes up if the account sends a lot of automated messages. This plugin never sends messages to other people, which helps, but the risk is not zero. If losing your number would hurt, don't use this.

## Commands

| Command | What it does |
|---|---|
| `hermes ea setup [number]` | Write the safe WhatsApp settings |
| `hermes ea status` | Safety checks, lockdown yes/no, what's stored |
| `hermes ea selftest` | Run test messages through the safety gate |
| `hermes ea chats` | List stored chats |
| `hermes ea exclude <chat>` | Stop recording a chat and delete its history |
| `hermes ea forget` | Delete everything stored (asks first) |

## Wipe everything

```bash
hermes ea forget
hermes plugins remove growthx-ea
rm -rf ~/.hermes/plugin-data/growthx-ea
```

To stop the agent reading WhatsApp at all, also set `WHATSAPP_ENABLED=false` in `~/.hermes/.env` and unlink the device in WhatsApp (Settings → Linked devices).

## For developers

Tests don't need Hermes installed:

```bash
pip install pytest pyyaml
pytest tests
```

License: MIT.
