# Checkpoint „po etapie 2”

Wspólny punkt startu do ćwiczenia etapu 7 („udział % w tabeli ekspozycji”). Zawiera wynik
etapu 2: panel Streamlit „what-if” dla portfela walutowego przeliczanego kursami średnimi NBP
(tabela A), testy oraz bramkę issue. Dane portfela są syntetyczne.

Ten katalog jest szablonem. Kopiujesz jego zawartość do korzenia własnego repo `moj-kokpit`
(to, które zakładasz w kroku 3 etapu 0) i dalej pracujesz już tylko tam.

## Zawartość

| Ścieżka | Co to jest |
|---|---|
| `wallet_scenario.py` | moduł domenowy: `Holding`, `BaseRate`, `ScenarioResult` (z polem `lines`), `load_base_rates(fetch=…)`, `scenario()`, `validate_pct` |
| `nbp_convert.py` | kursy NBP z lokalnego cache'u SQLite (`~/.cache/nbp_convert/`, ścieżkę zmienia `NBP_CACHE_PATH`) |
| `app.py` | panel Streamlit: portfel, suwak zmiany kursów, metryki i tabela ekspozycji |
| `tests/` | testy aplikacji (bez sieci) i testy bramki issue |
| `.github/ISSUE_TEMPLATE/praca.yml` | formularz issue z sekcją „Kryteria akceptacji” |
| `.github/workflows/issue-check.yml`, `scripts/issue_check.py` | bramka issue: sprawdza sekcje, kryteria w bloku `bash` z oczekiwanym wynikiem i jedną etykietę `state:` |
| `.github/workflows/ci.yml` | job `pytest` (oraz `mypy`) przy każdym pushu na `main` i PR-ze |

## Skopiowanie do własnego repo

GitHub uruchamia workflow tylko z katalogu `.github/` w korzeniu repo. W repo trenera ten
katalog leży w podkatalogu, więc `issue-check` i `pytest` nie działają tutaj. Zaczną działać
w Twoim repo, gdy skopiujesz checkpoint do jego korzenia razem z `.github/`.

```bash
git clone https://github.com/sitekwb/szkolenie-2026-10-06-materialy ~/materialy
cd ~/moj-kokpit                       # Twoje repo z etapu 0, sklonowane lokalnie
cp -r ~/materialy/checkpoint-etap-2/. .   # kropka na końcu kopiuje też .github/ i .gitignore
git add -A
git commit -m "Checkpoint po etapie 2"
git push
gh label create "state:nowe" --description "Issue czeka na przyjęcie" --color ededed
```

Etykieta `state:nowe` musi istnieć w repo: formularz `praca.yml` ją nadaje, a bramka wymaga
dokładnie jednej etykiety z prefiksem `state:`.

## Uruchomienie

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Przy starcie panel pobiera brakujące kursy NBP do lokalnego cache'u (najwyżej raz na godzinę).
Bez sieci pokazuje ostrzeżenie i korzysta z tego, co jest w cache'u.

## Testy i typy

```bash
.venv/bin/pytest -q        # exit 0, bez dostępu do sieci
.venv/bin/mypy .           # Success
python3 scripts/issue_check.py tests/issues/dobry.md                # exit 0
python3 scripts/issue_check.py tests/issues/zly-kryterium-opisowe.md # exit 1
```

Testy nie łączą się z NBP: sieć i zegar są podmienione atrapami. Test panelu
(`tests/test_app.py`) uruchamia `app.py` przez `streamlit.testing.v1.AppTest`. Pierwsze
uruchomienie w świeżym venv importuje Streamlit i pandas od zera i na wolnej maszynie trwa
długo, dlatego limit czasu `AppTest` wynosi 120 s.

`mypy` korzysta z przypiętego pakietu `pandas-stubs` (typy dla pandas), konfiguracja jest
w `mypy.ini`.

## Ćwiczenie: kolumna „Udział %”

Tabela ekspozycji w `app.py` pokazuje dla każdej waluty kwotę, kurs i wartość w PLN
(`result.lines`). Twoje zadanie: dodać funkcję `udzialy(linie)`, która zwraca udział wartości
PLN każdej waluty w portfelu, i kolumnę „Udział %” w tabeli. Cykl:

1. Issue z formularza `praca.yml`; kryteria akceptacji to komendy `pytest`. Workflow
   `issue-check` ma być zielony.
2. TDD: najpierw czerwony test w `tests/test_udzialy.py`, potem kod.
3. PR z `Closes #N` i wyjściem komend z kryteriów; job `pytest` zielony.
4. Recenzja przez świeżą sesję agenta, werdykt jako komentarz na PR-ze.
5. Scalenie i sprawdzenie `gh pr view N --json state,mergedAt`.

Zwróć uwagę na typ `Decimal`, pusty portfel i to, czy zaokrąglone udziały sumują się
do dokładnie 100,00.
