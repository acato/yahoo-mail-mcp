"""Tests for fetch_message and fetch_messages_bulk."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _addr(name, email):
    return SimpleNamespace(name=name, email=email)


def _attachment(filename, ct="application/pdf", size=1024):
    return SimpleNamespace(filename=filename, content_type=ct, size=size, payload=b"<bytes>")


def _fake_msg(uid, *, text="plain", html="<p>html</p>", attachments=None):
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
        flags=("\\Seen",),
        text=text,
        html=html,
        attachments=attachments or [],
    )


class _FakeFolder:
    def __init__(self):
        self.set_calls: list[str] = []

    def set(self, name):
        self.set_calls.append(name)


class _FakeMailBox:
    def __init__(self, fetch_response):
        self.folder = _FakeFolder()
        self._fetch_response = fetch_response
        self.last_fetch_criteria = None
        self.last_fetch_kwargs: dict = {}

    def fetch(self, criteria, **kwargs):
        self.last_fetch_criteria = criteria
        self.last_fetch_kwargs = kwargs
        return iter(self._fetch_response)


def _patch_pool(monkeypatch, mailbox):
    pool = MagicMock()
    pool.get.return_value = mailbox
    monkeypatch.setattr("yahoo_mail_mcp.server._get_pool", lambda: pool)


# --------------------- fetch_message -----------------------------------------


def test_fetch_message_headers(monkeypatch):
    from yahoo_mail_mcp.server import fetch_message

    mb = _FakeMailBox(fetch_response=[_fake_msg("42")])
    _patch_pool(monkeypatch, mb)

    out = fetch_message("primary", "Inbox", "42", fields="headers")
    assert out["uid"] == "42"
    assert out["subject"] == "Subject 42"
    assert "body_text" not in out
    assert "body_html" not in out
    assert "attachments" not in out
    assert mb.last_fetch_kwargs.get("headers_only") is True
    assert mb.last_fetch_kwargs.get("mark_seen") is False


def test_fetch_message_body(monkeypatch):
    from yahoo_mail_mcp.server import fetch_message

    mb = _FakeMailBox(fetch_response=[_fake_msg("42", text="hello", html="<b>hello</b>")])
    _patch_pool(monkeypatch, mb)

    out = fetch_message("primary", "Inbox", "42", fields="body")
    assert out["body_text"] == "hello"
    assert out["body_html"] == "<b>hello</b>"
    assert "attachments" not in out
    assert mb.last_fetch_kwargs.get("headers_only") is False


def test_fetch_message_full_includes_attachment_meta(monkeypatch):
    from yahoo_mail_mcp.server import fetch_message

    atts = [
        _attachment("invoice.pdf", "application/pdf", 4096),
        _attachment("photo.jpg", "image/jpeg", 99999),
    ]
    mb = _FakeMailBox(fetch_response=[_fake_msg("42", attachments=atts)])
    _patch_pool(monkeypatch, mb)

    out = fetch_message("primary", "Inbox", "42", fields="full")
    assert out["attachments"] == [
        {"filename": "invoice.pdf", "content_type": "application/pdf", "size": 4096},
        {"filename": "photo.jpg", "content_type": "image/jpeg", "size": 99999},
    ]
    # Critical: payload is NEVER surfaced via fetch_message.
    for att in out["attachments"]:
        assert "payload" not in att


def test_fetch_message_missing_uid(monkeypatch):
    from yahoo_mail_mcp.server import fetch_message

    mb = _FakeMailBox(fetch_response=[])
    _patch_pool(monkeypatch, mb)

    out = fetch_message("primary", "Inbox", "9999")
    assert out == {"uid": "9999", "missing": True}


def test_fetch_message_invalid_fields_raises(monkeypatch):
    from yahoo_mail_mcp.server import fetch_message

    mb = _FakeMailBox(fetch_response=[])
    _patch_pool(monkeypatch, mb)

    with pytest.raises(ValueError, match="fields must be"):
        fetch_message("primary", "Inbox", "1", fields="garbage")


# --------------------- fetch_messages_bulk -----------------------------------


def test_fetch_bulk_preserves_order(monkeypatch):
    from yahoo_mail_mcp.server import fetch_messages_bulk

    # Server returns in different order than requested
    server_order = [_fake_msg("3"), _fake_msg("1"), _fake_msg("2")]
    mb = _FakeMailBox(fetch_response=server_order)
    _patch_pool(monkeypatch, mb)

    out = fetch_messages_bulk("primary", "Inbox", ["1", "2", "3"])
    assert [m["uid"] for m in out] == ["1", "2", "3"]


def test_fetch_bulk_marks_missing_uids(monkeypatch):
    from yahoo_mail_mcp.server import fetch_messages_bulk

    # Server only returns 2 of the 3 requested
    mb = _FakeMailBox(fetch_response=[_fake_msg("1"), _fake_msg("3")])
    _patch_pool(monkeypatch, mb)

    out = fetch_messages_bulk("primary", "Inbox", ["1", "2", "3"])
    assert out[0]["uid"] == "1"
    assert out[1] == {"uid": "2", "missing": True}
    assert out[2]["uid"] == "3"


def test_fetch_bulk_empty_uids_returns_empty(monkeypatch):
    from yahoo_mail_mcp.server import fetch_messages_bulk

    mb = _FakeMailBox(fetch_response=[])
    _patch_pool(monkeypatch, mb)

    assert fetch_messages_bulk("primary", "Inbox", []) == []


def test_fetch_bulk_caps_at_100(monkeypatch):
    from yahoo_mail_mcp.server import BULK_FETCH_LIMIT_MAX, fetch_messages_bulk

    mb = _FakeMailBox(fetch_response=[])
    _patch_pool(monkeypatch, mb)

    too_many = [str(i) for i in range(BULK_FETCH_LIMIT_MAX + 1)]
    with pytest.raises(ValueError, match=f"at most {BULK_FETCH_LIMIT_MAX}"):
        fetch_messages_bulk("primary", "Inbox", too_many)


def test_fetch_bulk_invalid_fields(monkeypatch):
    from yahoo_mail_mcp.server import fetch_messages_bulk

    mb = _FakeMailBox(fetch_response=[])
    _patch_pool(monkeypatch, mb)

    with pytest.raises(ValueError, match="fields must be"):
        fetch_messages_bulk("primary", "Inbox", ["1"], fields="nope")


def test_fetch_bulk_uses_bulk_flag(monkeypatch):
    from yahoo_mail_mcp.server import fetch_messages_bulk

    mb = _FakeMailBox(fetch_response=[_fake_msg("1"), _fake_msg("2")])
    _patch_pool(monkeypatch, mb)

    fetch_messages_bulk("primary", "Inbox", ["1", "2"], fields="headers")
    assert mb.last_fetch_kwargs.get("bulk") is True
    assert mb.last_fetch_kwargs.get("mark_seen") is False
    assert mb.last_fetch_kwargs.get("headers_only") is True
