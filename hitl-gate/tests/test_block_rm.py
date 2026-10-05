"""Run the hook as Claude Code does: JSON on stdin, decision in the exit code."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "block_rm.py"
SETTINGS = ROOT / ".claude" / "settings.json"


def run_hook(stdin: str) -> subprocess.CompletedProcess[str]:
    """Run the hook script with ``stdin`` and capture its result."""
    return subprocess.run(
        [sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, check=False, timeout=10
    )


def bash_call(command: str) -> str:
    """Build a PreToolUse payload for a Bash call, as Claude Code sends it."""
    return json.dumps(
        {
            "session_id": "test",
            "cwd": str(ROOT),
            "permission_mode": "default",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command, "description": "test"},
            "tool_use_id": "toolu_test",
        }
    )


@pytest.mark.parametrize(
    "command",
    [
        "rm notatka-hitl.txt",
        "rm -rf build/",
        "/bin/rm -rf build/",
        "/usr/bin/env rm -f x",
        "bash -c 'rm -rf build/'",
        'bash -c "/bin/rm -rf build"',
        "sh -lc 'cd /tmp && rm x'",
        "\\rm x",
        "'rm' x",
        'r""m x',
        "FOO=bar rm -rf tmp/",
        "sudo -n rm x",
        "git status && rm x",
        "ls | xargs rm",
        "echo $(rm x)",
        "echo `rm x`",
        "eval 'rm x'",
        "CMD=rm; $CMD -rf build",
        "${RM:-rm} x",
        "find . -name '*.tmp' -delete",
        "find . -exec rm {} ;",
        "timeout 5 rm x",
        "rmdir build",
        "unlink x",
        "echo 'unbalanced",
    ],
)
def test_blocks_deleting_commands(command: str) -> None:
    result = run_hook(bash_call(command))
    assert result.returncode == 2, (command, result.stderr)
    assert "Blocked by the hitl-gate PreToolUse hook" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "ls -la",
        "echo 'rm is just text here'",
        "grep -r rm .",
        "python3 -m pytest -q",
        "cat notatka-hitl.txt",
        "bash -c 'git status'",
        "find . -name '*.py'",
        "FORM=1 make test",
        "mkdir -p build && touch build/x",
    ],
)
def test_allows_safe_commands(command: str) -> None:
    result = run_hook(bash_call(command))
    assert result.returncode == 0, (command, result.stderr)
    assert result.stderr == ""


def test_reason_names_the_command() -> None:
    result = run_hook(bash_call("bash -c '/bin/rm -rf build'"))
    assert result.returncode == 2
    assert "'rm' deletes files (seen as '/bin/rm')" in result.stderr


def test_other_tools_pass_through() -> None:
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "rm.txt", "content": "rm -rf /"}})
    assert run_hook(payload).returncode == 0


@pytest.mark.parametrize(
    "stdin",
    [
        "not json",
        "[]",
        json.dumps({"tool_name": "Bash", "tool_input": {}}),
        json.dumps({"tool_name": "Bash"}),
    ],
)
def test_malformed_input_fails_closed(stdin: str) -> None:
    result = run_hook(stdin)
    assert result.returncode == 2
    assert result.stderr


def test_settings_wire_the_hook_and_rules() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash"
    assert entry["hooks"][0]["type"] == "command"
    assert ".claude/hooks/block_rm.py" in entry["hooks"][0]["command"]
    rules = settings["permissions"]
    assert "Bash(rm *)" in rules["deny"]
    assert set(rules) >= {"deny", "ask", "allow"}
