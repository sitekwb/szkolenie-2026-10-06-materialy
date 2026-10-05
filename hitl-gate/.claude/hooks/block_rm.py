#!/usr/bin/env python3
"""Claude Code ``PreToolUse`` hook that hard-blocks file deletion in Bash calls.

Claude Code runs this script before every ``Bash`` tool call and writes the
hook input as JSON to stdin (``tool_name``, ``tool_input.command`` and more).
Exit code 2 blocks the call and Claude Code passes stderr to the agent as the
reason; exit code 0 lets the normal permission flow continue.

A permission rule such as ``Bash(rm *)`` matches the command text, so it misses
``/bin/rm -rf build/`` or ``bash -c 'rm -rf build/'``. This hook parses the
command instead:

* it splits lists, pipelines, subshells, groups and new lines, and treats shell
  keywords (``if``, ``then``, ``do``, ``{``, ``!`` ...) as the start of a new command;
* it strips paths, quotes, backslashes, redirections and leading ``VAR=value``;
* it unwraps ``sudo``, ``env``, ``nice``, ``timeout``, ``xargs``, ``busybox`` and
  similar wrappers, including their options that take a value;
* it re-checks the code a shell runs: ``bash -c``, ``eval``, ``$(...)``, backticks,
  heredocs and here-strings, ``echo ... | sh`` and aliases;
* it blocks ``rm``, ``rmdir``, ``unlink``, ``shred``, ``find -delete`` and ``git clean``.

It fails closed: a command name computed at run time (``$CMD``) and input it
cannot parse are blocked. Script files and other piped input are not visible to
it and pass.
It only checks command names; see the README for what it does not catch.

Standard library only; Python 3.12 or newer.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Final

BLOCKED_COMMANDS: Final = frozenset({"rm", "rmdir", "unlink", "shred"})
"""Command basenames that delete files."""

SHELLS: Final = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish", "ash"})
"""Shells that run a command string (``-c``), stdin or a script file."""

WRAPPER_OPTIONS_WITH_VALUE: Final[dict[str, frozenset[str]]] = {
    "sudo": frozenset({"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "-R"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-u", "-C", "--unset", "--chdir"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"}),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "xargs": frozenset({"-I", "-L", "-n", "-P", "-s", "-d", "-E", "-a", "--arg-file", "--delimiter"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "time": frozenset({"-f", "-o", "--format", "--output"}),
    "chrt": frozenset(),
    "taskset": frozenset(),
    "nohup": frozenset(),
    "exec": frozenset({"-a"}),
    "builtin": frozenset(),
    "command": frozenset(),
    "busybox": frozenset(),
    "toybox": frozenset(),
    "setsid": frozenset(),
    "unbuffer": frozenset(),
    "watch": frozenset({"-n", "-d", "--interval"}),
}
"""Commands that run their remaining words as another command, with their value-taking options."""

WRAPPER_POSITIONALS: Final[dict[str, int]] = {"timeout": 1, "chrt": 1, "taskset": 1}
"""Wrappers that take positional arguments before the wrapped command (duration, priority, mask)."""

KEYWORDS: Final = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "time"})
"""Reserved words after which a new command starts."""

CLAUSE_WORDS: Final = frozenset({"for", "select", "case", "fi", "done", "esac", "in"})
"""Reserved words that start or end a clause containing no command of its own."""

LOOKUP_COMMANDS: Final = frozenset({"type", "which", "whereis", "man", "help", "hash", "apropos", "info"})
"""Commands that only look a name up; ``type rm`` is not ``rm``."""

FIND_EXEC: Final = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
"""``find`` actions that run another command."""

OPERATORS: Final = ";&|()\n"
REDIRECTION: Final = re.compile(r"^\d*[<>]")
ASSIGNMENT: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
SUBSTITUTION: Final = re.compile(r"\$\(([^()]*)\)|`([^`]*)`|<\(([^()]*)\)")
HEREDOC: Final = re.compile(r"<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
MAX_DEPTH: Final = 8
HEREDOC_PLACEHOLDER: Final = "\0heredoc"


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


@dataclass(slots=True)
class Segment:
    """One simple command and how its stdin is fed."""

    words: list[str] = field(default_factory=list)
    piped_from: Segment | None = None
    """The previous command of a ``|`` pipeline, if any."""
    stdin_code: list[str] = field(default_factory=list)
    """Heredoc bodies and here-strings attached to this command."""


def _block(reason: str) -> Verdict:
    return Verdict(blocked=True, reason=reason)


def _split_heredocs(command: str) -> tuple[str, dict[int, list[str]]]:
    """Remove heredoc bodies from ``command``.

    Returns:
        The command without bodies, and the bodies keyed by the index of the line
        that opened them, in source order.

    """
    lines = command.split("\n")
    kept: list[str] = []
    bodies: dict[int, list[str]] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        for match in HEREDOC.finditer(line):
            delimiter = match.group(2)
            body: list[str] = []
            while index < len(lines) and lines[index].strip() != delimiter:
                body.append(lines[index])
                index += 1
            index += 1
            bodies.setdefault(len(kept) - 1, []).append("\n".join(body))
    return "\n".join(kept), bodies


def _tokenize(command: str) -> list[str]:
    """Tokenize like a POSIX shell, keeping operators and new lines as separate tokens.

    Raises:
        ValueError: when quotes are unbalanced.

    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=OPERATORS + "<>")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _segments(command: str) -> Iterator[Segment]:
    """Split a command line into simple commands, attaching pipes, heredocs and here-strings.

    Raises:
        ValueError: when quotes are unbalanced.

    """
    text, _ = _split_heredocs(command)
    current = Segment()
    tokens = iter(_tokenize(text))
    for token in tokens:
        if token and set(token) <= set(OPERATORS):
            nxt = Segment(piped_from=current if token in {"|", "|&"} else None)
            yield current
            current = nxt
        elif REDIRECTION.match(token) or (token and set(token) <= set("<>&")):
            target = next(tokens, "")
            if token.endswith("<<<"):
                current.stdin_code.append(target)
            elif token.endswith(("<<", "<<-")):
                current.stdin_code.append(HEREDOC_PLACEHOLDER)
        else:
            current.words.append(token)
    yield current


def _attach_heredocs(segments: list[Segment], command: str) -> None:
    """Replace heredoc placeholders with the bodies, in the order they appear."""
    _, bodies = _split_heredocs(command)
    ordered = [body for _, group in sorted(bodies.items()) for body in group]
    for segment in segments:
        for index, code in enumerate(segment.stdin_code):
            if code == HEREDOC_PLACEHOLDER:
                segment.stdin_code[index] = ordered.pop(0) if ordered else ""


def _strip_prefix(words: list[str]) -> list[str]:
    """Drop leading reserved words and ``VAR=value`` assignments."""
    rest = list(words)
    while rest:
        match rest[0]:
            case "time":
                rest.pop(0)
                while rest and rest[0] in {"-p", "--"}:
                    rest.pop(0)
            case "function":
                del rest[:2]
            case word if word in KEYWORDS or ASSIGNMENT.match(word):
                rest.pop(0)
            case _:
                break
    return rest


def _unwrap(name: str, args: list[str]) -> list[str]:
    """Return the wrapped command of a wrapper call (options and positionals removed)."""
    with_value = WRAPPER_OPTIONS_WITH_VALUE[name]
    rest = list(args)
    while rest and rest[0].startswith("-") and rest[0] != "-":
        option = rest.pop(0)
        if option == "--":
            break
        if option in with_value and rest:
            rest.pop(0)
    while rest and ASSIGNMENT.match(rest[0]):
        rest.pop(0)
    return rest[WRAPPER_POSITIONALS.get(name, 0) :]


def _check_shell(words: list[str], segment: Segment, depth: int) -> Verdict:
    """Check a shell invocation: ``-c`` string, stdin, heredoc or script file."""
    args = words[1:]
    for index, word in enumerate(args):
        if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
            if index + 1 >= len(args):
                return _block("shell -c without a command string")
            return check_command(args[index + 1], depth + 1)
    if any(not word.startswith(("-", "+")) for word in args):
        return ALLOWED  # a script file: its content is not visible to the hook (see README)
    for code in segment.stdin_code:
        if (verdict := check_command(code, depth + 1)).blocked:
            return verdict
    if (source := segment.piped_from) is not None:
        feeder = _strip_prefix(source.words)
        if feeder and os.path.basename(feeder[0]) in {"echo", "printf"}:
            text = " ".join(word for word in feeder[1:] if not word.startswith("-"))
            return check_command(text.replace("\\n", "\n"), depth + 1)
    return ALLOWED


def _check_words(words: list[str], segment: Segment, depth: int) -> Verdict:
    """Inspect one simple command, given as words."""
    rest = _strip_prefix(words)
    while rest:
        head = rest[0]
        name = os.path.basename(head)
        match name:
            case _ if "$" in head or "`" in head:
                return _block(f"command name {head!r} is computed at run time and cannot be checked")
            case _ if name in BLOCKED_COMMANDS:
                return _block(f"{name!r} deletes files (seen as {head!r})")
            case _ if name in CLAUSE_WORDS or name in LOOKUP_COMMANDS:
                return ALLOWED
            case "command" if any(word in {"-v", "-V"} for word in rest[1:]):
                return ALLOWED
            case _ if name in SHELLS:
                return _check_shell(rest, segment, depth)
            case "eval":
                return check_command(" ".join(rest[1:]), depth + 1)
            case "alias":
                for word in rest[1:]:
                    _, sep, value = word.partition("=")
                    if sep and (verdict := check_command(value, depth + 1)).blocked:
                        return verdict
                return ALLOWED
            case "git":
                subcommand = _unwrap_git(rest[1:])
                if subcommand[:1] == ["clean"]:
                    return _block("'git clean' deletes untracked files")
                return ALLOWED
            case "find":
                if "-delete" in rest:
                    return _block("'find -delete' deletes files")
                for index, word in enumerate(rest):
                    if (
                        word in FIND_EXEC
                        and (verdict := _check_words(rest[index + 1 :], segment, depth)).blocked
                    ):
                        return verdict
                return ALLOWED
            case "env" if any(word in {"-S", "--split-string"} for word in rest[1:]):
                position = next(i for i, w in enumerate(rest) if w in {"-S", "--split-string"})
                return check_command(" ".join(rest[position + 1 :]), depth + 1)
            case _ if name in WRAPPER_OPTIONS_WITH_VALUE:
                rest = _strip_prefix(_unwrap(name, rest[1:]))
                if not rest:
                    return ALLOWED
                continue
            case _:
                return ALLOWED
    return ALLOWED


def _unwrap_git(args: list[str]) -> list[str]:
    """Skip git global options (``-C dir``, ``-c key=value`` ...) and return the subcommand onward."""
    rest = list(args)
    while rest and rest[0].startswith("-"):
        option = rest.pop(0)
        if option in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"} and rest:
            rest.pop(0)
    return rest


def check_command(command: str, depth: int = 0) -> Verdict:
    """Decide whether a Bash command line deletes files.

    Args:
        command: The command line exactly as the agent sent it.
        depth: Current nesting level of ``bash -c``/``eval``/substitutions.

    Returns:
        A blocking verdict with a reason, or ``ALLOWED``.

    """
    if depth > MAX_DEPTH:
        return _block("command is nested too deeply to check")
    for match in SUBSTITUTION.finditer(command):
        inner = next(group for group in match.groups() if group is not None)
        if (verdict := check_command(inner, depth + 1)).blocked:
            return verdict
    try:
        segments = list(_segments(command))
    except ValueError as error:
        return _block(f"command cannot be parsed ({error})")
    _attach_heredocs(segments, command)
    for segment in segments:
        if (verdict := _check_words(segment.words, segment, depth)).blocked:
            return verdict
    return ALLOWED


def decide(payload: object) -> Verdict:
    """Decide on a decoded ``PreToolUse`` hook input."""
    if not isinstance(payload, dict):
        return _block("hook input is not a JSON object")
    if payload.get("tool_name") != "Bash":
        return ALLOWED
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return _block("Bash call without a string 'command'")
    return check_command(command)


def main() -> int:
    """Read the hook input from stdin, print a reason on block, return the exit code."""
    try:
        payload: object = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        verdict = _block(f"hook input is not valid JSON ({error.msg})")
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
