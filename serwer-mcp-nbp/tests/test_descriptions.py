"""Review test of the tool surface (REQ-29).

We pin the SHA-256 of the canonical JSON of what the agent actually receives from ``tools/list``
(names, titles, descriptions, ``inputSchema``, ``outputSchema``, annotations) and of the server
instructions. Changing a description, field, limit or type in ``server.py`` or ``descriptions.py``
without bumping ``DESCRIPTIONS_VERSION`` and adding the new digest after review fails the test.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from mcp import Client

from nbp_mcp_server import descriptions, nbp
from nbp_mcp_server.server import build_server

pytestmark = pytest.mark.anyio

REVIEWED_DIGESTS: dict[str, str] = {
    # Audit trail of every reviewed manifest: one line per DESCRIPTIONS_VERSION, append only.
    # The test checks only the current version; older lines record what was approved before.
    # 1.1.0: original release with Polish tool, parameter and field names.
    "1.1.0": "0923b3c23014bb4a36c9978686161702d6f092509bbac9fb7284f217758d9bc6",
    # 2.0.0: everything renamed to English, portfolio maxItems added to the input schema.
    "2.0.0": "c4b5fb8d5cb42b1cf459947f8000169e0dba7271e3b5c5fe647a8bc0b8fc748f",
    # 2.1.0: portfolio total is the exact sum rounded once (tool and total_pln descriptions).
    "2.1.0": "ee4d9ceacb7cacaf099233e2861f4b4ad446ed6af72b4b712b990c867f021022",
}
"""Descriptions version -> SHA-256 of the tool manifest approved in code review. Append, do not overwrite."""


async def manifest() -> dict[str, Any]:
    """Return the canonical manifest of tools and instructions, as an MCP client sees it."""
    server = build_server(nbp.NBPClient(nbp.create_http_client()))
    async with Client(server) as c:
        tools = (await c.list_tools()).tools
        instructions = c.instructions
    return {
        "instructions": instructions,
        "tools": sorted(
            (t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools),
            key=lambda t: str(t["name"]),
        ),
    }


def digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


async def test_manifest_reviewed_for_current_version() -> None:
    data = await manifest()
    assert descriptions.DESCRIPTIONS_VERSION in REVIEWED_DIGESTS, (
        "New descriptions version without a review entry"
    )
    assert digest(data) == REVIEWED_DIGESTS[descriptions.DESCRIPTIONS_VERSION], (
        "Tool descriptions or schemas changed without bumping DESCRIPTIONS_VERSION and a review (REQ-29): "
        + digest(data)
    )


async def test_manifest_contains_schemas_and_instructions() -> None:
    data = await manifest()
    assert data["instructions"] == descriptions.SERVER_INSTRUCTIONS
    portfolio = next(t for t in data["tools"] if t["name"] == "portfolio_value")
    assert "inputSchema" in portfolio
    assert "Positive decimal amount" in json.dumps(portfolio["inputSchema"])
    assert portfolio["inputSchema"]["properties"]["positions"]["maxItems"] == nbp.MAX_PORTFOLIO_POSITIONS


async def test_field_description_change_changes_digest() -> None:
    data = await manifest()
    changed = json.loads(json.dumps(data).replace("Positive decimal amount", "Any decimal amount"))
    assert digest(changed) != digest(data)
