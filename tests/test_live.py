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


def _pick_folder(pool, account_name, *roles):
    """Pick the first non-empty folder matching one of the canonical roles."""
    from yahoo_mail_mcp.server import list_folders

    folders = list_folders(account_name)
    for entry in folders:
        if entry["normalized"] in roles and entry["total"] > 0:
            return entry["name"]
    return None


def test_search_smoke_spam_folder(pool, account_name):
    """Search the spam folder — typically smallest live folder, fast to scan."""
    from yahoo_mail_mcp.server import search

    folder = _pick_folder(pool, account_name, "spam")
    if folder is None:
        pytest.skip("no non-empty spam folder")

    result = search(account_name, folder, limit=5)
    assert result["total"] > 0
    assert len(result["hits"]) <= 5
    assert all("uid" in h for h in result["hits"])
    assert all("from" in h for h in result["hits"])
    assert all("subject" in h for h in result["hits"])
    # Newest-first invariant
    if len(result["hits"]) >= 2:
        uids = [int(h["uid"]) for h in result["hits"]]
        assert uids == sorted(uids, reverse=True)


def test_fetch_message_headers_smoke(pool, account_name):
    """Fetch one envelope from the spam folder."""
    from yahoo_mail_mcp.server import fetch_message, search

    folder = _pick_folder(pool, account_name, "spam")
    if folder is None:
        pytest.skip("no non-empty spam folder")

    hits = search(account_name, folder, limit=1)["hits"]
    if not hits:
        pytest.skip("spam folder reported non-empty by STATUS but search returned 0 hits")

    uid = hits[0]["uid"]
    msg = fetch_message(account_name, folder, uid, fields="headers")
    assert msg.get("uid") == uid
    assert "subject" in msg
    assert "body_text" not in msg
    assert "body_html" not in msg


def test_fetch_bulk_preserves_order_live(pool, account_name):
    """Bulk-fetch the first few hits and verify the order is preserved."""
    from yahoo_mail_mcp.server import fetch_messages_bulk, search

    folder = _pick_folder(pool, account_name, "spam")
    if folder is None:
        pytest.skip("no non-empty spam folder")

    hits = search(account_name, folder, limit=3)["hits"]
    if len(hits) < 2:
        pytest.skip("need at least 2 messages in spam folder")

    uids = [h["uid"] for h in hits]
    fetched = fetch_messages_bulk(account_name, folder, uids, fields="headers")
    assert [m["uid"] for m in fetched] == uids


def test_search_unread_in_spam_smoke(pool, account_name):
    """Combine criteria: unread + size lower-bound."""
    from yahoo_mail_mcp.server import search

    folder = _pick_folder(pool, account_name, "spam")
    if folder is None:
        pytest.skip("no non-empty spam folder")

    result = search(account_name, folder, seen=False, limit=10)
    # No assertion about non-zero count — spam folder may have all seen.
    assert "total" in result
    assert isinstance(result["total"], int)
    for h in result["hits"]:
        assert "\\Seen" not in h["flags"]
