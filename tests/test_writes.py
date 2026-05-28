"""Tests for mark_read, move_message, delete_message — write-side tools.

Uses a fake MailBox that records flag/move/delete calls and serves predetermined
fetch responses for the existence-check / flag-readback steps.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _addr(name, email):
    return SimpleNamespace(name=name, email=email)


def _fake_msg(uid, *, flags=("\\Seen",)):
    return SimpleNamespace(
        uid=uid,
        from_=f"Sender <s{uid}@example.com>",
        from_values=_addr("Sender", f"s{uid}@example.com"),
        to="you@yahoo.com",
        to_values=[_addr("You", "you@yahoo.com")],
        cc="",
        cc_values=[],
        subject=f"Subject {uid}",
        date=datetime(2026, 5, 1, 12, 0, 0),
        size_rfc822=1234,
        flags=flags,
        text="",
        html="",
        attachments=[],
    )


class _FakeFolder:
    def __init__(self):
        self.set_calls: list[str] = []

    def set(self, name):
        self.set_calls.append(name)


class _FakeMailBox:
    """Records flag/move/delete calls; returns scripted fetch responses.

    Each fetch consumes one entry from `fetch_queue` (FIFO) so tests can script
    "first fetch shows existence, second fetch shows post-op flags".
    """

    def __init__(self, fetch_queue):
        self.folder = _FakeFolder()
        self.fetch_queue = list(fetch_queue)
        self.flag_calls: list[tuple] = []  # (uids, flagset, value)
        self.move_calls: list[tuple] = []  # (uids, target)
        self.delete_calls: list[tuple] = []  # (uids,)

    def fetch(self, criteria, **kwargs):
        if not self.fetch_queue:
            return iter([])
        return iter(self.fetch_queue.pop(0))

    def flag(self, uids, flagset, value):
        self.flag_calls.append((tuple(uids), tuple(flagset), value))

    def move(self, uids, target):
        self.move_calls.append((tuple(uids), target))

    def delete(self, uids):
        self.delete_calls.append((tuple(uids),))


def _patch_pool(monkeypatch, mailbox):
    pool = MagicMock()
    pool.get.return_value = mailbox
    monkeypatch.setattr("yahoo_mail_mcp.server._get_pool", lambda: pool)


# --------------------- mark_read ---------------------------------------------


def test_mark_read_sets_seen(monkeypatch):
    from yahoo_mail_mcp.server import mark_read

    # First fetch: existence check (unseen). Second fetch: post-flag readback.
    mb = _FakeMailBox(
        fetch_queue=[[_fake_msg("42", flags=())], [_fake_msg("42", flags=("\\Seen",))]]
    )
    _patch_pool(monkeypatch, mb)

    result = mark_read("primary", "Inbox", "42", read=True)

    assert result["uid"] == "42"
    assert "\\Seen" in result["flags"]
    assert len(mb.flag_calls) == 1
    uids, flagset, value = mb.flag_calls[0]
    assert uids == ("42",)
    assert value is True
    # MailMessageFlags.SEEN is "\\Seen"
    assert "\\Seen" in flagset


def test_mark_read_clears_seen(monkeypatch):
    from yahoo_mail_mcp.server import mark_read

    mb = _FakeMailBox(
        fetch_queue=[[_fake_msg("42", flags=("\\Seen",))], [_fake_msg("42", flags=())]]
    )
    _patch_pool(monkeypatch, mb)

    result = mark_read("primary", "Inbox", "42", read=False)

    assert "\\Seen" not in result["flags"]
    _, _, value = mb.flag_calls[0]
    assert value is False


def test_mark_read_missing_uid(monkeypatch):
    from yahoo_mail_mcp.server import mark_read

    mb = _FakeMailBox(fetch_queue=[[]])
    _patch_pool(monkeypatch, mb)

    assert mark_read("primary", "Inbox", "9999") == {"uid": "9999", "missing": True}
    assert mb.flag_calls == []  # never attempted the flag op


# --------------------- move_message ------------------------------------------


def test_move_message_happy(monkeypatch):
    from yahoo_mail_mcp.server import move_message

    mb = _FakeMailBox(fetch_queue=[[_fake_msg("42")]])
    _patch_pool(monkeypatch, mb)

    result = move_message("primary", "Inbox", "42", "Archive")

    assert result == {"uid": "42", "target_folder": "Archive", "moved": True}
    assert mb.move_calls == [(("42",), "Archive")]
    assert mb.folder.set_calls == ["Inbox"]


def test_move_message_missing_uid_does_not_call_move(monkeypatch):
    from yahoo_mail_mcp.server import move_message

    mb = _FakeMailBox(fetch_queue=[[]])
    _patch_pool(monkeypatch, mb)

    assert move_message("primary", "Inbox", "9999", "Archive") == {
        "uid": "9999",
        "missing": True,
    }
    assert mb.move_calls == []


# --------------------- delete_message ----------------------------------------


def test_delete_message_with_expunge(monkeypatch):
    from yahoo_mail_mcp.server import delete_message

    mb = _FakeMailBox(fetch_queue=[[_fake_msg("42")]])
    _patch_pool(monkeypatch, mb)

    result = delete_message("primary", "Inbox", "42", expunge=True)

    assert result == {"uid": "42", "deleted": True, "expunged": True}
    assert mb.delete_calls == [(("42",),)]
    assert mb.flag_calls == []


def test_delete_message_without_expunge_uses_flag(monkeypatch):
    from yahoo_mail_mcp.server import delete_message

    mb = _FakeMailBox(fetch_queue=[[_fake_msg("42")]])
    _patch_pool(monkeypatch, mb)

    result = delete_message("primary", "Inbox", "42", expunge=False)

    assert result == {"uid": "42", "deleted": True, "expunged": False}
    assert mb.delete_calls == []
    assert len(mb.flag_calls) == 1
    uids, flagset, value = mb.flag_calls[0]
    assert uids == ("42",)
    assert value is True
    assert "\\Deleted" in flagset


def test_delete_message_missing_uid(monkeypatch):
    from yahoo_mail_mcp.server import delete_message

    mb = _FakeMailBox(fetch_queue=[[]])
    _patch_pool(monkeypatch, mb)

    assert delete_message("primary", "Inbox", "9999") == {
        "uid": "9999",
        "missing": True,
    }
    assert mb.delete_calls == []
    assert mb.flag_calls == []


# --------------------- safety: existence check always runs first -------------


@pytest.mark.parametrize(
    "tool_call",
    [
        lambda: __import__("yahoo_mail_mcp.server", fromlist=["mark_read"]).mark_read(
            "primary", "Inbox", "42"
        ),
        lambda: __import__("yahoo_mail_mcp.server", fromlist=["move_message"]).move_message(
            "primary", "Inbox", "42", "Trash"
        ),
        lambda: __import__("yahoo_mail_mcp.server", fromlist=["delete_message"]).delete_message(
            "primary", "Inbox", "42"
        ),
    ],
    ids=["mark_read", "move_message", "delete_message"],
)
def test_no_op_if_uid_missing(monkeypatch, tool_call):
    """Every write tool must check existence before issuing the destructive op."""
    mb = _FakeMailBox(fetch_queue=[[]])
    _patch_pool(monkeypatch, mb)

    result = tool_call()

    assert result.get("missing") is True
    assert mb.flag_calls == []
    assert mb.move_calls == []
    assert mb.delete_calls == []
