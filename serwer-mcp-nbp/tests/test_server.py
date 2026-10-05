"""In-process MCP contract tests (KA-08.1-KA-08.4, KA-05.4, NFR-05, NFR-08)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
from fake_nbp import FakeNBP
from mcp import Client
from mcp_types import CallToolResult, TextContent

from nbp_mcp_server import nbp
from nbp_mcp_server.server import TOOL_NAMES, build_server, check_environment


@pytest.fixture
def fake() -> FakeNBP:
    return FakeNBP()


@pytest.fixture
async def mcp_client(fake: FakeNBP) -> AsyncIterator[Client]:
    server = build_server(nbp.NBPClient(nbp.create_http_client(fake.transport())))
    async with Client(server) as c:
        yield c


pytestmark = pytest.mark.anyio


def text(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def error(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error is True
    assert result.structured_content is None
    # The SDK prefixes the message with "Error executing tool <name>: ".
    data: dict[str, Any] = json.loads(text(result).partition(": ")[2])
    assert set(data) == {"error", "reason"}
    assert all(isinstance(v, str) for v in data.values()), "KA-08.4: no numeric field"
    return data


async def test_tool_list_exactly_read_only(mcp_client: Client) -> None:
    tools = (await mcp_client.list_tools()).tools
    assert {t.name for t in tools} == TOOL_NAMES == {"get_nbp_rate", "convert_to_pln", "portfolio_value"}
    for t in tools:
        assert t.annotations is not None
        assert t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False
        assert t.output_schema is not None


async def test_get_nbp_rate_result_shape(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_nbp_rate", {"currency": "EUR", "date": "2026-06-12"})
    assert result.is_error is False
    assert result.structured_content == {
        "currency": "EUR",
        "rate": "4.2484",
        "requested_date": "2026-06-12",
        "quote_date": "2026-06-12",
        "forward_fill": False,
        "table_number": "112/A/NBP/2026",
        "source": "NBP, Table A of average exchange rates (api.nbp.pl)",
        "is_current": True,
    }


async def test_get_nbp_rate_forward_fill_on_holiday(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("get_nbp_rate", {"currency": "usd", "date": "2026-06-04"})
    assert result.structured_content is not None
    assert result.structured_content["quote_date"] == "2026-06-03"
    assert result.structured_content["forward_fill"] is True


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"currency": "BTC", "date": "2026-06-12"}, "invalid_currency"),
        ({"currency": "EUR", "date": "2999-01-01"}, "invalid_date"),
        ({"currency": "EUR", "date": "12/06/2026"}, "invalid_date"),
    ],
)
async def test_validation_before_request(
    mcp_client: Client, fake: FakeNBP, arguments: dict[str, Any], code: str
) -> None:
    assert error(await mcp_client.call_tool("get_nbp_rate", arguments))["error"] == code
    assert fake.requests == [], "KA-08.3: validation before any NBP request"


async def test_missing_rate_is_error_not_number(mcp_client: Client, fake: FakeNBP) -> None:
    fake.mode = "outage"
    result = await mcp_client.call_tool("get_nbp_rate", {"currency": "EUR", "date": "2026-06-12"})
    assert error(result)["error"] == "nbp_unavailable"


async def test_convert_to_pln(mcp_client: Client) -> None:
    result = await mcp_client.call_tool(
        "convert_to_pln", {"amount": "1250.50", "currency": "EUR", "date": "2026-06-13"}
    )
    assert result.structured_content is not None
    assert result.structured_content["amount_pln"] == "5312.62"  # 1250.50 * 4.2484 = 5312.6242
    assert result.structured_content["rate"]["quote_date"] == "2026-06-12"


async def test_convert_invalid_amount(mcp_client: Client) -> None:
    result = await mcp_client.call_tool(
        "convert_to_pln", {"amount": "1e10", "currency": "EUR", "date": "2026-06-12"}
    )
    assert error(result)["error"] == "invalid_amount"


async def test_portfolio_value_single_request(mcp_client: Client, fake: FakeNBP) -> None:
    positions = [
        {"currency": "EUR", "amount": "1000.00"},
        {"currency": "USD", "amount": "2500"},
        {"currency": "PLN", "amount": "100.10"},
    ]
    result = await mcp_client.call_tool("portfolio_value", {"date": "2026-06-12", "positions": positions})
    data = result.structured_content
    assert data is not None
    totals = [p["amount_pln"] for p in data["positions"]]
    assert data["total_pln"] == str(sum(map(nbp.validate_amount, totals)))
    assert totals[0] == "4248.40"
    assert totals[2] == "100.10"
    assert len(fake.requests) == 1, "OG-09: one batch request for all currencies"


async def test_portfolio_value_no_partial_total(mcp_client: Client) -> None:
    positions = [{"currency": "EUR", "amount": "1"}, {"currency": "THB", "amount": "1"}]
    result = await mcp_client.call_tool("portfolio_value", {"date": "2026-06-12", "positions": positions})
    assert error(result)["error"] == "no_quote"


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        ({"currency": "EUR", "amount": "-5"}, "invalid_amount"),
        ({"currency": "BTC", "amount": "1"}, "invalid_currency"),
    ],
    ids=["bad-amount", "bad-currency"],
)
async def test_portfolio_value_validates_all_positions_before_request(
    mcp_client: Client, fake: FakeNBP, bad: dict[str, str], code: str
) -> None:
    positions = [{"currency": "EUR", "amount": "1"}, {"currency": "USD", "amount": "2"}, bad]
    result = await mcp_client.call_tool("portfolio_value", {"date": "2026-06-12", "positions": positions})
    assert error(result)["error"] == code
    assert fake.requests == [], "KA-08.3: an invalid last position must not cost an NBP request"


@pytest.mark.parametrize(
    "positions",
    [[], [{"currency": "EUR", "amount": "1", "hidden": "x"}]],
    ids=["empty", "extra-field"],
)
async def test_portfolio_value_schema_validation(mcp_client: Client, positions: list[dict[str, str]]) -> None:
    result = await mcp_client.call_tool("portfolio_value", {"positions": positions})
    assert result.is_error is True
    assert "Traceback" not in text(result)


async def test_unknown_write_tool(mcp_client: Client) -> None:
    result = await mcp_client.call_tool("create_transfer", {"amount": "1"})
    assert result.is_error is True


@pytest.mark.parametrize("variable", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY"])
def test_refuses_to_start_with_model_key(variable: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        check_environment({variable: "sk-secret-123"})
    assert e.value.code == 3
    err = capsys.readouterr().err
    assert variable in err
    assert "sk-secret-123" not in err


def test_empty_key_does_not_block() -> None:
    check_environment({"ANTHROPIC_API_KEY": "", "PATH": "/usr/bin"})


async def test_portfolio_value_too_many_positions(mcp_client: Client, fake: FakeNBP) -> None:
    positions = [{"currency": "EUR", "amount": "1"}] * (nbp.MAX_PORTFOLIO_POSITIONS + 1)
    result = await mcp_client.call_tool("portfolio_value", {"positions": positions})
    assert result.is_error is True
    assert "Traceback" not in text(result)
    assert fake.requests == [], "maxItems is enforced by schema validation before the tool runs"


@pytest.mark.parametrize("amount", ["0", "-100"])
async def test_convert_non_positive_amount(mcp_client: Client, amount: str) -> None:
    result = await mcp_client.call_tool(
        "convert_to_pln", {"amount": amount, "currency": "EUR", "date": "2026-06-12"}
    )
    assert error(result)["error"] == "invalid_amount"


async def test_convert_jpy_rate_per_unit(mcp_client: Client) -> None:
    result = await mcp_client.call_tool(
        "convert_to_pln", {"amount": "100000", "currency": "JPY", "date": "2026-06-12"}
    )
    data = result.structured_content
    assert data is not None
    rate = Decimal(data["rate"]["rate"])
    assert rate < 1, "NBP quotes JPY per 1 unit, not per 100"
    assert data["amount_pln"] == str(nbp.round_to_grosz(100000 * rate))
    assert (data["rate"]["rate"], data["amount_pln"]) == ("0.022923", "2292.30")
