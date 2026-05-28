"""Configuration loading for yahoo-mail-mcp.

Reads ~/.config/yahoo-mail-mcp/config.toml (or YAHOO_MAIL_MCP_CONFIG override),
merges environment-variable credentials, validates with pydantic.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "yahoo-mail-mcp" / "config.toml"
ENV_CONFIG_PATH = "YAHOO_MAIL_MCP_CONFIG"


class Defaults(BaseModel):
    host: str = "imap.mail.yahoo.com"
    port: int = 993
    use_tls: bool = True
    connect_timeout: int = 10
    read_timeout: int = 30


class Account(BaseModel):
    address: str
    password: str | None = None
    host: str | None = None
    port: int | None = None
    use_tls: bool | None = None

    @field_validator("address")
    @classmethod
    def _address_has_at(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError(f"address must contain '@': {v!r}")
        return v


class Config(BaseModel):
    defaults: Defaults = Field(default_factory=Defaults)
    accounts: dict[str, Account] = Field(default_factory=dict)

    def resolve_password(self, account_name: str) -> str:
        """Return the app password for an account, env-var first then file.

        Raises:
            KeyError: if the account isn't configured at all.
            ValueError: if no password is available from any source.
        """
        if account_name not in self.accounts:
            raise KeyError(f"unknown account: {account_name!r}")
        env_var = f"YAHOO_MAIL_MCP_{account_name.upper().replace('-', '_')}_PASSWORD"
        if os.environ.get(env_var):
            return os.environ[env_var]
        pw = self.accounts[account_name].password
        if pw:
            return pw
        raise ValueError(
            f"no password for account {account_name!r}: "
            f"set {env_var} or accounts.{account_name}.password in config"
        )

    def effective_host(self, account_name: str) -> tuple[str, int, bool]:
        """Return (host, port, use_tls) for an account, account fields override defaults."""
        acct = self.accounts[account_name]
        return (
            acct.host or self.defaults.host,
            acct.port or self.defaults.port,
            self.defaults.use_tls if acct.use_tls is None else acct.use_tls,
        )


def config_path() -> Path:
    """Return the active config path (env-var override or default)."""
    override = os.environ.get(ENV_CONFIG_PATH)
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> Config:
    """Load and validate config from disk. Returns empty-accounts Config if file missing."""
    target = path or config_path()
    if not target.exists():
        return Config()
    with target.open("rb") as fh:
        raw = tomllib.load(fh)
    return Config(**raw)
