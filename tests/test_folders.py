"""Tests for folder-name normalization."""

from __future__ import annotations

import pytest

from yahoo_mail_mcp.folders import canonical_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Inbox
        ("INBOX", "inbox"),
        ("Inbox", "inbox"),
        ("inbox", "inbox"),
        # Sent
        ("Sent", "sent"),
        ("Sent Items", "sent"),
        ("SENT MESSAGES", "sent"),
        ("Sent Mail", "sent"),
        # Drafts
        ("Drafts", "drafts"),
        ("Draft", "drafts"),
        # Trash
        ("Trash", "trash"),
        ("Deleted", "trash"),
        ("Deleted Items", "trash"),
        ("Deleted Messages", "trash"),
        ("Bin", "trash"),
        # Spam / Junk / Bulk Mail
        ("Spam", "spam"),
        ("Junk", "spam"),
        ("Junk Email", "spam"),
        ("Junk E-mail", "spam"),
        ("Bulk", "spam"),
        ("Bulk Mail", "spam"),
        # Archive
        ("Archive", "archive"),
        ("All Mail", "archive"),
    ],
)
def test_canonical_name_recognized(raw, expected):
    assert canonical_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Personal",
        "Receipts",
        "Travel/2026",
        "Family/Photos",
        "Newsletters/Tech",
        "",
        "  ",
        "Foo Bar Baz",
    ],
)
def test_canonical_name_user_folder_returns_none(raw):
    assert canonical_name(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Hierarchical folders normalize on the leaf only
        ("Personal/Inbox", "inbox"),
        ("Work/Sent", "sent"),
        ("INBOX.Drafts", "drafts"),
        ("INBOX.Spam", "spam"),
        # Mixed delimiters
        ("inbox/spam", "spam"),
    ],
)
def test_canonical_name_hierarchy_leaf(raw, expected):
    assert canonical_name(raw) == expected


def test_canonical_name_strips_whitespace():
    assert canonical_name("  Inbox  ") == "inbox"
    assert canonical_name("Sent Items   ") == "sent"


def test_canonical_name_handles_empty():
    assert canonical_name("") is None
