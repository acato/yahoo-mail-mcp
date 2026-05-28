"""MCP server entry point for yahoo-mail-mcp.

Day 1 skeleton: registers the FastMCP server and one tool (`list_accounts`)
that exercises the config loader. Real IMAP tools land in Day 2+.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from yahoo_mail_mcp import __version__
from yahoo_mail_mcp.config import Config, load_config

mcp = FastMCP("yahoo-mail-mcp")


def _config() -> Config:
    """Load fresh config on each call so file edits don't require a server restart."""
    return load_config()


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
def server_info() -> dict[str, str]:
    """Return server version and configuration locations for diagnostics."""
    from yahoo_mail_mcp.config import config_path

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
