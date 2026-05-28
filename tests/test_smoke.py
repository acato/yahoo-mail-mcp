"""Smoke tests: package imports + MCP server boots."""

from __future__ import annotations


def test_package_imports():
    import yahoo_mail_mcp

    assert hasattr(yahoo_mail_mcp, "__version__")


def test_server_module_imports():
    from yahoo_mail_mcp import server

    assert hasattr(server, "main")
    assert hasattr(server, "mcp")


def test_server_info_tool_runs(tmp_path, monkeypatch):
    """The server_info tool should return without needing a config file."""
    monkeypatch.setenv("YAHOO_MAIL_MCP_CONFIG", str(tmp_path / "nope.toml"))
    from yahoo_mail_mcp.server import server_info

    info = server_info()  # FastMCP returns the original callable from @mcp.tool()
    assert "version" in info
    assert "config_path" in info
    assert info["config_exists"] == "False"
