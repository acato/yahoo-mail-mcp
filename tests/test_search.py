"""Tests for the search tool and its criteria builder."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from yahoo_mail_mcp.server import _build_search_criteria, _envelope_dict


def _addr(name: str, email: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, email=email)


def _fake_msg(uid: str, *, subject="s", date_dt=None, size=100, seen=True) -> SimpleNamespace:
    """Build a minimal stand-in for an imap_tools MailMessage.

    Note: imap_tools' from_values is singular (one From sender), to_values/
    cc_values are iterables — we mirror that shape.
    """
    return SimpleNamespace(
        uid=uid,
        from_=f"From {uid} <from-{uid}@example.com>",
        from_values=_addr(f"From {uid}", f"from-{uid}@example.com"),
        to="you@yahoo.com",
        to_values=[_addr("You", "you@yahoo.com")],
        cc="",
        cc_values=[],
        subject=subject,
        date=date_dt or datetime(2026, 5, 1, 12, 0, 0),
        size_rfc822=size,
        flags=("\\Seen",) if seen else (),
        text="plain body",
        html="<p>html body</p>",
        attachments=[],
    )


# --------------------- _build_search_criteria ---------------------------------


def test_build_criteria_returns_ALL_when_empty():
    out = _build_search_criteria(
        from_addr=None,
        to_addr=None,
        subject=None,
        body_text=None,
        since=None,
        before=None,
        seen=None,
        larger_bytes=None,
        smaller_bytes=None,
    )
    assert out == "ALL"


def test_build_criteria_text_and_size_and_seen():
    out = _build_search_criteria(
        from_addr="spam@x.com",
        to_addr=None,
        subject="receipt",
        body_text="invoice",
        since=None,
        before=None,
        seen=False,
        larger_bytes=1024,
        smaller_bytes=1_000_000,
    )
    # imap_tools.AND returns a string under the hood; we just check it's
    # non-empty and contains our fields somewhere.
    s = str(out)
    assert "spam@x.com" in s
    assert "receipt" in s
    assert "invoice" in s
    assert "1024" in s
    assert "1000000" in s


def test_build_criteria_parses_iso_dates():
    out = _build_search_criteria(
        from_addr=None,
        to_addr=None,
        subject=None,
        body_text=None,
        since="2026-01-15",
        before="2026-03-01",
        seen=None,
        larger_bytes=None,
        smaller_bytes=None,
    )
    # Date format in IMAP is dd-Mon-yyyy; both dates should appear by month
    s = str(out)
    assert "Jan-2026" in s
    assert "Mar-2026" in s


def test_build_criteria_bad_date_raises():
    with pytest.raises(ValueError):
        _build_search_criteria(
            from_addr=None,
            to_addr=None,
            subject=None,
            body_text=None,
            since="not-a-date",
            before=None,
            seen=None,
            larger_bytes=None,
            smaller_bytes=None,
        )


# --------------------- _envelope_dict ----------------------------------------


def test_envelope_dict_basic():
    m = _fake_msg("1001")
    e = _envelope_dict(m)
    assert e["uid"] == "1001"
    assert e["from"] == "from-1001@example.com"
    assert e["from_display"].startswith("From 1001 ")
    assert e["to"] == ["you@yahoo.com"]
    assert e["cc"] == []
    assert e["date"] == "2026-05-01T12:00:00"
    assert e["size"] == 100
    assert e["flags"] == ["\\Seen"]


def test_envelope_dict_no_from():
    m = _fake_msg("42")
    m.from_values = None
    m.from_ = ""
    e = _envelope_dict(m)
    assert e["from"] == ""
    assert e["from_display"] == ""


def test_envelope_dict_no_date():
    m = _fake_msg("42")
    m.date = None
    assert _envelope_dict(m)["date"] is None


# --------------------- search() end-to-end via fake MailBox -------------------


class _FakeFolder:
    def __init__(self):
        self.set_calls: list[str] = []

    def set(self, name):
        self.set_calls.append(name)


class _FakeMailBox:
    """Fake MailBox that records folder.set, .uids, and .fetch calls."""

    def __init__(self, uids_response, fetch_response):
        self.folder = _FakeFolder()
        self._uids_response = uids_response
        self._fetch_response = fetch_response
        self.last_uids_criteria = None
        self.last_fetch_criteria = None
        self.last_fetch_kwargs: dict = {}

    def uids(self, criteria):
        self.last_uids_criteria = criteria
        return list(self._uids_response)

    def fetch(self, criteria, **kwargs):
        self.last_fetch_criteria = criteria
        self.last_fetch_kwargs = kwargs
        return iter(self._fetch_response)


def _patch_pool(monkeypatch, mailbox):
    """Replace _get_pool() with one that returns a MagicMock whose .get() returns `mailbox`."""
    pool = MagicMock()
    pool.get.return_value = mailbox
    monkeypatch.setattr("yahoo_mail_mcp.server._get_pool", lambda: pool)


def test_search_returns_sorted_newest_first(monkeypatch):
    from yahoo_mail_mcp.server import search

    uids_in_folder = ["100", "200", "50", "150"]
    fake_msgs = [_fake_msg(u) for u in uids_in_folder]
    mb = _FakeMailBox(uids_response=uids_in_folder, fetch_response=fake_msgs)
    _patch_pool(monkeypatch, mb)

    result = search("primary", "Inbox", subject="x")

    assert result["total"] == 4
    assert result["limit"] == 100
    assert result["offset"] == 0
    # Order in `hits` should be descending UID: 200, 150, 100, 50
    assert [h["uid"] for h in result["hits"]] == ["200", "150", "100", "50"]
    assert mb.folder.set_calls == ["Inbox"]
    assert mb.last_fetch_kwargs.get("mark_seen") is False
    assert mb.last_fetch_kwargs.get("headers_only") is True


def test_search_pagination(monkeypatch):
    from yahoo_mail_mcp.server import search

    # 10 UIDs from 100..109
    uids = [str(i) for i in range(100, 110)]
    fake_msgs = [_fake_msg(u) for u in uids]
    mb = _FakeMailBox(uids_response=uids, fetch_response=fake_msgs)
    _patch_pool(monkeypatch, mb)

    page1 = search("primary", "Inbox", limit=3, offset=0)
    assert page1["total"] == 10
    assert [h["uid"] for h in page1["hits"]] == ["109", "108", "107"]

    page2 = search("primary", "Inbox", limit=3, offset=3)
    assert [h["uid"] for h in page2["hits"]] == ["106", "105", "104"]


def test_search_offset_past_end_returns_empty(monkeypatch):
    from yahoo_mail_mcp.server import search

    mb = _FakeMailBox(uids_response=["100", "200"], fetch_response=[])
    _patch_pool(monkeypatch, mb)

    result = search("primary", "Inbox", limit=10, offset=50)
    assert result["total"] == 2
    assert result["hits"] == []


def test_search_no_results(monkeypatch):
    from yahoo_mail_mcp.server import search

    mb = _FakeMailBox(uids_response=[], fetch_response=[])
    _patch_pool(monkeypatch, mb)

    result = search("primary", "Inbox", from_addr="nobody@nowhere")
    assert result["total"] == 0
    assert result["hits"] == []


def test_search_limit_validation(monkeypatch):
    from yahoo_mail_mcp.server import SEARCH_LIMIT_MAX, search

    mb = _FakeMailBox(uids_response=[], fetch_response=[])
    _patch_pool(monkeypatch, mb)

    with pytest.raises(ValueError, match="limit must be >= 1"):
        search("primary", "Inbox", limit=0)
    with pytest.raises(ValueError, match=f"limit must be <= {SEARCH_LIMIT_MAX}"):
        search("primary", "Inbox", limit=SEARCH_LIMIT_MAX + 1)
    with pytest.raises(ValueError, match="offset must be >= 0"):
        search("primary", "Inbox", offset=-1)


def test_search_passes_criteria_to_uids(monkeypatch):
    from yahoo_mail_mcp.server import search

    mb = _FakeMailBox(uids_response=[], fetch_response=[])
    _patch_pool(monkeypatch, mb)

    search("primary", "Inbox", from_addr="spam@x.com", seen=False, since="2026-01-01")

    criteria_str = str(mb.last_uids_criteria)
    assert "spam@x.com" in criteria_str
    # Yahoo seen=False maps to UNSEEN
    assert "UNSEEN" in criteria_str.upper()
    assert "Jan-2026" in criteria_str
