"""E2E test: server as a stdio subprocess, MCP SDK client: initialize -> list_tools -> call_tool."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

TESTS = Path(__file__).parent


pytestmark = pytest.mark.anyio


def params(script: list[str], env: dict[str, str] | None = None) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=script, env=env, cwd=TESTS)


async def test_stdio_initialize_list_call() -> None:
    async with Client(params([str(TESTS / "server_with_fake.py")]), mode="legacy") as c:
        assert c.server_info is not None
        assert c.server_info.name == "nbp"
        names = {t.name for t in (await c.list_tools()).tools}
        assert names == {"get_nbp_rate", "convert_to_pln", "portfolio_value"}
        result = await c.call_tool("get_nbp_rate", {"currency": "EUR", "date": "2026-06-14"})
        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["rate"] == "4.2484"
        assert result.structured_content["quote_date"] == "2026-06-12"
        portfolio = await c.call_tool(
            "portfolio_value",
            {
                "date": "2026-06-12",
                "positions": [{"currency": "EUR", "amount": "1000"}, {"currency": "CHF", "amount": "10"}],
            },
        )
        assert portfolio.is_error is False
        failed = await c.call_tool("get_nbp_rate", {"currency": "EUR", "date": "2999-01-01"})
        assert failed.is_error is True


@pytest.mark.parametrize("mode", ["auto"])
async def test_stdio_auto_mode(mode: str) -> None:
    async with Client(params([str(TESTS / "server_with_fake.py")]), mode=mode) as c:
        assert len((await c.list_tools()).tools) == 3


def test_stdio_refuses_to_start_with_model_key() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "nbp_mcp_server"],
        env={"ANTHROPIC_API_KEY": "sk-test", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 3
    assert result.stdout == "", "stdout is the protocol channel - message on stderr only"
    assert "ANTHROPIC_API_KEY" in result.stderr
    assert "sk-test" not in result.stderr
