"""Live integration tests against a real Yahoo Mail account.

These tests are skipped unless YAHOO_MAIL_MCP_LIVE=1. They read the same
config the server reads (~/.config/yahoo-mail-mcp/config.toml by default,
or YAHOO_MAIL_MCP_CONFIG override). The first configured account is used —
no vendor-specific defaults in the test code.

Write tests (move/delete/etc) live in a separate file and require an
additional --allow-writes opt-in plus a designated test folder.
"""

from __future__ import annotations

import os

import pytest

LIVE_ENABLED = os.environ.get("YAHOO_MAIL_MCP_LIVE") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="live tests skipped — set YAHOO_MAIL_MCP_LIVE=1 to enable",
)


@pytest.fixture(scope="module")
def pool():
    from yahoo_mail_mcp.config import load_config
    from yahoo_mail_mcp.imap_client import ConnectionPool

    cfg = load_config()
    if not cfg.accounts:
        pytest.skip("no accounts configured in ~/.config/yahoo-mail-mcp/config.toml")
    p = ConnectionPool(cfg)
    try:
        yield p
    finally:
        p.close_all()


@pytest.fixture(scope="module")
def account_name():
    from yahoo_mail_mcp.config import load_config

    cfg = load_config()
    if not cfg.accounts:
        pytest.skip("no accounts configured")
    return next(iter(cfg.accounts))


def test_login_and_list_folders(pool, account_name):
    mb = pool.get(account_name)
    folders = list(mb.folder.list())
    assert len(folders) > 0
    # Every Yahoo account has an Inbox.
    folder_names_lower = [f.name.lower() for f in folders]
    assert any("inbox" in n for n in folder_names_lower)


def test_list_folders_tool_returns_inbox(pool, account_name):
    # Drive the actual MCP tool function (not the FastMCP decorator wrapper).
    from yahoo_mail_mcp.server import list_folders

    result = list_folders(account_name)
    assert len(result) > 0
    normalized = [entry["normalized"] for entry in result]
    assert "inbox" in normalized


def test_connection_reuse_within_session(pool, account_name):
    mb1 = pool.get(account_name)
    mb2 = pool.get(account_name)
    assert mb1 is mb2
