"""Test na żywo z prawdziwym api.nbp.pl (pomijany domyślnie; uruchom: ``pytest -m live``)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_stdio_prawdziwe_nbp() -> None:
    parametry = StdioServerParameters(
        command=sys.executable, args=["-m", "serwer_mcp_nbp"], cwd=Path(__file__).parent
    )
    async with Client(parametry, mode="legacy") as c:
        kurs = await c.call_tool("kurs_nbp", {"waluta": "EUR", "data": "2026-06-12"})
        assert kurs.structured_content is not None
        assert kurs.structured_content["kurs"] == "4.2484"
        assert kurs.structured_content["numer_tabeli"] == "112/A/NBP/2026"
        dzis = await c.call_tool("kurs_nbp", {"waluta": "USD"})
        assert dzis.is_error is False, dzis.content
        assert dzis.structured_content is not None
        assert date.fromisoformat(dzis.structured_content["data_notowania"]) <= date.today()
        portfel = await c.call_tool(
            "wartosc_portfela",
            {"pozycje": [{"waluta": "EUR", "kwota": "1000"}, {"waluta": "JPY", "kwota": "100000"}]},
        )
        assert portfel.is_error is False, portfel.content
        print(kurs.structured_content, dzis.structured_content, portfel.structured_content, sep="\n")
