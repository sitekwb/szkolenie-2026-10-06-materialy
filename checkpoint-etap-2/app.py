"""Panel Streamlit „what-if": wartość portfela walutowego w PLN przy kursach NBP przesuniętych o X%.

Kursy bazowe pochodzą z lokalnego cache'u SQLite tabel A NBP (``nbp_convert``),
uzupełnianego o brakujące dni przy starcie (najwyżej raz na godzinę).

Uruchomienie: ``streamlit run app.py``.
"""

from __future__ import annotations

import sqlite3
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.error import URLError

import pandas as pd
import streamlit as st

import nbp_convert
from nbp_convert import CENT, Conversion, NBPError, yesterday_warsaw
from wallet_scenario import Holding, load_base_rates, scenario

DEFAULT_PORTFOLIO = pd.DataFrame(
    {"Waluta": ["EUR", "USD", "CHF", "GBP"], "Kwota": ["1000", "500", "200", "300"]}
)


@st.cache_data(ttl=3600, show_spinner="Aktualizuję cache kursów NBP…")
def ensure_rates() -> str | None:
    """Uzupełnia lokalny cache kursów NBP o brakujące dni — najwyżej raz na godzinę.

    Błąd też jest cache'owany (jako komunikat), by każdy rerun nie ponawiał requestów;
    ponowienie wymusza przycisk „Odśwież kursy".

    Returns:
        ``None`` przy powodzeniu albo komunikat błędu.
    """
    try:
        nbp_convert.ensure_cache()
    except (NBPError, URLError, TimeoutError, OSError, sqlite3.Error) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


@st.cache_data(ttl=3600)
def unit_conversion(code: str) -> Conversion | str:
    """Czyta kurs dla 1 jednostki waluty z lokalnego cache'u SQLite (bez sieci).

    Wynik jest cache'owany w pamięci, by ruch suwaka nie czytał dysku; błąd też
    (jako komunikat) — ponowienie wymusza przycisk „Odśwież kursy".

    Args:
        code: Kod waluty (wielkie litery).

    Returns:
        ``Conversion`` dla kwoty 1 albo komunikat błędu.
    """
    try:
        return nbp_convert.convert_to_pln(Decimal(1), code)
    except (NBPError, URLError, TimeoutError, InvalidOperation, ValueError) as exc:
        return f"{type(exc).__name__}: {exc}"


def cached_fetch(amount: Decimal | int | str, currency: str, *, timeout: float = 10.0) -> Conversion:
    """Adapter o kontrakcie ``fetch`` z ``wallet_scenario`` korzystający z cache kursu jednostkowego.

    Args:
        amount: Kwota w walucie.
        currency: Kod waluty.
        timeout: Ignorowany (zachowany dla zgodności sygnatury).

    Returns:
        ``Conversion`` przeliczony lokalnie z kursu z cache.
    """
    unit = unit_conversion(currency)
    if isinstance(unit, str):
        raise NBPError(unit)
    pln = (Decimal(amount) * unit.mid).quantize(CENT, rounding=ROUND_HALF_UP)
    return Conversion(pln, unit.mid, unit.effective_date, unit.table_no)


def parse_holdings(frame: pd.DataFrame) -> tuple[list[Holding], list[str]]:
    """Zamienia tabelę z edytora na pozycje portfela, zbierając błędy wierszy.

    Args:
        frame: Ramka z kolumnami ``Waluta`` i ``Kwota`` (tekst).

    Returns:
        Para (poprawne pozycje, komunikaty błędów po polsku).
    """
    holdings: list[Holding] = []
    errors: list[str] = []
    for i, row in enumerate(frame.itertuples(index=False), start=1):
        code, raw = row.Waluta, row.Kwota
        if pd.isna(code) and pd.isna(raw):
            continue
        try:
            holdings.append(Holding(str(code), Decimal(str(raw).strip().replace(",", "."))))
        except (InvalidOperation, ValueError, TypeError) as exc:
            errors.append(f"Wiersz {i}: pominięty ({exc})")
    return holdings, errors


st.set_page_config(page_title="What-if kursów NBP", layout="wide")
st.title("Portfel walutowy — scenariusz zmiany kursów")

with st.sidebar:
    st.header("Portfel")
    edited = st.data_editor(
        DEFAULT_PORTFOLIO,
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "Waluta": st.column_config.TextColumn("Waluta", max_chars=3, validate=r"^[A-Za-z]{3}$", required=True),
            "Kwota": st.column_config.TextColumn("Kwota", required=True),
        },
        key="portfolio",
    )

if st.sidebar.button("Odśwież kursy NBP"):
    ensure_rates.clear()
    unit_conversion.clear()

if (cache_error := ensure_rates()) is not None:
    st.warning(f"Nie udało się zaktualizować cache'u kursów — używam danych lokalnych: {cache_error}")

pct_float = st.slider("Zmiana kursów [%]", -30.0, 30.0, 0.0, step=0.5)
pct = Decimal(str(pct_float))

holdings, row_errors = parse_holdings(edited)
for msg in row_errors:
    st.warning(msg)

loaded = load_base_rates(holdings, fetch=cached_fetch)
for msg in loaded.errors.values():
    st.error(msg + " — pozycja pominięta w sumie.")

result = scenario(holdings, loaded.rates, pct)

c1, c2, c3 = st.columns(3)
c1.metric("Wartość portfela (PLN)", f"{result.total:,.2f}", delta=f"{result.delta_pln:+,.2f} PLN")
c2.metric("Wartość bazowa (PLN)", f"{result.base_total:,.2f}")
c3.metric(
    "Zmiana",
    "—" if result.delta_pct is None else f"{result.delta_pct:+.2f}%",
    delta=None if result.delta_pct is None else f"{result.delta_pct:+.2f}%",
)

st.dataframe(
    pd.DataFrame(
        [
            {
                "Waluta": line.code,
                "Kwota": str(line.amount),
                "Kurs bazowy": str(line.base_mid),
                "Kurs po zmianie": str(line.shifted_mid.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
                "Wartość bazowa (PLN)": str(line.base_value),
                "Wartość (PLN)": str(line.value),
            }
            for line in result.lines
        ]
    ),
    hide_index=True,
)

if loaded.rates:
    sources = sorted({(r.effective_date, r.table_no) for r in loaded.rates.values()})
    st.caption(
        "Kursy bazowe: średnie NBP (tabela A, z lokalnego cache'u) — "
        + "; ".join(f"tabela {no} z {d.isoformat()}" for d, no in sources)
        + f". Kurs z dnia nie późniejszego niż {yesterday_warsaw().isoformat()} (wczoraj, czas polski), "
        "więc nie jest to kurs bieżący. PLN liczony 1:1, bez przesunięcia."
    )
elif not holdings:
    st.info("Portfel jest pusty — dodaj pozycje w tabeli po lewej.")
