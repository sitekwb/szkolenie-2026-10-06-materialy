"""Scenariusz „what-if": wartość portfela walutowego w PLN przy kursach przesuniętych o zadany procent.

Moduł jest czysty (bez UI). Sieć dotyka wyłącznie przez wstrzykiwalny ``fetch``
(domyślnie ``nbp_convert.convert_to_pln``), wołany raz na walutę.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.error import URLError

from nbp_convert import CENT, Conversion, NBPError, convert_to_pln

type Fetch = Callable[..., Conversion]

PLN = "PLN"
HUNDRED = Decimal(100)
MAX_PCT = Decimal(1000)  # górny „rozsądny" limit przesunięcia
_CODE_RE = re.compile(r"[A-Z]{3}")


def _require_decimal(value: object, name: str) -> Decimal:
    """Sprawdza, że ``value`` to skończony ``Decimal`` (``float`` odrzucany).

    Raises:
        TypeError: Gdy ``value`` nie jest ``Decimal``.
        ValueError: Gdy ``value`` nie jest skończone.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} musi być Decimal, a nie {type(value).__name__}.")
    if not value.is_finite():
        raise ValueError(f"{name} musi być skończone: {value!r}.")
    return value


def validate_pct(pct: Decimal) -> Decimal:
    """Waliduje procent przesunięcia kursu.

    Args:
        pct: Zmiana kursu w procentach, np. ``Decimal("10")`` = +10%.

    Returns:
        Ten sam ``pct`` po walidacji.

    Raises:
        TypeError: Gdy ``pct`` nie jest ``Decimal``.
        ValueError: Gdy ``pct`` nie jest skończone, ``<= -100`` lub ``> MAX_PCT``.
    """
    _require_decimal(pct, "pct")
    if pct <= -HUNDRED:
        raise ValueError("Zmiana kursu musi być większa niż -100% (kurs musi pozostać dodatni).")
    if pct > MAX_PCT:
        raise ValueError(f"Zmiana kursu nie może przekraczać {MAX_PCT}%.")
    return pct


@dataclass(frozen=True, slots=True)
class Holding:
    """Pozycja portfela.

    Attributes:
        code: Kod waluty ISO 4217 (normalizowany do wielkich liter).
        amount: Kwota w walucie (``Decimal``, nieujemna).
    """

    code: str
    amount: Decimal

    def __post_init__(self) -> None:
        """Normalizuje kod i waliduje kwotę.

        Raises:
            TypeError: Gdy kwota nie jest ``Decimal``.
            ValueError: Dla złego kodu waluty lub kwoty ujemnej/nieskończonej.
        """
        code = self.code.strip().upper()
        if not _CODE_RE.fullmatch(code):
            raise ValueError(f"Niepoprawny kod waluty: {self.code!r}.")
        if _require_decimal(self.amount, "amount") < 0:
            raise ValueError(f"Kwota nie może być ujemna: {self.amount}.")
        object.__setattr__(self, "code", code)


@dataclass(frozen=True, slots=True)
class BaseRate:
    """Kurs bazowy NBP dla waluty.

    Attributes:
        code: Kod waluty.
        mid: Kurs średni (PLN za 1 jednostkę).
        effective_date: Data publikacji kursu.
        table_no: Numer tabeli NBP.
    """

    code: str
    mid: Decimal
    effective_date: date
    table_no: str


@dataclass(frozen=True, slots=True)
class RatesLoad:
    """Wynik pobierania kursów: częściowe kursy oraz błędy per waluta.

    Attributes:
        rates: Kursy, które udało się pobrać, wg kodu waluty.
        errors: Komunikaty błędów (PL) wg kodu waluty.
    """

    rates: dict[str, BaseRate] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScenarioLine:
    """Wiersz scenariusza dla jednej waluty.

    Attributes:
        code: Kod waluty.
        amount: Kwota w walucie.
        base_mid: Kurs bazowy (dla PLN ``1``).
        shifted_mid: Kurs po przesunięciu (niezaokrąglony; dla PLN ``1``).
        base_value: Wartość w PLN po kursie bazowym (do groszy).
        value: Wartość w PLN po kursie przesuniętym (do groszy).
    """

    code: str
    amount: Decimal
    base_mid: Decimal
    shifted_mid: Decimal
    base_value: Decimal
    value: Decimal


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Podsumowanie scenariusza.

    Attributes:
        lines: Wiersze per waluta (bez walut, dla których brak kursu).
        total: Suma wartości po przesunięciu (suma zaokrąglonych pozycji).
        base_total: Suma wartości bazowych.
        delta_pln: ``total - base_total``.
        delta_pct: Zmiana procentowa (do 0.01 pp.) lub ``None`` przy zerowej bazie.
        missing: Waluty pominięte z braku kursu.
    """

    lines: tuple[ScenarioLine, ...]
    total: Decimal
    base_total: Decimal
    delta_pln: Decimal
    delta_pct: Decimal | None
    missing: tuple[str, ...]


def merge_holdings(holdings: Iterable[Holding]) -> list[Holding]:
    """Scala duplikaty walut, sumując kwoty (kolejność pierwszego wystąpienia).

    Args:
        holdings: Pozycje portfela.

    Returns:
        Lista pozycji z unikalnymi kodami walut.
    """
    totals: dict[str, Decimal] = {}
    for h in holdings:
        totals[h.code] = totals.get(h.code, Decimal(0)) + h.amount
    return [Holding(code, amount) for code, amount in totals.items()]


def load_base_rates(holdings: Iterable[Holding], *, fetch: Fetch = convert_to_pln) -> RatesLoad:
    """Pobiera kursy bazowe — jedno wywołanie ``fetch`` na walutę (PLN pomijany).

    Błędy pojedynczej waluty (``NBPError``, ``URLError``, ``InvalidOperation``,
    ``ValueError``, ``TimeoutError``) są zbierane i nie przerywają pobierania pozostałych.

    Args:
        holdings: Pozycje portfela (duplikaty są scalane).
        fetch: Funkcja o kontrakcie ``convert_to_pln(amount, currency)``.

    Returns:
        ``RatesLoad`` z częściowymi kursami i błędami.
    """
    result = RatesLoad()
    for h in merge_holdings(holdings):
        if h.code == PLN:
            continue
        try:
            conv = fetch(h.amount, h.code)
        except (NBPError, URLError, InvalidOperation, ValueError, TimeoutError) as exc:
            result.errors[h.code] = f"Nie udało się pobrać kursu {h.code}: {exc}"
            continue
        result.rates[h.code] = BaseRate(h.code, conv.mid, conv.effective_date, conv.table_no)
    return result


def _to_cent(value: Decimal) -> Decimal:
    """Zaokrągla do groszy half-up (jak ``convert_to_pln``)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def shifted_value(holding: Holding, rate: BaseRate, pct: Decimal) -> Decimal:
    """Wartość pozycji w PLN przy kursie przesuniętym o ``pct`` procent.

    Przy ``pct == 0`` wynik jest identyczny z ``Conversion.amount_pln``.

    Args:
        holding: Pozycja portfela.
        rate: Kurs bazowy tej waluty.
        pct: Zmiana kursu w procentach (``Decimal``).

    Returns:
        Kwota w PLN zaokrąglona do groszy (half-up).

    Raises:
        TypeError: Gdy ``pct`` nie jest ``Decimal``.
        ValueError: Dla niepoprawnego ``pct`` lub niezgodnej waluty.
    """
    validate_pct(pct)
    if holding.code != rate.code:
        raise ValueError(f"Kurs {rate.code} nie pasuje do pozycji {holding.code}.")
    return _to_cent(holding.amount * rate.mid * (1 + pct / HUNDRED))


def scenario(holdings: Iterable[Holding], rates: Mapping[str, BaseRate], pct: Decimal) -> ScenarioResult:
    """Liczy wartość portfela w PLN przy kursach przesuniętych o ``pct``.

    PLN wchodzi 1:1 i nie podlega przesunięciu. Waluty bez kursu trafiają do ``missing``.

    Args:
        holdings: Pozycje portfela (duplikaty są scalane).
        rates: Kursy bazowe wg kodu waluty.
        pct: Zmiana kursów w procentach (``Decimal``).

    Returns:
        ``ScenarioResult`` z wierszami i sumami.
    """
    validate_pct(pct)
    factor = 1 + pct / HUNDRED
    lines: list[ScenarioLine] = []
    missing: list[str] = []
    for h in merge_holdings(holdings):
        if h.code == PLN:
            pln = _to_cent(h.amount)
            lines.append(ScenarioLine(PLN, h.amount, Decimal(1), Decimal(1), pln, pln))
        elif (rate := rates.get(h.code)) is None:
            missing.append(h.code)
        else:
            lines.append(
                ScenarioLine(
                    code=h.code,
                    amount=h.amount,
                    base_mid=rate.mid,
                    shifted_mid=rate.mid * factor,
                    base_value=shifted_value(h, rate, Decimal(0)),
                    value=shifted_value(h, rate, pct),
                )
            )
    total = sum((line.value for line in lines), Decimal("0.00"))
    base_total = sum((line.base_value for line in lines), Decimal("0.00"))
    delta = total - base_total
    delta_pct = _to_cent(delta / base_total * HUNDRED) if base_total else None
    return ScenarioResult(tuple(lines), total, base_total, delta, delta_pct, tuple(missing))
