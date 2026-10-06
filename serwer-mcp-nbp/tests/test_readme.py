"""README stays in sync with the code: the model-key variables listed under Troubleshooting."""

from __future__ import annotations

import re
from pathlib import Path

from nbp_mcp_server.server import MODEL_KEY_VARIABLES

README = Path(__file__).resolve().parents[1] / "README.md"
_BLOCK = re.compile(
    r"<!-- model-key-variables:[^>]*-->\n(?P<body>.*?)<!-- /model-key-variables -->",
    re.DOTALL,
)
_CLAUDE_MCP_ADD = re.compile(r"claude mcp add nbp .*?nbp-mcp-server", re.DOTALL)


def _readme() -> str:
    """Return the README text."""
    return README.read_text(encoding="utf-8")


def test_readme_lists_exactly_model_key_variables() -> None:
    """The Troubleshooting list equals ``MODEL_KEY_VARIABLES`` (no name missing, none extra)."""
    match = _BLOCK.search(_readme())
    assert match, "model-key-variables block missing from README.md"
    listed = re.findall(r"^- `([A-Z0-9_]+)`$", match["body"], re.MULTILINE)
    assert sorted(listed) == sorted(MODEL_KEY_VARIABLES)


def test_readme_add_command_overrides_every_variable() -> None:
    """Every ``claude mcp add nbp`` command with ``-e`` overrides each variable with an empty value."""
    commands = [c for c in _CLAUDE_MCP_ADD.findall(_readme()) if " -e " in c]
    assert commands, "no 'claude mcp add nbp ... -e' command in README.md"
    for command in commands:
        assert command.index("nbp") < command.index(" -e ")
        assert " -- " in command
        for name in MODEL_KEY_VARIABLES:
            assert f"-e {name}= " in command, f"{name} not overridden in: {command}"
