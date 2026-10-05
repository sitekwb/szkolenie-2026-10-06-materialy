"""Offline tests for agent.py: a fake `query` replaces the Claude Code CLI."""

from __future__ import annotations

import asyncio
import csv
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, Message, ResultError, ResultMessage

import agent

DEMO_DIR = Path(__file__).resolve().parent.parent


def _result(subtype: str, result: str | None) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=subtype != "success",
        num_turns=1,
        session_id="test",
        result=result,
    )


def test_options_match_slide() -> None:
    opts = agent.build_options()
    assert opts.allowed_tools == ["Read"]
    assert opts.permission_mode == "dontAsk"
    assert opts.max_turns == 3
    assert agent.PROMPT == "Przeczytaj kursy.csv, zapisz raport.md"


def test_success_prints_subtype_and_text(capsys: pytest.CaptureFixture[str]) -> None:
    seen: dict[str, Any] = {}

    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        seen["prompt"] = prompt
        yield _result("success", "Write denied, raport.md not created.")

    assert asyncio.run(agent.run(fake_query)) == 0
    assert seen["prompt"] == agent.PROMPT
    assert capsys.readouterr().out == "success Write denied, raport.md not created.\n"


def test_max_turns_error_is_readable(capsys: pytest.CaptureFixture[str]) -> None:
    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        yield _result("error_max_turns", None)
        raise ResultError(
            "Claude Code returned an error result",
            data={"subtype": "error_max_turns", "terminal_reason": "max_turns"},
            exit_code=1,
        )

    assert asyncio.run(agent.run(fake_query)) == 1
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "error_max_turns None",
        "Run stopped: error_max_turns (terminal reason: max_turns)",
    ]
    assert "Traceback" not in out


def test_rates_are_synthetic_and_well_formed() -> None:
    with (DEMO_DIR / "kursy.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows and {r["currency"] for r in rows} <= {"EUR", "USD", "CHF"}
    assert all(float(r["rate_pln"]) > 0 for r in rows)


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1",
    reason="live call to Claude; set RUN_LIVE=1 (uses your Claude Code login)",
)
def test_live_write_is_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copy(DEMO_DIR / "kursy.csv", tmp_path / "kursy.csv")
    monkeypatch.chdir(tmp_path)
    assert asyncio.run(agent.run()) in (0, 1)
    assert not (tmp_path / "raport.md").exists()
