# Contributing to yahoo-mail-mcp

Thanks for your interest! This project is alpha — under active development with frequent API changes.

## Ground rules

- **Read [DESIGN.md](DESIGN.md) first.** The MVP scope, tool signatures, and Yahoo IMAP quirks are documented there. If your change diverges from the design, propose the design change in an issue or PR first.
- **No vendor-specific defaults.** This codebase must work for any Yahoo Mail user. No hardcoded addresses, folder names, or paths from any specific environment.
- **No secrets in code, tests, or fixtures.** App passwords come from config files or environment variables. PRs that hardcode credentials will be rejected.

## Development setup

```bash
git clone https://github.com/acato/yahoo-mail-mcp
cd yahoo-mail-mcp
uv sync --all-extras
uv run pytest
uv run ruff check
uv run ruff format --check
```

Python 3.11+ required. Dependencies are managed by [uv](https://docs.astral.sh/uv/). See the [Windows: avoid Microsoft Store Python](README.md#windows-avoid-microsoft-store-python) note if you're on Windows.

## Testing

- **Unit tests** (`tests/unit/`) — fast, no network, run on every CI build. Use fixture IMAP responses; never make real network calls in unit scope.
- **Live integration tests** (`tests/integration/`) — gated behind the `YAHOO_MAIL_MCP_LIVE=1` env var. Skipped by default. CI never runs these.
- New tools must come with at least unit-test coverage of the happy path + one error case (e.g., auth failed, UIDVALIDITY changed, search-too-broad). Use `pytest -k` to scope while iterating.

For live tests against your own Yahoo account:

```bash
export YAHOO_MAIL_MCP_LIVE=1
export YAHOO_MAIL_MCP_PRIMARY_ADDRESS=you@yahoo.com
export YAHOO_MAIL_MCP_PRIMARY_PASSWORD='...'
uv run pytest tests/integration
```

Write tests (move, delete) are gated behind an additional `--allow-writes` flag and only operate against a designated `_mcp-test/` folder. Never commit a real config file or `.env` containing credentials. `.gitignore` excludes `*.env`, `config.local.*`, and `*.password`.

## Code style

- `ruff check` and `ruff format` are CI-enforced.
- Type hints are required on public functions (the MCP tool surface). Internal helpers may omit them but they're encouraged.
- Docstrings on every public function. Use the Google docstring style.

## License compatibility

This project is Apache-2.0. **All runtime dependencies must be compatible with Apache-2.0** — that means MIT, BSD, ISC, Apache-2.0, or other permissive licenses. **No GPL, LGPL, AGPL, or MPL** runtime deps without explicit project-owner approval. PRs that add copyleft runtime deps will be rejected.

Dev-only deps may be more permissive about licensing (e.g., GPL test tooling) but must be opt-in via the `[dev]` extras group, never required at runtime.

## Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`. Scope is optional (`feat(search): ...`).

## License

By contributing, you agree that your contributions will be licensed under Apache-2.0.
