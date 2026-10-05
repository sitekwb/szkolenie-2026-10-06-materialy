"""Unit tests of the NBP client: validation, forward-fill, cache, trust boundary (no network)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from fake_nbp import FakeNBP

from nbp_mcp_server import nbp
from nbp_mcp_server.nbp import ErrorCode, NBPClient, RateError

pytestmark = pytest.mark.anyio


def client(fake: FakeNBP, clock: list[float] | None = None) -> NBPClient:
    now = clock if clock is not None else [0.0]
    return NBPClient(nbp.create_http_client(fake.transport()), clock=lambda: now[0], retry_backoff_s=0)


@pytest.mark.parametrize("value", ["eur", " EUR ", "Eur"])
def test_currency_normalized(value: str) -> None:
    assert nbp.validate_currency(value) == "EUR"


@pytest.mark.parametrize("value", ["", "EURO", "XYZ", "BTC", "E1R", "PLN;rm -rf"])
def test_currency_outside_table_rejected(value: str) -> None:
    with pytest.raises(RateError) as e:
        nbp.validate_currency(value)
    assert e.value.code is ErrorCode.INVALID_CURRENCY


@pytest.mark.parametrize(
    "value", ["2026-13-01", "2026-02-30", "12.06.2026", "2026-6-12", "today", "2001-12-31"]
)
def test_invalid_date(value: str) -> None:
    with pytest.raises(RateError) as e:
        nbp.validate_date(value)
    assert e.value.code is ErrorCode.INVALID_DATE


def test_future_date_rejected() -> None:
    tomorrow = (nbp.today() + timedelta(days=1)).isoformat()
    with pytest.raises(RateError, match="future"):
        nbp.validate_date(tomorrow)


def test_missing_date_means_today() -> None:
    assert nbp.validate_date(None) == nbp.today()
    assert nbp.validate_date("  ") == nbp.today()


@pytest.mark.parametrize(("value", "expected"), [("1250.50", "1250.50"), ("10", "10"), ("0.0001", "0.0001")])
def test_amount(value: str, expected: str) -> None:
    assert nbp.validate_amount(value) == Decimal(expected)


@pytest.mark.parametrize(
    "value", ["1,5", "1e9", "NaN", "Infinity", "0.00001", "1" * 16, "", "0", "0.00", "-10"]
)
def test_invalid_amount(value: str) -> None:
    with pytest.raises(RateError) as e:
        nbp.validate_amount(value)
    assert e.value.code is ErrorCode.INVALID_AMOUNT


@pytest.mark.parametrize(("value", "expected"), [("0.005", "0.01"), ("0.004", "0.00"), ("-0.005", "-0.01")])
def test_round_half_up(value: str, expected: str) -> None:
    assert nbp.round_to_grosz(Decimal(value)) == Decimal(expected)


async def test_rate_on_quote_day() -> None:
    r = await client(FakeNBP()).rate("EUR", date(2026, 6, 12))
    assert (r.rate, r.quote_date, r.table_number) == (
        Decimal("4.2484"),
        date(2026, 6, 12),
        "112/A/NBP/2026",
    )


@pytest.mark.parametrize(
    ("day", "quote_day"),
    [
        (date(2026, 6, 4), date(2026, 6, 3)),
        (date(2026, 6, 13), date(2026, 6, 12)),
        (date(2026, 6, 14), date(2026, 6, 12)),
    ],
    ids=["corpus-christi", "saturday", "sunday"],
)
async def test_forward_fill_without_interpolation(day: date, quote_day: date) -> None:
    fake = FakeNBP()
    c = client(fake)
    r = await c.rate("EUR", day)
    previous = await c.rate("EUR", quote_day)
    assert r.requested_date == day
    assert r.quote_date == quote_day
    assert r.rate == previous.rate


async def test_pln_without_request() -> None:
    fake = FakeNBP()
    r = await client(fake).rate("PLN", date(2026, 6, 13))
    assert r.rate == 1
    assert fake.requests == []


async def test_cache_one_request_per_window() -> None:
    fake = FakeNBP()
    c = client(fake)
    for currency in ("EUR", "USD", "CHF", "GBP", "EUR"):
        await c.rate(currency, date(2026, 6, 12))
    assert len(fake.requests) == 1
    assert fake.requests[0].url.path == "/api/exchangerates/tables/a/2026-05-30/2026-06-12/"


async def test_cache_expires_after_ttl() -> None:
    fake, now = FakeNBP(), [0.0]
    c = client(fake, now)
    await c.rate("EUR", date(2026, 6, 12))
    now[0] = nbp.TTL_ARCHIVED_S + 1
    await c.rate("EUR", date(2026, 6, 12))
    assert len(fake.requests) == 2


async def test_cache_has_entry_limit() -> None:
    c = client(FakeNBP())
    for i in range(nbp.MAX_CACHE_ENTRIES + 5):
        await c.window_until(date(2026, 1, 1) + timedelta(days=i))
    assert len(c._cache) == nbp.MAX_CACHE_ENTRIES


async def test_nbp_outage_returns_stale_cache_with_flag() -> None:
    fake, now = FakeNBP(), [0.0]
    c = client(fake, now)
    await c.rate("EUR", date(2026, 6, 12))
    fake.mode, now[0] = "outage", nbp.TTL_ARCHIVED_S + 1
    r = await c.rate("EUR", date(2026, 6, 12))
    assert r.is_current is False
    assert r.rate == Decimal("4.2484")


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("outage", ErrorCode.NBP_UNAVAILABLE),
        ("garbage", ErrorCode.INVALID_NBP_RESPONSE),
        ("oversized", ErrorCode.INVALID_NBP_RESPONSE),
        ("empty", ErrorCode.NO_QUOTE),
    ],
)
async def test_nbp_errors_without_default_value(mode: str, code: ErrorCode) -> None:
    fake = FakeNBP()
    fake.mode = mode  # type: ignore[assignment]
    with pytest.raises(RateError) as e:
        await client(fake).rate("EUR", date(2026, 6, 12))
    assert e.value.code is code
    assert "{" not in e.value.reason


async def test_currency_without_quote_in_window() -> None:
    with pytest.raises(RateError) as e:
        await client(FakeNBP()).rate("THB", date(2026, 6, 12))
    assert e.value.code is ErrorCode.NO_QUOTE


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", "https://api.nbp.pl/api/exchangerates/tables/a/"),
        ("DELETE", "https://api.nbp.pl/api/exchangerates/tables/a/"),
        ("GET", "http://api.nbp.pl/api/exchangerates/tables/a/"),
        ("GET", "https://evil.example/api/"),
        ("GET", "https://api.nbp.pl.evil.example/api/"),
    ],
)
async def test_allowlist_only_get_https_api_nbp(method: str, url: str) -> None:
    fake = FakeNBP()
    http = nbp.create_http_client(fake.transport())
    with pytest.raises(httpx.UnsupportedProtocol, match="Blocked"):
        await http.request(method, url)
    assert fake.requests == []


def test_redirects_disabled() -> None:
    assert nbp.create_http_client().follow_redirects is False


@pytest.mark.parametrize("failure", ["outage", "timeout"])
async def test_retry_once_after_transient_failure(failure: str) -> None:
    fake = FakeNBP(transient_failures=1, failure=failure)  # type: ignore[arg-type]
    r = await client(fake).rate("EUR", date(2026, 6, 12))
    assert r.rate == Decimal("4.2484")
    assert len(fake.requests) == 2


@pytest.mark.parametrize("mode", ["outage", "timeout"])
async def test_retry_gives_up_after_second_failure(mode: str) -> None:
    fake = FakeNBP(mode=mode)  # type: ignore[arg-type]
    with pytest.raises(RateError) as e:
        await client(fake).rate("EUR", date(2026, 6, 12))
    assert e.value.code is ErrorCode.NBP_UNAVAILABLE
    assert len(fake.requests) == nbp.MAX_ATTEMPTS == 2


@pytest.mark.parametrize("mode", ["empty", "garbage", "oversized"])
async def test_no_retry_for_404_or_invalid_response(mode: str) -> None:
    fake = FakeNBP(mode=mode)  # type: ignore[arg-type]
    with pytest.raises(RateError):
        await client(fake).rate("EUR", date(2026, 6, 12))
    assert len(fake.requests) == 1


async def test_no_retry_for_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400, text="400 BadRequest")

    requests: list[httpx.Request] = []
    c = NBPClient(nbp.create_http_client(httpx.MockTransport(handler)), retry_backoff_s=0)
    with pytest.raises(RateError) as e:
        await c.rate("EUR", date(2026, 6, 12))
    assert e.value.code is ErrorCode.NBP_UNAVAILABLE
    assert len(requests) == 1


async def test_retry_stays_within_deadline() -> None:
    fake = FakeNBP(transient_failures=1)
    c = NBPClient(nbp.create_http_client(fake.transport()), retry_backoff_s=5.0, deadline_s=0.05)
    with pytest.raises(RateError) as e:
        await c.rate("EUR", date(2026, 6, 12))
    assert e.value.code is ErrorCode.NBP_UNAVAILABLE
    assert "in time" in e.value.reason
    assert len(fake.requests) == 1


def test_retry_budget_fits_deadline() -> None:
    assert nbp.TIMEOUT.read is not None
    budget = nbp.MAX_ATTEMPTS * nbp.TIMEOUT.read + nbp.RETRY_BACKOFF_S
    assert budget <= nbp.FETCH_DEADLINE_S
