"""MCP server entry point for yahoo-mail-mcp.

Registers the FastMCP server and the user-facing tools. Tools that need an
IMAP connection borrow one from a process-level ConnectionPool that's lazily
constructed and lazily refreshed when the config file changes.
"""

from __future__ import annotations

import atexit
import contextlib
from datetime import date as date_cls
from typing import Any

from imap_tools import AND, MailMessageFlags
from mcp.server.fastmcp import FastMCP

from yahoo_mail_mcp import __version__
from yahoo_mail_mcp.config import Config, config_path, load_config
from yahoo_mail_mcp.folders import canonical_name
from yahoo_mail_mcp.imap_client import ConnectionPool

# Hard caps that keep individual tool responses tractable for the LLM. If the
# user wants more, they paginate via `offset`.
SEARCH_LIMIT_MAX = 500
BULK_FETCH_LIMIT_MAX = 100

mcp = FastMCP("yahoo-mail-mcp")

# Process-level pool. Lazily built; rebuilt if the config file mtime changes
# so that adding an account doesn't require restarting the MCP.
_pool: ConnectionPool | None = None
_pool_config_mtime: float | None = None


def _config() -> Config:
    """Load fresh config on each call so file edits don't require a server restart."""
    return load_config()


def _get_pool() -> ConnectionPool:
    """Return the process-wide ConnectionPool, rebuilding it if config changed."""
    global _pool, _pool_config_mtime
    cfg_path = config_path()
    current_mtime = cfg_path.stat().st_mtime if cfg_path.exists() else 0.0
    if _pool is None or current_mtime != _pool_config_mtime:
        if _pool is not None:
            _pool.close_all()
        _pool = ConnectionPool(_config())
        _pool_config_mtime = current_mtime
    return _pool


@atexit.register
def _shutdown_pool() -> None:
    """Best-effort cleanup of IMAP connections on interpreter exit."""
    global _pool
    if _pool is not None:
        with contextlib.suppress(Exception):
            _pool.close_all()
        _pool = None


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
def list_folders(host: str) -> list[dict[str, int | str | None]]:
    """List every folder in the account's mailbox with message counts.

    Args:
        host: account nickname from config (e.g., "primary"). Use
            list_accounts() to see the valid nicknames.

    Returns:
        One entry per folder with:
          - name: original IMAP folder name (e.g., "Bulk Mail")
          - normalized: canonical role ("inbox", "sent", "drafts", "trash",
            "spam", "archive") or null for user-created folders
          - total: total message count
          - unseen: unread message count
    """
    pool = _get_pool()
    mb = pool.get(host)
    out: list[dict[str, int | str | None]] = []
    for folder in mb.folder.list():
        try:
            stats = mb.folder.status(folder.name, options=("MESSAGES", "UNSEEN"))
            total = int(stats.get("MESSAGES", 0))
            unseen = int(stats.get("UNSEEN", 0))
        except Exception:
            # Some IMAP servers refuse STATUS on certain folders (e.g.,
            # \Noselect). Report -1 so the LLM knows the count is unknown.
            total = -1
            unseen = -1
        out.append(
            {
                "name": folder.name,
                "normalized": canonical_name(folder.name),
                "total": total,
                "unseen": unseen,
            }
        )
    return out


def _build_search_criteria(
    *,
    from_addr: str | None,
    to_addr: str | None,
    subject: str | None,
    body_text: str | None,
    since: str | None,
    before: str | None,
    seen: bool | None,
    larger_bytes: int | None,
    smaller_bytes: int | None,
) -> Any:
    """Translate user-friendly args into an imap_tools.AND criteria object.

    Date strings are ISO format (YYYY-MM-DD). Yahoo IMAP only supports day-
    level granularity for SINCE/BEFORE.

    Returns AND("ALL") when no criteria are supplied so the caller still gets
    a well-formed search query.
    """
    kwargs: dict[str, Any] = {}
    if from_addr is not None:
        kwargs["from_"] = from_addr
    if to_addr is not None:
        kwargs["to"] = to_addr
    if subject is not None:
        kwargs["subject"] = subject
    if body_text is not None:
        kwargs["body"] = body_text
    if since is not None:
        kwargs["date_gte"] = date_cls.fromisoformat(since)
    if before is not None:
        kwargs["date_lt"] = date_cls.fromisoformat(before)
    if seen is not None:
        kwargs["seen"] = seen
    if larger_bytes is not None:
        kwargs["size_gt"] = larger_bytes
    if smaller_bytes is not None:
        kwargs["size_lt"] = smaller_bytes
    if not kwargs:
        return "ALL"
    return AND(**kwargs)


def _envelope_dict(msg: Any) -> dict[str, Any]:
    """Format an imap_tools.MailMessage as the envelope-style dict the LLM gets.

    Note: imap_tools exposes `from_values` as a single EmailAddress (the From
    header is one sender), while `to_values` and `cc_values` are iterables.
    """
    from_value = msg.from_values  # single EmailAddress or None
    to_values = list(msg.to_values) if msg.to_values else []
    cc_values = list(msg.cc_values) if msg.cc_values else []
    return {
        "uid": msg.uid,
        "from": from_value.email if from_value else "",
        "from_display": msg.from_ or "",
        "to": [a.email for a in to_values],
        "cc": [a.email for a in cc_values],
        "subject": msg.subject or "",
        "date": msg.date.isoformat() if msg.date else None,
        "size": msg.size_rfc822 or 0,
        "flags": list(msg.flags),
    }


@mcp.tool()
def search(
    host: str,
    folder: str,
    from_addr: str | None = None,
    to_addr: str | None = None,
    subject: str | None = None,
    body_text: str | None = None,
    since: str | None = None,
    before: str | None = None,
    seen: bool | None = None,
    larger_bytes: int | None = None,
    smaller_bytes: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Search a folder and return matching message envelopes, newest first.

    All criteria are AND-combined. None values are ignored. At least one
    criterion is recommended; omitting all returns the full folder.

    Args:
        host: account nickname from config.
        folder: IMAP folder name (e.g., "Inbox", "Bulk Mail"). Get the exact
            name from list_folders().
        from_addr: substring or address match against the From header.
        to_addr: substring or address match against the To header.
        subject: substring match in Subject.
        body_text: substring match in the message body.
        since: ISO date "YYYY-MM-DD"; matches messages dated on or after.
        before: ISO date "YYYY-MM-DD"; matches messages strictly before.
        seen: true = only read messages; false = only unread; null = both.
        larger_bytes: minimum message size in bytes.
        smaller_bytes: maximum message size in bytes.
        limit: max hits to return (capped at 500). Defaults to 100.
        offset: pagination offset into the matched UIDs (newest-first order).

    Returns:
        dict with:
          - total: total number of matching UIDs in the folder
          - limit, offset: echoed back so the LLM can paginate
          - hits: list of envelope dicts (uid, from, from_display, to, cc,
            subject, date, size, flags). Empty if total == 0 or offset > total.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if limit > SEARCH_LIMIT_MAX:
        raise ValueError(f"limit must be <= {SEARCH_LIMIT_MAX}")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    pool = _get_pool()
    mb = pool.get(host)
    mb.folder.set(folder)

    criteria = _build_search_criteria(
        from_addr=from_addr,
        to_addr=to_addr,
        subject=subject,
        body_text=body_text,
        since=since,
        before=before,
        seen=seen,
        larger_bytes=larger_bytes,
        smaller_bytes=smaller_bytes,
    )

    uids = list(mb.uids(criteria))
    # Newest-first by numeric UID. IMAP UIDs are monotonically assigned, so
    # higher = more recent within a folder (modulo UIDVALIDITY changes).
    uids.sort(key=int, reverse=True)
    total = len(uids)
    page = uids[offset : offset + limit]

    if not page:
        return {"total": total, "limit": limit, "offset": offset, "hits": []}

    fetched = list(
        mb.fetch(
            AND(uid=",".join(page)),
            headers_only=True,
            mark_seen=False,
            bulk=True,
        )
    )
    # Preserve the requested newest-first ordering even if the server returned
    # the hits in a different order.
    by_uid = {m.uid: m for m in fetched}
    hits = [_envelope_dict(by_uid[u]) for u in page if u in by_uid]

    return {"total": total, "limit": limit, "offset": offset, "hits": hits}


def _format_message(msg: Any, fields: str) -> dict[str, Any]:
    """Format a fetched message at the requested verbosity."""
    out = _envelope_dict(msg)
    if fields == "headers":
        return out
    # body + full
    out["body_text"] = msg.text or ""
    out["body_html"] = msg.html or ""
    if fields == "body":
        return out
    # full: include attachment metadata but never the payload
    out["attachments"] = [
        {
            "filename": att.filename,
            "content_type": att.content_type,
            "size": att.size,
        }
        for att in (msg.attachments or [])
    ]
    return out


@mcp.tool()
def fetch_message(
    host: str,
    folder: str,
    uid: str,
    fields: str = "headers",
) -> dict[str, Any]:
    """Fetch a single message by UID.

    Args:
        host: account nickname from config.
        folder: IMAP folder name.
        uid: the UID returned by search().
        fields: one of "headers" (envelope only — fastest), "body" (envelope
            + plain text + HTML body), or "full" (everything plus attachment
            filenames/types/sizes; attachment content is NOT included here).

    Returns:
        Message dict. Raises ValueError if `fields` is not one of the three
        allowed values. Returns {"uid": uid, "missing": true} if the UID is
        not present in the folder (e.g., already deleted).
    """
    if fields not in ("headers", "body", "full"):
        raise ValueError(f"fields must be 'headers', 'body', or 'full'; got {fields!r}")

    pool = _get_pool()
    mb = pool.get(host)
    mb.folder.set(folder)

    messages = list(
        mb.fetch(
            AND(uid=uid),
            headers_only=(fields == "headers"),
            mark_seen=False,
        )
    )
    if not messages:
        return {"uid": uid, "missing": True}
    return _format_message(messages[0], fields)


@mcp.tool()
def fetch_messages_bulk(
    host: str,
    folder: str,
    uids: list[str],
    fields: str = "headers",
) -> list[dict[str, Any]]:
    """Fetch many messages at once by UID. Capped at 100 per call.

    Useful after `search()` returns a list of UIDs and you want envelopes
    or bodies for all of them in one round-trip.

    Args:
        host: account nickname from config.
        folder: IMAP folder name.
        uids: list of UID strings (max 100). Order is preserved in the response.
        fields: same semantics as fetch_message ("headers" | "body" | "full").

    Returns:
        List of message dicts in the same order as the requested UIDs. UIDs
        that aren't present in the folder produce a {"uid": uid, "missing": true}
        entry rather than being silently dropped.
    """
    if not uids:
        return []
    if len(uids) > BULK_FETCH_LIMIT_MAX:
        raise ValueError(
            f"uids must contain at most {BULK_FETCH_LIMIT_MAX} entries; got {len(uids)}"
        )
    if fields not in ("headers", "body", "full"):
        raise ValueError(f"fields must be 'headers', 'body', or 'full'; got {fields!r}")

    pool = _get_pool()
    mb = pool.get(host)
    mb.folder.set(folder)

    fetched = list(
        mb.fetch(
            AND(uid=",".join(uids)),
            headers_only=(fields == "headers"),
            mark_seen=False,
            bulk=True,
        )
    )
    by_uid = {m.uid: m for m in fetched}
    return [
        _format_message(by_uid[u], fields) if u in by_uid else {"uid": u, "missing": True}
        for u in uids
    ]


def _current_flags(mb: Any, uid: str) -> list[str]:
    """Return the current flag list for one UID after a STORE operation.

    Falls back to an empty list if the UID can't be re-fetched (e.g., the
    operation triggered an expunge in the same call).
    """
    refetched = list(mb.fetch(AND(uid=uid), headers_only=True, mark_seen=False))
    return list(refetched[0].flags) if refetched else []


@mcp.tool()
def mark_read(host: str, folder: str, uid: str, read: bool = True) -> dict[str, Any]:
    """Add or remove the \\Seen flag on a single message.

    Args:
        host: account nickname from config.
        folder: IMAP folder name containing the message.
        uid: the UID of the message.
        read: true (default) sets \\Seen; false clears it.

    Returns:
        dict with `uid` and `flags` (the message's flag list after the
        operation). If the UID is not in the folder, returns
        {"uid": uid, "missing": true}.
    """
    pool = _get_pool()
    mb = pool.get(host)
    mb.folder.set(folder)

    existing = list(mb.fetch(AND(uid=uid), headers_only=True, mark_seen=False))
    if not existing:
        return {"uid": uid, "missing": True}

    mb.flag([uid], [MailMessageFlags.SEEN], read)
    return {"uid": uid, "flags": _current_flags(mb, uid)}


@mcp.tool()
def move_message(host: str, folder: str, uid: str, target_folder: str) -> dict[str, Any]:
    """Move a single message from `folder` to `target_folder`.

    Uses the IMAP MOVE extension when the server supports it (Yahoo does),
    falling back to COPY + STORE \\Deleted + EXPUNGE otherwise. The
    underlying `imap_tools.MailBox.move` handles both paths.

    Args:
        host: account nickname from config.
        folder: current IMAP folder of the message.
        uid: source UID.
        target_folder: destination IMAP folder. Must already exist.

    Returns:
        dict with `uid` (source), `target_folder`, and `moved: true`. The
        new UID at the destination is not always knowable from the server's
        response — callers that need it should `search` the target folder.
        If the UID is not in `folder`, returns {"uid": uid, "missing": true}.
    """
    pool = _get_pool()
    mb = pool.get(host)
    mb.folder.set(folder)

    existing = list(mb.fetch(AND(uid=uid), headers_only=True, mark_seen=False))
    if not existing:
        return {"uid": uid, "missing": True}

    mb.move([uid], target_folder)
    return {"uid": uid, "target_folder": target_folder, "moved": True}


@mcp.tool()
def delete_message(host: str, folder: str, uid: str, expunge: bool = True) -> dict[str, Any]:
    """Delete a single message.

    With `expunge=true` (default): flags \\Deleted and immediately expunges,
    so the message is gone from the folder. With `expunge=false`: only sets
    the \\Deleted flag; on most IMAP servers the message stays visible
    (struck-through in clients) until something else expunges.

    **Yahoo quirk:** Yahoo's IMAP auto-expunges on every `STORE \\Deleted`,
    so `expunge=false` is effectively a no-op against Yahoo — the message
    disappears either way. The returned `expunged` field reflects what *you*
    asked for, not what Yahoo actually did under the hood.

    Args:
        host: account nickname from config.
        folder: IMAP folder containing the message.
        uid: UID to delete.
        expunge: whether to expunge immediately. Defaults to true.

    Returns:
        dict with `uid`, `deleted: true`, and `expunged: bool`. If the UID
        is not in the folder, returns {"uid": uid, "missing": true}.
    """
    pool = _get_pool()
    mb = pool.get(host)
    mb.folder.set(folder)

    existing = list(mb.fetch(AND(uid=uid), headers_only=True, mark_seen=False))
    if not existing:
        return {"uid": uid, "missing": True}

    if expunge:
        mb.delete([uid])
    else:
        mb.flag([uid], [MailMessageFlags.DELETED], True)

    return {"uid": uid, "deleted": True, "expunged": bool(expunge)}


@mcp.tool()
def server_info() -> dict[str, str]:
    """Return server version and configuration locations for diagnostics."""
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
