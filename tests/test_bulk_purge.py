"""Tests for bulk_purge_from — the safety-gated bulk delete tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _FakeFolder:
    def __init__(self):
        self.set_calls: list[str] = []

    def set(self, name):
        self.set_calls.append(name)


class _FakeMailBox:
    """Fake MailBox that scripts uids() responses and records delete() calls."""

    def __init__(self, uids_response):
        self.folder = _FakeFolder()
        self._uids_response = list(uids_response)
        self.last_uids_criteria = None
        self.delete_calls: list[tuple] = []

    def uids(self, criteria):
        self.last_uids_criteria = criteria
        return list(self._uids_response)

    def delete(self, uids):
        self.delete_calls.append(tuple(uids))


def _patch_pool(monkeypatch, mailbox):
    pool = MagicMock()
    pool.get.return_value = mailbox
    monkeypatch.setattr("yahoo_mail_mcp.server._get_pool", lambda: pool)


# --------------------- happy path -------------------------------------------


def test_bulk_purge_matches_and_deletes(monkeypatch):
    from yahoo_mail_mcp.server import bulk_purge_from

    mb = _FakeMailBox(uids_response=["100", "101", "102"])
    _patch_pool(monkeypatch, mb)

    result = bulk_purge_from("primary", "Bulk", "spam@bad.com", confirm_count=3)

    assert result == {
        "purged_count": 3,
        "confirmed_count": 3,
        "from_address": "spam@bad.com",
        "folder": "Bulk",
    }
    assert mb.delete_calls == [("100", "101", "102")]
    assert mb.folder.set_calls == ["Bulk"]
    # Criteria used FROM
    assert "spam@bad.com" in str(mb.last_uids_criteria)


def test_bulk_purge_zero_matches_zero_expected(monkeypatch):
    from yahoo_mail_mcp.server import bulk_purge_from

    mb = _FakeMailBox(uids_response=[])
    _patch_pool(monkeypatch, mb)

    result = bulk_purge_from("primary", "Bulk", "nobody@x.com", confirm_count=0)

    assert result == {
        "purged_count": 0,
        "confirmed_count": 0,
        "from_address": "nobody@x.com",
        "folder": "Bulk",
    }
    assert mb.delete_calls == []


# --------------------- count-mismatch refusal --------------------------------


def test_bulk_purge_refuses_when_more_than_expected(monkeypatch):
    from yahoo_mail_mcp.server import bulk_purge_from

    mb = _FakeMailBox(uids_response=["100", "101", "102", "103"])
    _patch_pool(monkeypatch, mb)

    result = bulk_purge_from("primary", "Bulk", "spam@bad.com", confirm_count=3)

    assert result["refused"] is True
    assert result["reason"] == "count_mismatch"
    assert result["expected_count"] == 3
    assert result["actual_count"] == 4
    assert "spam@bad.com" in result["from_address"]
    # CRITICAL: no delete on refusal
    assert mb.delete_calls == []


def test_bulk_purge_refuses_when_fewer_than_expected(monkeypatch):
    from yahoo_mail_mcp.server import bulk_purge_from

    mb = _FakeMailBox(uids_response=["100"])
    _patch_pool(monkeypatch, mb)

    result = bulk_purge_from("primary", "Bulk", "spam@bad.com", confirm_count=10)

    assert result["refused"] is True
    assert result["actual_count"] == 1
    assert result["expected_count"] == 10
    assert mb.delete_calls == []


def test_bulk_purge_refuses_when_expected_nonzero_actual_zero(monkeypatch):
    """Important edge: caller thinks there are messages but there aren't."""
    from yahoo_mail_mcp.server import bulk_purge_from

    mb = _FakeMailBox(uids_response=[])
    _patch_pool(monkeypatch, mb)

    result = bulk_purge_from("primary", "Bulk", "spam@bad.com", confirm_count=5)

    assert result["refused"] is True
    assert result["actual_count"] == 0
    assert result["expected_count"] == 5
    assert mb.delete_calls == []


# --------------------- validation -------------------------------------------


def test_bulk_purge_negative_confirm_count_raises(monkeypatch):
    from yahoo_mail_mcp.server import bulk_purge_from

    mb = _FakeMailBox(uids_response=[])
    _patch_pool(monkeypatch, mb)

    with pytest.raises(ValueError, match="confirm_count must be >= 0"):
        bulk_purge_from("primary", "Bulk", "x@y.com", confirm_count=-1)


def test_bulk_purge_message_includes_corrected_confirm_count(monkeypatch):
    """The refusal message should tell the LLM exactly what count to use."""
    from yahoo_mail_mcp.server import bulk_purge_from

    mb = _FakeMailBox(uids_response=["1", "2", "3", "4", "5", "6", "7"])
    _patch_pool(monkeypatch, mb)

    result = bulk_purge_from("primary", "Bulk", "x@y.com", confirm_count=5)

    assert "confirm_count=7" in result["message"]
