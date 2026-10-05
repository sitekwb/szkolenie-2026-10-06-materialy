"""Testy bramki issue: dobre przykłady przechodzą, złe padają, a testy same da się obalić.

Uruchamianie z korzenia repo docelowego (albo z dowolnego katalogu)::

    python3 -m pytest tests -q

Testy nie potrzebują sieci, ``gh`` ani zależności poza ``pytest``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

KORZEN = Path(__file__).resolve().parent.parent
SKRYPT = KORZEN / "scripts" / "issue_check.py"
ISSUES = Path(__file__).resolve().parent / "issues"
ZDARZENIA = Path(__file__).resolve().parent / "zdarzenia"
SZABLON = KORZEN / ".github" / "ISSUE_TEMPLATE" / "praca.yml"
WORKFLOW = KORZEN / ".github" / "workflows" / "issue-check.yml"


def _zaladuj() -> ModuleType:
    """Załaduj skrypt jako moduł (nie jest pakietem, więc przez ``importlib``)."""
    spec = importlib.util.spec_from_file_location("issue_check", SKRYPT)
    assert spec is not None
    assert spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules["issue_check"] = modul
    spec.loader.exec_module(modul)
    return modul


ic = _zaladuj()

#: Każdy zły przykład łamie dokładnie jedną regułę. Mapa służy testowi sabotażu.
ZLE: dict[str, str] = {
    "zly-brak-sekcji-kryteriow.md": "sekcje",
    "zly-kryterium-opisowe.md": "blok-kryteriow",
    "zly-brak-oczekiwanego-wyniku.md": "oczekiwany-wynik",
    "zly-dwie-etykiety-stanu.md": "etykieta-stanu",
    "zly-zero-etykiet-stanu.md": "etykieta-stanu",
    "zly-pusta-sekcja.md": "pusta-sekcja",
    "zly-blok-bez-bash.md": "jezyk-bloku",
    "zly-placeholder-z-szablonu.md": "placeholder",
    "zly-duplikat-sekcji.md": "duplikat",
    "zly-wstrzykniecie-powloki.md": "oczekiwany-wynik",
}
DOBRE: tuple[str, ...] = ("dobry.md", "dobry-z-formularza.md")


def uruchom(
    *argumenty: str | Path, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Uruchom skrypt jako osobny proces, tak jak robi to workflow."""
    srodowisko = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ISSUE_CHECK_", "GITHUB_"))
    }
    srodowisko |= env or {}
    return subprocess.run(
        [sys.executable, str(SKRYPT), *map(str, argumenty)],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd or KORZEN,
        env=srodowisko,
    )


@pytest.fixture
def pusty_katalog(tmp_path: Path) -> Path:
    """Katalog roboczy bez ``.github/issue-check.json`` (domyślna konfiguracja)."""
    return tmp_path


# --------------------------------------------------------------------------- #
# Przykłady dobre i złe
# --------------------------------------------------------------------------- #


def test_mapa_obejmuje_wszystkie_zle_przyklady() -> None:
    """Każdy plik ``zly-*.md`` ma przypisaną regułę i jest ich co najmniej sześć."""
    na_dysku = {p.name for p in ISSUES.glob("zly-*.md")}
    assert na_dysku == set(ZLE)
    assert len(na_dysku) >= 6
    assert set(ZLE.values()) <= {r.value for r in ic.Regula}


@pytest.mark.parametrize("nazwa", DOBRE)
def test_dobry_przechodzi(nazwa: str, pusty_katalog: Path) -> None:
    """Dobre issue: kod 0 i brak komunikatów o błędach."""
    wynik = uruchom(ISSUES / nazwa, cwd=pusty_katalog)
    assert wynik.returncode == 0, wynik.stderr
    assert wynik.stderr == ""


@pytest.mark.parametrize(("nazwa", "regula"), sorted(ZLE.items()))
def test_zly_pada_na_swojej_regule(
    nazwa: str, regula: str, pusty_katalog: Path
) -> None:
    """Złe issue: kod 1 i komunikat wskazujący właśnie tę regułę."""
    wynik = uruchom(ISSUES / nazwa, cwd=pusty_katalog)
    assert wynik.returncode == 1, wynik.stdout
    assert f"[{regula}]" in wynik.stderr


# --------------------------------------------------------------------------- #
# Sabotaż: testy są falsyfikowalne
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("nazwa", "regula"), sorted(ZLE.items()))
def test_sabotaz_usuniecie_reguly_przepuszcza_zly_przyklad(
    nazwa: str, regula: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Po usunięciu reguły z rejestru zły przykład przechodzi.

    Gdyby ktoś skasował regułę, ``test_zly_pada_na_swojej_regule`` zrobiłby się
    czerwony; ten test dowodzi tego wprost, więc żaden zły przykład nie pada
    „przypadkiem” na innej regule.
    """
    issue = ic.wczytaj_markdown(ISSUES / nazwa)
    assert not ic.sprawdz(issue).przechodzi
    okrojony = {r: f for r, f in ic.REGULY.items() if r != ic.Regula(regula)}
    monkeypatch.setattr(ic, "REGULY", okrojony)
    assert ic.sprawdz(issue).przechodzi, ic.sprawdz(issue).naruszenia


@pytest.mark.parametrize(("nazwa", "regula"), sorted(ZLE.items()))
def test_sabotaz_przez_cli(nazwa: str, regula: str, pusty_katalog: Path) -> None:
    """To samo przez ``--wylacz``: zły przykład bez swojej reguły daje kod 0."""
    wynik = uruchom(ISSUES / nazwa, "--wylacz", regula, cwd=pusty_katalog)
    assert wynik.returncode == 0, wynik.stderr


# --------------------------------------------------------------------------- #
# Zdarzenie GitHub i bezpieczeństwo
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("plik", "kod"),
    [
        ("dobry.json", 0),
        ("zly-dwie-etykiety-stanu.json", 1),
        ("zly-wstrzykniecie-powloki.json", 1),
    ],
)
def test_zdarzenie_github(plik: str, kod: int, pusty_katalog: Path) -> None:
    """Tryb ``--event`` czyta JSON zdarzenia ``issues`` tak jak w GitHub Actions."""
    wynik = uruchom("--event", ZDARZENIA / plik, cwd=pusty_katalog)
    assert wynik.returncode == kod, wynik.stderr


@pytest.mark.parametrize(
    "argumenty",
    [
        (ISSUES / "zly-wstrzykniecie-powloki.md",),
        ("--event", ZDARZENIA / "zly-wstrzykniecie-powloki.json"),
    ],
)
def test_wstrzykniecie_powloki_nic_nie_wykonuje(
    argumenty: tuple[str | Path, ...], pusty_katalog: Path
) -> None:
    """``$(…)`` i backticki w treści i tytule issue to tekst, nie komendy."""
    wynik = uruchom(*argumenty, cwd=pusty_katalog)
    assert wynik.returncode == 1
    assert "$(touch WSTRZYKNIETE_1)" in wynik.stderr
    assert list(pusty_katalog.glob("WSTRZYKNIETE*")) == []


def test_adnotacja_ci_koduje_nowe_linie(tmp_path: Path) -> None:
    """W GitHub Actions komunikat idzie jako ``::error::`` z zakodowanym ``%``/CR/LF."""
    zdarzenie = json.loads((ZDARZENIA / "dobry.json").read_text(encoding="utf-8"))
    zdarzenie["issue"]["body"] = zdarzenie["issue"]["body"].replace(
        "# 5", "\r\n::warning::wstrzyknieta 100%"
    )
    plik = tmp_path / "zdarzenie.json"
    plik.write_text(json.dumps(zdarzenie), encoding="utf-8")
    wynik = uruchom("--event", plik, cwd=tmp_path, env={"GITHUB_ACTIONS": "true"})
    assert wynik.returncode == 1
    assert all(linia.startswith("::error::") for linia in wynik.stderr.splitlines())
    assert ic.zabezpiecz_adnotacje("a%\nb\r") == "a%25%0Ab%0D"


# --------------------------------------------------------------------------- #
# Konfiguracja
# --------------------------------------------------------------------------- #


def _konfiguracja(katalog: Path, dane: dict[str, object]) -> Path:
    """Zapisz ``.github/issue-check.json`` w katalogu roboczym."""
    plik = katalog / ".github" / "issue-check.json"
    plik.parent.mkdir(parents=True, exist_ok=True)
    plik.write_text(json.dumps(dane), encoding="utf-8")
    return plik


def test_prefiks_stanu_z_pliku_konfiguracji(tmp_path: Path) -> None:
    """Inny prefiks w ``.github/issue-check.json``: ``state:nowe`` przestaje się liczyć."""
    _konfiguracja(tmp_path, {"prefiks_stanu": "status/"})
    wynik = uruchom(ISSUES / "dobry.md", cwd=tmp_path)
    assert wynik.returncode == 1
    assert "status/" in wynik.stderr


def test_prefiks_stanu_ze_srodowiska_i_argumentu(pusty_katalog: Path) -> None:
    """Zmienna środowiskowa i argument ustawiają prefiks; argument wygrywa."""
    dobry = ISSUES / "dobry.md"
    assert (
        uruchom(
            dobry, cwd=pusty_katalog, env={"ISSUE_CHECK_PREFIKS_STANU": "x:"}
        ).returncode
        == 1
    )
    assert (
        uruchom(
            dobry,
            "--prefiks-stanu",
            "state:",
            cwd=pusty_katalog,
            env={"ISSUE_CHECK_PREFIKS_STANU": "x:"},
        ).returncode
        == 0
    )


def test_milestone_i_etykieta_typu_wlaczane_konfiguracja(tmp_path: Path) -> None:
    """Reguły opcjonalne są domyślnie wyłączone, a po włączeniu działają."""
    dobry = ISSUES / "dobry.md"
    assert uruchom(dobry, cwd=tmp_path).returncode == 0
    assert uruchom(dobry, "--wymagaj-milestone", cwd=tmp_path).returncode == 1
    _konfiguracja(tmp_path, {"etykiety_typu": ["bug", "enhancement"]})
    assert uruchom(dobry, cwd=tmp_path).returncode == 0
    _konfiguracja(tmp_path, {"etykiety_typu": ["bug"]})
    wynik = uruchom(dobry, cwd=tmp_path)
    assert wynik.returncode == 1
    assert "[etykieta-typu]" in wynik.stderr


@pytest.mark.parametrize(
    "dane",
    [
        {"nieznany": 1},
        {"prefiks_stanu": 5},
        {"prefiks_stanu": ""},
        {"sekcja_kryteriow": "Inna"},
    ],
)
def test_zla_konfiguracja_to_blad_uzycia(
    tmp_path: Path, dane: dict[str, object]
) -> None:
    """Literówka w konfiguracji nie może po cichu wyłączyć reguły: kod 2."""
    _konfiguracja(tmp_path, dane)
    assert uruchom(ISSUES / "dobry.md", cwd=tmp_path).returncode == 2


@pytest.mark.parametrize(
    "argumenty",
    [(), ("nie-ma-takiego.md",), ("--event", "nie-ma.json"), ("--event", "dobry.md")],
)
def test_bledy_uzycia(argumenty: tuple[str, ...]) -> None:
    """Brak wejścia, brak pliku albo nie-JSON jako zdarzenie: kod 2."""
    zamiana = tuple(str(ISSUES / a) if a == "dobry.md" else a for a in argumenty)
    assert uruchom(*zamiana).returncode == 2


# --------------------------------------------------------------------------- #
# Spójność z szablonem i workflow
# --------------------------------------------------------------------------- #


def test_szablon_ma_wszystkie_sekcje_i_blok_bash() -> None:
    """Etykiety pól formularza = sekcje wymagane przez skrypt; kryteria w ```bash."""
    tekst = SZABLON.read_text(encoding="utf-8")
    etykiety = re.findall(r"^\s+label:\s*(.+?)\s*$", tekst, flags=re.MULTILINE)
    assert tuple(etykiety) == ic.Konfiguracja().sekcje
    assert "```bash" in tekst


def test_niewypelniony_szablon_nie_przechodzi() -> None:
    """Issue z placeholderem z szablonu oblewa bramkę (placeholder to nie kryterium)."""
    tekst = SZABLON.read_text(encoding="utf-8")
    wartosc = tekst.split("value: |", 1)[1]
    blok = "\n".join(
        x.strip() for x in wartosc.splitlines() if x.strip().startswith(("```", "<"))
    )
    issue = ic.wczytaj_markdown(ISSUES / "dobry.md")
    podmienione = re.sub(r"```bash\n.*?```", blok, issue.body, flags=re.DOTALL)
    wynik = ic.sprawdz(ic.Issue(podmienione, issue.labels, None, "szablon"))
    assert {n.regula for n in wynik.naruszenia} == {ic.Regula.PLACEHOLDER}


def test_workflow_nie_interpoluje_tresci_issue() -> None:
    """Workflow: brak ``${{ github.event.issue.* }}``, akcje przypięte do SHA, minimalne uprawnienia."""
    tekst = WORKFLOW.read_text(encoding="utf-8")
    assert "github.event.issue" not in tekst
    assert "pull_request_target" not in tekst
    assert "secrets." not in tekst
    uses = re.findall(r"uses:\s*(\S+)", tekst)
    assert uses
    assert all(re.search(r"@[0-9a-f]{40}$", u) for u in uses)
    assert re.search(
        r"^permissions:\n  issues: read\n  contents: read\n", tekst, re.MULTILINE
    )
    assert "write" not in tekst
