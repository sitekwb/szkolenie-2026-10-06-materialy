# Bramka issue

Bramka sprawdza każde issue w repo, zanim ruszy praca. Issue przechodzi tylko wtedy, gdy:

1. ma wszystkie sekcje szablonu: Cel, Kontekst, Zakres, Poza zakresem, Powiązane, Kryteria akceptacji, każdą niepustą i bez powtórzeń;
2. kryteria akceptacji są blokiem ```` ```bash ````, a każda niepusta linia to komenda z oczekiwanym wynikiem po `  # `, na przykład `python3 -m pytest tests -q  # exit 0`; opis słowny i niezmieniony placeholder z szablonu nie przechodzą;
3. ma dokładnie jedną etykietę stanu, czyli etykietę z prefiksem `state:` i niepustą nazwą po nim, np. `state:nowe` (prefiks da się zmienić; wielkość liter nie ma znaczenia, więc `State:b` też jest etykietą stanu).

Wynik to status joba `issue-check` w zakładce Actions: zielony albo czerwony, z listą niespełnionych reguł w logu i w adnotacjach. Bramka niczego w issue nie zmienia: nie komentuje, nie nadaje etykiet, nie potrzebuje sekretów.

## Zawartość

Układ katalogu odpowiada korzeniowi repo docelowego.

| Plik | Rola |
|------|------|
| `.github/ISSUE_TEMPLATE/praca.yml` | formularz issue z sześcioma sekcjami; nadaje etykietę `state:nowe` |
| `.github/workflows/issue-check.yml` | workflow uruchamiany przy założeniu, edycji, zmianie etykiet i milestone'a |
| `scripts/issue_check.py` | walidator; Python 3.12+, tylko biblioteka standardowa |
| `tests/test_issue_check.py` | testy bramki (pytest) |
| `tests/issues/dobry*.md`, `tests/issues/zly-*.md` | przykłady: dobre przechodzą, każdy zły łamie dokładnie jedną regułę |
| `tests/zdarzenia/*.json` | te same przykłady w postaci JSON-a zdarzenia GitHub `issues` |

## Przeniesienie do własnego repo

Z katalogu, w którym leży `bramka-issue/`, do sklonowanego repo docelowego (`../moje-repo` to przykład):

```bash
mkdir -p ../moje-repo/docs
cp -R bramka-issue/.github bramka-issue/scripts bramka-issue/tests ../moje-repo/
cp bramka-issue/README.md ../moje-repo/docs/bramka-issue.md   # własny README repo zostaje nietknięty
cd ../moje-repo
python3 -m venv .venv && . .venv/bin/activate && pip install pytest
printf '.venv/\n__pycache__/\n' >> .gitignore   # venv i cache Pythona nie trafiają do repo
python3 -m pytest tests/test_issue_check.py -q   # wszystkie zielone
gh label create "state:nowe" --description "Issue czeka na przyjęcie" --color ededed
git add .github scripts/issue_check.py tests/test_issue_check.py tests/issues tests/zdarzenia docs/bramka-issue.md .gitignore
git commit -m "Bramka issue: szablon, workflow, walidator i testy"
git push
```

Uwagi do kroków:

- `cp -R` nadpisuje pliki o tych samych ścieżkach. Jeśli repo ma już `.github/ISSUE_TEMPLATE/praca.yml`, `.github/workflows/issue-check.yml` albo `scripts/issue_check.py`, sprawdź `git diff` przed commitem.
- Etykieta `state:nowe` musi istnieć w repo. Bez niej GitHub nie nada jej z szablonu i bramka zgłosi brak etykiety stanu.
- Workflow działa tylko z gałęzi domyślnej. Po pushu załóż próbne issue z szablonu „Praca (zadanie w repo)” i sprawdź w Actions, że job `issue-check` się uruchomił.
- Żeby check blokował pracę, a nie tylko ostrzegał, zespół umawia się, że nie zaczyna issue z czerwonym `issue-check`. Status issue nie blokuje technicznie merge'a PR-a; PR-y pilnuje osobny CI.

## Konfiguracja

Domyślnie nic nie trzeba ustawiać. Zmiany wpisuje się do `.github/issue-check.json` w repo docelowym; nieznany klucz albo zły typ kończy się kodem 2, więc literówka nie wyłączy reguły po cichu.

```json
{
  "_opis": "Klucze z podkreśleniem są ignorowane, służą za komentarze.",
  "prefiks_stanu": "state:",
  "wymagaj_etykiety_stanu": true,
  "etykiety_typu": ["bug", "enhancement"],
  "wymagaj_milestone": true
}
```

| Klucz | Domyślnie | Znaczenie |
|-------|-----------|-----------|
| `prefiks_stanu` | `"state:"` | prefiks etykiety stanu; issue ma mieć dokładnie jedną taką etykietę |
| `wymagaj_etykiety_stanu` | `true` | `false` wyłącza regułę etykiety stanu |
| `etykiety_typu` | `[]` | gdy niepuste, issue musi mieć co najmniej jedną z tych etykiet |
| `wymagaj_milestone` | `false` | `true` wymaga ustawionego milestone'a |
| `sekcje` | sześć sekcji szablonu | nagłówki, które issue musi mieć; zmieniając je, zmień też `label:` w `praca.yml` |
| `sekcja_kryteriow` | `"Kryteria akceptacji"` | sekcja z komendami; musi być na liście `sekcje` |
| `jezyki_bloku` | `["bash", "sh"]` | dopuszczalne języki bloku kryteriów |
| `placeholdery` | `["<komenda>", "<oczekiwany wynik>"]` | teksty z szablonu, których nie wolno zostawić |

Kolejność pierwszeństwa: wartości domyślne, potem plik (`--config PLIK`, `$ISSUE_CHECK_CONFIG` albo `.github/issue-check.json`), potem zmienne `ISSUE_CHECK_PREFIKS_STANU` i `ISSUE_CHECK_WYMAGAJ_MILESTONE` (`1`/`0`), na końcu argumenty `--prefiks-stanu` i `--wymagaj-milestone`. Zmieniając prefiks, zmień też `labels:` w `praca.yml`.

## Uruchomienie lokalne

```bash
python3 scripts/issue_check.py tests/issues/dobry.md                      # exit 0
python3 scripts/issue_check.py tests/issues/zly-dwie-etykiety-stanu.md    # exit 1, komunikat [etykieta-stanu]
python3 scripts/issue_check.py --event tests/zdarzenia/dobry.json         # exit 0, tak samo jak w Actions
```

Plik Markdown może mieć front matter z etykietami i milestone'em:

```markdown
---
labels: [enhancement, "state:nowe"]
milestone: "Sprint 1"
---

## Cel
...
```

Kody wyjścia: 0 issue przechodzi, 1 issue łamie regułę (lista na stderr), 2 błąd użycia (brak pliku, zły JSON, zła konfiguracja).

## Bezpieczeństwo workflow

Treść i tytuł issue pisze każdy, kto może założyć issue, więc workflow traktuje je jak dane niezaufane:

- żadne pole `github.event.issue.*` nie trafia wyrażeniem `${{ }}` do `run:`; skrypt czyta zdarzenie z pliku `$GITHUB_EVENT_PATH`, więc `$(…)` i backticki w treści pozostają tekstem (test `test_wstrzykniecie_powloki_nic_nie_wykonuje`);
- komunikaty z fragmentami treści są kodowane przed wypisaniem jako adnotacja `::error::`, żeby znak nowej linii nie wstrzyknął komendy workflow;
- `permissions:` tylko `issues: read` i `contents: read`; brak sekretów, brak `pull_request_target`, `persist-credentials: false`;
- akcje przypięte do pełnego SHA commita z komentarzem wersji. Aktualizując wersję, sprawdź SHA w repozytorium akcji, np. `gh api repos/actions/checkout/commits/v7.0.1 --jq .sha`.

## Odporność na złośliwą treść

Regexy w skrypcie działają w czasie liniowym (kwantyfikatory zaborcze, bez sąsiadujących kwantyfikatorów o wspólnym alfabecie). Testy `test_redos_czas_liniowy` sprawdzają, że nagłówek albo kryterium ze 100 000 spacji i treść o rozmiarze 1 MB są walidowane w czasie poniżej 1 s.

## Falsyfikowalność

Check, który nie potrafi paść, nic nie sprawdza. Dlatego testy udowadniają dwie rzeczy:

1. Każdy przykład `tests/issues/zly-*.md` kończy się kodem 1 i komunikatem dokładnie tej reguły, którą łamie (mapa `ZLE` w teście). Złych przykładów jest jedenaście: brak sekcji kryteriów, kryterium opisowe, linia bez oczekiwanego wyniku, dwie etykiety stanu, zero etykiet stanu, etykieta `state:` bez nazwy, pusta sekcja, blok bez `bash`, placeholder z szablonu, powtórzona sekcja, wstrzyknięcie powłoki.
2. Test sabotażu usuwa regułę z rejestru `REGULY` (albo wyłącza ją flagą `--wylacz`) i sprawdza, że jej zły przykład zaczyna przechodzić. Zły przykład nie pada więc „przypadkiem” na innej regule, a usunięcie reguły z kodu daje czerwony test.

Sprawdzenie ręczne: zakomentuj w `REGULY` linię `Regula.ETYKIETA_STANU: _etykieta_stanu,` i uruchom `python3 -m pytest tests -q`. Testy `zly-dwie-etykiety-stanu` i `zly-zero-etykiet-stanu` robią się czerwone.

Ta sama zasada obowiązuje kryteria akceptacji w issue: każda linia ma oczekiwany wynik, a co najmniej jedno kryterium powinno paść na stanie sprzed zmiany.
