"""Smoke-test aplikacji Streamlit przez ``AppTest`` z zamockowanym NBP (offline)."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import nbp_convert
from nbp_convert import CENT, Conversion, NBPError

APP = Path(__file__).resolve().parent.parent / "app.py"
# Pierwsze AppTest w świeżym venv importuje Streamlit i pandas od zera (zmierzone: 22 s na laptopie,
# 5 s przy ciepłym starcie); 30 s dawało sporadyczny timeout, stąd zapas.
APPTEST_TIMEOUT = 120
MIDS = {"EUR": Decimal("4.25"), "USD": Decimal("3.70"), "CHF": Decimal("4.50")}


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Podmienia ``convert_to_pln`` na fake (GBP -> NBPError), ``ensure_cache`` na no-op i zwraca log wywołań."""
    log: list[str] = []

    def fake(
        amount: Decimal | int | str,
        currency: str,
        *,
        dzien: date | None = None,
        cache_path: Path = nbp_convert.DEFAULT_CACHE_PATH,
    ) -> Conversion:
        log.append(currency)
        if currency not in MIDS:
            raise NBPError(f"Brak kursu {currency}")
        mid = MIDS[currency]
        return Conversion((Decimal(amount) * mid).quantize(CENT, rounding=ROUND_HALF_UP), mid, date(2026, 9, 30), "1/A")

    monkeypatch.setattr(nbp_convert, "convert_to_pln", fake)
    monkeypatch.setattr(nbp_convert, "ensure_cache", lambda *args, **kwargs: 0)
    import streamlit as st

    st.cache_data.clear()
    return log


def test_app_runs_and_slider_does_not_refetch(calls: list[str]) -> None:
    at = AppTest.from_file(str(APP), default_timeout=APPTEST_TIMEOUT).run()
    assert not at.exception
    # 1000*4.25 + 500*3.70 + 200*4.50 = 7000.00 (GBP brak kursu -> błąd, pominięty)
    assert at.metric[0].value == "7,000.00"
    assert any("GBP" in e.value for e in at.error)
    n = len(calls)
    at.slider[0].set_value(10.0).run()
    assert not at.exception
    assert at.metric[0].value == "7,700.00"
    assert len(calls) == n  # brak dodatkowych requestów przy ruchu suwaka


def test_app_survives_cache_update_failure(calls: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object, **kwargs: object) -> int:
        raise NBPError("API niedostępne")

    monkeypatch.setattr(nbp_convert, "ensure_cache", broken)
    at = AppTest.from_file(str(APP), default_timeout=APPTEST_TIMEOUT).run()
    assert not at.exception
    assert any("cache" in w.value for w in at.warning)
    assert at.metric[0].value == "7,000.00"  # odczyty z (zamockowanego) cache'u działają dalej
