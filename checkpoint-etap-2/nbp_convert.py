"""Przeliczanie kwot w walucie obcej na PLN po kursie średnim NBP (tabela A).

Kursy są pobierane z góry (``prefetch``) do lokalnego cache'u SQLite;
``convert_to_pln`` czyta wyłącznie z cache'u i nie wykonuje żądań sieciowych.
``ensure_cache`` dociąga jedynie dni brakujące w cache'u.

Ścieżkę cache'u można nadpisać zmienną środowiskową ``NBP_CACHE_PATH``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

NBP_TABLES_URL = "https://api.nbp.pl/api/exchangerates/tables/a/{start}/{end}/?format=json"
MAX_RANGE_DAYS = 93  # twardy limit zakresu w API NBP (inaczej HTTP 400)
WARSAW = ZoneInfo("Europe/Warsaw")
LOOKBACK_DAYS = 10  # zapas na weekendy i dłuższe ciągi świąt
PREFETCH_DAYS = 400  # domyślna głębokość pobierania wstecz
CENT = Decimal("0.01")
DEFAULT_CACHE_PATH = Path(
    os.environ.get("NBP_CACHE_PATH", Path.home() / ".cache" / "nbp_convert" / "tabela_a.sqlite3")
)

# ``covered_days`` rozróżnia „brak kursu, bo święto" od „nie pobrano": sam brak
# wiersza w ``rates`` nic nie mówi, dopóki dzień nie został objęty pobraniem.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS rates (
    code TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    mid TEXT NOT NULL,
    table_no TEXT NOT NULL,
    PRIMARY KEY (code, effective_date)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS covered_days (day TEXT PRIMARY KEY) WITHOUT ROWID;
"""


class NBPError(RuntimeError):
    """Błąd pobierania, odczytu z cache'u lub interpretacji kursu NBP."""


@dataclass(frozen=True, slots=True)
class Conversion:
    """Wynik przeliczenia wraz z użytym kursem.

    Attributes:
        amount_pln: Kwota w złotych, zaokrąglona do groszy.
        mid: Kurs średni NBP (PLN za 1 jednostkę waluty).
        effective_date: Data publikacji użytego kursu.
        table_no: Numer tabeli NBP, np. ``"190/A/NBP/2026"``.
    """

    amount_pln: Decimal
    mid: Decimal
    effective_date: date
    table_no: str


def yesterday_warsaw() -> date:
    """Zwraca wczorajszą datę wg czasu polskiego (a nie czasu systemowego)."""
    return datetime.now(WARSAW).date() - timedelta(days=1)


def _days(start: date, end: date) -> Iterator[date]:
    """Iteruje po kolejnych dniach kalendarzowych z przedziału domkniętego."""
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def _fetch_tables(start: date, end: date, timeout: float) -> list[dict[str, Any]]:
    """Pobiera tabele A opublikowane w przedziale (maks. ``MAX_RANGE_DAYS`` dni).

    Przedział bez publikacji (same święta/weekend) NBP zwraca jako HTTP 404 —
    to nie błąd, tylko pusta lista.
    """
    url = NBP_TABLES_URL.format(start=start.isoformat(), end=end.isoformat())
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            tables: list[dict[str, Any]] = json.load(response, parse_float=Decimal)
            return tables
    except HTTPError as exc:
        if exc.code == 404:
            return []
        raise NBPError(f"Błąd HTTP {exc.code} z API NBP dla {start}..{end}.") from exc


def prefetch(
    start: date | None = None,
    end: date | None = None,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    timeout: float = 10.0,
) -> int:
    """Pobiera tabele A z NBP dla przedziału dat i zapisuje je w cache'u.

    Operacja jest idempotentna (ponowne pobranie nadpisuje wpisy tych samych dni).
    Przedział dzielony jest na porcje po ``MAX_RANGE_DAYS`` dni, każda zapisywana
    w osobnej transakcji. Dzisiejszy dzień nie jest pobierany, bo kurs mógł
    jeszcze nie zostać opublikowany — ``end`` jest przycinany do wczoraj.

    Args:
        start: Pierwszy dzień (domyślnie ``end`` − ``PREFETCH_DAYS``).
        end: Ostatni dzień (domyślnie wczoraj wg czasu polskiego).
        cache_path: Plik bazy SQLite; katalog zostanie utworzony.
        timeout: Limit czasu pojedynczego żądania HTTP w sekundach.

    Returns:
        Liczba zapisanych tabel NBP.

    Raises:
        ValueError: Gdy po przycięciu ``start`` jest późniejszy niż ``end``.
        NBPError: Przy błędzie API NBP.
    """
    last = yesterday_warsaw()
    end = min(end or last, last)
    start = start or end - timedelta(days=PREFETCH_DAYS)
    if start > end:
        raise ValueError(f"Pusty przedział: {start}..{end}.")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    stored = 0
    with closing(sqlite3.connect(cache_path)) as conn:
        conn.executescript(_SCHEMA)
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(chunk_start + timedelta(days=MAX_RANGE_DAYS - 1), end)
            tables = _fetch_tables(chunk_start, chunk_end, timeout)
            with conn:  # jedna transakcja: kursy + pokrycie dni
                conn.executemany(
                    "INSERT OR REPLACE INTO rates VALUES (?, ?, ?, ?)",
                    (
                        (rate["code"], table["effectiveDate"], str(rate["mid"]), table["no"])
                        for table in tables
                        for rate in table["rates"]
                    ),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO covered_days VALUES (?)",
                    ((day.isoformat(),) for day in _days(chunk_start, chunk_end)),
                )
            stored += len(tables)
            chunk_start = chunk_end + timedelta(days=1)
    return stored


def ensure_cache(
    days: int = PREFETCH_DAYS,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    timeout: float = 10.0,
) -> int:
    """Uzupełnia cache o dni z ostatnich ``days`` dni, których jeszcze nie pobrano.

    Sprawdza ``covered_days`` dla przedziału [wczoraj − ``days``, wczoraj]
    (brak pliku = brakują wszystkie dni). Gdy niczego nie brakuje, nie wykonuje
    żadnego żądania; w przeciwnym razie wywołuje ``prefetch`` od najwcześniejszego
    brakującego dnia do wczoraj.

    Args:
        days: Głębokość okna wstecz od wczoraj (w dniach).
        cache_path: Plik bazy SQLite.
        timeout: Limit czasu pojedynczego żądania HTTP w sekundach.

    Returns:
        Liczba zapisanych tabel NBP (0, gdy cache był kompletny).

    Raises:
        NBPError: Przy błędzie API NBP.
    """
    end = yesterday_warsaw()
    start = end - timedelta(days=days)
    wanted = {day.isoformat() for day in _days(start, end)}
    if cache_path.exists():
        with closing(sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)) as conn:
            try:
                covered = {
                    day
                    for (day,) in conn.execute(
                        "SELECT day FROM covered_days WHERE day BETWEEN ? AND ?",
                        (start.isoformat(), end.isoformat()),
                    )
                }
            except sqlite3.OperationalError:  # plik bez schematu
                covered = set()
        wanted -= covered
    if not wanted:
        return 0
    return prefetch(date.fromisoformat(min(wanted)), end, cache_path=cache_path, timeout=timeout)


def _lookup_rate(code: str, on_or_before: date, cache_path: Path) -> tuple[Decimal, date, str]:
    """Czyta z cache'u ostatni kurs opublikowany nie później niż ``on_or_before``.

    Raises:
        NBPError: Gdy cache nie istnieje, nie obejmuje całego okna
            ``LOOKBACK_DAYS`` albo nie zawiera kursu dla ``code``.
    """
    window_start = (on_or_before - timedelta(days=LOOKBACK_DAYS)).isoformat()
    window_end = on_or_before.isoformat()
    hint = f"Uruchom: python {Path(__file__).name} prefetch"
    if not cache_path.exists():
        raise NBPError(f"Brak cache'u kursów ({cache_path}). {hint}")

    # Tryb tylko do odczytu — zapytania nigdy nie modyfikują cache'u.
    with closing(sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)) as conn:
        (covered,) = conn.execute(
            "SELECT COUNT(*) FROM covered_days WHERE day BETWEEN ? AND ?",
            (window_start, window_end),
        ).fetchone()
        if covered < LOOKBACK_DAYS + 1:
            raise NBPError(f"Cache nie obejmuje okresu {window_start}..{window_end}. {hint}")
        row = conn.execute(
            "SELECT mid, effective_date, table_no FROM rates "
            "WHERE code = ? AND effective_date BETWEEN ? AND ? "
            "ORDER BY effective_date DESC LIMIT 1",
            (code, window_start, window_end),
        ).fetchone()
    if row is None:
        raise NBPError(f"Brak kursu {code} w tabeli A w okresie {window_start}..{window_end}.")
    mid, effective_date, table_no = row
    return Decimal(mid), date.fromisoformat(effective_date), table_no


def convert_to_pln(
    amount: Decimal | int | str,
    currency: str,
    *,
    dzien: date | None = None,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> Conversion:
    """Przelicza kwotę w walucie obcej na PLN po kursie średnim NBP z wskazanego dnia.

    Kurs jest czytany z lokalnego cache'u (patrz ``prefetch``); funkcja nie
    wykonuje żądań sieciowych. NBP nie publikuje kursów w weekendy i święta.
    Jeśli w danym dniu kursu nie było, używany jest ostatni kurs opublikowany
    przed tą datą (zgodnie z praktyką rozliczeniową: kurs z ostatniego dnia
    roboczego poprzedzającego dzień). Faktycznie użyty dzień zwraca
    ``Conversion.effective_date``.

    Args:
        amount: Kwota w walucie obcej. Zalecane ``Decimal`` lub ``str``;
            ``float`` jest odrzucany, by nie wprowadzać błędów binarnych.
        currency: Trzyliterowy kod ISO 4217 (np. ``"EUR"``), wielkość liter bez znaczenia.
        dzien: Dzień, na który pobierany jest kurs. Domyślnie wczoraj wg czasu polskiego.
        cache_path: Plik cache'u SQLite wypełniany przez ``prefetch``.

    Returns:
        ``Conversion`` z kwotą w PLN zaokrągloną do groszy (half-up).

    Raises:
        TypeError: Gdy ``amount`` jest typu ``float``.
        ValueError: Dla niepoprawnego kodu waluty, kwoty, waluty ``PLN`` lub ``dzien`` z przyszłości.
        NBPError: Gdy cache nie zawiera potrzebnego kursu (np. nie wykonano
            ``prefetch`` albo ``dzien`` to dziś — dzisiejszy dzień nie jest cache'owany).
    """
    if isinstance(amount, float):
        raise TypeError("Podaj kwotę jako Decimal, int lub str — nie float.")
    value = Decimal(amount)
    if not value.is_finite():
        raise ValueError(f"Niepoprawna kwota: {amount!r}.")

    code = currency.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError(f"Niepoprawny kod waluty: {currency!r}.")
    if code == "PLN":
        raise ValueError("Waluta PLN nie wymaga przeliczenia.")

    if dzien is None:
        dzien = yesterday_warsaw()
    elif dzien > datetime.now(WARSAW).date():
        raise ValueError(f"Data z przyszłości: {dzien}.")

    mid, effective_date, table_no = _lookup_rate(code, dzien, cache_path)
    return Conversion(
        amount_pln=(value * mid).quantize(CENT, rounding=ROUND_HALF_UP),
        mid=mid,
        effective_date=effective_date,
        table_no=table_no,
    )


def _main(argv: list[str] | None = None) -> int:
    """Wiersz poleceń: ``prefetch`` (pobranie do cache'u) i ``convert`` (przeliczenie)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH, help="plik cache'u SQLite")
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("prefetch", help="pobierz tabele A z NBP do cache'u")
    pre.add_argument("--from", dest="start", type=date.fromisoformat, help="RRRR-MM-DD")
    pre.add_argument("--to", dest="end", type=date.fromisoformat, help="RRRR-MM-DD")

    conv = sub.add_parser("convert", help="przelicz kwotę na PLN (z cache'u)")
    conv.add_argument("amount")
    conv.add_argument("currency")
    conv.add_argument("--dzien", type=date.fromisoformat, help="RRRR-MM-DD, domyślnie wczoraj")

    args = parser.parse_args(argv)
    try:
        match args.command:
            case "prefetch":
                count = prefetch(args.start, args.end, cache_path=args.cache)
                print(f"Zapisano {count} tabel w {args.cache}")
            case "convert":
                result = convert_to_pln(args.amount, args.currency, dzien=args.dzien, cache_path=args.cache)
                print(
                    f"{args.amount} {args.currency.upper()} = {result.amount_pln} PLN "
                    f"(kurs {result.mid} z {result.effective_date}, {result.table_no})"
                )
    except (NBPError, ValueError, TypeError, ArithmeticError) as exc:
        parser.exit(1, f"Błąd: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
