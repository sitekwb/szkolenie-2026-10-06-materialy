"""Klient API NBP (tabela A): walidacja wejścia, cache w pamięci, forward-fill.

Granica zaufania (OG-22): wszystko, co przychodzi z ``api.nbp.pl``, jest **niezaufane** —
odpowiedź przechodzi limit rozmiaru, parsowanie liczb jako ``Decimal`` i walidację kształtu
modelami Pydantic, zanim zostanie użyta. Klient zna wyłącznie metodę ``GET`` i wyłącznie host
``api.nbp.pl`` po HTTPS (OG-07, OG-12, FR-26).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Final, Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

log = logging.getLogger(__name__)

HOST_NBP: Final = "api.nbp.pl"
BAZOWY_URL: Final = f"https://{HOST_NBP}/api/"
STREFA_NBP: Final = ZoneInfo("Europe/Warsaw")
NAJWCZESNIEJSZA_DATA: Final = date(2002, 1, 2)
"""Najstarsze dane kursowe w API NBP (dokumentacja api.nbp.pl)."""
LIMIT_DNI_ZAPYTANIA: Final = 93
"""Pojedyncze zapytanie o serię nie może obejmować więcej niż 93 dni (dokumentacja api.nbp.pl)."""
OKNO_FORWARD_FILL_DNI: Final = 14
"""Ile dni wstecz szukać ostatniego notowania; najdłuższe przerwy świąteczne NBP są krótsze."""
MAKS_ROZMIAR_ODPOWIEDZI: Final = 1_000_000
LIMIT_CZASU: Final = httpx.Timeout(10.0, connect=5.0)
TTL_BIEZACE_S: Final = 600.0
"""TTL okna obejmującego dzisiaj: tabela A jest publikowana raz dziennie, ale nie o stałej godzinie."""
TTL_ARCHIWALNE_S: Final = 12 * 3600.0
MAKS_WPISOW_CACHE: Final = 128
MAKS_POZYCJI_PORTFELA: Final = 50
GROSZ: Final = Decimal("0.01")

WALUTY_TABELI_A: Final = frozenset(
    [
        "THB",
        "USD",
        "AUD",
        "HKD",
        "CAD",
        "NZD",
        "SGD",
        "EUR",
        "HUF",
        "CHF",
        "GBP",
        "UAH",
        "JPY",
        "CZK",
        "DKK",
        "ISK",
        "NOK",
        "SEK",
        "RON",
        "TRY",
        "ILS",
        "CLP",
        "PHP",
        "MXN",
        "ZAR",
        "BRL",
        "MYR",
        "IDR",
        "INR",
        "KRW",
        "CNY",
        "XDR",
    ]
)
"""Kody z tabeli A NBP (stan z 2026-10-05). Kod spoza listy jest odrzucany przed żądaniem do NBP."""
WALUTY_OBSLUGIWANE: Final = WALUTY_TABELI_A | {"PLN"}

_WZORZEC_DATY: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_WZORZEC_KWOTY: Final = re.compile(r"-?\d{1,15}(\.\d{1,4})?")


class KodBledu(StrEnum):
    """Zamknięty zbiór powodów błędu narzędzia (FR-31)."""

    NIEPRAWIDLOWA_WALUTA = "nieprawidlowa_waluta"
    NIEPRAWIDLOWA_DATA = "nieprawidlowa_data"
    NIEPRAWIDLOWA_KWOTA = "nieprawidlowa_kwota"
    NIEPRAWIDLOWY_PORTFEL = "nieprawidlowy_portfel"
    BRAK_NOTOWANIA = "brak_notowania"
    NBP_NIEDOSTEPNE = "nbp_niedostepne"
    NIEPRAWIDLOWA_ODPOWIEDZ_NBP = "nieprawidlowa_odpowiedz_nbp"


class BladKursu(Exception):
    """Ustrukturyzowany błąd dziedzinowy; serwer zamienia go na błąd narzędzia MCP."""

    def __init__(self, kod: KodBledu, powod: str) -> None:
        """Tworzy błąd z kodem z zamkniętego zbioru i opisem dla człowieka."""
        super().__init__(powod)
        self.kod = kod
        self.powod = powod

    def jako_json(self) -> str:
        """Zwraca błąd jako JSON bez pól liczbowych (KA-08.4)."""
        return json.dumps({"blad": self.kod.value, "powod": self.powod}, ensure_ascii=False)


# --- walidacja wejścia (UC-08 krok 3, KA-08.3) -------------------------------------------------


def dzisiaj() -> date:
    """Zwraca bieżącą datę w strefie NBP (Europe/Warsaw)."""
    return datetime.now(STREFA_NBP).date()


def waliduj_walute(waluta: str) -> str:
    """Normalizuje i sprawdza kod waluty ISO 4217 względem tabeli A i PLN."""
    kod = waluta.strip().upper()
    if kod not in WALUTY_OBSLUGIWANE:
        raise BladKursu(
            KodBledu.NIEPRAWIDLOWA_WALUTA,
            f"Waluta {kod[:8]!r} nie jest obsługiwana; dozwolone: PLN i kody tabeli A NBP.",
        )
    return kod


def waliduj_date(data: str | None) -> date:
    """Sprawdza datę ISO-8601 (RRRR-MM-DD): nie z przyszłości, nie starszą niż dane NBP."""
    if data is None or not data.strip():
        return dzisiaj()
    tekst = data.strip()
    if not _WZORZEC_DATY.fullmatch(tekst):
        raise BladKursu(KodBledu.NIEPRAWIDLOWA_DATA, "Data musi mieć format RRRR-MM-DD.")
    try:
        wynik = date.fromisoformat(tekst)
    except ValueError:
        raise BladKursu(KodBledu.NIEPRAWIDLOWA_DATA, f"Nie ma takiego dnia: {tekst}.") from None
    if wynik > dzisiaj():
        raise BladKursu(KodBledu.NIEPRAWIDLOWA_DATA, f"Data {tekst} jest z przyszłości.")
    if wynik < NAJWCZESNIEJSZA_DATA:
        raise BladKursu(
            KodBledu.NIEPRAWIDLOWA_DATA, f"NBP publikuje kursy od {NAJWCZESNIEJSZA_DATA.isoformat()}."
        )
    return wynik


def waliduj_kwote(kwota: str) -> Decimal:
    """Parsuje kwotę dziesiętną z tekstu (najwyżej 15 cyfr całkowitych i 4 po kropce)."""
    tekst = kwota.strip()
    if not _WZORZEC_KWOTY.fullmatch(tekst):
        raise BladKursu(
            KodBledu.NIEPRAWIDLOWA_KWOTA, "Kwota musi być liczbą dziesiętną z kropką, np. '1250.50'."
        )
    try:
        return Decimal(tekst)
    except InvalidOperation:  # pragma: no cover - wykluczone przez wzorzec
        raise BladKursu(KodBledu.NIEPRAWIDLOWA_KWOTA, "Kwota nie jest liczbą.") from None


def na_grosze(wartosc: Decimal) -> Decimal:
    """Zaokrągla do grosza metodą ROUND_HALF_UP (FR-01)."""
    return wartosc.quantize(GROSZ, rounding=ROUND_HALF_UP)


# --- kształt odpowiedzi NBP (OG-22) ------------------------------------------------------------

KodWaluty = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class _KursTabeli(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    code: KodWaluty
    mid: Annotated[Decimal, Field(gt=0, lt=Decimal(1_000_000))]


class _TabelaNBP(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    table: Literal["A"]
    no: Annotated[str, StringConstraints(max_length=32, pattern=r"^[0-9]{1,3}/A/NBP/[0-9]{4}$")]
    effectiveDate: date
    rates: Annotated[list[_KursTabeli], Field(max_length=200)]


class _OdpowiedzTabel(BaseModel):
    model_config = ConfigDict(frozen=True)
    tabele: Annotated[list[_TabelaNBP], Field(max_length=LIMIT_DNI_ZAPYTANIA + 1)]


@dataclass(frozen=True, slots=True)
class Notowanie:
    """Kurs średni z jednej tabeli A."""

    kurs: Decimal
    data_notowania: date
    numer_tabeli: str


type OknoNotowan = dict[date, tuple[str, dict[str, Decimal]]]
"""Data notowania -> (numer tabeli, kod waluty -> kurs średni)."""


@dataclass(slots=True)
class _WpisCache:
    okno: OknoNotowan
    pobrano: float
    ttl: float

    def swiezy(self, teraz: float) -> bool:
        return teraz - self.pobrano < self.ttl


@dataclass(frozen=True, slots=True)
class WynikKursu:
    """Kurs z forward-fillem wraz z metadanymi pochodzenia (FR-03)."""

    waluta: str
    kurs: Decimal
    data_zadana: date
    data_notowania: date
    numer_tabeli: str
    dane_aktualne: bool
    """``False``, gdy NBP nie odpowiedziało i użyto przeterminowanego wpisu z cache."""


def _sprawdz_zadanie(zadanie: httpx.Request) -> None:
    """Hak bezpieczeństwa: tylko GET, tylko HTTPS, tylko api.nbp.pl (allowlista)."""
    if zadanie.method != "GET" or zadanie.url.scheme != "https" or zadanie.url.host != HOST_NBP:
        raise httpx.UnsupportedProtocol(f"Zablokowane żądanie {zadanie.method} {zadanie.url.host}")


def utworz_klienta_http(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """Tworzy klienta HTTP z allowlistą hosta, limitami czasu i bez przekierowań."""

    async def hak(zadanie: httpx.Request) -> None:
        _sprawdz_zadanie(zadanie)

    return httpx.AsyncClient(
        base_url=BAZOWY_URL,
        timeout=LIMIT_CZASU,
        follow_redirects=False,
        headers={"Accept": "application/json", "User-Agent": "serwer-mcp-nbp/1.0"},
        event_hooks={"request": [hak]},
        transport=transport,
    )


@dataclass(slots=True)
class KlientNBP:
    """Odczyt kursów tabeli A z cache'em w pamięci procesu (OG-09) i forward-fillem (FR-03).

    Jedno żądanie pobiera okno ``OKNO_FORWARD_FILL_DNI`` dni dla **wszystkich** walut naraz
    (``/exchangerates/tables/a/{od}/{do}/``), więc wycena portfela z wieloma walutami kosztuje
    jedno wywołanie NBP, a kolejne pytania o ten sam dzień obsługuje cache.
    """

    http: httpx.AsyncClient
    zegar: Callable[[], float] = time.monotonic
    liczba_zapytan: int = 0
    _cache: OrderedDict[tuple[date, date], _WpisCache] = field(default_factory=OrderedDict)
    _blokada: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def zamknij(self) -> None:
        """Zamyka połączenia HTTP."""
        await self.http.aclose()

    async def _pobierz_okno(self, od: date, do: date) -> OknoNotowan:
        """Pobiera tabele A z zakresu dat; 404 oznacza brak notowań (nie błąd)."""
        if (do - od).days + 1 > LIMIT_DNI_ZAPYTANIA:  # pragma: no cover - stała okna jest mniejsza
            raise ValueError("Zakres przekracza limit NBP")
        sciezka = f"exchangerates/tables/a/{od.isoformat()}/{do.isoformat()}/"
        self.liczba_zapytan += 1
        log.info("NBP GET %s (zapytanie nr %d)", sciezka, self.liczba_zapytan)
        try:
            async with self.http.stream("GET", sciezka, params={"format": "json"}) as odp:
                if odp.status_code == httpx.codes.NOT_FOUND:
                    return {}
                if odp.status_code != httpx.codes.OK:
                    raise BladKursu(KodBledu.NBP_NIEDOSTEPNE, f"NBP odpowiedziało kodem {odp.status_code}.")
                tresc = bytearray()
                async for kawalek in odp.aiter_bytes():
                    tresc.extend(kawalek)
                    if len(tresc) > MAKS_ROZMIAR_ODPOWIEDZI:
                        raise BladKursu(
                            KodBledu.NIEPRAWIDLOWA_ODPOWIEDZ_NBP, "Odpowiedź NBP przekracza limit rozmiaru."
                        )
        except httpx.HTTPError as blad:
            raise BladKursu(
                KodBledu.NBP_NIEDOSTEPNE, f"Brak połączenia z NBP ({type(blad).__name__})."
            ) from None
        try:
            surowe = json.loads(bytes(tresc), parse_float=Decimal)
            tabele = _OdpowiedzTabel(tabele=surowe).tabele
        except (ValueError, ValidationError):
            raise BladKursu(
                KodBledu.NIEPRAWIDLOWA_ODPOWIEDZ_NBP, "Odpowiedź NBP ma nieoczekiwany kształt."
            ) from None
        return {t.effectiveDate: (t.no, {k.code: k.mid for k in t.rates}) for t in tabele}

    async def okno_do(self, data: date) -> tuple[OknoNotowan, bool]:
        """Zwraca okno notowań kończące się na ``data`` oraz flagę aktualności danych."""
        klucz = (data - timedelta(days=OKNO_FORWARD_FILL_DNI - 1), data)
        async with self._blokada:
            teraz = self.zegar()
            wpis = self._cache.get(klucz)
            if wpis is not None and wpis.swiezy(teraz):
                self._cache.move_to_end(klucz)
                return wpis.okno, True
            try:
                okno = await self._pobierz_okno(*klucz)
            except BladKursu as blad:
                if wpis is not None and blad.kod is KodBledu.NBP_NIEDOSTEPNE:
                    log.warning("NBP niedostępne, używam przeterminowanego wpisu cache %s", klucz)
                    return wpis.okno, False
                raise
            ttl = TTL_BIEZACE_S if data >= dzisiaj() else TTL_ARCHIWALNE_S
            self._cache[klucz] = _WpisCache(okno=okno, pobrano=teraz, ttl=ttl)
            self._cache.move_to_end(klucz)
            while len(self._cache) > MAKS_WPISOW_CACHE:
                self._cache.popitem(last=False)
            return okno, True

    async def kurs(self, waluta: str, data: date) -> WynikKursu:
        """Kurs średni z dnia ``data`` albo z ostatniego wcześniejszego notowania (forward-fill).

        Raises:
            BladKursu: Brak notowania w oknie albo awaria NBP — nigdy wartość domyślna (FR-31).

        """
        if waluta == "PLN":
            return WynikKursu("PLN", Decimal(1), data, data, "stała PLN = 1", dane_aktualne=True)
        okno, aktualne = await self.okno_do(data)
        for dzien in sorted(okno, reverse=True):
            if dzien > data:
                continue
            numer, kursy = okno[dzien]
            if (kurs := kursy.get(waluta)) is not None:
                return WynikKursu(waluta, kurs, data, dzien, numer, aktualne)
        raise BladKursu(
            KodBledu.BRAK_NOTOWANIA,
            f"Brak notowania {waluta} w tabeli A NBP w dniu {data.isoformat()} "
            f"ani w {OKNO_FORWARD_FILL_DNI - 1} dniach wcześniej.",
        )
