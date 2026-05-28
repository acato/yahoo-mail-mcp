"""MCP server entry point for yahoo-mail-mcp.

Registers the FastMCP server and the user-facing tools. Tools that need an
IMAP connection borrow one from a process-level ConnectionPool that's lazily
constructed and lazily refreshed when the config file changes.
"""

from __future__ import annotations

import atexit
import contextlib

from mcp.server.fastmcp import FastMCP

from yahoo_mail_mcp import __version__
from yahoo_mail_mcp.config import Config, config_path, load_config
from yahoo_mail_mcp.folders import canonical_name
from yahoo_mail_mcp.imap_client import ConnectionPool

mcp = FastMCP("yahoo-mail-mcp")

# Process-level pool. Lazily built; rebuilt if the config file mtime changes
# so that adding an account doesn't require restarting the MCP.
_pool: ConnectionPool | None = None
_pool_config_mtime: float | None = None


def _config() -> Config:
    """Load fresh config on each call so file edits don't require a server restart."""
    return load_config()


def _get_pool() -> ConnectionPool:
    """Return the process-wide ConnectionPool, rebuilding it if config changed."""
    global _pool, _pool_config_mtime
    cfg_path = config_path()
    current_mtime = cfg_path.stat().st_mtime if cfg_path.exists() else 0.0
    if _pool is None or current_mtime != _pool_config_mtime:
        if _pool is not None:
            _pool.close_all()
        _pool = ConnectionPool(_config())
        _pool_config_mtime = current_mtime
    return _pool


@atexit.register
def _shutdown_pool() -> None:
    """Best-effort cleanup of IMAP connections on interpreter exit."""
    global _pool
    if _pool is not None:
        with contextlib.suppress(Exception):
            _pool.close_all()
        _pool = None


@mcp.tool()
def list_accounts() -> list[dict[str, str]]:
    """List configured Yahoo accounts.

    Returns one entry per [accounts.<name>] in the config file with the
    nickname and email address. Use the nickname as the `host` parameter
    for other tool calls.
    """
    cfg = _config()
    return [{"name": name, "address": acct.address} for name, acct in cfg.accounts.items()]


@mcp.tool()
def list_folders(host: str) -> list[dict[str, int | str | None]]:
    """List every folder in the account's mailbox with message counts.

    Args:
        host: account nickname from config (e.g., "primary"). Use
            list_accounts() to see the valid nicknames.

    Returns:
        One entry per folder with:
          - name: original IMAP folder name (e.g., "Bulk Mail")
          - normalized: canonical role ("inbox", "sent", "drafts", "trash",
            "spam", "archive") or null for user-created folders
          - total: total message count
          - unseen: unread message count
    """
    pool = _get_pool()
    mb = pool.get(host)
    out: list[dict[str, int | str | None]] = []
    for folder in mb.folder.list():
        try:
            stats = mb.folder.status(folder.name, options=("MESSAGES", "UNSEEN"))
            total = int(stats.get("MESSAGES", 0))
            unseen = int(stats.get("UNSEEN", 0))
        except Exception:
            # Some IMAP servers refuse STATUS on certain folders (e.g.,
            # \Noselect). Report -1 so the LLM knows the count is unknown.
            total = -1
            unseen = -1
        out.append(
            {
                "name": folder.name,
                "normalized": canonical_name(folder.name),
                "total": total,
                "unseen": unseen,
            }
        )
    return out


@mcp.tool()
def server_info() -> dict[str, str]:
    """Return server version and configuration locations for diagnostics."""
    cfg_path = config_path()
    return {
        "version": __version__,
        "config_path": str(cfg_path),
        "config_exists": str(cfg_path.exists()),
    }


def main() -> None:
    """Console-script entry point. Runs the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
