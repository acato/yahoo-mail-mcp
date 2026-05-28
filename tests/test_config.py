"""Tests for the config loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from yahoo_mail_mcp.config import Config, load_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return p


def test_load_missing_returns_empty():
    cfg = load_config(Path("/no/such/file/that/exists.toml"))
    assert isinstance(cfg, Config)
    assert cfg.accounts == {}


def test_load_minimal(tmp_path):
    p = _write(
        tmp_path,
        """
        [accounts.primary]
        address = "you@yahoo.com"
        password = "xxxxxxxxxxxxxxxx"
        """,
    )
    cfg = load_config(p)
    assert "primary" in cfg.accounts
    assert cfg.accounts["primary"].address == "you@yahoo.com"
    assert cfg.defaults.host == "imap.mail.yahoo.com"
    assert cfg.defaults.port == 993


def test_address_validation(tmp_path):
    p = _write(
        tmp_path,
        """
        [accounts.bad]
        address = "not-an-email"
        """,
    )
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        load_config(p)


def test_resolve_password_env_wins_over_file(tmp_path, monkeypatch):
    p = _write(
        tmp_path,
        """
        [accounts.primary]
        address = "you@yahoo.com"
        password = "from_file"
        """,
    )
    cfg = load_config(p)
    monkeypatch.setenv("YAHOO_MAIL_MCP_PRIMARY_PASSWORD", "from_env")
    assert cfg.resolve_password("primary") == "from_env"


def test_resolve_password_file_fallback(tmp_path, monkeypatch):
    p = _write(
        tmp_path,
        """
        [accounts.primary]
        address = "you@yahoo.com"
        password = "from_file"
        """,
    )
    cfg = load_config(p)
    monkeypatch.delenv("YAHOO_MAIL_MCP_PRIMARY_PASSWORD", raising=False)
    assert cfg.resolve_password("primary") == "from_file"


def test_resolve_password_missing(tmp_path, monkeypatch):
    p = _write(
        tmp_path,
        """
        [accounts.primary]
        address = "you@yahoo.com"
        """,
    )
    cfg = load_config(p)
    monkeypatch.delenv("YAHOO_MAIL_MCP_PRIMARY_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="no password"):
        cfg.resolve_password("primary")


def test_resolve_password_unknown_account(tmp_path):
    p = _write(
        tmp_path,
        """
        [accounts.primary]
        address = "you@yahoo.com"
        password = "x"
        """,
    )
    cfg = load_config(p)
    with pytest.raises(KeyError):
        cfg.resolve_password("nonexistent")


def test_effective_host_account_overrides_defaults(tmp_path):
    p = _write(
        tmp_path,
        """
        [defaults]
        port = 143

        [accounts.primary]
        address = "you@yahoo.com"
        password = "x"
        port = 993
        use_tls = false
        """,
    )
    cfg = load_config(p)
    host, port, tls = cfg.effective_host("primary")
    assert host == "imap.mail.yahoo.com"
    assert port == 993
    assert tls is False


def test_account_name_with_dash_env_var(tmp_path, monkeypatch):
    p = _write(
        tmp_path,
        """
        [accounts.work-mail]
        address = "you@yahoo.com"
        """,
    )
    cfg = load_config(p)
    monkeypatch.setenv("YAHOO_MAIL_MCP_WORK_MAIL_PASSWORD", "from_env")
    assert cfg.resolve_password("work-mail") == "from_env"
