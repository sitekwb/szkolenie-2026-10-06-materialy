"""MCP server (stdio transport) with three read-only tools: rate, conversion, portfolio.

stdout is the JSON-RPC protocol channel, so all logs go to stderr. The server has no tools that
change state (OG-15, ADR-04, NFR-05), writes nothing to disk and refuses to start when a model
service key is present in its environment (OG-04, ADR-06).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Annotated, Final

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import __version__, descriptions
from .nbp import (
    MAX_PORTFOLIO_POSITIONS,
    NBPClient,
    RateError,
    RateResult,
    create_http_client,
    round_to_grosz,
    validate_amount,
    validate_currency,
    validate_date,
)

log = logging.getLogger("nbp_mcp_server")

TOOL_NAMES: Final = frozenset({"get_nbp_rate", "convert_to_pln", "portfolio_value"})
MODEL_KEY_VARIABLES: Final = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
)
"""Variables whose non-empty value blocks startup (ADR-06: the MCP server holds no model key)."""
SOURCE: Final = "NBP, Table A of average exchange rates (api.nbp.pl)"
_READ_ONLY: Final = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)


class Rate(BaseModel):
    """Result of ``get_nbp_rate`` (KA-08.2): rate, code, requested date, actual quote date, source."""

    model_config = ConfigDict(frozen=True)
    currency: str
    rate: str = Field(description="Average rate in PLN per 1 unit of the currency, decimal text.")
    requested_date: str
    quote_date: str = Field(description="Actual quote date (forward-fill, FR-03).")
    forward_fill: bool
    table_number: str
    source: str
    is_current: bool = Field(description="False: NBP unavailable, an older cache entry was used.")

    @classmethod
    def from_result(cls, r: RateResult) -> Rate:
        """Build the response model from an NBP client result."""
        return cls(
            currency=r.currency,
            rate=str(r.rate),
            requested_date=r.requested_date.isoformat(),
            quote_date=r.quote_date.isoformat(),
            forward_fill=r.quote_date != r.requested_date,
            table_number=r.table_number,
            source=SOURCE,
            is_current=r.is_current,
        )


class Conversion(BaseModel):
    """Result of ``convert_to_pln``."""

    model_config = ConfigDict(frozen=True)
    amount: str
    amount_pln: str = Field(description="Amount in PLN, ROUND_HALF_UP to the grosz (0.01 PLN).")
    rate: Rate


class Position(BaseModel):
    """A portfolio position supplied by the caller (synthetic data)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    currency: Annotated[str, Field(min_length=3, max_length=3, description="ISO 4217 code, e.g. EUR.")]
    amount: Annotated[
        str, Field(max_length=24, description="Positive decimal amount as text, e.g. '1000.00'.")
    ]


class PositionValuation(BaseModel):
    """Valuation of a single portfolio position."""

    model_config = ConfigDict(frozen=True)
    currency: str
    amount: str
    amount_pln: str
    rate: str
    quote_date: str


class PortfolioValue(BaseModel):
    """Result of ``portfolio_value``."""

    model_config = ConfigDict(frozen=True)
    requested_date: str
    total_pln: str
    positions: list[PositionValuation]
    source: str
    is_current: bool


def _tool_error(error: RateError) -> ToolError:
    """Turn a domain error into an MCP tool error (``isError: true``) without a stack trace."""
    return ToolError(error.to_json())


def build_server(client: NBPClient) -> MCPServer:
    """Register three read-only tools on the given NBP client and return the MCP server."""
    server = MCPServer(
        name="nbp",
        title="NBP average exchange rates (Table A)",
        instructions=descriptions.SERVER_INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",
    )

    @server.tool(name="get_nbp_rate", description=descriptions.GET_NBP_RATE, annotations=_READ_ONLY)
    async def get_nbp_rate(currency: str, date: str | None = None) -> Rate:
        try:
            return Rate.from_result(await client.rate(validate_currency(currency), validate_date(date)))
        except RateError as error:
            raise _tool_error(error) from None

    @server.tool(name="convert_to_pln", description=descriptions.CONVERT_TO_PLN, annotations=_READ_ONLY)
    async def convert_to_pln(amount: str, currency: str, date: str | None = None) -> Conversion:
        try:
            value = validate_amount(amount)
            result = await client.rate(validate_currency(currency), validate_date(date))
        except RateError as error:
            raise _tool_error(error) from None
        return Conversion(
            amount=str(value),
            amount_pln=str(round_to_grosz(value * result.rate)),
            rate=Rate.from_result(result),
        )

    @server.tool(name="portfolio_value", description=descriptions.PORTFOLIO_VALUE, annotations=_READ_ONLY)
    async def portfolio_value(
        positions: Annotated[list[Position], Field(min_length=1, max_length=MAX_PORTFOLIO_POSITIONS)],
        date: str | None = None,
    ) -> PortfolioValue:
        try:
            day = validate_date(date)
            # Validate every position before the first NBP request (KA-08.3), so a bad
            # amount in the last position never costs a network call.
            parsed = [(validate_currency(p.currency), validate_amount(p.amount)) for p in positions]
            valuations: list[PositionValuation] = []
            total = Decimal("0.00")
            current = True
            for currency, amount in parsed:
                r = await client.rate(currency, day)
                current &= r.is_current
                amount_pln = round_to_grosz(amount * r.rate)
                total += amount_pln
                valuations.append(
                    PositionValuation(
                        currency=r.currency,
                        amount=str(amount),
                        amount_pln=str(amount_pln),
                        rate=str(r.rate),
                        quote_date=r.quote_date.isoformat(),
                    )
                )
        except RateError as error:
            raise _tool_error(error) from None
        return PortfolioValue(
            requested_date=day.isoformat(),
            total_pln=str(total),
            positions=valuations,
            source=SOURCE,
            is_current=current,
        )

    return server


def check_environment(env: Mapping[str, str]) -> None:
    """Refuse to start when a non-empty model service key is in the environment (ADR-06, OG-04).

    Raises:
        SystemExit: Code 3 with a message on stderr (variable name, never its value).

    """
    if found := [n for n in MODEL_KEY_VARIABLES if env.get(n, "").strip()]:
        print(
            "nbp-mcp-server: refusing to start - a model service key is in the environment: "
            f"{', '.join(found)}. The MCP server must not hold this key (ADR-06). "
            "Remove the variable or override it with an empty value (server name before -e): "
            "claude mcp add nbp --transport stdio --scope user -e NAME= -- <path>",
            file=sys.stderr,
        )
        raise SystemExit(3)


async def _live_check(currency: str) -> int:
    """Send one request to the real NBP; JSON result on stdout (diagnostic mode, not MCP)."""
    client = NBPClient(create_http_client())
    try:
        result = Rate.from_result(await client.rate(validate_currency(currency), validate_date(None)))
    except RateError as error:
        print(error.to_json(), file=sys.stderr)
        return 1
    finally:
        await client.close()
    print(result.model_dump_json(indent=2))
    return 0


def run(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Run the server over stdio; ``transport`` is replaced only by e2e tests."""
    client = NBPClient(create_http_client(transport))
    build_server(client).run("stdio")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of ``nbp-mcp-server``."""
    parser = argparse.ArgumentParser(
        prog="nbp-mcp-server", description="MCP server (stdio) with NBP average exchange rates, Table A."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--check",
        metavar="CURRENCY",
        help="instead of the MCP server: one request to api.nbp.pl for today's rate, result on stdout",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr, level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    check_environment(os.environ)
    if args.check:
        try:
            return asyncio.run(_live_check(args.check))
        except RateError as error:
            print(error.to_json(), file=sys.stderr)
            return 2
    run()
    return 0


__all__ = ["TOOL_NAMES", "build_server", "check_environment", "main"]
