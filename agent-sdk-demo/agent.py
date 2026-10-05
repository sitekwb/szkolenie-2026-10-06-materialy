"""Minimal Claude Agent SDK agent from the workshop stage "Claude Agent SDK".

The permission boundary lives in the options, not in the prompt:

* ``allowed_tools=["Read"]`` pre-approves ``Read`` (it does not remove other tools),
* ``permission_mode="dontAsk"`` denies every call that would need approval,
  so the requested write of ``raport.md`` is denied,
* ``max_turns`` (3) caps the agent loop; when exceeded the result subtype is
  ``error_max_turns``.

Exercise: set the turn limit in build_options() to 1 and run ``python agent.py``.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable

from claude_agent_sdk import ClaudeAgentOptions, Message, ResultError, ResultMessage, query

# Same prompt as on the slide and in the equivalent `claude -p` command.
PROMPT = "Przeczytaj kursy.csv, zapisz raport.md"

type QueryFn = Callable[..., AsyncIterator[Message]]


def build_options() -> ClaudeAgentOptions:
    """Return the agent options that define the permission boundary."""
    return ClaudeAgentOptions(allowed_tools=["Read"], permission_mode="dontAsk", max_turns=3)


async def run(query_fn: QueryFn = query) -> int:
    """Run the agent once and print the result subtype and text.

    Args:
        query_fn: The SDK ``query`` function; replaceable in tests.

    Returns:
        Process exit code: 0 on success, 1 when Claude Code reports an error result.
    """
    try:
        async for msg in query_fn(prompt=PROMPT, options=build_options()):
            if isinstance(msg, ResultMessage):
                print(msg.subtype, msg.result)
    except ResultError as err:
        # The CLI ends a failed run (e.g. error_max_turns) with a non-zero exit;
        # the SDK raises ResultError. Show why, without a traceback.
        print(f"Run stopped: {err.subtype} (terminal reason: {err.terminal_reason})")
        return 1
    return 0


def main() -> None:
    """Entry point for ``python agent.py``."""
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
