"""IMAP connection management for yahoo-mail-mcp.

A small per-process connection pool keyed by account name. Connections are
created lazily on first use and proactively evicted after 8 minutes of
inactivity — under Yahoo's ~10-minute idle-timeout, so the next operation
never races against a half-dead socket.

The pool is intentionally simple (synchronous, single-process, no IDLE).
Multi-account use is supported by having one pooled connection per account
name.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING

from yahoo_mail_mcp.config import Config

if TYPE_CHECKING:
    from imap_tools import MailBox


# Yahoo evicts idle IMAP connections at ~10 minutes. We proactively close
# at 8 to avoid handing the caller a half-dead socket.
EVICTION_SECONDS = 8 * 60


@dataclass
class _PooledConnection:
    mailbox: MailBox
    last_used: float  # caller sets via time.monotonic() so tests can mock the clock


class ConnectionPool:
    """One logged-in MailBox per account, with idle-timeout eviction.

    Construction takes a `Config`; mailboxes are created on first `get()`.
    Always close with `close_all()` on shutdown.
    """

    def __init__(self, config: Config, *, mailbox_factory=None):
        """Build the pool.

        Args:
            config: loaded Config with at least one account.
            mailbox_factory: optional callable (host, port) -> MailBox-like
                object. Defaults to imap_tools.MailBox. Tests inject a fake
                here to avoid touching the network.
        """
        self._config = config
        self._conns: dict[str, _PooledConnection] = {}
        self._lock = Lock()
        if mailbox_factory is None:
            from imap_tools import MailBox as _MailBox

            self._mailbox_factory = _MailBox
        else:
            self._mailbox_factory = mailbox_factory

    def get(self, account: str) -> MailBox:
        """Return a live, logged-in MailBox for `account`.

        Reuses the pooled connection if fresh; reconnects if past the idle
        eviction threshold or never connected.

        Raises:
            KeyError: account not in config.
            ValueError: no credential available for the account.
            Any IMAP-level errors propagate from imap_tools.
        """
        with self._lock:
            now = time.monotonic()
            existing = self._conns.get(account)
            if existing is not None:
                if now - existing.last_used > EVICTION_SECONDS:
                    self._close_locked(account)
                else:
                    existing.last_used = now
                    return existing.mailbox
            return self._create_locked(account)

    def close(self, account: str) -> None:
        """Log out + drop a single account's connection if it exists."""
        with self._lock:
            self._close_locked(account)

    def close_all(self) -> None:
        """Log out + drop every pooled connection. Safe to call on shutdown."""
        with self._lock:
            for name in list(self._conns):
                self._close_locked(name)

    # ----- internals (caller must hold self._lock) ---------------------------

    def _create_locked(self, account: str):
        host, port, _use_tls = self._config.effective_host(account)
        password = self._config.resolve_password(account)
        address = self._config.accounts[account].address
        mb = self._mailbox_factory(host, port=port)
        mb.login(address, password)
        self._conns[account] = _PooledConnection(mailbox=mb, last_used=time.monotonic())
        return mb

    def _close_locked(self, account: str) -> None:
        conn = self._conns.pop(account, None)
        if conn is None:
            return
        # Best-effort: a dead socket on logout is not a caller-visible problem.
        # The pool entry is dropped either way.
        with contextlib.suppress(Exception):
            conn.mailbox.logout()
