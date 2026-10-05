"""Testy ``nbp_convert`` — całkowicie offline (sieć i „wczoraj" zamockowane)."""

from __future__ import annotations

import io
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

import nbp_convert
from nbp_convert import NBPError, convert_to_pln, ensure_cache, prefetch

TODAY_YESTERDAY = date(2026, 9, 30)  # środa
type Range = tuple[date, date]
type FetchLog = list[Range]


def _table(day: date, mids: dict[str, str]) -> dict[str, Any]:
    """Buduje tabelę A w formacie API NBP (dni robocze tylko)."""
    return {
        "no": f"{day.timetuple().tm_yday}/A/NBP/{day.year}",
        "effectiveDate": day.isoformat(),
        "rates": [{"code": c, "mid": Decimal(m)} for c, m in mids.items()],
    }


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> date:
    """Zamraża ``yesterday_warsaw`` na stałą datę."""
    monkeypatch.setattr(nbp_convert, "yesterday_warsaw", lambda: TODAY_YESTERDAY)
    return TODAY_YESTERDAY


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Każda próba użycia sieci kończy test błędem."""

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("żądanie sieciowe!")

    monkeypatch.setattr(nbp_convert, "urlopen", boom)


@pytest.fixture
def fake_fetch(monkeypatch: pytest.MonkeyPatch, frozen: date) -> FetchLog:
    """Podmienia ``_fetch_tables``: dni robocze, EUR=4.2500, USD=1.005; loguje zakresy."""
    log: FetchLog = []

    def fake(start: date, end: date, timeout: float) -> list[dict[str, Any]]:
        log.append((start, end))
        return [
            _table(day, {"EUR": "4.2500", "USD": "1.005"})
            for day in nbp_convert._days(start, end)
            if day.weekday() < 5
        ]

    monkeypatch.setattr(nbp_convert, "_fetch_tables", fake)
    return log


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    """Ścieżka cache'u w katalogu tymczasowym."""
    return tmp_path / "nbp" / "cache.sqlite3"


def _rows(path: Path, sql: str) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


# --- prefetch ------------------------------------------------------------------


def test_prefetch_stores_rates_and_covered_days(fake_fetch: FetchLog, cache: Path) -> None:
    stored = prefetch(date(2026, 9, 21), date(2026, 9, 27), cache_path=cache)  # pon..niedz
    assert stored == 5
    assert len(_rows(cache, "SELECT * FROM rates")) == 10
    assert len(_rows(cache, "SELECT * FROM covered_days")) == 7


def test_prefetch_is_idempotent(fake_fetch: FetchLog, cache: Path) -> None:
    prefetch(date(2026, 9, 1), date(2026, 9, 20), cache_path=cache)
    first = _rows(cache, "SELECT * FROM rates ORDER BY 1, 2")
    prefetch(date(2026, 9, 1), date(2026, 9, 20), cache_path=cache)
    assert _rows(cache, "SELECT * FROM rates ORDER BY 1, 2") == first


def test_prefetch_chunks_by_max_range(fake_fetch: FetchLog, cache: Path) -> None:
    end = TODAY_YESTERDAY
    prefetch(end - timedelta(days=199), end, cache_path=cache)
    sizes = [(e - s).days + 1 for s, e in fake_fetch]
    assert sizes == [93, 93, 14]
    assert all(b[0] == a[1] + timedelta(days=1) for a, b in zip(fake_fetch, fake_fetch[1:]))


def test_prefetch_clips_end_to_yesterday(fake_fetch: FetchLog, cache: Path) -> None:
    prefetch(date(2026, 9, 25), date(2026, 10, 5), cache_path=cache)
    assert fake_fetch[-1][1] == TODAY_YESTERDAY


# --- _fetch_tables -------------------------------------------------------------


def _http_error(code: int) -> Callable[..., Any]:
    def raiser(*args: object, **kwargs: object) -> Any:
        raise HTTPError("https://api.nbp.pl", code, "err", {}, io.BytesIO())  # type: ignore[arg-type]

    return raiser


def test_fetch_tables_404_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nbp_convert, "urlopen", _http_error(404))
    assert nbp_convert._fetch_tables(date(2026, 1, 1), date(2026, 1, 1), 1.0) == []


def test_fetch_tables_other_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nbp_convert, "urlopen", _http_error(400))
    with pytest.raises(NBPError, match="400"):
        nbp_convert._fetch_tables(date(2026, 1, 1), date(2026, 1, 1), 1.0)


# --- convert_to_pln ------------------------------------------------------------


@pytest.fixture
def filled(fake_fetch: FetchLog, cache: Path, no_network: None) -> Path:
    """Cache wypełniony za wrzesień 2026, sieć następnie zablokowana."""
    prefetch(date(2026, 9, 1), TODAY_YESTERDAY, cache_path=cache)
    return cache


def test_convert_weekend_falls_back_to_friday(filled: Path) -> None:
    result = convert_to_pln("10", "eur", dzien=date(2026, 9, 27), cache_path=filled)  # niedziela
    assert result.effective_date == date(2026, 9, 25)
    assert result.amount_pln == Decimal("42.50")


def test_convert_rounds_half_up(filled: Path) -> None:
    result = convert_to_pln(Decimal(1), "USD", dzien=date(2026, 9, 29), cache_path=filled)
    assert result.amount_pln == Decimal("1.01")


def test_convert_default_day_is_yesterday(filled: Path) -> None:
    assert convert_to_pln(1, "EUR", cache_path=filled).effective_date == TODAY_YESTERDAY


@pytest.mark.parametrize(
    ("amount", "currency", "dzien", "exc"),
    [
        (1, "EUR", date(2099, 1, 1), ValueError),
        (1.5, "EUR", None, TypeError),
        (1, "PLN", None, ValueError),
        (1, "EU", None, ValueError),
        (1, "E1R", None, ValueError),
    ],
)
def test_convert_validation(filled: Path, amount: Any, currency: str, dzien: date | None, exc: type[Exception]) -> None:
    with pytest.raises(exc):
        convert_to_pln(amount, currency, dzien=dzien, cache_path=filled)


def test_convert_missing_cache_file(frozen: date, no_network: None, tmp_path: Path) -> None:
    with pytest.raises(NBPError, match="Brak cache"):
        convert_to_pln(1, "EUR", cache_path=tmp_path / "missing.sqlite3")


def test_convert_uncovered_window(filled: Path) -> None:
    with pytest.raises(NBPError, match="nie obejmuje"):
        convert_to_pln(1, "EUR", dzien=date(2026, 8, 15), cache_path=filled)


def test_convert_unknown_currency(filled: Path) -> None:
    with pytest.raises(NBPError, match="Brak kursu XYZ"):
        convert_to_pln(1, "XYZ", cache_path=filled)


# --- ensure_cache --------------------------------------------------------------


def test_ensure_cache_fetches_only_missing(
    fake_fetch: FetchLog, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ensure_cache(30, cache_path=cache) > 0
    assert fake_fetch == [(TODAY_YESTERDAY - timedelta(days=30), TODAY_YESTERDAY)]

    fake_fetch.clear()
    assert ensure_cache(30, cache_path=cache) == 0
    assert fake_fetch == []

    next_day = TODAY_YESTERDAY + timedelta(days=1)
    monkeypatch.setattr(nbp_convert, "yesterday_warsaw", lambda: next_day)
    assert ensure_cache(30, cache_path=cache) == 1  # czwartek
    assert fake_fetch == [(next_day, next_day)]
