# serwer-mcp-nbp

Serwer MCP z kursami średnimi NBP (tabela A, `api.nbp.pl`) dla etapu 4 warsztatu („Skille, MCP”).
Działa lokalnie w transporcie `stdio`, wyłącznie do odczytu. Napisany w Pythonie z oficjalnym SDK MCP
(`mcp` 2.x, klasa `MCPServer`).

| Narzędzie | Parametry | Zwraca |
|---|---|---|
| `kurs_nbp` | `waluta` (ISO 4217 z tabeli A albo PLN), `data` (RRRR-MM-DD, opcjonalna; domyślnie dzisiaj) | kurs, kod waluty, data żądana, **faktyczna data notowania**, numer tabeli, źródło, `forward_fill`, `dane_aktualne` |
| `przelicz_na_pln` | `kwota` (tekst dziesiętny, np. `"1250.50"`), `waluta`, `data` | kwota w PLN (`Decimal`, ROUND_HALF_UP do grosza) i kurs jak wyżej |
| `wartosc_portfela` | `pozycje` (lista `{waluta, kwota}`, od 1 do 50), `data` | wycena każdej pozycji i suma w PLN |

Kwoty i kursy zwracamy jako tekst dziesiętny, żeby nie tracić precyzji przez `float`.
Opisy narzędzi, które czyta agent, są w [`src/serwer_mcp_nbp/opisy.py`](src/serwer_mcp_nbp/opisy.py).

## Instalacja na czystym Ubuntu 24.04

Potrzebne są: zwykły użytkownik, Python 3.12 z systemu, `git`, `python3-venv`. Nie trzeba uv, Node ani roota
(poza instalacją pakietów systemowych).

```bash
# Krok 1. Pakiety systemowe (jednorazowo)
sudo apt update && sudo apt install -y git curl wget gh python3-venv python3-pip

# Krok 2. Kod i środowisko wirtualne
git clone https://github.com/sitekwb/szkolenie-2026-10-06-materialy.git ~/szkolenie-2026-10-06-materialy
cd ~/szkolenie-2026-10-06-materialy/serwer-mcp-nbp
python3 -m venv .venv
.venv/bin/pip install .

# Krok 3. Sprawdzenie bez klienta MCP: jedno zapytanie do api.nbp.pl, JSON na stdout
.venv/bin/serwer-mcp-nbp --sprawdz EUR

# Krok 4. Claude Code (pomiń, jeśli już jest): instalator natywny trafia do ~/.local/bin
curl -fsSL https://claude.ai/install.sh | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && export PATH="$HOME/.local/bin:$PATH"

# Krok 5. Rejestracja w Claude Code (zakres użytkownika: serwer widoczny we wszystkich projektach)
claude mcp add --transport stdio --scope user nbp -- ~/szkolenie-2026-10-06-materialy/serwer-mcp-nbp/.venv/bin/serwer-mcp-nbp
claude mcp list
```

`claude mcp list` powinien pokazać `nbp: … - ✓ Connected`. W sesji Claude Code polecenie `/mcp` pokazuje
serwer i jego trzy narzędzia.

Kilka uwag:

- Ścieżka po `--` musi być bezwzględna. Shell rozwija `~` przed wywołaniem `claude`.
- `--scope project` zapisuje konfigurację do `.mcp.json` w repozytorium projektu. Ten plik trafia do gita, a
  Claude Code pyta każdego członka zespołu o zgodę przed pierwszym użyciem. Ścieżka do venv jest jednak
  lokalna dla maszyny, więc na warsztacie lepszy jest zakres `user`. Domyślny zakres to `local`: tylko ty,
  tylko bieżący projekt.
- Serwer **odmawia startu** (kod 3, komunikat na stderr), gdy w jego środowisku jest niepusty klucz usługi
  modelu: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` albo `OPENAI_API_KEY` (ADR-06).
  Jeśli trzymasz taki klucz w powłoce, a proces serwera go dziedziczy, nadpisz go pustą wartością przy
  rejestracji, np. `claude mcp add --transport stdio --scope user -e ANTHROPIC_API_KEY= nbp -- …`.
- Usunięcie serwera: `claude mcp remove --scope user nbp`.

## Przykładowe pytania w Claude Code

- „Jaki był kurs średni EUR w NBP 12 czerwca 2026?”
- „Ile to 1250,50 CHF w złotych według NBP z 4 czerwca 2026? Podaj, z którego dnia jest notowanie.”
- „Wyceń portfel: 10 000 EUR, 5 000 USD, 200 000 JPY na ostatni dzień roboczy września 2026.”
- „Jaki był kurs BTC?” (oczekiwany wynik: błąd `nieprawidlowa_waluta`, bez zgadywania).

Wszystkie kwoty w przykładach są syntetyczne.

## Rozwój i testy

```bash
cd serwer-mcp-nbp
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check . && .venv/bin/ruff format --check .   # lint
.venv/bin/mypy                                              # typy, tryb strict (src i tests)
.venv/bin/pytest -q                                         # jednostkowe, kontraktowe MCP i e2e stdio, bez sieci
.venv/bin/pytest -q -m live                                 # jeden test na prawdziwym api.nbp.pl
```

- `tests/test_nbp.py`: walidacja, forward-fill, cache, allowlista i błędy NBP (atrapa `httpx.MockTransport`
  z nagraniem `tests/dane/`).
- `tests/test_serwer.py`: kontrakt MCP w pamięci procesu (`mcp.Client(serwer)`).
- `tests/test_e2e_stdio.py`: serwer jako podproces stdio i klient SDK (initialize → list_tools → call_tool)
  na atrapie NBP. Do tego test odmowy startu z kluczem modelu.
- `tests/test_live.py`: to samo na prawdziwym NBP. Domyślnie pominięty, uruchamiany przez `-m live`.
- `tests/test_opisy.py`: przegląd opisów narzędzi (REQ-29).

CI (`.github/workflows/ci.yml`) uruchamia lint, typy i testy bez `live` na Pythonie 3.12 i 3.13.

## Wymagania z definicji arc42 i ich pokrycie

Źródło: definicja aplikacji „Kokpit rewaluacji portfela walutowego” (`wymagania-aplikacji.pdf` w korzeniu
repozytorium). Gwiazdka (*) oznacza odstępstwo opisane w następnej sekcji.

| ID | Wymaganie (skrót) | Jak spełnione | Test |
|---|---|---|---|
| FR-27 | Serwer MCP lokalny, `stdio`, instalowany jedną komendą `claude mcp add` | `MCPServer.run("stdio")`; komenda w „Instalacja”, krok 5 | `test_e2e_stdio.py::test_stdio_initialize_list_call`; test w Dockerze (opis PR) |
| FR-28* | Narzędzia `kurs_nbp(waluta, data)`, `przelicz_na_pln(kwota, waluta, data)`, `wartosc_portfela(data, …)`, wyłącznie odczytowe | Trzy narzędzia z `read_only_hint=True`. `wartosc_portfela` dostaje portfel w argumencie `pozycje` | `test_serwer.py::test_lista_narzedzi_dokladnie_odczytowa`, `test_wartosc_portfela_*` |
| FR-03 | Forward-fill bez interpolacji, z faktyczną datą notowania | `KlientNBP.kurs`: ostatnie notowanie ≤ dzień żądany w oknie 14 dni; pola `data_notowania` i `forward_fill` | `test_nbp.py::test_forward_fill_bez_interpolacji` (Boże Ciało, sobota, niedziela) |
| FR-01 | `Decimal`, ROUND_HALF_UP do grosza, PLN = 1 | `json.loads(parse_float=Decimal)`, `na_grosze`, stała dla PLN | `test_round_half_up`, `test_przelicz_na_pln`, `test_pln_bez_zapytania` |
| FR-31, NFR-08 | Brak danych to ustrukturyzowany błąd, nigdy wartość domyślna | `BladKursu` → `ToolError` → `isError: true`, treść `{"blad", "powod"}` bez pól liczbowych; portfel bez sumy częściowej | `test_brak_kursu_to_blad_nie_liczba`, `test_wartosc_portfela_bez_sumy_czesciowej`, `test_bledy_nbp_bez_wartosci_domyslnej` |
| FR-26, OG-15, ADR-04, NFR-05, KA-05.4 | Zero narzędzi zmieniających stan | Brak takich narzędzi; klient HTTP przepuszcza tylko `GET` | `test_lista_narzedzi_dokladnie_odczytowa` (równość zbiorów), `test_nieznane_narzedzie_zapisujace`, `test_allowlista_tylko_get_https_api_nbp` |
| FR-30* | Tylko `GET`, bez dostępu do plików projektu i do klucza modelu | Hak `_sprawdz_zadanie`: `GET`, HTTPS, host `api.nbp.pl`; serwer nie czyta ani nie zapisuje plików | `test_allowlista_tylko_get_https_api_nbp`, `test_przekierowania_wylaczone` |
| OG-04, ADR-06 | Serwer MCP nie ma klucza modelu i odmawia startu, gdy go wykryje | `sprawdz_srodowisko`: kod 3, nazwa zmiennej na stderr, wartość nigdy | `test_odmowa_startu_z_kluczem_modelu`, `test_stdio_odmowa_startu_z_kluczem_modelu` |
| OG-07, OG-12 | Jedyna integracja zewnętrzna: `api.nbp.pl`, tabela A, bez klucza | `BAZOWY_URL`, allowlista hosta, brak przekierowań | jak wyżej |
| OG-09 | NBP odpytywane oszczędnie i wsadowo | Jedno żądanie `tables/a/{od}/{do}` daje okno 14 dni dla **wszystkich** walut; cache LRU w pamięci (128 wpisów, TTL 10 min dla okna z dzisiejszą datą, 12 h dla archiwalnych); licznik zapytań w logu (stderr) | `test_cache_jedno_zapytanie_na_okno`, `test_wartosc_portfela_jedno_zapytanie`, `test_cache_wygasa_po_ttl`, `test_cache_ma_limit_wpisow` |
| FR-04 (analogicznie) | Awaria NBP degraduje do starszych danych z oznaczeniem | Po awarii NBP przeterminowany wpis cache zwracany z `dane_aktualne: false`; bez wpisu: błąd `nbp_niedostepne` | `test_awaria_nbp_daje_przeterminowany_cache_z_flaga` |
| OG-19 | Testy bez sieci | `httpx.MockTransport` z nagraniem; test na żywo za markerem `live` | `pytest -q` (domyślnie `-m 'not live'`) |
| OG-22 | Odpowiedź z zewnątrz jest niezaufana i przechodzi walidację kształtu | Limit 1 MB czytany strumieniowo, modele Pydantic (`table == "A"`, wzorzec numeru tabeli, kod `[A-Z]{3}`, `0 < mid < 10^6`, najwyżej 94 tabele) | `test_bledy_nbp_bez_wartosci_domyslnej[smieci, za_duzo]` |
| OG-25 | Daty ISO-8601, dzień roboczy NBP, forward-fill | Format `RRRR-MM-DD`, strefa `Europe/Warsaw`, zakres od 2002-01-02 do dzisiaj | `test_nieprawidlowa_data`, `test_data_z_przyszlosci_odrzucona` |
| UC-08*, KA-08.3 | Walidacja (kod z listy, ISO, nie z przyszłości) **przed** żądaniem | `waliduj_*` przed `KlientNBP.kurs`; lista walut tabeli A | `test_walidacja_przed_zapytaniem` (atrapa nie dostaje żądania) |
| KA-08.1 | Lista narzędzi dokładnie równa oczekiwanej | Równość zbiorów, nie sprawdzanie obecności | `test_lista_narzedzi_dokladnie_odczytowa` |
| KA-08.2 | `kurs_nbp("EUR", "2026-06-12")`: kurs, kod, data żądana, data notowania, źródło; zgodność ze schematem | Model `Kurs` jako `outputSchema` | `test_kurs_nbp_ksztalt_wyniku`, `test_live.py` |
| KA-08.4 | Brak kursu i pusty cache: błąd bez pól liczbowych | jak FR-31 | `test_serwer.py::blad()` sprawdza, że wszystkie pola są tekstem |
| KA-08.5* | `POST`/`PUT`/`DELETE` odrzucone | Brak backendu. Klient blokuje każdą metodę inną niż `GET` przed wysłaniem | `test_allowlista_tylko_get_https_api_nbp[POST, DELETE]` |
| REQ-29 | Opisy narzędzi wersjonowane i przeglądane jak kod | Opisy w `opisy.py` z `WERSJA_OPISOW`; test przypina SHA-256 dla wersji | `test_opisy.py` (zmiana opisu bez podbicia wersji i nowego skrótu = czerwony test) |
| REQ-48 | Rozszerzenie przechodzi przegląd całej treści przed instalacją | Kod i opisy są jawne w tym repozytorium; model zagrożeń poniżej | przegląd PR |
| ADR-02* | MCP jako mechanizm integracji kursów NBP z agentem | Jest serwer MCP. Odstępstwo: nie jest adapterem backendu | — |
| OG-14* | Proces odrębny, `stdio`, na maszynie uczestnika | Spełnione poza „komunikuje się z backendem przez `X-API-Key`” | `test_e2e_stdio.py` |
| NFR-09 / RY-03 | Kokpit działa bez serwera MCP; serwer stawiany prosto | Serwer jest niezależny od backendu; jeden venv, bez Dockera i Node | test w Dockerze (opis PR) |

## Odstępstwa od definicji arc42

Decyzja trenera z 2026-10-05: **serwer pyta `api.nbp.pl` bezpośrednio, zamiast być adapterem GET do backendu
Kokpitu.** Dzięki temu działa na czystej maszynie uczestnika bez uruchomionego backendu i bez klucza `X-API-Key`.
Definicja zostanie dostosowana w osobnym issue w repozytorium warsztatu.

| Miejsce w definicji | Treść definicji | W tej wersji |
|---|---|---|
| OG-14 | Komunikacja z backendem tylko przez publiczne API z `X-API-Key` | Brak backendu i klucza. Jedyny ruch wychodzący to HTTPS GET do `api.nbp.pl` (allowlista) |
| FR-30 | `GET` do backendu z osobnym kluczem odczytowym | `GET` do `api.nbp.pl` bez klucza (usługa publiczna). Brak plików i klucza modelu bez zmian |
| FR-28 | `wartosc_portfela(data, waluty?)` liczy portfel z backendu | `wartosc_portfela(data, pozycje)`: portfel podaje wywołujący (waluta + kwota), serwer niczego nie przechowuje |
| ADR-02 | Cienki adapter bez logiki dziedzinowej | Serwer ma własny forward-fill, cache i przeliczenie `Decimal` (kopia reguł FR-01 i FR-03) |
| §6.2 (07, „Agent pyta serwer MCP o kurs”) | `MCP → GET /api/rates/EUR?asOf=…` do backendu, cache w `nbp/cache` | `MCP → GET api.nbp.pl/api/exchangerates/tables/a/{od}/{do}/`, cache w pamięci serwera |
| KA-08.5 | Backend odrzuca `POST`/`PUT`/`DELETE` kodem `403` | Nie ma backendu, więc nie ma `403`. Serwer nie wystawia zapisu, a klient HTTP odrzuca każdą metodę poza `GET`, zanim żądanie wyjdzie (`test_allowlista_tylko_get_https_api_nbp`) |
| UC-08 | Warunek wstępny: działa backend, w zmiennej jest klucz odczytowy | Warunek wstępny: dostęp do `api.nbp.pl`. Przypadek 4a (zły klucz) nie występuje |
| 02, 06, 08 (kontekst, widok blokowy, sekrety) | Strzałka MCP → kokpit; jedyny sekret serwera to klucz odczytowy MCP | Strzałka MCP → `api.nbp.pl`; serwer **nie ma żadnego sekretu** |

## Model zagrożeń

| Zagrożenie | Wektor | Kontrola w serwerze |
|---|---|---|
| Agent wykonuje operację zmieniającą stan | Wstrzyknięcie poleceń w treść, którą czyta agent | Brak narzędzi zapisujących; klient zna tylko `GET` |
| Wyprowadzenie danych na zewnątrz (SSRF, exfiltracja) | Spreparowany parametr albo przekierowanie | Parametry nie trafiają do hosta ani schematu URL; allowlista `https://api.nbp.pl`; `follow_redirects=False`; kod waluty z zamkniętej listy przed złożeniem ścieżki |
| Zatruta lub zbyt duża odpowiedź źródła | Kompromitacja lub awaria NBP, atak pośrodku | Tylko HTTPS z weryfikacją certyfikatu; limit 1 MB; walidacja kształtu Pydantic; błąd zamiast danych częściowych |
| Halucynowana liczba po błędzie | Agent „uzupełnia” brak kursu | Błąd ustrukturyzowany bez pól liczbowych; instrukcja serwera zabrania zastępowania błędu liczbą |
| Zatruty opis narzędzia (*tool poisoning*) | Zmiana `opisy.py` w łańcuchu dostaw | Opisy w jednym pliku, wersjonowane, z przypiętym SHA-256 w teście (REQ-29); instaluj z przejrzanego commita |
| Denial-of-wallet, wyciek klucza modelu | Proces MCP dziedziczy klucz modelu | Odmowa startu przy niepustym kluczu (ADR-06) |
| Nadużycie publicznego API (OG-09) | Pętla agenta odpytująca NBP | Cache w pamięci, jedno żądanie na okno dla wszystkich walut, limity czasu 5 s na połączenie i 10 s na żądanie |
| Uszkodzenie protokołu i wyciek stack trace'ów | Logi na stdout, wyjątki w odpowiedzi | Logi wyłącznie na stderr; błędy narzędzi jako `isError` z krótkim powodem bez śladu stosu |
| Ślad na dysku | Cache plikowy, logi | Brak zapisu na dysk; cache żyje tylko w pamięci procesu |

Ograniczenia: lista walut tabeli A jest zapisana w kodzie (stan z 2026-10-05). Gdy NBP doda walutę, trzeba
zaktualizować `WALUTY_TABELI_A`. Serwer nie obsługuje tabel B i C.

## Licencja

MIT, zob. [`../LICENSE`](../LICENSE).
