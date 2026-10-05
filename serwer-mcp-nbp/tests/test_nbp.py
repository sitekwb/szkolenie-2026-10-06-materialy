"""Testy jednostkowe klienta NBP: walidacja, forward-fill, cache, granica zaufania (bez sieci)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from atrapa_nbp import AtrapaNBP

from serwer_mcp_nbp import nbp
from serwer_mcp_nbp.nbp import BladKursu, KlientNBP, KodBledu

pytestmark = pytest.mark.anyio


def klient(atrapa: AtrapaNBP, zegar: list[float] | None = None) -> KlientNBP:
    czas = zegar if zegar is not None else [0.0]
    return KlientNBP(nbp.utworz_klienta_http(atrapa.transport()), zegar=lambda: czas[0])


@pytest.mark.parametrize("wejscie", ["eur", " EUR ", "Eur"])
def test_waluta_normalizowana(wejscie: str) -> None:
    assert nbp.waliduj_walute(wejscie) == "EUR"


@pytest.mark.parametrize("wejscie", ["", "EURO", "XYZ", "BTC", "E1R", "PLN;rm -rf"])
def test_waluta_spoza_tabeli_odrzucona(wejscie: str) -> None:
    with pytest.raises(BladKursu) as e:
        nbp.waliduj_walute(wejscie)
    assert e.value.kod is KodBledu.NIEPRAWIDLOWA_WALUTA


@pytest.mark.parametrize(
    "wejscie", ["2026-13-01", "2026-02-30", "12.06.2026", "2026-6-12", "dzisiaj", "2001-12-31"]
)
def test_nieprawidlowa_data(wejscie: str) -> None:
    with pytest.raises(BladKursu) as e:
        nbp.waliduj_date(wejscie)
    assert e.value.kod is KodBledu.NIEPRAWIDLOWA_DATA


def test_data_z_przyszlosci_odrzucona() -> None:
    jutro = (nbp.dzisiaj() + timedelta(days=1)).isoformat()
    with pytest.raises(BladKursu, match="przyszłości"):
        nbp.waliduj_date(jutro)


def test_brak_daty_to_dzisiaj() -> None:
    assert nbp.waliduj_date(None) == nbp.dzisiaj()
    assert nbp.waliduj_date("  ") == nbp.dzisiaj()


@pytest.mark.parametrize(
    ("wejscie", "oczekiwane"), [("1250.50", "1250.50"), ("-10", "-10"), ("0.0001", "0.0001")]
)
def test_kwota(wejscie: str, oczekiwane: str) -> None:
    assert nbp.waliduj_kwote(wejscie) == Decimal(oczekiwane)


@pytest.mark.parametrize("wejscie", ["1,5", "1e9", "NaN", "Infinity", "0.00001", "1" * 16, ""])
def test_nieprawidlowa_kwota(wejscie: str) -> None:
    with pytest.raises(BladKursu) as e:
        nbp.waliduj_kwote(wejscie)
    assert e.value.kod is KodBledu.NIEPRAWIDLOWA_KWOTA


@pytest.mark.parametrize(
    ("wejscie", "oczekiwane"), [("0.005", "0.01"), ("0.004", "0.00"), ("-0.005", "-0.01")]
)
def test_round_half_up(wejscie: str, oczekiwane: str) -> None:
    assert nbp.na_grosze(Decimal(wejscie)) == Decimal(oczekiwane)


async def test_kurs_z_dnia_notowania() -> None:
    w = await klient(AtrapaNBP()).kurs("EUR", date(2026, 6, 12))
    assert (w.kurs, w.data_notowania, w.numer_tabeli) == (
        Decimal("4.2484"),
        date(2026, 6, 12),
        "112/A/NBP/2026",
    )


@pytest.mark.parametrize(
    ("dzien", "notowanie"),
    [
        (date(2026, 6, 4), date(2026, 6, 3)),
        (date(2026, 6, 13), date(2026, 6, 12)),
        (date(2026, 6, 14), date(2026, 6, 12)),
    ],
    ids=["boze-cialo", "sobota", "niedziela"],
)
async def test_forward_fill_bez_interpolacji(dzien: date, notowanie: date) -> None:
    atrapa = AtrapaNBP()
    k = klient(atrapa)
    w = await k.kurs("EUR", dzien)
    poprzedni = await k.kurs("EUR", notowanie)
    assert w.data_zadana == dzien
    assert w.data_notowania == notowanie
    assert w.kurs == poprzedni.kurs


async def test_pln_bez_zapytania() -> None:
    atrapa = AtrapaNBP()
    w = await klient(atrapa).kurs("PLN", date(2026, 6, 13))
    assert w.kurs == 1
    assert atrapa.zadania == []


async def test_cache_jedno_zapytanie_na_okno() -> None:
    atrapa = AtrapaNBP()
    k = klient(atrapa)
    for waluta in ("EUR", "USD", "CHF", "GBP", "EUR"):
        await k.kurs(waluta, date(2026, 6, 12))
    assert len(atrapa.zadania) == 1
    assert atrapa.zadania[0].url.path == "/api/exchangerates/tables/a/2026-05-30/2026-06-12/"


async def test_cache_wygasa_po_ttl() -> None:
    atrapa, czas = AtrapaNBP(), [0.0]
    k = klient(atrapa, czas)
    await k.kurs("EUR", date(2026, 6, 12))
    czas[0] = nbp.TTL_ARCHIWALNE_S + 1
    await k.kurs("EUR", date(2026, 6, 12))
    assert len(atrapa.zadania) == 2


async def test_cache_ma_limit_wpisow() -> None:
    k = klient(AtrapaNBP())
    for i in range(nbp.MAKS_WPISOW_CACHE + 5):
        await k.okno_do(date(2026, 1, 1) + timedelta(days=i))
    assert len(k._cache) == nbp.MAKS_WPISOW_CACHE


async def test_awaria_nbp_daje_przeterminowany_cache_z_flaga() -> None:
    atrapa, czas = AtrapaNBP(), [0.0]
    k = klient(atrapa, czas)
    await k.kurs("EUR", date(2026, 6, 12))
    atrapa.tryb, czas[0] = "awaria", nbp.TTL_ARCHIWALNE_S + 1
    w = await k.kurs("EUR", date(2026, 6, 12))
    assert w.dane_aktualne is False
    assert w.kurs == Decimal("4.2484")


@pytest.mark.parametrize(
    ("tryb", "kod"),
    [
        ("awaria", KodBledu.NBP_NIEDOSTEPNE),
        ("smieci", KodBledu.NIEPRAWIDLOWA_ODPOWIEDZ_NBP),
        ("za_duzo", KodBledu.NIEPRAWIDLOWA_ODPOWIEDZ_NBP),
        ("puste", KodBledu.BRAK_NOTOWANIA),
    ],
)
async def test_bledy_nbp_bez_wartosci_domyslnej(tryb: str, kod: KodBledu) -> None:
    atrapa = AtrapaNBP()
    atrapa.tryb = tryb  # type: ignore[assignment]
    with pytest.raises(BladKursu) as e:
        await klient(atrapa).kurs("EUR", date(2026, 6, 12))
    assert e.value.kod is kod
    assert "{" not in e.value.powod


async def test_waluta_bez_notowania_w_oknie() -> None:
    with pytest.raises(BladKursu) as e:
        await klient(AtrapaNBP()).kurs("THB", date(2026, 6, 12))
    assert e.value.kod is KodBledu.BRAK_NOTOWANIA


@pytest.mark.parametrize(
    ("metoda", "url"),
    [
        ("POST", "https://api.nbp.pl/api/exchangerates/tables/a/"),
        ("DELETE", "https://api.nbp.pl/api/exchangerates/tables/a/"),
        ("GET", "http://api.nbp.pl/api/exchangerates/tables/a/"),
        ("GET", "https://evil.example/api/"),
        ("GET", "https://api.nbp.pl.evil.example/api/"),
    ],
)
async def test_allowlista_tylko_get_https_api_nbp(metoda: str, url: str) -> None:
    atrapa = AtrapaNBP()
    http = nbp.utworz_klienta_http(atrapa.transport())
    with pytest.raises(httpx.UnsupportedProtocol, match="Zablokowane"):
        await http.request(metoda, url)
    assert atrapa.zadania == []


def test_przekierowania_wylaczone() -> None:
    assert nbp.utworz_klienta_http().follow_redirects is False
