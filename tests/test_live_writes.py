"""Live integration tests for write tools — mark_read, move_message, delete_message.

DOUBLE OPT-IN required to run:
  - YAHOO_MAIL_MCP_LIVE=1
  - YAHOO_MAIL_MCP_LIVE_WRITES=1

Tests borrow a real message from the account's spam folder, route it through
a dedicated `_mcp-test` folder for destructive ops, and (for non-destructive
tests) restore the original state. The user must explicitly opt in to having
their spam folder touched.
"""

from __future__ import annotations

import contextlib
import os

import pytest
from imap_tools import AND, H, MailMessageFlags

LIVE_ENABLED = os.environ.get("YAHOO_MAIL_MCP_LIVE") == "1"
WRITES_ENABLED = os.environ.get("YAHOO_MAIL_MCP_LIVE_WRITES") == "1"

TEST_FOLDER = "_mcp-test"

pytestmark = pytest.mark.skipif(
    not (LIVE_ENABLED and WRITES_ENABLED),
    reason="live-write tests skipped — set YAHOO_MAIL_MCP_LIVE=1 AND YAHOO_MAIL_MCP_LIVE_WRITES=1",
)


# --------------------- fixtures ----------------------------------------------


@pytest.fixture(scope="module")
def pool():
    """Use the same global pool the tool functions use.

    Important: write tests must NOT spin up a second IMAP connection. If they
    do, mutations performed via the tool functions land in the server's pool
    but verification queries from the test's own pool race against Yahoo's
    cross-session sync and see stale state. One process, one pool.
    """
    from yahoo_mail_mcp.config import load_config
    from yahoo_mail_mcp.server import _get_pool

    cfg = load_config()
    if not cfg.accounts:
        pytest.skip("no accounts configured")
    yield _get_pool()


@pytest.fixture(scope="module")
def account_name():
    from yahoo_mail_mcp.config import load_config

    cfg = load_config()
    if not cfg.accounts:
        pytest.skip("no accounts")
    return next(iter(cfg.accounts))


@pytest.fixture(scope="module")
def spam_folder(pool, account_name):
    """Return the first non-empty folder canonicalized as 'spam'."""
    from yahoo_mail_mcp.server import list_folders

    for entry in list_folders(account_name):
        if entry["normalized"] == "spam" and entry["total"] > 0:
            return entry["name"]
    pytest.skip("no non-empty spam folder available")


@pytest.fixture(scope="module")
def test_folder(pool, account_name):
    """Ensure _mcp-test exists; idempotent."""
    mb = pool.get(account_name)
    with contextlib.suppress(Exception):
        mb.folder.create(TEST_FOLDER)
    return TEST_FOLDER


# --------------------- helpers -----------------------------------------------


def _pick_recent_uid(mb, folder: str) -> str | None:
    """Return the highest-numbered UID in `folder`, or None if empty."""
    mb.folder.set(folder)
    uids = list(mb.uids("ALL"))
    if not uids:
        return None
    return sorted(uids, key=int, reverse=True)[0]


def _get_message_id(mb, uid: str) -> str | None:
    """Return the value of the Message-ID header for a UID, or None."""
    fetched = list(mb.fetch(AND(uid=uid), headers_only=True, mark_seen=False))
    if not fetched:
        return None
    mid = fetched[0].headers.get("message-id")
    return mid[0] if mid else None


def _find_by_message_id(mb, folder: str, message_id: str) -> list[str]:
    """Return all UIDs in `folder` whose Message-ID header matches."""
    mb.folder.set(folder)
    return list(mb.uids(AND(header=H("Message-ID", message_id))))


# --------------------- mark_read ---------------------------------------------


def test_live_mark_read_toggles_seen(pool, account_name, spam_folder):
    """Toggle \\Seen on a real spam message and restore original state."""
    from yahoo_mail_mcp.server import mark_read

    mb = pool.get(account_name)
    uid = _pick_recent_uid(mb, spam_folder)
    if uid is None:
        pytest.skip("spam folder empty")

    original = next(mb.fetch(AND(uid=uid), headers_only=True, mark_seen=False))
    was_seen = "\\Seen" in original.flags

    try:
        # Toggle to opposite of current state.
        result = mark_read(account_name, spam_folder, uid, read=not was_seen)
        assert ("\\Seen" in result["flags"]) is (not was_seen)

        # Toggle back.
        result = mark_read(account_name, spam_folder, uid, read=was_seen)
        assert ("\\Seen" in result["flags"]) is was_seen
    finally:
        # Belt-and-braces: force-restore the original \\Seen state.
        with contextlib.suppress(Exception):
            mb.flag([uid], [MailMessageFlags.SEEN], was_seen)


def test_live_mark_read_missing_uid(pool, account_name, spam_folder):
    from yahoo_mail_mcp.server import mark_read

    result = mark_read(account_name, spam_folder, "999999999")
    assert result == {"uid": "999999999", "missing": True}


# --------------------- move_message ------------------------------------------


def test_live_move_message_relocates(pool, account_name, spam_folder, test_folder):
    """Move a spam message into _mcp-test, verify, then move back."""
    from yahoo_mail_mcp.server import move_message

    mb = pool.get(account_name)
    source_uid = _pick_recent_uid(mb, spam_folder)
    if source_uid is None:
        pytest.skip("spam folder empty")
    message_id = _get_message_id(mb, source_uid)
    if not message_id:
        pytest.skip("source message has no Message-ID — can't track across move")

    try:
        result = move_message(account_name, spam_folder, source_uid, test_folder)
        assert result["moved"] is True

        # Source no longer has it.
        assert _find_by_message_id(mb, spam_folder, message_id) == []

        # Destination does (under a new UID).
        dest_uids = _find_by_message_id(mb, test_folder, message_id)
        assert len(dest_uids) == 1
    finally:
        # Return the message to spam if it ended up in _mcp-test.
        with contextlib.suppress(Exception):
            stragglers = _find_by_message_id(mb, test_folder, message_id)
            if stragglers:
                mb.move(stragglers, spam_folder)


def test_live_move_message_missing_uid(pool, account_name, spam_folder, test_folder):
    from yahoo_mail_mcp.server import move_message

    result = move_message(account_name, spam_folder, "999999999", test_folder)
    assert result == {"uid": "999999999", "missing": True}


# --------------------- delete_message ----------------------------------------
# User explicitly authorized deleting today's spam ("they are all junk").
# Destructive ops route through _mcp-test for isolation.


def test_live_delete_message_with_expunge(pool, account_name, spam_folder, test_folder):
    """Move a spam message into _mcp-test, then delete with expunge. Permanent."""
    from yahoo_mail_mcp.server import delete_message

    mb = pool.get(account_name)
    source_uid = _pick_recent_uid(mb, spam_folder)
    if source_uid is None:
        pytest.skip("spam folder empty")
    message_id = _get_message_id(mb, source_uid)
    if not message_id:
        pytest.skip("source message has no Message-ID")

    # Relocate to test folder so the destructive op operates there.
    mb.folder.set(spam_folder)
    mb.move([source_uid], test_folder)
    test_uids = _find_by_message_id(mb, test_folder, message_id)
    assert len(test_uids) == 1
    test_uid = test_uids[0]

    result = delete_message(account_name, test_folder, test_uid, expunge=True)
    assert result == {"uid": test_uid, "deleted": True, "expunged": True}

    # Verify gone.
    assert _find_by_message_id(mb, test_folder, message_id) == []


def test_live_delete_message_without_expunge_yahoo_auto_expunges(
    pool, account_name, spam_folder, test_folder
):
    """Yahoo IMAP auto-expunges on STORE \\Deleted regardless of expunge param.

    The tool still reports `expunged: False` because that's what the caller
    asked for, but the message disappears anyway. See DESIGN.md §5 Yahoo
    quirks.
    """
    from yahoo_mail_mcp.server import delete_message

    mb = pool.get(account_name)
    source_uid = _pick_recent_uid(mb, spam_folder)
    if source_uid is None:
        pytest.skip("spam folder empty")
    message_id = _get_message_id(mb, source_uid)
    if not message_id:
        pytest.skip("source message has no Message-ID")

    mb.folder.set(spam_folder)
    mb.move([source_uid], test_folder)
    test_uids = _find_by_message_id(mb, test_folder, message_id)
    assert len(test_uids) == 1
    test_uid = test_uids[0]

    result = delete_message(account_name, test_folder, test_uid, expunge=False)
    # The tool reports caller intent (expunged: False), not Yahoo's actual
    # behavior. Both are honest; just different perspectives.
    assert result == {"uid": test_uid, "deleted": True, "expunged": False}

    # Despite expunge=False, Yahoo auto-removed the message.
    assert _find_by_message_id(mb, test_folder, message_id) == []


def test_live_delete_message_missing_uid(pool, account_name, test_folder):
    from yahoo_mail_mcp.server import delete_message

    result = delete_message(account_name, test_folder, "999999999")
    assert result == {"uid": "999999999", "missing": True}
