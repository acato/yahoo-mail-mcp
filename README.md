# yahoo-mail-mcp

MCP server for triaging a Yahoo Mail mailbox over IMAP. Built for spammy 100k+ message accounts where you need read / search / move / delete / mark-read / bulk-purge-from-sender capability but **do not** need send, IDLE, or thread reconstruction.

> ⚠️ **Alpha.** Day 1 skeleton. Not yet usable.

## Why

The community MCP options for Yahoo Mail are either generic IMAP wrappers without Yahoo-specific quirk handling (`codefuturist/email-mcp`, LGPL-3, no Yahoo IDLE/rate-limit patches), unmaintained (`jtokib/yahoo-mail-mcp-server`, last update Jan 2025), or missing IDLE/pagination guards required to handle Yahoo at 100k+ messages.

This server is a focused, opinionated triage tool. It **deliberately** does not try to be a general IMAP MCP — Yahoo's quirks are baked in. See [DESIGN.md](DESIGN.md) for the full scope and rationale.

## Scope

### In scope (v0 MVP)
- Multi-account aware (`list_accounts`)
- Folder enumeration with counts (`list_folders`)
- IMAP SEARCH wrapper with bounded result sets (`search`)
- Single-message fetch with header/body/full modes (`fetch_message`)
- Batched bulk fetch, capped per call (`fetch_messages_bulk`)
- Move / delete / mark-read for triage (`move_message`, `delete_message`, `mark_read`)
- Bulk purge from a single sender (`bulk_purge_from`, Phase 2)

### Out of scope (deliberate)
- ❌ Sending mail (use a sending-focused MCP)
- ❌ IMAP IDLE / push (Yahoo's 9-min IDLE drop is where it bites hardest; polled triage is fine)
- ❌ Thread reconstruction (Yahoo's IMAP threading is unreliable)
- ❌ Corpus indexing / search-over-archive (separate concern; not this MCP's job)

## Install

> Not yet on PyPI. Until then, from source:

```bash
git clone https://github.com/acato/yahoo-mail-mcp
cd yahoo-mail-mcp
uv sync
uv run yahoo-mail-mcp
```

### Windows: avoid Microsoft Store Python

If `uv` picks Microsoft Store Python (path under `\WindowsApps\PythonSoftwareFoundation...`) when creating the venv, the MCP runs fine from a terminal but fails to launch from GUI hosts like the Claude desktop app, IDE extensions, or scheduled tasks. You will see:

```
Unable to create process using "...\WindowsApps\PythonSoftwareFoundation.Python.3.12_...\python.exe"
```

Pin `uv` to a non-Store interpreter — uv's managed Python is easiest:

```powershell
uv python install 3.12
uv venv --python 3.12 --python-preference only-managed --clear
uv sync
```

Verify: `Get-Content .venv\pyvenv.cfg` — the `home =` line should point under `AppData\Roaming\uv\python\...`, **not** `\WindowsApps\`.

## Configure

Copy `examples/config.toml` to `~/.config/yahoo-mail-mcp/config.toml` and fill in your accounts. App-password auth (Yahoo killed OAuth2 for third-party developers years ago — see [DESIGN.md §3](DESIGN.md#3-authentication)). Generate at: Yahoo Account Security → Generate app password.

Per-account env vars also supported (precedence: env > file). Schema in [DESIGN.md §6](DESIGN.md#6-configuration).

## Wire into Claude Code

```bash
claude mcp add yahoo-mail-mcp -- uv run --directory /path/to/yahoo-mail-mcp yahoo-mail-mcp
```

## Compatibility

- Python 3.11+
- Yahoo Mail IMAP (`imap.mail.yahoo.com:993`, TLS required)
- App password authentication (16-char lowercase string from Yahoo Account Security)

## Documentation

- [DESIGN.md](DESIGN.md) — architecture, tool surface, Yahoo IMAP quirks
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, tests, release process

## License

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Trademarks

"Yahoo" and "Yahoo Mail" are trademarks of Yahoo Inc. This project is an independent integration with the standard Yahoo Mail IMAP service and is not affiliated with, endorsed by, or sponsored by Yahoo Inc. or Apollo Global Management.
