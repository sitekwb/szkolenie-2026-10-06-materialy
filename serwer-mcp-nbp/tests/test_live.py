"""Live test against the real api.nbp.pl (skipped by default; run with ``pytest -m live``)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_stdio_real_nbp() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "nbp_mcp_server"], cwd=Path(__file__).parent
    )
    async with Client(params, mode="legacy") as c:
        rate = await c.call_tool("get_nbp_rate", {"currency": "EUR", "date": "2026-06-12"})
        assert rate.structured_content is not None
        assert rate.structured_content["rate"] == "4.2484"
        assert rate.structured_content["table_number"] == "112/A/NBP/2026"
        today = await c.call_tool("get_nbp_rate", {"currency": "USD"})
        assert today.is_error is False, today.content
        assert today.structured_content is not None
        assert date.fromisoformat(today.structured_content["quote_date"]) <= date.today()
        portfolio = await c.call_tool(
            "portfolio_value",
            {"positions": [{"currency": "EUR", "amount": "1000"}, {"currency": "JPY", "amount": "100000"}]},
        )
        assert portfolio.is_error is False, portfolio.content
        print(rate.structured_content, today.structured_content, portfolio.structured_content, sep="\n")
