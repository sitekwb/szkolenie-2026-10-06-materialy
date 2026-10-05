"""Testy offline logiki scenariusza portfela (fake ``fetch`` zamiast API NBP)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.error import URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nbp_convert import CENT, Conversion, NBPError  # noqa: E402
from wallet_scenario import (  # noqa: E402
    BaseRate,
    Holding,
    load_base_rates,
    merge_holdings,
    scenario,
    shifted_value,
)

MIDS: dict[str, Decimal] = {
    "EUR": Decimal("4.2617"),
    "USD": Decimal("3.6873"),
    "CHF": Decimal("4.5711"),
    "GBP": Decimal("4.9123"),
}
EFF = date(2026, 9, 30)
TABLE = "190/A/NBP/2026"


def fake_fetch(fail: dict[str, Exception] | None = None) -> tuple[Callable[..., Conversion], list[str]]:
    """Zwraca fałszywy ``fetch`` naśladujący ``convert_to_pln`` oraz log wywołań."""
    calls: list[str] = []

    def fetch(amount: Decimal | int | str, currency: str, *, timeout: float = 10.0) -> Conversion:
        calls.append(currency)
        if fail and currency in fail:
            raise fail[currency]
        mid = MIDS[currency]
        return Conversion((Decimal(amount) * mid).quantize(CENT, rounding=ROUND_HALF_UP), mid, EFF, TABLE)

    return fetch, calls


def portfolio() -> list[Holding]:
    return [Holding("EUR", Decimal("1000")), Holding("USD", Decimal("500.55")), Holding("PLN", Decimal("100"))]


def test_pct_zero_equals_convert_to_pln() -> None:
    fetch, _ = fake_fetch()
    loaded = load_base_rates(portfolio(), fetch=fetch)
    result = scenario(portfolio(), loaded.rates, Decimal("0"))
    by_code = {line.code: line for line in result.lines}
    for h in portfolio():
        if h.code != "PLN":
            assert by_code[h.code].value == fetch(h.amount, h.code).amount_pln
    assert result.delta_pln == Decimal("0.00")


def test_pct_plus_ten_hand_computed() -> None:
    fetch, _ = fake_fetch()
    loaded = load_base_rates(portfolio(), fetch=fetch)
    result = scenario(portfolio(), loaded.rates, Decimal("10"))
    by_code = {line.code: line for line in result.lines}
    # 1000 * 4.2617 * 1.1 = 4687.87 ; 500.55 * 3.6873 * 1.1 = 2030.2503... -> 2030.25
    assert by_code["EUR"].value == Decimal("4687.87")
    assert by_code["USD"].value == Decimal("2030.25")
    assert by_code["PLN"].value == Decimal("100.00")
    assert result.total == Decimal("6818.12")
    # baza: 4261.70 + 1845.68 + 100.00
    assert result.base_total == Decimal("6207.38")
    assert result.delta_pln == Decimal("610.74")


def test_pln_pass_through_without_fetch() -> None:
    fetch, calls = fake_fetch()
    holdings = [Holding("pln", Decimal("123.456"))]
    loaded = load_base_rates(holdings, fetch=fetch)
    assert calls == [] and loaded.rates == {} and loaded.errors == {}
    result = scenario(holdings, loaded.rates, Decimal("25"))
    assert result.total == Decimal("123.46") == result.base_total


def test_one_currency_error_does_not_block_others() -> None:
    fetch, _ = fake_fetch({"USD": NBPError("brak"), "CHF": URLError("offline")})
    holdings = [Holding("EUR", Decimal("1")), Holding("USD", Decimal("1")), Holding("CHF", Decimal("1"))]
    loaded = load_base_rates(holdings, fetch=fetch)
    assert set(loaded.rates) == {"EUR"}
    assert set(loaded.errors) == {"USD", "CHF"}
    result = scenario(holdings, loaded.rates, Decimal("0"))
    assert [line.code for line in result.lines] == ["EUR"]
    assert result.missing == ("USD", "CHF")
    assert result.total == Decimal("4.26")


def test_float_rejected() -> None:
    rate = BaseRate("EUR", MIDS["EUR"], EFF, TABLE)
    with pytest.raises(TypeError):
        shifted_value(Holding("EUR", Decimal("1")), rate, 10.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Holding("EUR", 1.5)  # type: ignore[arg-type]


@pytest.mark.parametrize("pct", ["-100", "-150", "NaN", "Infinity"])
def test_invalid_pct_rejected(pct: str) -> None:
    rate = BaseRate("EUR", MIDS["EUR"], EFF, TABLE)
    with pytest.raises(ValueError):
        shifted_value(Holding("EUR", Decimal("1")), rate, Decimal(pct))


def test_empty_portfolio() -> None:
    fetch, calls = fake_fetch()
    loaded = load_base_rates([], fetch=fetch)
    result = scenario([], loaded.rates, Decimal("5"))
    assert calls == [] and result.lines == ()
    assert result.total == result.base_total == Decimal("0.00")
    assert result.delta_pct is None


def test_duplicates_merged_single_fetch() -> None:
    fetch, calls = fake_fetch()
    holdings = [Holding("eur", Decimal("100")), Holding("EUR", Decimal("50")), Holding("USD", Decimal("1"))]
    assert merge_holdings(holdings) == [Holding("EUR", Decimal("150")), Holding("USD", Decimal("1"))]
    load_base_rates(holdings, fetch=fetch)
    assert calls == ["EUR", "USD"]


def test_invalid_holding() -> None:
    with pytest.raises(ValueError):
        Holding("EU", Decimal("1"))
    with pytest.raises(ValueError):
        Holding("EUR", Decimal("-1"))
