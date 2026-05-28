"""Folder-name normalization.

Yahoo Mail folder names vary by account age, prior email clients used, and
locale. This module maps known variants onto a small canonical set so that
the LLM can reason about folders by role rather than by exact label.

Canonical names: inbox, sent, drafts, trash, spam, archive.

Custom user-created folders return None — callers should fall back to the
original name when no canonical mapping exists.
"""

from __future__ import annotations

# Lowercase leaf-name → canonical role.
# Keep this map small and high-confidence. We do NOT try to be clever about
# user-created folders that happen to contain the word "sent" etc.
_NORMALIZATION: dict[str, str] = {
    # Inbox
    "inbox": "inbox",
    # Sent
    "sent": "sent",
    "sent items": "sent",
    "sent messages": "sent",
    "sent mail": "sent",
    # Drafts
    "drafts": "drafts",
    "draft": "drafts",
    # Trash
    "trash": "trash",
    "deleted": "trash",
    "deleted items": "trash",
    "deleted messages": "trash",
    "bin": "trash",
    # Spam / Junk / Bulk Mail (Yahoo's legacy name was "Bulk Mail")
    "spam": "spam",
    "junk": "spam",
    "junk email": "spam",
    "junk e-mail": "spam",
    "bulk": "spam",
    "bulk mail": "spam",
    # Archive
    "archive": "archive",
    "all mail": "archive",
}


def canonical_name(folder_name: str) -> str | None:
    """Return the canonical role of a folder, or None if not recognized.

    Matching is case-insensitive against the leaf segment of the folder path
    (separated by either '/' or '.', the two common IMAP hierarchy delimiters).
    Trailing/leading whitespace is stripped.

    Args:
        folder_name: raw folder name from the IMAP server (e.g., "INBOX",
            "Bulk Mail", "Personal/Receipts").

    Returns:
        One of "inbox", "sent", "drafts", "trash", "spam", "archive", or
        None if the folder is user-created or otherwise unrecognized.
    """
    if not folder_name:
        return None
    # Strip both common IMAP hierarchy delimiters; keep the leaf.
    leaf = folder_name.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    return _NORMALIZATION.get(leaf.strip().lower())
