"""Testy kontraktowe MCP w pamięci procesu (KA-08.1–KA-08.4, KA-05.4, NFR-05, NFR-08)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from atrapa_nbp import AtrapaNBP
from mcp import Client
from mcp_types import CallToolResult, TextContent

from serwer_mcp_nbp import nbp
from serwer_mcp_nbp.serwer import NAZWY_NARZEDZI, sprawdz_srodowisko, zbuduj_serwer


@pytest.fixture
def atrapa() -> AtrapaNBP:
    return AtrapaNBP()


@pytest.fixture
async def klient_mcp(atrapa: AtrapaNBP) -> AsyncIterator[Client]:
    serwer = zbuduj_serwer(nbp.KlientNBP(nbp.utworz_klienta_http(atrapa.transport())))
    async with Client(serwer) as c:
        yield c


pytestmark = pytest.mark.anyio


def tekst(wynik: CallToolResult) -> str:
    blok = wynik.content[0]
    assert isinstance(blok, TextContent)
    return blok.text


def blad(wynik: CallToolResult) -> dict[str, Any]:
    assert wynik.is_error is True
    assert wynik.structured_content is None
    # SDK poprzedza komunikat prefiksem "Error executing tool <nazwa>: ".
    dane: dict[str, Any] = json.loads(tekst(wynik).partition(": ")[2])
    assert set(dane) == {"blad", "powod"}
    assert all(isinstance(v, str) for v in dane.values()), "KA-08.4: żadnego pola liczbowego"
    return dane


async def test_lista_narzedzi_dokladnie_odczytowa(klient_mcp: Client) -> None:
    narzedzia = (await klient_mcp.list_tools()).tools
    assert (
        {n.name for n in narzedzia} == NAZWY_NARZEDZI == {"kurs_nbp", "przelicz_na_pln", "wartosc_portfela"}
    )
    for n in narzedzia:
        assert n.annotations is not None
        assert n.annotations.read_only_hint is True
        assert n.annotations.destructive_hint is False
        assert n.output_schema is not None


async def test_kurs_nbp_ksztalt_wyniku(klient_mcp: Client) -> None:
    wynik = await klient_mcp.call_tool("kurs_nbp", {"waluta": "EUR", "data": "2026-06-12"})
    assert wynik.is_error is False
    assert wynik.structured_content == {
        "waluta": "EUR",
        "kurs": "4.2484",
        "data_zadana": "2026-06-12",
        "data_notowania": "2026-06-12",
        "forward_fill": False,
        "numer_tabeli": "112/A/NBP/2026",
        "zrodlo": "NBP, tabela A kursów średnich (api.nbp.pl)",
        "dane_aktualne": True,
    }


async def test_kurs_nbp_forward_fill_w_swieto(klient_mcp: Client) -> None:
    wynik = await klient_mcp.call_tool("kurs_nbp", {"waluta": "usd", "data": "2026-06-04"})
    assert wynik.structured_content is not None
    assert wynik.structured_content["data_notowania"] == "2026-06-03"
    assert wynik.structured_content["forward_fill"] is True


@pytest.mark.parametrize(
    ("argumenty", "kod"),
    [
        ({"waluta": "BTC", "data": "2026-06-12"}, "nieprawidlowa_waluta"),
        ({"waluta": "EUR", "data": "2999-01-01"}, "nieprawidlowa_data"),
        ({"waluta": "EUR", "data": "12/06/2026"}, "nieprawidlowa_data"),
    ],
)
async def test_walidacja_przed_zapytaniem(
    klient_mcp: Client, atrapa: AtrapaNBP, argumenty: dict[str, Any], kod: str
) -> None:
    assert blad(await klient_mcp.call_tool("kurs_nbp", argumenty))["blad"] == kod
    assert atrapa.zadania == [], "KA-08.3: walidacja przed żądaniem do NBP"


async def test_brak_kursu_to_blad_nie_liczba(klient_mcp: Client, atrapa: AtrapaNBP) -> None:
    atrapa.tryb = "awaria"
    assert blad(await klient_mcp.call_tool("kurs_nbp", {"waluta": "EUR", "data": "2026-06-12"}))["blad"] == (
        "nbp_niedostepne"
    )


async def test_przelicz_na_pln(klient_mcp: Client) -> None:
    wynik = await klient_mcp.call_tool(
        "przelicz_na_pln", {"kwota": "1250.50", "waluta": "EUR", "data": "2026-06-13"}
    )
    assert wynik.structured_content is not None
    assert wynik.structured_content["kwota_pln"] == "5312.62"  # 1250.50 * 4.2484 = 5312.6242
    assert wynik.structured_content["kurs"]["data_notowania"] == "2026-06-12"


async def test_przelicz_zla_kwota(klient_mcp: Client) -> None:
    wynik = await klient_mcp.call_tool(
        "przelicz_na_pln", {"kwota": "1e10", "waluta": "EUR", "data": "2026-06-12"}
    )
    assert blad(wynik)["blad"] == "nieprawidlowa_kwota"


async def test_wartosc_portfela_jedno_zapytanie(klient_mcp: Client, atrapa: AtrapaNBP) -> None:
    pozycje = [
        {"waluta": "EUR", "kwota": "1000.00"},
        {"waluta": "USD", "kwota": "2500"},
        {"waluta": "PLN", "kwota": "100.10"},
    ]
    wynik = await klient_mcp.call_tool("wartosc_portfela", {"data": "2026-06-12", "pozycje": pozycje})
    dane = wynik.structured_content
    assert dane is not None
    sumy = [p["kwota_pln"] for p in dane["pozycje"]]
    assert dane["suma_pln"] == str(sum(map(nbp.waliduj_kwote, sumy)))
    assert sumy[0] == "4248.40"
    assert sumy[2] == "100.10"
    assert len(atrapa.zadania) == 1, "OG-09: jedno zapytanie wsadowe na wszystkie waluty"


async def test_wartosc_portfela_bez_sumy_czesciowej(klient_mcp: Client) -> None:
    pozycje = [{"waluta": "EUR", "kwota": "1"}, {"waluta": "THB", "kwota": "1"}]
    wynik = await klient_mcp.call_tool("wartosc_portfela", {"data": "2026-06-12", "pozycje": pozycje})
    assert blad(wynik)["blad"] == "brak_notowania"


@pytest.mark.parametrize(
    "pozycje",
    [[], [{"waluta": "EUR", "kwota": "1"}] * 51, [{"waluta": "EUR", "kwota": "1", "ukryte": "x"}]],
    ids=["pusty", "za-dlugi", "dodatkowe-pole"],
)
async def test_wartosc_portfela_walidacja_schematu(klient_mcp: Client, pozycje: list[dict[str, str]]) -> None:
    wynik = await klient_mcp.call_tool("wartosc_portfela", {"pozycje": pozycje})
    assert wynik.is_error is True
    assert "Traceback" not in tekst(wynik)


async def test_nieznane_narzedzie_zapisujace(klient_mcp: Client) -> None:
    wynik = await klient_mcp.call_tool("utworz_transfer", {"kwota": "1"})
    assert wynik.is_error is True


@pytest.mark.parametrize("zmienna", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY"])
def test_odmowa_startu_z_kluczem_modelu(zmienna: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        sprawdz_srodowisko({zmienna: "sk-tajne-123"})
    assert e.value.code == 3
    err = capsys.readouterr().err
    assert zmienna in err
    assert "sk-tajne-123" not in err


def test_pusty_klucz_nie_blokuje() -> None:
    sprawdz_srodowisko({"ANTHROPIC_API_KEY": "", "PATH": "/usr/bin"})
