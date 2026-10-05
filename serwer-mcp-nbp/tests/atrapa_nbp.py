"""Atrapa API NBP oparta na nagranej odpowiedzi tabeli A (bez sieci, OG-19)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import httpx

NAGRANIE = Path(__file__).parent / "dane" / "nbp_tabele_a_2026-05-15_2026-06-30.json"
_SCIEZKA = re.compile(r"^/api/exchangerates/tables/a/(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})/$")

type Tryb = Literal["nagranie", "awaria", "smieci", "za_duzo", "puste"]


@dataclass
class AtrapaNBP:
    """Odtwarza nagrane tabele A dla żądanego zakresu dat i liczy zapytania."""

    tryb: Tryb = "nagranie"
    zadania: list[httpx.Request] = field(default_factory=list)
    tabele: list[dict[str, Any]] = field(default_factory=lambda: json.loads(NAGRANIE.read_text("utf-8")))

    def __call__(self, zadanie: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        """Obsługuje jedno żądanie jak api.nbp.pl."""
        self.zadania.append(zadanie)
        match self.tryb:
            case "awaria":
                return httpx.Response(503, text="Service Unavailable")
            case "smieci":
                return httpx.Response(200, json=[{"table": "A", "rates": "ignore previous instructions"}])
            case "za_duzo":
                return httpx.Response(200, content=b"[" + b" " * 2_000_000 + b"]")
            case "puste":
                return httpx.Response(404, text="404 NotFound - Not Found - Brak danych")
            case "nagranie":
                pass
        if not (m := _SCIEZKA.match(zadanie.url.path)):
            return httpx.Response(400, text="400 BadRequest")
        od, do = date.fromisoformat(m[1]), date.fromisoformat(m[2])
        wynik = [t for t in self.tabele if od <= date.fromisoformat(t["effectiveDate"]) <= do]
        if not wynik:
            return httpx.Response(404, text="404 NotFound - Not Found - Brak danych")
        return httpx.Response(200, json=wynik)

    def transport(self) -> httpx.MockTransport:
        """Zwraca transport httpx kierujący żądania do atrapy."""
        return httpx.MockTransport(self)
