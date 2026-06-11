from __future__ import annotations

from typer.testing import CliRunner

from tvcli import mcp
from tvcli.cli import app


class FakeFastMCP:
    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict[str, object] = {}
        self.ran = False

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator

    def run(self) -> None:
        self.ran = True


def test_build_server_registers_expected_tools(monkeypatch) -> None:
    monkeypatch.setattr(mcp, "FastMCP", FakeFastMCP)

    server = mcp.build_server()

    assert isinstance(server, FakeFastMCP)
    assert {"data_screen", "ta_get", "ta_matrix", "ohlcv_get", "chart_shot"} <= set(
        server.tools
    )


def test_mcp_serve_command_routes_to_runner(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_runner() -> None:
        captured["ran"] = True

    monkeypatch.setattr("tvcli.commands.mcp.run_mcp_server", fake_runner)

    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code == 0
    assert captured["ran"] is True
