#!/usr/bin/env python3
"""Bramka issue: sekcje szablonu, kryteria akceptacji sprawdzalne komendą, etykieta stanu.

Skrypt jest uniwersalny: nie zna nazwy repo, firmy, milestone'ów ani etykiet
projektowych. Wszystko, co zależy od repo, ustawia się konfiguracją (zob. niżej).
Działa na gołym Pythonie 3.12+ (tylko biblioteka standardowa) i nigdy nie
modyfikuje issue: tylko czyta dane wejściowe i zwraca kod wyjścia.

Wejście (dokładnie jedno z dwóch)
---------------------------------
* ``issue_check.py <plik.md>``: plik Markdown z opcjonalnym front matterem
  (``labels: [a, b]``, ``milestone: "..."``); do testów i pracy lokalnej.
* ``issue_check.py --event <zdarzenie.json>``: JSON zdarzenia GitHub ``issues``
  (w GitHub Actions to plik spod ``$GITHUB_EVENT_PATH``). Treść issue jest
  czytana z pliku jako dane, nigdy nie trafia do powłoki.

Konfiguracja (późniejsze źródło nadpisuje wcześniejsze)
-------------------------------------------------------
1. wartości domyślne (:class:`Konfiguracja`);
2. plik JSON: ``--config PLIK`` albo ``$ISSUE_CHECK_CONFIG``, a gdy żadne nie jest
   podane, ``.github/issue-check.json`` w bieżącym katalogu (jeśli istnieje);
3. zmienne środowiskowe ``ISSUE_CHECK_PREFIKS_STANU``,
   ``ISSUE_CHECK_WYMAGAJ_MILESTONE`` (``1``/``0``);
4. argumenty ``--prefiks-stanu``, ``--wymagaj-milestone``.

Kody wyjścia
------------
0
    Issue przechodzi wszystkie reguły.
1
    Issue łamie co najmniej jedną regułę (lista na stderr; w GitHub Actions
    również jako adnotacje ``::error::``).
2
    Błąd użycia (brak pliku, zły JSON, zła konfiguracja).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Final

#: Plik konfiguracji szukany domyślnie (względem bieżącego katalogu).
DOMYSLNY_PLIK_KONFIGURACJI: Final[Path] = Path(".github/issue-check.json")

#: Placeholder, którym GitHub wypełnia niewypełnione pole formularza.
PUSTA_ODPOWIEDZ: Final[str] = "_no response_"

#: Regexy są liniowe: kwantyfikatory zaborcze (``++``, ``*+``) i brak sąsiadujących
#: kwantyfikatorów o wspólnym alfabecie, bo treść issue to dane niezaufane (ReDoS).
#: Nagłówek sekcji: poziom 2 lub 3 (formularz GitHub renderuje ``###``); końcowe
#: spacje i dwukropek obcina kod (``strip``), nie regex.
WZ_NAGLOWEK: Final[re.Pattern[str]] = re.compile(r"^(#{2,3})\s++(.*)$")

#: Ogrodzenie bloku kodu ``` lub ~~~ z opcjonalną nazwą języka.
WZ_PLOT: Final[re.Pattern[str]] = re.compile(
    r"^\s*+(?P<plot>`{3,}+|~{3,}+)\s*+(?P<jezyk>[\w+-]*+)"
)

#: Oczekiwany wynik: ``<komenda>  # <opis>`` (co najmniej jedna spacja przed ``#``).
WZ_OCZEKIWANY: Final[re.Pattern[str]] = re.compile(r"\s#\s++\S")


class Kod(IntEnum):
    """Kody wyjścia procesu."""

    OK = 0
    NIEZGODNE = 1
    UZYCIE = 2


class Regula(StrEnum):
    """Nazwy reguł; każdą da się wyłączyć (``--wylacz``), co wykorzystuje test sabotażu."""

    SEKCJE = "sekcje"
    PUSTA_SEKCJA = "pusta-sekcja"
    DUPLIKAT = "duplikat"
    BLOK_KRYTERIOW = "blok-kryteriow"
    JEZYK_BLOKU = "jezyk-bloku"
    PLACEHOLDER = "placeholder"
    OCZEKIWANY_WYNIK = "oczekiwany-wynik"
    ETYKIETA_STANU = "etykieta-stanu"
    ETYKIETA_TYPU = "etykieta-typu"
    MILESTONE = "milestone"


class BladUzyciaError(Exception):
    """Błąd wejścia lub konfiguracji (kod wyjścia 2)."""


@dataclass(frozen=True, slots=True)
class Konfiguracja:
    """Ustawienia bramki; wartości domyślne pasują do ``.github/ISSUE_TEMPLATE/praca.yml``."""

    #: Nagłówki sekcji, które issue musi mieć (niepuste).
    sekcje: tuple[str, ...] = (
        "Cel",
        "Kontekst",
        "Zakres",
        "Poza zakresem",
        "Powiązane",
        "Kryteria akceptacji",
    )
    #: Sekcja z kryteriami akceptacji (musi być też w :attr:`sekcje`).
    sekcja_kryteriow: str = "Kryteria akceptacji"
    #: Dopuszczalne nazwy języka bloku z kryteriami.
    jezyki_bloku: tuple[str, ...] = ("bash", "sh")
    #: Teksty z szablonu, których nie wolno zostawić w kryteriach.
    placeholdery: tuple[str, ...] = ("<komenda>", "<oczekiwany wynik>")
    #: Prefiks etykiety stanu; issue ma mieć dokładnie jedną taką etykietę.
    prefiks_stanu: str = "state:"
    #: Czy wymagać etykiety stanu (``false`` wyłącza regułę).
    wymagaj_etykiety_stanu: bool = True
    #: Dozwolone etykiety typu; pusta krotka = reguła wyłączona, inaczej co najmniej jedna.
    etykiety_typu: tuple[str, ...] = ()
    #: Czy issue musi mieć milestone.
    wymagaj_milestone: bool = False

    @classmethod
    def z_mapy(
        cls, dane: Mapping[str, object], baza: Konfiguracja | None = None
    ) -> Konfiguracja:
        """Nałóż klucze z mapy (np. z pliku JSON) na ``baza``; nieznany klucz to błąd."""
        wynik = baza or cls()
        znane = {f.name: f for f in fields(cls)}
        zmiany: dict[str, object] = {}
        for klucz, wartosc in dane.items():
            if klucz.startswith("_"):  # komentarze w pliku JSON, np. "_opis"
                continue
            if klucz not in znane:
                raise BladUzyciaError(
                    f"nieznany klucz konfiguracji: {klucz!r} (znane: {', '.join(znane)})"
                )
            domyslna = getattr(wynik, klucz)
            match domyslna, wartosc:
                case bool(), bool():
                    zmiany[klucz] = wartosc
                case str(), str():
                    zmiany[klucz] = wartosc
                case tuple(), list() if all(isinstance(x, str) for x in wartosc):
                    zmiany[klucz] = tuple(wartosc)
                case _:
                    raise BladUzyciaError(
                        f"klucz {klucz!r}: oczekiwano typu {type(domyslna).__name__}, "
                        f"dostałem {type(wartosc).__name__}"
                    )
        nowa = replace(wynik, **zmiany)  # type: ignore[arg-type]
        if nowa.sekcja_kryteriow not in nowa.sekcje:
            raise BladUzyciaError(
                f"sekcja_kryteriow {nowa.sekcja_kryteriow!r} nie jest w 'sekcje'"
            )
        return nowa


@dataclass(frozen=True, slots=True)
class Issue:
    """Znormalizowane wejście walidatora."""

    body: str
    labels: tuple[str, ...]
    milestone: str | None
    zrodlo: str


@dataclass(frozen=True, slots=True)
class Naruszenie:
    """Pojedyncze niespełnione kryterium."""

    regula: Regula
    komunikat: str


@dataclass(slots=True)
class Wynik:
    """Zebrane naruszenia."""

    naruszenia: list[Naruszenie] = field(default_factory=list)

    @property
    def przechodzi(self) -> bool:
        """Czy issue przeszło wszystkie reguły."""
        return not self.naruszenia


# --------------------------------------------------------------------------- #
# Wejście
# --------------------------------------------------------------------------- #


def _skalar(wartosc: str) -> str:
    """Zdejmij cudzysłowy z prostego skalaru YAML."""
    if len(wartosc) >= 2 and wartosc[0] == wartosc[-1] and wartosc[0] in "\"'":
        return wartosc[1:-1]
    return wartosc


def rozdziel_front_matter(tekst: str) -> tuple[dict[str, str | list[str]], str]:
    """Oddziel prosty front matter (skalary, listy ``[a, b]`` i myślnikowe) od treści.

    Celowo bez PyYAML: bramka ma działać na gołym Pythonie.
    """
    linie = tekst.splitlines()
    if not linie or linie[0].strip() != "---":
        return {}, tekst
    koniec = next((i for i in range(1, len(linie)) if linie[i].strip() == "---"), None)
    if koniec is None:
        return {}, tekst
    dane: dict[str, str | list[str]] = {}
    klucz: str | None = None
    for linia in linie[1:koniec]:
        if not linia.strip():
            continue
        if linia.lstrip().startswith("-") and klucz is not None:
            lista = dane.setdefault(klucz, [])
            if isinstance(lista, list):
                lista.append(_skalar(linia.lstrip()[1:].strip()))
            continue
        nazwa, _, reszta = linia.partition(":")
        klucz, reszta = nazwa.strip(), reszta.strip()
        if reszta.startswith("[") and reszta.endswith("]"):
            dane[klucz] = [
                _skalar(c.strip()) for c in reszta[1:-1].split(",") if c.strip()
            ]
        else:
            dane[klucz] = _skalar(reszta) if reszta else []
    return dane, "\n".join(linie[koniec + 1 :])


def wczytaj_markdown(sciezka: Path) -> Issue:
    """Zbuduj :class:`Issue` z pliku Markdown z opcjonalnym front matterem."""
    meta, body = rozdziel_front_matter(sciezka.read_text(encoding="utf-8"))
    match meta.get("labels", []):
        case list() as lista:
            labels = tuple(lista)
        case str() as jedna if jedna:
            labels = (jedna,)
        case _:
            labels = ()
    milestone = meta.get("milestone")
    return Issue(
        body=body,
        labels=labels,
        milestone=milestone if isinstance(milestone, str) and milestone else None,
        zrodlo=str(sciezka),
    )


def wczytaj_zdarzenie(sciezka: Path) -> Issue:
    """Zbuduj :class:`Issue` z JSON-a zdarzenia GitHub ``issues`` (``$GITHUB_EVENT_PATH``)."""
    try:
        dane = json.loads(sciezka.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as blad:
        raise BladUzyciaError(f"{sciezka}: niepoprawny JSON: {blad}") from blad
    match dane:
        case {"issue": {"body": str() | None as body, **issue}}:
            pass
        case _:
            raise BladUzyciaError(
                f"{sciezka}: to nie jest zdarzenie 'issues' (brak issue.body)"
            )
    surowe = issue.get("labels")
    labels = tuple(
        str(e["name"])
        for e in (surowe if isinstance(surowe, list) else [])
        if isinstance(e, dict) and "name" in e
    )
    milestone = issue.get("milestone")
    tytul = milestone.get("title") if isinstance(milestone, dict) else None
    numer = issue.get("number")
    return Issue(
        body=body or "",
        labels=labels,
        milestone=str(tytul) if tytul else None,
        zrodlo=f"#{numer}" if isinstance(numer, int) else str(sciezka),
    )


# --------------------------------------------------------------------------- #
# Reguły
# --------------------------------------------------------------------------- #


def podziel_na_sekcje(body: str) -> tuple[dict[str, list[str]], list[str]]:
    """Zwróć mapę ``tytuł sekcji -> linie`` i listę powtórzonych nagłówków.

    Linie wewnątrz bloków kodu nigdy nie są nagłówkami.
    """
    sekcje: dict[str, list[str]] = {}
    duplikaty: list[str] = []
    biezaca: str | None = None
    w_bloku = False
    for linia in body.splitlines():
        if WZ_PLOT.match(linia):
            w_bloku = not w_bloku
        elif not w_bloku and (naglowek := WZ_NAGLOWEK.match(linia)):
            biezaca = naglowek.group(2).strip().rstrip(":").strip()
            if biezaca in sekcje and biezaca not in duplikaty:
                duplikaty.append(biezaca)
            sekcje.setdefault(biezaca, [])
            continue
        if biezaca is not None:
            sekcje[biezaca].append(linia)
    return sekcje, duplikaty


def _pusta(linie: Sequence[str]) -> bool:
    """Czy sekcja jest pusta (także gdy zawiera wyłącznie ``_No response_``)."""
    return not any(x.strip() and x.strip().lower() != PUSTA_ODPOWIEDZ for x in linie)


@dataclass(frozen=True, slots=True)
class BlokKodu:
    """Blok kodu z sekcji: nazwa języka i linie bez ogrodzeń."""

    jezyk: str
    linie: tuple[str, ...]


def bloki_kodu(linie: Sequence[str]) -> Iterator[BlokKodu]:
    """Wypluwaj kolejne bloki kodu z sekcji."""
    jezyk: str | None = None
    biezace: list[str] = []
    for linia in linie:
        if plot := WZ_PLOT.match(linia):
            if jezyk is None:
                jezyk, biezace = plot.group("jezyk").lower(), []
            else:
                yield BlokKodu(jezyk, tuple(biezace))
                jezyk = None
        elif jezyk is not None:
            biezace.append(linia)
    if jezyk is not None:
        yield BlokKodu(jezyk, tuple(biezace))


type Sprawdzacz = Callable[[Issue, Konfiguracja], Iterator[str]]


def _sekcje(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    sekcje, _ = podziel_na_sekcje(issue.body)
    for nazwa in k.sekcje:
        if nazwa not in sekcje:
            yield f"brak sekcji '{nazwa}' (nagłówek '## {nazwa}' lub '### {nazwa}')"


def _pusta_sekcja(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    sekcje, _ = podziel_na_sekcje(issue.body)
    for nazwa in k.sekcje:
        if nazwa in sekcje and _pusta(sekcje[nazwa]):
            yield f"sekcja '{nazwa}' jest pusta"


def _duplikat(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    _, duplikaty = podziel_na_sekcje(issue.body)
    for nazwa in duplikaty:
        yield f"sekcja '{nazwa}' występuje więcej niż raz"


def _bloki_kryteriow(issue: Issue, k: Konfiguracja) -> list[BlokKodu]:
    """Niepuste bloki kodu z sekcji kryteriów (pusta lista, gdy sekcji brak)."""
    sekcje, _ = podziel_na_sekcje(issue.body)
    linie = sekcje.get(k.sekcja_kryteriow, [])
    return [b for b in bloki_kodu(linie) if any(x.strip() for x in b.linie)]


def _komendy(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    for blok in _bloki_kryteriow(issue, k):
        yield from (x.strip() for x in blok.linie if x.strip())


def _blok_kryteriow(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    sekcje, _ = podziel_na_sekcje(issue.body)
    linie = sekcje.get(k.sekcja_kryteriow)
    if linie is not None and not _pusta(linie) and not _bloki_kryteriow(issue, k):
        yield (
            f"sekcja '{k.sekcja_kryteriow}' nie zawiera bloku kodu z komendami "
            "(opis słowny nie jest sprawdzalny)"
        )


def _jezyk_bloku(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    for blok in _bloki_kryteriow(issue, k):
        if blok.jezyk not in k.jezyki_bloku:
            yield (
                f"blok kryteriów ma język {blok.jezyk or '(brak)'!r}; "
                f"otwórz go jako ```{k.jezyki_bloku[0]}"
            )


def _placeholder(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    for linia in _komendy(issue, k):
        if any(p in linia for p in k.placeholdery):
            yield f"kryterium to niezmieniony placeholder z szablonu: {linia}"


def _oczekiwany_wynik(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    for linia in _komendy(issue, k):
        if not WZ_OCZEKIWANY.search(linia):
            yield f"kryterium bez oczekiwanego wyniku (dopisz '  # <oczekiwany wynik>'): {linia}"


def _etykieta_stanu(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    if not k.wymagaj_etykiety_stanu:
        return
    prefiks = k.prefiks_stanu.casefold()
    stany = [e for e in issue.labels if e.casefold().startswith(prefiks)]
    if puste := [e for e in stany if not e.casefold()[len(prefiks) :].strip()]:
        yield f"etykieta stanu bez nazwy po prefiksie: {', '.join(puste)}"
        return
    match stany:
        case [_]:
            pass
        case []:
            yield f"brak etykiety stanu (wymagana dokładnie jedna z prefiksem '{k.prefiks_stanu}')"
        case _:
            yield f"za dużo etykiet stanu ({', '.join(stany)}); wymagana dokładnie jedna"


def _etykieta_typu(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    if k.etykiety_typu and not set(issue.labels) & set(k.etykiety_typu):
        yield f"brak etykiety typu (jedna z: {', '.join(k.etykiety_typu)})"


def _milestone(issue: Issue, k: Konfiguracja) -> Iterator[str]:
    if k.wymagaj_milestone and not issue.milestone:
        yield "issue nie ma ustawionego milestone'a"


#: Rejestr reguł w kolejności raportowania.
REGULY: Final[Mapping[Regula, Sprawdzacz]] = {
    Regula.SEKCJE: _sekcje,
    Regula.PUSTA_SEKCJA: _pusta_sekcja,
    Regula.DUPLIKAT: _duplikat,
    Regula.BLOK_KRYTERIOW: _blok_kryteriow,
    Regula.JEZYK_BLOKU: _jezyk_bloku,
    Regula.PLACEHOLDER: _placeholder,
    Regula.OCZEKIWANY_WYNIK: _oczekiwany_wynik,
    Regula.ETYKIETA_STANU: _etykieta_stanu,
    Regula.ETYKIETA_TYPU: _etykieta_typu,
    Regula.MILESTONE: _milestone,
}


def sprawdz(
    issue: Issue,
    konfiguracja: Konfiguracja | None = None,
    wylaczone: frozenset[Regula] = frozenset(),
) -> Wynik:
    """Zastosuj wszystkie włączone reguły i zwróć zebrane naruszenia."""
    k = konfiguracja or Konfiguracja()
    wynik = Wynik()
    for regula, sprawdzacz in REGULY.items():
        if regula not in wylaczone:
            wynik.naruszenia.extend(Naruszenie(regula, m) for m in sprawdzacz(issue, k))
    return wynik


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _tak_nie(wartosc: str) -> bool:
    """Zamień ``1/0/true/false/tak/nie`` na bool."""
    match wartosc.strip().lower():
        case "1" | "true" | "tak" | "yes":
            return True
        case "0" | "false" | "nie" | "no" | "":
            return False
        case _:
            raise BladUzyciaError(f"oczekiwano 1/0, dostałem {wartosc!r}")


def zbuduj_konfiguracje(
    args: argparse.Namespace, srodowisko: Mapping[str, str]
) -> Konfiguracja:
    """Złóż konfigurację: domyślne < plik JSON < zmienne środowiskowe < argumenty."""
    k = Konfiguracja()
    jawny = args.config or srodowisko.get("ISSUE_CHECK_CONFIG")
    plik = Path(jawny) if jawny else DOMYSLNY_PLIK_KONFIGURACJI
    if plik.is_file():
        try:
            dane = json.loads(plik.read_text(encoding="utf-8"))
        except json.JSONDecodeError as blad:
            raise BladUzyciaError(f"{plik}: niepoprawny JSON: {blad}") from blad
        if not isinstance(dane, dict):
            raise BladUzyciaError(f"{plik}: oczekiwano obiektu JSON")
        k = Konfiguracja.z_mapy(dane, k)
    elif jawny:
        raise BladUzyciaError(f"nie ma pliku konfiguracji: {plik}")
    if (prefiks := srodowisko.get("ISSUE_CHECK_PREFIKS_STANU")) is not None:
        k = replace(k, prefiks_stanu=prefiks)
    if (milestone := srodowisko.get("ISSUE_CHECK_WYMAGAJ_MILESTONE")) is not None:
        k = replace(k, wymagaj_milestone=_tak_nie(milestone))
    if args.prefiks_stanu is not None:
        k = replace(k, prefiks_stanu=args.prefiks_stanu)
    if args.wymagaj_milestone:
        k = replace(k, wymagaj_milestone=True)
    if not k.prefiks_stanu:
        raise BladUzyciaError("prefiks etykiety stanu nie może być pusty")
    return k


def zabezpiecz_adnotacje(tekst: str) -> str:
    """Zakoduj tekst adnotacji ``::error::`` jak ``escapeData`` z ``@actions/core``.

    Komunikat zawiera fragmenty treści issue (dane niezaufane); bez kodowania
    znak nowej linii pozwoliłby wstrzyknąć własną komendę workflow.
    """
    return tekst.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _parser() -> argparse.ArgumentParser:
    """Zbuduj parser argumentów."""
    parser = argparse.ArgumentParser(
        prog="issue_check.py",
        description="Sprawdza issue: sekcje szablonu, kryteria akceptacji jako komendy "
        "z oczekiwanym wynikiem, dokładnie jedna etykieta stanu.",
    )
    wejscie = parser.add_mutually_exclusive_group(required=True)
    wejscie.add_argument("plik", nargs="?", type=Path, help="plik Markdown z issue")
    wejscie.add_argument("--event", type=Path, help="JSON zdarzenia GitHub 'issues'")
    parser.add_argument(
        "--config", help=f"plik JSON (domyślnie {DOMYSLNY_PLIK_KONFIGURACJI})"
    )
    parser.add_argument(
        "--prefiks-stanu", help="prefiks etykiety stanu (domyślnie 'state:')"
    )
    parser.add_argument(
        "--wymagaj-milestone", action="store_true", help="wymagaj milestone'a"
    )
    parser.add_argument(
        "--wylacz",
        action="append",
        default=[],
        choices=[r.value for r in Regula],
        help="wyłącz regułę (do testów sabotażu; nie używać w CI)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punkt wejścia CLI; zwraca kod wyjścia procesu."""
    args = _parser().parse_args(argv)
    try:
        konfiguracja = zbuduj_konfiguracje(args, os.environ)
        sciezka: Path = args.event or args.plik
        if not sciezka.is_file():
            raise BladUzyciaError(f"nie ma pliku: {sciezka}")
        issue = wczytaj_zdarzenie(sciezka) if args.event else wczytaj_markdown(sciezka)
    except BladUzyciaError as blad:
        print(f"błąd użycia: {blad}", file=sys.stderr)
        return Kod.UZYCIE

    wynik = sprawdz(issue, konfiguracja, frozenset(Regula(r) for r in args.wylacz))
    if wynik.przechodzi:
        print(f"{issue.zrodlo}: OK")
        return Kod.OK
    w_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    for n in wynik.naruszenia:
        tekst = f"{issue.zrodlo}: [{n.regula}] {n.komunikat}"
        print(
            f"::error::{zabezpiecz_adnotacje(tekst)}" if w_ci else tekst,
            file=sys.stderr,
        )
    return Kod.NIEZGODNE


if __name__ == "__main__":
    raise SystemExit(main())
