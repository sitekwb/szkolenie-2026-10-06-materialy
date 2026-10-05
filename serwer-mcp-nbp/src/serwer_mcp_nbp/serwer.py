"""Serwer MCP (transport stdio) z trzema narzędziami odczytowymi: kurs, przeliczenie, portfel.

stdout jest kanałem protokołu JSON-RPC, więc wszystkie logi idą na stderr. Serwer nie ma
narzędzi zmieniających stan (OG-15, ADR-04, NFR-05), nie zapisuje niczego na dysk i odmawia
startu, gdy w jego środowisku jest klucz usługi modelu (OG-04, ADR-06).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Annotated, Final

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import __version__, opisy
from .nbp import (
    MAKS_POZYCJI_PORTFELA,
    BladKursu,
    KlientNBP,
    KodBledu,
    WynikKursu,
    na_grosze,
    utworz_klienta_http,
    waliduj_date,
    waliduj_kwote,
    waliduj_walute,
)

log = logging.getLogger("serwer_mcp_nbp")

NAZWY_NARZEDZI: Final = frozenset({"kurs_nbp", "przelicz_na_pln", "wartosc_portfela"})
ZMIENNE_KLUCZA_MODELU: Final = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
)
"""Zmienne, których niepusta wartość blokuje start (ADR-06: serwer MCP nie ma klucza modelu)."""
ZRODLO: Final = "NBP, tabela A kursów średnich (api.nbp.pl)"
_ODCZYT: Final = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)


class Kurs(BaseModel):
    """Wynik ``kurs_nbp`` (KA-08.2): kurs, kod, data żądana, faktyczna data notowania, źródło."""

    model_config = ConfigDict(frozen=True)
    waluta: str
    kurs: str = Field(description="Kurs średni PLN za 1 jednostkę waluty, tekst dziesiętny.")
    data_zadana: str
    data_notowania: str = Field(description="Faktyczna data notowania (forward-fill, FR-03).")
    forward_fill: bool
    numer_tabeli: str
    zrodlo: str
    dane_aktualne: bool = Field(description="False: NBP niedostępne, użyto starszego wpisu z cache.")

    @classmethod
    def z_wyniku(cls, w: WynikKursu) -> Kurs:
        """Buduje model odpowiedzi z wyniku klienta NBP."""
        return cls(
            waluta=w.waluta,
            kurs=str(w.kurs),
            data_zadana=w.data_zadana.isoformat(),
            data_notowania=w.data_notowania.isoformat(),
            forward_fill=w.data_notowania != w.data_zadana,
            numer_tabeli=w.numer_tabeli,
            zrodlo=ZRODLO,
            dane_aktualne=w.dane_aktualne,
        )


class Przeliczenie(BaseModel):
    """Wynik ``przelicz_na_pln``."""

    model_config = ConfigDict(frozen=True)
    kwota: str
    kwota_pln: str = Field(description="Kwota w PLN, ROUND_HALF_UP do grosza.")
    kurs: Kurs


class Pozycja(BaseModel):
    """Pozycja portfela podana przez wywołującego (dane syntetyczne)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    waluta: Annotated[str, Field(min_length=3, max_length=3, description="Kod ISO 4217, np. EUR.")]
    kwota: Annotated[
        str, Field(max_length=24, description="Dodatnia kwota dziesiętna jako tekst, np. '1000.00'.")
    ]


class WycenaPozycji(BaseModel):
    """Wycena jednej pozycji portfela."""

    model_config = ConfigDict(frozen=True)
    waluta: str
    kwota: str
    kwota_pln: str
    kurs: str
    data_notowania: str


class WartoscPortfela(BaseModel):
    """Wynik ``wartosc_portfela``."""

    model_config = ConfigDict(frozen=True)
    data_zadana: str
    suma_pln: str
    pozycje: list[WycenaPozycji]
    zrodlo: str
    dane_aktualne: bool


def _blad_narzedzia(blad: BladKursu) -> ToolError:
    """Zamienia błąd dziedzinowy na błąd narzędzia MCP (``isError: true``) bez stack trace'u."""
    return ToolError(blad.jako_json())


def zbuduj_serwer(klient: KlientNBP) -> MCPServer:
    """Rejestruje trzy narzędzia odczytowe na podanym kliencie NBP i zwraca serwer MCP."""
    serwer = MCPServer(
        name="nbp",
        title="Kursy średnie NBP (tabela A)",
        instructions=opisy.INSTRUKCJE_SERWERA,
        version=__version__,
        log_level="WARNING",
    )

    @serwer.tool(name="kurs_nbp", description=opisy.KURS_NBP, annotations=_ODCZYT)
    async def kurs_nbp(waluta: str, data: str | None = None) -> Kurs:
        try:
            return Kurs.z_wyniku(await klient.kurs(waliduj_walute(waluta), waliduj_date(data)))
        except BladKursu as blad:
            raise _blad_narzedzia(blad) from None

    @serwer.tool(name="przelicz_na_pln", description=opisy.PRZELICZ_NA_PLN, annotations=_ODCZYT)
    async def przelicz_na_pln(kwota: str, waluta: str, data: str | None = None) -> Przeliczenie:
        try:
            wartosc = waliduj_kwote(kwota)
            wynik = await klient.kurs(waliduj_walute(waluta), waliduj_date(data))
        except BladKursu as blad:
            raise _blad_narzedzia(blad) from None
        return Przeliczenie(
            kwota=str(wartosc), kwota_pln=str(na_grosze(wartosc * wynik.kurs)), kurs=Kurs.z_wyniku(wynik)
        )

    @serwer.tool(name="wartosc_portfela", description=opisy.WARTOSC_PORTFELA, annotations=_ODCZYT)
    async def wartosc_portfela(
        pozycje: Annotated[list[Pozycja], Field(min_length=1)],
        data: str | None = None,
    ) -> WartoscPortfela:
        try:
            if len(pozycje) > MAKS_POZYCJI_PORTFELA:
                raise BladKursu(
                    KodBledu.ZA_DUZO_POZYCJI,
                    f"Portfel ma {len(pozycje)} pozycji; limit to {MAKS_POZYCJI_PORTFELA}.",
                )
            dzien = waliduj_date(data)
            wyceny: list[WycenaPozycji] = []
            suma = Decimal("0.00")
            aktualne = True
            for poz in pozycje:
                kwota = waliduj_kwote(poz.kwota)
                w = await klient.kurs(waliduj_walute(poz.waluta), dzien)
                aktualne &= w.dane_aktualne
                kwota_pln = na_grosze(kwota * w.kurs)
                suma += kwota_pln
                wyceny.append(
                    WycenaPozycji(
                        waluta=w.waluta,
                        kwota=str(kwota),
                        kwota_pln=str(kwota_pln),
                        kurs=str(w.kurs),
                        data_notowania=w.data_notowania.isoformat(),
                    )
                )
        except BladKursu as blad:
            raise _blad_narzedzia(blad) from None
        return WartoscPortfela(
            data_zadana=dzien.isoformat(),
            suma_pln=str(suma),
            pozycje=wyceny,
            zrodlo=ZRODLO,
            dane_aktualne=aktualne,
        )

    return serwer


def sprawdz_srodowisko(env: Mapping[str, str]) -> None:
    """Odmawia startu, gdy w środowisku jest niepusty klucz usługi modelu (ADR-06, OG-04).

    Raises:
        SystemExit: Kod 3 z komunikatem na stderr (nazwa zmiennej, nigdy jej wartość).

    """
    if znalezione := [n for n in ZMIENNE_KLUCZA_MODELU if env.get(n, "").strip()]:
        print(
            "serwer-mcp-nbp: odmowa startu - w środowisku jest klucz usługi modelu: "
            f"{', '.join(znalezione)}. Serwer MCP nie może mieć tego klucza (ADR-06). "
            "Usuń zmienną albo nadpisz ją pustą wartością (nazwa serwera przed -e): "
            "claude mcp add nbp --transport stdio --scope user -e NAZWA= -- <ścieżka>",
            file=sys.stderr,
        )
        raise SystemExit(3)


async def _sprawdz_na_zywo(waluta: str) -> int:
    """Jedno zapytanie do prawdziwego NBP; wynik JSON na stdout (tryb diagnostyczny, nie MCP)."""
    klient = KlientNBP(utworz_klienta_http())
    try:
        wynik = Kurs.z_wyniku(await klient.kurs(waliduj_walute(waluta), waliduj_date(None)))
    except BladKursu as blad:
        print(blad.jako_json(), file=sys.stderr)
        return 1
    finally:
        await klient.zamknij()
    print(wynik.model_dump_json(indent=2))
    return 0


def uruchom(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Uruchamia serwer w transporcie stdio; ``transport`` podmieniają wyłącznie testy e2e."""
    klient = KlientNBP(utworz_klienta_http(transport))
    zbuduj_serwer(klient).run("stdio")


def main(argv: Sequence[str] | None = None) -> int:
    """Punkt wejścia ``serwer-mcp-nbp``."""
    parser = argparse.ArgumentParser(
        prog="serwer-mcp-nbp", description="Serwer MCP (stdio) z kursami średnimi NBP, tabela A."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--sprawdz",
        metavar="WALUTA",
        help="zamiast serwera MCP: jedno zapytanie do api.nbp.pl o dzisiejszy kurs i wynik na stdout",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr, level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    sprawdz_srodowisko(os.environ)
    if args.sprawdz:
        try:
            return asyncio.run(_sprawdz_na_zywo(args.sprawdz))
        except BladKursu as blad:
            print(blad.jako_json(), file=sys.stderr)
            return 2
    uruchom()
    return 0


__all__ = ["NAZWY_NARZEDZI", "KodBledu", "main", "sprawdz_srodowisko", "zbuduj_serwer"]
