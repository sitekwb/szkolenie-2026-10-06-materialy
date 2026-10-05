"""Test e2e: serwer jako podproces stdio, klient MCP SDK: initialize -> list_tools -> call_tool."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

TESTY = Path(__file__).parent


pytestmark = pytest.mark.anyio


def parametry(skrypt: list[str], env: dict[str, str] | None = None) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=skrypt, env=env, cwd=TESTY)


async def test_stdio_initialize_list_call() -> None:
    async with Client(parametry([str(TESTY / "serwer_z_atrapa.py")]), mode="legacy") as c:
        assert c.server_info is not None
        assert c.server_info.name == "nbp"
        nazwy = {n.name for n in (await c.list_tools()).tools}
        assert nazwy == {"kurs_nbp", "przelicz_na_pln", "wartosc_portfela"}
        wynik = await c.call_tool("kurs_nbp", {"waluta": "EUR", "data": "2026-06-14"})
        assert wynik.is_error is False
        assert wynik.structured_content is not None
        assert wynik.structured_content["kurs"] == "4.2484"
        assert wynik.structured_content["data_notowania"] == "2026-06-12"
        portfel = await c.call_tool(
            "wartosc_portfela",
            {
                "data": "2026-06-12",
                "pozycje": [{"waluta": "EUR", "kwota": "1000"}, {"waluta": "CHF", "kwota": "10"}],
            },
        )
        assert portfel.is_error is False
        bledny = await c.call_tool("kurs_nbp", {"waluta": "EUR", "data": "2999-01-01"})
        assert bledny.is_error is True


@pytest.mark.parametrize("tryb", ["auto"])
async def test_stdio_tryb_auto(tryb: str) -> None:
    async with Client(parametry([str(TESTY / "serwer_z_atrapa.py")]), mode=tryb) as c:
        assert len((await c.list_tools()).tools) == 3


def test_stdio_odmowa_startu_z_kluczem_modelu() -> None:
    wynik = subprocess.run(
        [sys.executable, "-m", "serwer_mcp_nbp"],
        env={"ANTHROPIC_API_KEY": "sk-test", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert wynik.returncode == 3
    assert wynik.stdout == "", "stdout to kanał protokołu - komunikat tylko na stderr"
    assert "ANTHROPIC_API_KEY" in wynik.stderr
    assert "sk-test" not in wynik.stderr
