# agent-sdk-demo

A minimal [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) agent for the
workshop stage "Claude Agent SDK". It shows that the permission boundary is set by options
(and, in a real service, by the service key), not by the prompt.

| File | Purpose |
|---|---|
| `agent.py` | the script from the slide: `allowed_tools=["Read"]`, `permission_mode="dontAsk"`, `max_turns=3` |
| `kursy.csv` | synthetic exchange rates (not real data) that the agent reads |
| `constraints.txt` | exact dependency versions (`claude-agent-sdk==0.2.163`) |

The prompt is in Polish on purpose: it is the same text as on the slide and in the CLI command below.

## Setup

Requires Python 3.12+ and a Claude Code login. The `claude-agent-sdk` package bundles the
Claude Code executable, so a separate install is usually not needed.

```bash
cd agent-sdk-demo
python3 -m venv .venv
source .venv/bin/activate
pip install -c constraints.txt -r requirements.txt
```

Authentication: for learning on your own account, log in once with `claude` (`/login`) and
leave `ANTHROPIC_API_KEY` unset; the SDK then uses your subscription login. In production use an
API key (`ANTHROPIC_API_KEY`) or a cloud provider instead. Third-party products may not offer
claude.ai login without Anthropic's approval.

## Run

```bash
python agent.py
```

`raport.md` is never created: `dontAsk` denies every call that would need approval, and only `Read` is
pre-approved. Model behaviour varies, so the run ends in one of two ways:

- the subtype `success` and a reply saying the write was denied (exit code 0), or
- the agent reads `kursy.csv`, tries to write the report, the `Write` call is denied, and the third turn is
  used up before a final reply. The script then prints the lines below and exits with code 1:

  ```text
  error_max_turns None
  Run stopped: error_max_turns (terminal reason: max_turns)
  ```

Both results show the same boundary: the agent could not write the file.

The same boundary from the CLI:

```bash
claude -p "Przeczytaj kursy.csv, zapisz raport.md" \
  --allowedTools Read --permission-mode dontAsk --max-turns 3
```

## Exercise: turn limit

In `agent.py` change `max_turns=3` to `max_turns=1` and run `python agent.py` again. Expected:

```text
error_max_turns None
Run stopped: error_max_turns (terminal reason: max_turns)
```

The run hits the limit, there is no reply text (`None`), and the script exits with code 1.
The SDK raises `ResultError` after an error result; `agent.py` catches it and prints the reason
instead of a traceback. Model output varies, so the exact text on success may differ.

## Tests (no Claude call)

```bash
pip install -c constraints.txt -r requirements-dev.txt
ruff check . && ruff format --check . && mypy --strict . && pytest -q
RUN_LIVE=1 pytest -q -k live   # optional: one real call with your login
```
