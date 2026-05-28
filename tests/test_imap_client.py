"""Tests for the ConnectionPool. Uses a fake MailBox factory — no network."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yahoo_mail_mcp.config import load_config
from yahoo_mail_mcp.imap_client import EVICTION_SECONDS, ConnectionPool


def _config_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            [accounts.primary]
            address = "you@yahoo.com"
            password = "xxxxxxxxxxxxxxxx"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return p


class _FakeMailBox:
    """Stand-in for imap_tools.MailBox that records lifecycle calls."""

    def __init__(self, host, port=993):
        self.host = host
        self.port = port
        self.logged_in_as: str | None = None
        self.logged_out = False

    def login(self, address, password):
        self.logged_in_as = address
        self._password = password  # noqa: stored to assert later
        return self

    def logout(self):
        self.logged_out = True


def test_get_creates_and_logs_in(tmp_path):
    cfg = load_config(_config_file(tmp_path))
    factory = MagicMock(side_effect=_FakeMailBox)
    pool = ConnectionPool(cfg, mailbox_factory=factory)

    mb = pool.get("primary")

    assert isinstance(mb, _FakeMailBox)
    assert mb.host == "imap.mail.yahoo.com"
    assert mb.port == 993
    assert mb.logged_in_as == "you@yahoo.com"
    factory.assert_called_once()


def test_get_reuses_pooled_connection(tmp_path):
    cfg = load_config(_config_file(tmp_path))
    factory = MagicMock(side_effect=_FakeMailBox)
    pool = ConnectionPool(cfg, mailbox_factory=factory)

    first = pool.get("primary")
    second = pool.get("primary")

    assert first is second
    assert factory.call_count == 1


def test_get_evicts_after_idle_threshold(tmp_path, monkeypatch):
    cfg = load_config(_config_file(tmp_path))
    factory = MagicMock(side_effect=_FakeMailBox)
    pool = ConnectionPool(cfg, mailbox_factory=factory)

    # Fake the clock: first connect at t=0, second connect well past eviction.
    clock = [0.0]
    monkeypatch.setattr("yahoo_mail_mcp.imap_client.time.monotonic", lambda: clock[0])

    first = pool.get("primary")
    assert first.logged_out is False

    clock[0] = EVICTION_SECONDS + 1
    second = pool.get("primary")

    assert first is not second
    assert first.logged_out is True
    assert factory.call_count == 2


def test_close_drops_single_connection(tmp_path):
    cfg = load_config(_config_file(tmp_path))
    factory = MagicMock(side_effect=_FakeMailBox)
    pool = ConnectionPool(cfg, mailbox_factory=factory)

    mb = pool.get("primary")
    pool.close("primary")

    assert mb.logged_out is True
    # Next get() creates a fresh one
    pool.get("primary")
    assert factory.call_count == 2


def test_close_all_drops_every_connection(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            [accounts.primary]
            address = "you@yahoo.com"
            password = "p1"

            [accounts.secondary]
            address = "other@yahoo.com"
            password = "p2"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    factory = MagicMock(side_effect=_FakeMailBox)
    pool = ConnectionPool(cfg, mailbox_factory=factory)

    a = pool.get("primary")
    b = pool.get("secondary")
    pool.close_all()

    assert a.logged_out is True
    assert b.logged_out is True


def test_close_all_swallows_logout_exceptions(tmp_path):
    cfg = load_config(_config_file(tmp_path))

    class _BadLogout(_FakeMailBox):
        def logout(self):
            raise RuntimeError("socket already dead")

    pool = ConnectionPool(cfg, mailbox_factory=_BadLogout)
    pool.get("primary")
    # Must not raise even though logout() throws — pool should still drop the entry.
    pool.close_all()


def test_get_unknown_account_raises(tmp_path):
    cfg = load_config(_config_file(tmp_path))
    pool = ConnectionPool(cfg, mailbox_factory=MagicMock(side_effect=_FakeMailBox))

    with pytest.raises(KeyError):
        pool.get("nonexistent")


def test_get_missing_credential_raises(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            [accounts.primary]
            address = "you@yahoo.com"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    monkeypatch.delenv("YAHOO_MAIL_MCP_PRIMARY_PASSWORD", raising=False)
    pool = ConnectionPool(cfg, mailbox_factory=MagicMock(side_effect=_FakeMailBox))

    with pytest.raises(ValueError, match="no password"):
        pool.get("primary")
