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

Ten checkpoint jest jednym z możliwych punktów startu, wygodnym dla chętnych, a nie wymogiem.
Materiały nie narzucają technologii ani architektury Kokpitu: możesz robić ćwiczenie we własnym
kodzie i stosie (Streamlit, React, inny). Poniższe komendy `pytest` są przykładem dla tego
checkpointu; w Twoim stosie użyj odpowiednika testu jednostkowego.

Tabela ekspozycji w `app.py` pokazuje dla każdej waluty kwotę, kurs i wartość w PLN
(`result.lines`). Zadanie: dodać udział wartości PLN każdej waluty w portfelu jako kolumnę
„Udział %”. Kryteria opisują zachowanie:

1. Suma udziałów w tabeli wynosi 100,00 z dokładnością do ±0,01.
2. Pusty portfel (albo portfel o wartości zero) nie powoduje błędu.
3. Kolumna „Udział %” jest widoczna w tabeli ekspozycji na panelu.

Przykładowe kryteria akceptacji dla tego checkpointu (funkcja `udzialy(linie)` i plik
`tests/test_udzialy.py` powstają w ćwiczeniu):

```bash
pytest -q tests/test_udzialy.py  # exit 0
pytest -q tests/test_udzialy.py -k suma_100  # exit 0
pytest -q tests/test_udzialy.py -k pusty_portfel  # exit 0
pytest -q  # exit 0
```

Cykl:

1. Issue z formularza `praca.yml`; kryteria akceptacji to komendy z oczekiwanym wynikiem.
   Workflow `issue-check` ma być zielony.
2. TDD: najpierw czerwony test, potem kod.
3. PR z `Closes #N` i wyjściem komend z kryteriów; job `pytest` zielony.
4. Recenzja przez świeżą sesję agenta, werdykt jako komentarz na PR-ze.
5. Scalenie i sprawdzenie `gh pr view N --json state,mergedAt`.

Zwróć uwagę na typ `Decimal` zamiast `float` i na zaokrąglenia: udziały zaokrąglane każdy
osobno mogą dać sumę 99,99 albo 100,01.
