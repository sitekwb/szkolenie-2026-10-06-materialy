"""NBP API client (Table A): input validation, in-memory cache, forward-fill.

Trust boundary (OG-22): everything that comes from ``api.nbp.pl`` is **untrusted**. A response
passes a size limit, ``Decimal`` number parsing and shape validation with Pydantic models before
it is used. The client knows only the ``GET`` method and only the ``api.nbp.pl`` host over HTTPS
(OG-07, OG-12, FR-26).
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

NBP_HOST: Final = "api.nbp.pl"
BASE_URL: Final = f"https://{NBP_HOST}/api/"
NBP_TIMEZONE: Final = ZoneInfo("Europe/Warsaw")
EARLIEST_DATE: Final = date(2002, 1, 2)
"""Oldest exchange rate data in the NBP API (api.nbp.pl documentation)."""
MAX_QUERY_DAYS: Final = 93
"""A single series query must not span more than 93 days (api.nbp.pl documentation)."""
FORWARD_FILL_WINDOW_DAYS: Final = 14
"""How many days back to look for the last quote; the longest NBP holiday gaps are shorter."""
MAX_RESPONSE_BYTES: Final = 1_000_000
TIMEOUT: Final = httpx.Timeout(10.0, connect=5.0)
TTL_CURRENT_S: Final = 600.0
"""TTL of a window that includes today: Table A is published once a day, but not at a fixed hour."""
TTL_ARCHIVED_S: Final = 12 * 3600.0
MAX_CACHE_ENTRIES: Final = 128
MAX_PORTFOLIO_POSITIONS: Final = 50
GROSZ: Final = Decimal("0.01")
"""One grosz, the smallest PLN unit (0.01 PLN)."""

TABLE_A_CURRENCIES: Final = frozenset(
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
"""NBP Table A codes (as of 2026-10-05). A code outside this list is rejected before any NBP request."""
SUPPORTED_CURRENCIES: Final = TABLE_A_CURRENCIES | {"PLN"}

_DATE_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_AMOUNT_PATTERN: Final = re.compile(r"\d{1,15}(\.\d{1,4})?")


class ErrorCode(StrEnum):
    """Closed set of tool error reasons (FR-31)."""

    INVALID_CURRENCY = "invalid_currency"
    INVALID_DATE = "invalid_date"
    INVALID_AMOUNT = "invalid_amount"
    NO_QUOTE = "no_quote"
    NBP_UNAVAILABLE = "nbp_unavailable"
    INVALID_NBP_RESPONSE = "invalid_nbp_response"


class RateError(Exception):
    """Structured domain error; the server turns it into an MCP tool error."""

    def __init__(self, code: ErrorCode, reason: str) -> None:
        """Create an error with a code from the closed set and a human-readable reason."""
        super().__init__(reason)
        self.code = code
        self.reason = reason

    def to_json(self) -> str:
        """Return the error as JSON without numeric fields (KA-08.4)."""
        return json.dumps({"error": self.code.value, "reason": self.reason})


# --- input validation (UC-08 step 3, KA-08.3) --------------------------------------------------


def today() -> date:
    """Return the current date in the NBP timezone (Europe/Warsaw)."""
    return datetime.now(NBP_TIMEZONE).date()


def validate_currency(currency: str) -> str:
    """Normalize an ISO 4217 currency code and check it against Table A and PLN."""
    code = currency.strip().upper()
    if code not in SUPPORTED_CURRENCIES:
        raise RateError(
            ErrorCode.INVALID_CURRENCY,
            f"Currency {code[:8]!r} is not supported; allowed: PLN and NBP Table A codes.",
        )
    return code


def validate_date(value: str | None) -> date:
    """Check an ISO 8601 date (YYYY-MM-DD): not in the future, not older than NBP data."""
    if value is None or not value.strip():
        return today()
    text = value.strip()
    if not _DATE_PATTERN.fullmatch(text):
        raise RateError(ErrorCode.INVALID_DATE, "Date must have the format YYYY-MM-DD.")
    try:
        result = date.fromisoformat(text)
    except ValueError:
        raise RateError(ErrorCode.INVALID_DATE, f"No such day: {text}.") from None
    if result > today():
        raise RateError(ErrorCode.INVALID_DATE, f"Date {text} is in the future.")
    if result < EARLIEST_DATE:
        raise RateError(ErrorCode.INVALID_DATE, f"NBP publishes rates from {EARLIEST_DATE.isoformat()}.")
    return result


def validate_amount(amount: str) -> Decimal:
    """Parse a positive decimal amount from text (at most 15 integer digits and 4 after the dot)."""
    text = amount.strip()
    if not _AMOUNT_PATTERN.fullmatch(text):
        raise RateError(
            ErrorCode.INVALID_AMOUNT,
            "Amount must be a positive decimal number with a dot, e.g. '1250.50'.",
        )
    try:
        value = Decimal(text)
    except InvalidOperation:  # pragma: no cover - excluded by the pattern
        raise RateError(ErrorCode.INVALID_AMOUNT, "Amount is not a number.") from None
    if value <= 0:
        raise RateError(ErrorCode.INVALID_AMOUNT, "Amount must be greater than zero.")
    return value


def round_to_grosz(value: Decimal) -> Decimal:
    """Round to one grosz (0.01 PLN) with ROUND_HALF_UP (FR-01)."""
    return value.quantize(GROSZ, rounding=ROUND_HALF_UP)


# --- NBP response shape (OG-22) ----------------------------------------------------------------

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class _TableRate(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    code: CurrencyCode
    mid: Annotated[Decimal, Field(gt=0, lt=Decimal(1_000_000))]


class _NBPTable(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    table: Literal["A"]
    no: Annotated[str, StringConstraints(max_length=32, pattern=r"^[0-9]{1,3}/A/NBP/[0-9]{4}$")]
    effectiveDate: date
    rates: Annotated[list[_TableRate], Field(max_length=200)]


class _TablesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    tables: Annotated[list[_NBPTable], Field(max_length=MAX_QUERY_DAYS + 1)]


type QuoteWindow = dict[date, tuple[str, dict[str, Decimal]]]
"""Quote date -> (table number, currency code -> average rate)."""


@dataclass(slots=True)
class _CacheEntry:
    window: QuoteWindow
    fetched_at: float
    ttl: float

    def is_fresh(self, now: float) -> bool:
        return now - self.fetched_at < self.ttl


@dataclass(frozen=True, slots=True)
class RateResult:
    """A forward-filled rate with provenance metadata (FR-03)."""

    currency: str
    rate: Decimal
    requested_date: date
    quote_date: date
    table_number: str
    is_current: bool
    """``False`` when NBP did not respond and a stale cache entry was used."""


def _check_request(request: httpx.Request) -> None:
    """Security hook: GET only, HTTPS only, api.nbp.pl only (allowlist)."""
    if request.method != "GET" or request.url.scheme != "https" or request.url.host != NBP_HOST:
        raise httpx.UnsupportedProtocol(f"Blocked request {request.method} {request.url.host}")


def create_http_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """Create an HTTP client with a host allowlist, timeouts and no redirects."""

    async def hook(request: httpx.Request) -> None:
        _check_request(request)

    return httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={"Accept": "application/json", "User-Agent": "nbp-mcp-server/2.0"},
        event_hooks={"request": [hook]},
        transport=transport,
    )


@dataclass(slots=True)
class NBPClient:
    """Reads Table A rates with an in-process memory cache (OG-09) and forward-fill (FR-03).

    One request fetches a ``FORWARD_FILL_WINDOW_DAYS``-day window for **all** currencies at once
    (``/exchangerates/tables/a/{start}/{end}/``), so valuing a multi-currency portfolio costs one
    NBP call, and further questions about the same day are served from the cache.
    """

    http: httpx.AsyncClient
    clock: Callable[[], float] = time.monotonic
    request_count: int = 0
    _cache: OrderedDict[tuple[date, date], _CacheEntry] = field(default_factory=OrderedDict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def close(self) -> None:
        """Close HTTP connections."""
        await self.http.aclose()

    async def _fetch_window(self, start: date, end: date) -> QuoteWindow:
        """Fetch Table A for a date range; 404 means no quotes (not an error)."""
        if (end - start).days + 1 > MAX_QUERY_DAYS:  # pragma: no cover - the window constant is smaller
            raise ValueError("Range exceeds the NBP limit")
        path = f"exchangerates/tables/a/{start.isoformat()}/{end.isoformat()}/"
        self.request_count += 1
        log.info("NBP GET %s (request no. %d)", path, self.request_count)
        try:
            async with self.http.stream("GET", path, params={"format": "json"}) as response:
                if response.status_code == httpx.codes.NOT_FOUND:
                    return {}
                if response.status_code != httpx.codes.OK:
                    raise RateError(
                        ErrorCode.NBP_UNAVAILABLE, f"NBP responded with status {response.status_code}."
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise RateError(
                            ErrorCode.INVALID_NBP_RESPONSE, "NBP response exceeds the size limit."
                        )
        except httpx.HTTPError as error:
            raise RateError(
                ErrorCode.NBP_UNAVAILABLE, f"No connection to NBP ({type(error).__name__})."
            ) from None
        try:
            raw = json.loads(bytes(body), parse_float=Decimal)
            tables = _TablesResponse(tables=raw).tables
        except (ValueError, ValidationError):
            raise RateError(ErrorCode.INVALID_NBP_RESPONSE, "NBP response has an unexpected shape.") from None
        return {t.effectiveDate: (t.no, {r.code: r.mid for r in t.rates}) for t in tables}

    async def window_until(self, day: date) -> tuple[QuoteWindow, bool]:
        """Return the quote window ending on ``day`` and a data freshness flag."""
        key = (day - timedelta(days=FORWARD_FILL_WINDOW_DAYS - 1), day)
        async with self._lock:
            now = self.clock()
            entry = self._cache.get(key)
            if entry is not None and entry.is_fresh(now):
                self._cache.move_to_end(key)
                return entry.window, True
            try:
                window = await self._fetch_window(*key)
            except RateError as error:
                if entry is not None and error.code is ErrorCode.NBP_UNAVAILABLE:
                    log.warning("NBP unavailable, using stale cache entry %s", key)
                    return entry.window, False
                raise
            ttl = TTL_CURRENT_S if day >= today() else TTL_ARCHIVED_S
            self._cache[key] = _CacheEntry(window=window, fetched_at=now, ttl=ttl)
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)
            return window, True

    async def rate(self, currency: str, day: date) -> RateResult:
        """Average rate for ``day`` or for the last earlier quote (forward-fill).

        Raises:
            RateError: No quote in the window or an NBP failure, never a default value (FR-31).

        """
        if currency == "PLN":
            return RateResult("PLN", Decimal(1), day, day, "constant PLN = 1", is_current=True)
        window, current = await self.window_until(day)
        for quote_day in sorted(window, reverse=True):
            if quote_day > day:
                continue
            number, rates = window[quote_day]
            if (rate := rates.get(currency)) is not None:
                return RateResult(currency, rate, day, quote_day, number, current)
        raise RateError(
            ErrorCode.NO_QUOTE,
            f"No {currency} quote in NBP Table A on {day.isoformat()} "
            f"or in the {FORWARD_FILL_WINDOW_DAYS - 1} days before.",
        )
