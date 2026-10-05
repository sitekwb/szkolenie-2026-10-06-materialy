"""Test przeglądowy powierzchni narzędzi (REQ-29).

Przypinamy SHA-256 kanonicznego JSON-a tego, co agent faktycznie dostaje z ``tools/list`` (nazwy,
tytuły, opisy, ``inputSchema``, ``outputSchema``, adnotacje) oraz instrukcji serwera. Zmiana opisu,
pola, limitu albo typu w ``serwer.py`` lub ``opisy.py`` bez podbicia ``WERSJA_OPISOW`` i dopisania
nowego skrótu po przeglądzie oblewa test.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from mcp import Client

from serwer_mcp_nbp import nbp, opisy
from serwer_mcp_nbp.serwer import zbuduj_serwer

pytestmark = pytest.mark.anyio

PRZEJRZANE_SKROTY: dict[str, str] = {
    "1.1.0": "0923b3c23014bb4a36c9978686161702d6f092509bbac9fb7284f217758d9bc6",
}
"""Wersja opisów -> SHA-256 manifestu narzędzi zatwierdzonego w przeglądzie kodu. Dopisuj, nie nadpisuj."""


async def manifest() -> dict[str, Any]:
    """Zwraca kanoniczny manifest narzędzi i instrukcji, tak jak widzi go klient MCP."""
    serwer = zbuduj_serwer(nbp.KlientNBP(nbp.utworz_klienta_http()))
    async with Client(serwer) as c:
        narzedzia = (await c.list_tools()).tools
        instrukcje = c.instructions
    return {
        "instrukcje": instrukcje,
        "narzedzia": sorted(
            (n.model_dump(mode="json", by_alias=True, exclude_none=True) for n in narzedzia),
            key=lambda n: str(n["name"]),
        ),
    }


def skrot(dane: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dane, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


async def test_manifest_narzedzi_przejrzany_dla_biezacej_wersji() -> None:
    dane = await manifest()
    assert opisy.WERSJA_OPISOW in PRZEJRZANE_SKROTY, "Nowa wersja opisów bez wpisu po przeglądzie"
    assert skrot(dane) == PRZEJRZANE_SKROTY[opisy.WERSJA_OPISOW], (
        "Opisy lub schematy narzędzi zmieniły się bez podbicia WERSJA_OPISOW i przeglądu (REQ-29): "
        + skrot(dane)
    )


async def test_manifest_zawiera_schematy_i_instrukcje() -> None:
    dane = await manifest()
    assert dane["instrukcje"] == opisy.INSTRUKCJE_SERWERA
    portfel = next(n for n in dane["narzedzia"] if n["name"] == "wartosc_portfela")
    assert "inputSchema" in portfel
    assert "Dodatnia kwota" in json.dumps(portfel["inputSchema"], ensure_ascii=False)


async def test_zmiana_opisu_pola_zmienia_skrot() -> None:
    dane = await manifest()
    zmienione = json.loads(json.dumps(dane).replace("Dodatnia kwota", "Dowolna kwota"))
    assert skrot(zmienione) != skrot(dane)
