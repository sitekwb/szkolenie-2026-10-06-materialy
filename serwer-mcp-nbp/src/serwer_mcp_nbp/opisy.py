"""Opisy narzędzi MCP jako wersjonowany artefakt (REQ-29).

Agent czyta te opisy jak polecenie, więc każda zmiana treści jest zmianą zachowania systemu.
Zmiana dowolnego opisu (także opisu pola, limitu lub typu parametru w ``serwer.py``) wymaga
podbicia ``WERSJA_OPISOW`` i dopisania skrótu SHA-256 manifestu ``tools/list`` do
``tests/test_opisy.py`` — inaczej test przeglądowy pada.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final

WERSJA_OPISOW: Final = "1.1.0"

INSTRUKCJE_SERWERA: Final = (
    "Serwer udostępnia wyłącznie odczyt kursów średnich NBP (tabela A, api.nbp.pl). "
    "Nie ma narzędzi zmieniających stan. Kwoty i kursy są zwracane jako tekst dziesiętny, "
    "żeby nie tracić precyzji. Błąd narzędzia oznacza brak danych: nie zastępuj go własną liczbą."
)

KURS_NBP: Final = (
    "Zwraca kurs średni NBP (tabela A) waluty obcej względem PLN na wskazany dzień. "
    "Gdy w danym dniu nie było notowania (weekend, święto), zwraca ostatnie wcześniejsze "
    "notowanie i podaje jego faktyczną datę w polu data_notowania. "
    "Parametry: waluta - kod ISO 4217 z tabeli A (np. EUR, USD, CHF); "
    "data - dzień w formacie RRRR-MM-DD, nie z przyszłości; pominięta oznacza dzisiaj. "
    "Gdy kursu nie ma, narzędzie zwraca błąd z powodem, nigdy wartość domyślną."
)

PRZELICZ_NA_PLN: Final = (
    "Przelicza kwotę w walucie obcej na PLN po kursie średnim NBP (tabela A) z danego dnia, "
    "z forward-fillem dla dni bez notowania. Zaokrąglenie ROUND_HALF_UP do grosza. "
    "Parametry: kwota - dodatnia liczba dziesiętna jako tekst, np. '1250.50'; waluta - kod ISO 4217 "
    "z tabeli A albo PLN; data - RRRR-MM-DD, nie z przyszłości; pominięta oznacza dzisiaj."
)

WARTOSC_PORTFELA: Final = (
    "Wycenia w PLN portfel podany w argumencie (lista pozycji: waluta i kwota) po kursach "
    "średnich NBP (tabela A) z danego dnia, z forward-fillem. Zwraca wycenę każdej pozycji "
    "i sumę. Serwer nie przechowuje żadnego portfela. Gdy dla którejkolwiek waluty brak kursu, "
    "całe narzędzie zwraca błąd zamiast sumy częściowej. Kwoty dodatnie, najwyżej 50 pozycji."
)

OPISY: Final[dict[str, str]] = {
    "instrukcje": INSTRUKCJE_SERWERA,
    "kurs_nbp": KURS_NBP,
    "przelicz_na_pln": PRZELICZ_NA_PLN,
    "wartosc_portfela": WARTOSC_PORTFELA,
}


def skrot_opisow(opisy: dict[str, str] | None = None) -> str:
    """Zwraca SHA-256 kanonicznej postaci JSON opisów narzędzi.

    Args:
        opisy: Słownik opisów; domyślnie bieżące ``OPISY``.

    Returns:
        Skrót szesnastkowy, przypięty w teście przeglądowym dla danej ``WERSJA_OPISOW``.

    """
    dane = json.dumps(opisy if opisy is not None else OPISY, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(dane.encode("utf-8")).hexdigest()
