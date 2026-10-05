# nbp-mcp-server

An MCP server with NBP average exchange rates (Table A, `api.nbp.pl`) for stage 4 of the workshop
("Skills, MCP"). It runs locally over the `stdio` transport and is read-only. Written in Python with the
official MCP SDK (`mcp` 2.x, class `MCPServer`). The Python package is `nbp_mcp_server` and the command is
`nbp-mcp-server`; the repository directory keeps its original name.

| Tool | Parameters | Returns |
|---|---|---|
| `get_nbp_rate` | `currency` (ISO 4217 from Table A or PLN), `date` (YYYY-MM-DD, optional; defaults to today) | rate, currency code, requested date, **actual quote date**, table number, source, `forward_fill`, `is_current` |
| `convert_to_pln` | `amount` (positive, decimal text, e.g. `"1250.50"`), `currency`, `date` | amount in PLN (`Decimal`, ROUND_HALF_UP to the grosz, 0.01 PLN) and the rate as above. The NBP rate is always per 1 unit of currency (also for JPY and HUF) |
| `portfolio_value` | `positions` (list of `{currency, amount}`, positive amounts, 1 to 50 positions, enforced by `maxItems` in the input schema), `date` | value of each position (rounded to the grosz) and the total in PLN: the exact sum of the unrounded values, rounded once (ROUND_HALF_UP), so it can differ by a few grosz from the sum of the rounded positions (50 × 1 EUR at 4.2484: positions 4.25 each, total 212.42, not 212.50) |

Amounts and rates are returned as decimal text so that no precision is lost through `float`.
The tool descriptions the agent reads live in [`src/nbp_mcp_server/descriptions.py`](src/nbp_mcp_server/descriptions.py).

Version 2.0.0 renamed every identifier visible to an MCP client (tools, parameters, result fields, error
codes, command) from Polish to English; behavior is unchanged. The full mapping is in the pull request that
introduced 2.0.0.

## Installation on a clean Ubuntu 24.04

You need a regular user, the system Python 3.12, `git` and `python3-venv`. No uv, Node or root is required
(apart from installing system packages).

```bash
# Step 1. System packages (once)
sudo apt update && sudo apt install -y git curl wget gh python3-venv python3-pip

# Step 2. Code and virtual environment
git clone https://github.com/sitekwb/szkolenie-2026-10-06-materialy.git ~/szkolenie-2026-10-06-materialy
cd ~/szkolenie-2026-10-06-materialy/serwer-mcp-nbp
python3 -m venv .venv
.venv/bin/pip install -c constraints.txt .

# Step 3. Check without an MCP client: one request to api.nbp.pl, JSON on stdout
.venv/bin/nbp-mcp-server --check EUR

# Step 4. Claude Code (skip if already installed): the native installer goes to ~/.local/bin
curl -fsSL https://claude.ai/install.sh | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && export PATH="$HOME/.local/bin:$PATH"

# Step 5. Register in Claude Code (user scope: the server is visible in all projects)
claude mcp add --transport stdio --scope user nbp -- ~/szkolenie-2026-10-06-materialy/serwer-mcp-nbp/.venv/bin/nbp-mcp-server
claude mcp list
```

`claude mcp list` should show `nbp: … - ✓ Connected`. Inside a Claude Code session the `/mcp` command shows
the server and its three tools.

A few notes:

- The path after `--` must be absolute. The shell expands `~` before `claude` is called.
- `--scope project` writes the configuration to `.mcp.json` in the project repository. That file goes into
  git, and Claude Code asks every team member for consent before first use. The venv path is local to the
  machine, though, so the `user` scope fits the workshop better. The default scope is `local`: only you, only
  the current project.
- The server **refuses to start** (exit code 3, message on stderr) when a non-empty model service key is in its
  environment: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` or `OPENAI_API_KEY`
  (ADR-06). If you keep such a key in your shell and the server process inherits it, override it with an empty
  value at registration. The server name must come **before** `-e` (the `-e` option takes several values and
  would otherwise swallow the name), and `--` ends the options:
  `claude mcp add nbp --transport stdio --scope user -e ANTHROPIC_API_KEY= -- ~/szkolenie-2026-10-06-materialy/serwer-mcp-nbp/.venv/bin/nbp-mcp-server`.
  Claude Code passes `ANTHROPIC_API_KEY` from the shell to the MCP process, so without this the server will
  not start. When you log in with `/login` or with `CLAUDE_CODE_OAUTH_TOKEN` the workaround is not needed,
  because Claude Code does not pass that token to MCP servers (verified in Docker with Claude Code 2.1.289).
- Removing the server: `claude mcp remove --scope user nbp`.

`constraints.txt` pins the exact version of every runtime and dev dependency, so each installation gets the
same MCP SDK and the same `tools/list` manifest whose SHA-256 is pinned in `tests/test_descriptions.py`
(REQ-29). It is plain pip, no extra tool. To update the pins: in a fresh venv run `pip install -e '.[dev]'`,
then `pip freeze --exclude-editable > constraints.txt`, run the tests and review the diff.

## Errors

Every tool error is `isError: true` with the body `{"error": "<code>", "reason": "<text>"}` and no numeric
fields. Codes form a closed set: `invalid_currency`, `invalid_date`, `invalid_amount`, `invalid_positions`,
`invalid_arguments`, `no_quote`, `nbp_unavailable`, `invalid_nbp_response`. Arguments that break the input
schema (for example an empty `positions` list, more than 50 positions, a number where text is expected, an
unknown field) are rejected by the MCP SDK before the tool runs. `NBPServer.call_tool` maps that rejection
to a fixed code and reason chosen by the top-level parameter name; the Pydantic message and the rejected
values are never returned, so nothing the caller sent is echoed back to the agent. The schema itself stays
strict (`minItems`, `maxItems`, `additionalProperties: false`), so the agent still sees the limits.

A failed NBP request is retried **once** after 0.5 s when NBP answers with HTTP 5xx or the request times out
or cannot connect. A 404 (no quotes), other 4xx, an invalid response and a request blocked by the host
allowlist are not retried. Both attempts together are bounded by a 21 s deadline.

## Example questions in Claude Code

- "What was the NBP average EUR rate on 12 June 2026?"
- "How much is 1250.50 CHF in PLN at the NBP rate of 4 June 2026? Say which day the quote is from."
- "Value the portfolio: 10,000 EUR, 5,000 USD, 200,000 JPY on the last business day of September 2026."
- "What was the BTC rate?" (expected result: an `invalid_currency` error, no guessing).

All amounts in the examples are synthetic.

## Development and tests

```bash
cd serwer-mcp-nbp
python3 -m venv .venv && .venv/bin/pip install -c constraints.txt -e '.[dev]'
.venv/bin/ruff check . && .venv/bin/ruff format --check .   # lint
.venv/bin/mypy                                              # types, strict mode (src and tests)
.venv/bin/pytest -q                                         # unit, MCP contract and e2e stdio, no network
.venv/bin/pytest -q -m live                                 # one test against the real api.nbp.pl
```

- `tests/test_nbp.py`: validation, forward-fill, cache, allowlist and NBP errors (fake `httpx.MockTransport`
  with the recording in `tests/data/`).
- `tests/test_server.py`: MCP contract in-process (`mcp.Client(server)`).
- `tests/test_e2e_stdio.py`: the server as a stdio subprocess and the SDK client (initialize → list_tools →
  call_tool) against the fake NBP, plus the test that startup is refused with a model key.
- `tests/test_live.py`: the same against the real NBP. Skipped by default, run with `-m live`.
- `tests/test_descriptions.py`: review of the tool manifest (REQ-29). The test pins the SHA-256 of the
  `tools/list` result (names, descriptions, `inputSchema`, `outputSchema`, annotations) and of the server
  instructions for the current `DESCRIPTIONS_VERSION`.

CI (`.github/workflows/ci.yml`) runs lint, types and the non-`live` tests on Python 3.12 and 3.13.

The recording in `tests/data/` is a verbatim NBP API response, so it contains NBP's own currency names
(for example `"currency": "euro"`). The server ignores that field.

## Requirements from the arc42 definition and their coverage

Source: the application definition (`wymagania-aplikacji.pdf` in the repository root). An asterisk (*) marks a
deviation described in the next section.

| ID | Requirement (short) | How it is met | Test |
|---|---|---|---|
| FR-27 | Local MCP server, `stdio`, installed with one `claude mcp add` command | `MCPServer.run("stdio")`; command in "Installation", step 5 | `test_e2e_stdio.py::test_stdio_initialize_list_call`; Docker test (PR description) |
| FR-28* | Tools for rate, conversion to PLN and portfolio value, read-only | Three tools with `read_only_hint=True`. `portfolio_value` receives the portfolio in the `positions` argument | `test_server.py::test_tool_list_exactly_read_only`, `test_portfolio_value_*` |
| FR-03 | Forward-fill without interpolation, with the actual quote date | `NBPClient.rate`: last quote ≤ requested day within a 14-day window; fields `quote_date` and `forward_fill` | `test_nbp.py::test_forward_fill_without_interpolation` (Corpus Christi, Saturday, Sunday) |
| FR-01 | `Decimal`, ROUND_HALF_UP to the grosz, PLN = 1 | `json.loads(parse_float=Decimal)`, `round_to_grosz`, constant for PLN; amount must be positive; portfolio total rounded once from unrounded values | `test_round_half_up`, `test_portfolio_total_rounded_once`, `test_convert_to_pln`, `test_convert_jpy_rate_per_unit`, `test_convert_non_positive_amount`, `test_pln_without_request` |
| FR-31, NFR-08 | Missing data is a structured error, never a default value | `RateError` → `ToolError` → `isError: true`, body `{"error", "reason"}` without numeric fields; portfolio without a partial total | `test_missing_rate_is_error_not_number`, `test_portfolio_value_no_partial_total`, `test_nbp_errors_without_default_value`, `test_schema_errors_are_structured` |
| FR-26, OG-15, ADR-04, NFR-05, KA-05.4 | Zero tools that change state | No such tools; the HTTP client lets only `GET` through | `test_tool_list_exactly_read_only` (set equality), `test_unknown_write_tool`, `test_allowlist_only_get_https_api_nbp` |
| FR-30* | `GET` only, no access to project files or to the model key | Hook `_check_request`: `GET`, HTTPS, host `api.nbp.pl`; the server neither reads nor writes files | `test_allowlist_only_get_https_api_nbp`, `test_redirects_disabled` |
| OG-04, ADR-06 | The MCP server holds no model key and refuses to start when it detects one | `check_environment`: exit code 3, variable name on stderr, never its value | `test_refuses_to_start_with_model_key`, `test_stdio_refuses_to_start_with_model_key` |
| OG-07, OG-12 | The only external integration: `api.nbp.pl`, Table A, no key | `BASE_URL`, host allowlist, no redirects | as above |
| OG-09 | NBP is queried sparingly and in batches | One `tables/a/{start}/{end}` request returns a 14-day window for **all** currencies; in-memory LRU cache (128 entries, TTL 10 min for a window including today, 12 h for archived ones); request counter in the log (stderr) | `test_cache_one_request_per_window`, `test_portfolio_value_single_request`, `test_cache_expires_after_ttl`, `test_cache_has_entry_limit` |
| FR-04 (by analogy) | An NBP outage degrades to older data with a flag | One retry after HTTP 5xx, a timeout or a connection error; after a second failure a stale cache entry is returned with `is_current: false`; without an entry: error `nbp_unavailable` | `test_nbp_outage_returns_stale_cache_with_flag`, `test_retry_*`, `test_no_retry_*` |
| OG-19 | Tests without network | `httpx.MockTransport` with a recording; the live test sits behind the `live` marker | `pytest -q` (default `-m 'not live'`) |
| OG-22 | External responses are untrusted and pass shape validation | 1 MB limit read as a stream, Pydantic models (`table == "A"`, table number pattern, code `[A-Z]{3}`, `0 < mid < 10^6`, at most 94 tables) | `test_nbp_errors_without_default_value[garbage, oversized]` |
| OG-25 | ISO 8601 dates, NBP business day, forward-fill | Format `YYYY-MM-DD`, timezone `Europe/Warsaw`, range from 2002-01-02 to today | `test_invalid_date`, `test_future_date_rejected` |
| UC-08*, KA-08.3 | Validation (code from the list, ISO, not in the future) **before** the request | `validate_*` before `NBPClient.rate`; for `portfolio_value` every position is validated before the first request; list of Table A currencies; `maxItems: 50` in the schema | `test_validation_before_request`, `test_portfolio_value_validates_all_positions_before_request`, `test_schema_errors_are_structured` (the fake receives no request) |
| KA-08.1 | Tool list exactly equal to the expected one | Set equality, not a presence check | `test_tool_list_exactly_read_only` |
| KA-08.2 | `get_nbp_rate("EUR", "2026-06-12")`: rate, code, requested date, quote date, source; schema conformance | Model `Rate` as `outputSchema` | `test_get_nbp_rate_result_shape`, `test_live.py` |
| KA-08.4 | Missing rate and empty cache: error without numeric fields | as FR-31 | `test_server.py::error()` checks that all fields are text |
| KA-08.5* | `POST`/`PUT`/`DELETE` rejected | No backend. The client blocks every method other than `GET` before sending | `test_allowlist_only_get_https_api_nbp[POST, DELETE]` |
| REQ-29 | Tool descriptions **and parameter schemas** are versioned and reviewed like code | Descriptions in `descriptions.py` with `DESCRIPTIONS_VERSION` (currently 2.1.0); the test pins the SHA-256 of the whole `tools/list` manifest (descriptions, `inputSchema`, `outputSchema`, annotations) and of the server instructions | `test_descriptions.py`: changing a field description, a limit or a type in `server.py` without a version bump and a new digest turns the test red |
| REQ-48 | An extension passes a review of its whole content before installation | Code and descriptions are public in this repository; threat model below | PR review |
| ADR-02* | MCP as the mechanism integrating NBP rates with the agent | There is an MCP server. Deviation: it is not a backend adapter | — |
| OG-14* | Separate process, `stdio`, on the participant's machine | Met except "talks to the backend via `X-API-Key`" | `test_e2e_stdio.py` |
| NFR-09 / RY-03 | The cockpit works without the MCP server; the server is simple to set up | The server is independent of the backend; one venv, no Docker or Node | Docker test (PR description) |

## Deviations from the arc42 definition

Trainer's decision of 2026-10-05: **the server queries `api.nbp.pl` directly instead of being a GET adapter to
the cockpit backend.** This lets it run on a participant's clean machine without a running backend and without
an `X-API-Key`. The definition will be adjusted in a separate issue in the workshop repository.

| Place in the definition | Definition text | In this version |
|---|---|---|
| OG-14 | Communication with the backend only through the public API with `X-API-Key` | No backend and no key. The only outgoing traffic is HTTPS GET to `api.nbp.pl` (allowlist) |
| FR-30 | `GET` to the backend with a separate read-only key | `GET` to `api.nbp.pl` without a key (public service). No files and no model key, unchanged |
| FR-28 | The portfolio-value tool (`data`, optional currencies) computes the portfolio from the backend | `portfolio_value(positions, date)`: the caller supplies the portfolio (currency + amount), the server stores nothing |
| ADR-02 | Thin adapter without domain logic | The server has its own forward-fill, cache and `Decimal` conversion (a copy of rules FR-01 and FR-03) |
| §6.2 (07, "Agent asks the MCP server for a rate") | `MCP → GET /api/rates/EUR?asOf=…` to the backend, cache in `nbp/cache` | `MCP → GET api.nbp.pl/api/exchangerates/tables/a/{start}/{end}/`, cache in server memory |
| KA-08.5 | The backend rejects `POST`/`PUT`/`DELETE` with `403` | There is no backend, so there is no `403`. The server exposes no write, and the HTTP client rejects every method other than `GET` before the request leaves (`test_allowlist_only_get_https_api_nbp`) |
| UC-08 | Precondition: the backend runs and a read-only key is in a variable | Precondition: access to `api.nbp.pl`. Alternative flow 4a (wrong key) does not occur |
| 02, 06, 08 (context, building block view, secrets) | Arrow MCP → cockpit; the server's only secret is the read-only MCP key | Arrow MCP → `api.nbp.pl`; the server has **no secret at all** |
| OG-12 | Offline fallback from seeded data so the application works without network | **No fallback from seeded data.** Without network the server returns only entries it already has in its in-memory cache (with `is_current: false`). After a process restart the cache is empty and every question ends with an `nbp_unavailable` error. This version ships no built-in data |
| OG-22 | Untrusted content reaching the agent is wrapped in an unambiguous delimiter | **No delimiter.** Instead the server passes no free text from NBP to the agent. The result consists only of fields that passed shape validation: code `[A-Z]{3}`, decimal number, date, table number matching `NNN/A/NBP/YYYY`. The server drops the currency name and the body of NBP HTTP errors. Error messages come from the server, not from NBP |

## Threat model

| Threat | Vector | Control in the server |
|---|---|---|
| The agent performs a state-changing operation | Prompt injection in content the agent reads | No write tools; the client knows only `GET` |
| Data exfiltration (SSRF, exfiltration) | Crafted parameter or redirect | Parameters never reach the host or URL scheme; allowlist `https://api.nbp.pl`; `follow_redirects=False`; currency code checked against a closed list before the path is built |
| Poisoned or oversized source response | NBP compromise or outage, man in the middle | HTTPS only with certificate verification; 1 MB limit; Pydantic shape validation; error instead of partial data |
| Hallucinated number after an error | The agent "fills in" a missing rate | Structured error without numeric fields; the server instructions forbid replacing an error with a number |
| Poisoned tool description (*tool poisoning*) | Change to `descriptions.py` in the supply chain | Descriptions in one file, versioned, with a pinned SHA-256 in a test (REQ-29); install from a reviewed commit |
| Denial of wallet, model key leak | The MCP process inherits the model key | Refusal to start with a non-empty key (ADR-06) |
| Abuse of the public API (OG-09) | Agent loop hammering NBP | In-memory cache, one request per window for all currencies, timeouts of 5 s per connection and 10 s per request, at most 50 portfolio positions |
| Protocol corruption and stack trace leaks | Logs on stdout, exceptions in responses | Logs only on stderr; tool errors as `isError` with a short reason and no stack trace |
| Traces on disk | File cache, logs | Nothing is written to disk; the cache lives only in process memory |

Outgoing proxy. The HTTP client keeps the httpx default `trust_env=True`, so it honours `HTTPS_PROXY`,
`ALL_PROXY`, `NO_PROXY` and `SSL_CERT_FILE`/`SSL_CERT_DIR` from the environment of the server process. On
a corporate network this is what lets the server reach `api.nbp.pl`. It also means that whoever controls
that environment (the `claude mcp add -e` options, the shell profile, the MCP configuration file) decides
which proxy sees the traffic and which certificate authorities are trusted: a proxy with an added root CA can
read and change the NBP responses. The host allowlist still applies to the target URL, and every response
still passes the size limit and shape validation, so a tampered rate stays a well-formed number: treat the
proxy and CA settings of the server process as part of the trust boundary and set them only from a reviewed
configuration.

Limitations: the Table A currency list is hard-coded (as of 2026-10-05). When NBP adds a currency,
`TABLE_A_CURRENCIES` has to be updated. The server does not support Tables B and C.

## License

MIT, see [`../LICENSE`](../LICENSE).
