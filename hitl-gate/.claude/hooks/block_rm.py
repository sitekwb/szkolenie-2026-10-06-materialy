#!/usr/bin/env python3
"""Claude Code ``PreToolUse`` hook that hard-blocks ``rm`` in Bash calls.

Claude Code runs this script before every ``Bash`` tool call and writes the
hook input as JSON to stdin (``tool_name``, ``tool_input.command`` and more).
Exit code 2 blocks the call and Claude Code passes stderr to the agent as the
reason; exit code 0 lets the normal permission flow continue.

A permission rule such as ``Bash(rm *)`` matches the command text, so it misses
``/bin/rm -rf build/`` or ``bash -c 'rm -rf build/'``. This hook parses the
command instead: it splits command lists and pipelines, strips paths, quotes,
backslashes and variable assignments, unwraps ``bash -c``, ``env``, ``sudo``,
``xargs``, ``eval`` and command substitutions, and refuses command names it
cannot resolve (``$CMD -rf build``). It fails closed: unparseable input is
blocked, not allowed.

Standard library only; Python 3.12 or newer.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

BLOCKED_COMMANDS: Final = frozenset({"rm", "rmdir", "unlink", "shred"})
"""Command basenames that delete files."""

SHELLS: Final = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
"""Shells whose ``-c`` argument is itself a command line."""

WRAPPERS: Final = frozenset(
    {"env", "sudo", "doas", "command", "exec", "nohup", "nice", "time", "xargs", "builtin", "timeout"}
)
"""Commands that run their remaining arguments as another command."""

SEPARATORS: Final = frozenset({";", "&&", "||", "|", "&", "|&", ";;", "(", ")", "\n"})
"""Shell control operators that end a simple command."""

FIND_EXEC: Final = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
"""``find`` actions that run another command."""

ASSIGNMENT: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SUBSTITUTION: Final = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
MAX_DEPTH: Final = 8


class Exit(IntEnum):
    """Hook exit codes understood by Claude Code."""

    ALLOW = 0
    """No decision: the normal permission flow applies."""
    BLOCK = 2
    """Blocking error: the tool call is refused and stderr goes to the agent."""


@dataclass(frozen=True, slots=True)
class Verdict:
    """Outcome of inspecting one command line."""

    blocked: bool
    reason: str = ""


ALLOWED: Final = Verdict(blocked=False)


def _simple_commands(tokens: list[str]) -> Iterator[list[str]]:
    """Split a token stream into simple commands at shell control operators."""
    current: list[str] = []
    for token in tokens:
        if token in SEPARATORS or set(token) <= set(";&|()\n"):
            if current:
                yield current
            current = []
        else:
            current.append(token)
    if current:
        yield current


def _tokenize(command: str) -> list[str]:
    """Tokenize a command line like a POSIX shell, keeping operators separate.

    Raises:
        ValueError: when quotes are unbalanced.

    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _check_argv(argv: list[str], depth: int) -> Verdict:  # noqa: PLR0911, PLR0912 - one case per shell form
    """Inspect one simple command (already split into words)."""
    words = list(argv)
    while words:
        head = words[0]
        if ASSIGNMENT.match(head):
            words.pop(0)
            continue
        name = os.path.basename(head)
        match name:
            case _ if "$" in head or "`" in head:
                return Verdict(True, f"command name {head!r} is computed at run time and cannot be checked")
            case _ if name in BLOCKED_COMMANDS:
                return Verdict(True, f"{name!r} deletes files (seen as {head!r})")
            case _ if name in SHELLS:
                rest = words[1:]
                for index, word in enumerate(rest):
                    if word.startswith("-") and not word.startswith("--") and "c" in word:
                        if index + 1 < len(rest):
                            return check_command(rest[index + 1], depth + 1)
                        return ALLOWED
                return ALLOWED
            case "eval":
                return check_command(" ".join(words[1:]), depth + 1)
            case _ if name in WRAPPERS:
                words = [w for w in words[1:] if not w.startswith("-")]
                if name == "timeout" and words:
                    words = words[1:]
                continue
            case "find":
                if "-delete" in words:
                    return Verdict(True, "'find -delete' deletes files")
                for index, word in enumerate(words):
                    if word in FIND_EXEC and (verdict := _check_argv(words[index + 1 :], depth)).blocked:
                        return verdict
                return ALLOWED
            case _:
                return ALLOWED
    return ALLOWED


def check_command(command: str, depth: int = 0) -> Verdict:
    """Decide whether a Bash command line deletes files.

    Args:
        command: The command line exactly as the agent sent it.
        depth: Current nesting level of ``bash -c``/``eval``/substitutions.

    Returns:
        A blocking verdict with a reason, or ``ALLOWED``.

    """
    if depth > MAX_DEPTH:
        return Verdict(True, "command is nested too deeply to check")
    for match in SUBSTITUTION.finditer(command):
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        if (verdict := check_command(inner, depth + 1)).blocked:
            return verdict
    try:
        tokens = _tokenize(command)
    except ValueError as error:
        return Verdict(True, f"command cannot be parsed ({error})")
    for argv in _simple_commands(tokens):
        if (verdict := _check_argv(argv, depth)).blocked:
            return verdict
    return ALLOWED


def decide(payload: object) -> Verdict:
    """Decide on a decoded ``PreToolUse`` hook input."""
    if not isinstance(payload, dict):
        return Verdict(True, "hook input is not a JSON object")
    if payload.get("tool_name") != "Bash":
        return ALLOWED
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return Verdict(True, "Bash call without a string 'command'")
    return check_command(command)


def main() -> int:
    """Read the hook input from stdin, print a reason on block, return the exit code."""
    try:
        payload: object = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        verdict = Verdict(True, f"hook input is not valid JSON ({error.msg})")
    else:
        verdict = decide(payload)
    if not verdict.blocked:
        return Exit.ALLOW
    print(
        f"Blocked by the hitl-gate PreToolUse hook: {verdict.reason}. "
        "Deleting files needs a human: ask the user to run the command themselves.",
        file=sys.stderr,
    )
    return Exit.BLOCK


if __name__ == "__main__":
    sys.exit(main())
