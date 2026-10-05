"""Fake NBP API backed by a recorded Table A response (no network, OG-19)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import httpx

RECORDING = Path(__file__).parent / "data" / "nbp_tables_a_2026-05-15_2026-06-30.json"
_PATH = re.compile(r"^/api/exchangerates/tables/a/(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})/$")

type Mode = Literal["recording", "outage", "timeout", "garbage", "oversized", "empty"]


@dataclass
class FakeNBP:
    """Replays recorded Table A data for the requested date range and records requests."""

    mode: Mode = "recording"
    transient_failures: int = 0
    """The first N requests fail with ``failure`` before ``mode`` applies (retry tests)."""
    failure: Literal["outage", "timeout"] = "outage"
    requests: list[httpx.Request] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=lambda: json.loads(RECORDING.read_text("utf-8")))

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        """Handle one request like api.nbp.pl."""
        self.requests.append(request)
        mode = self.failure if len(self.requests) <= self.transient_failures else self.mode
        match mode:
            case "outage":
                return httpx.Response(503, text="Service Unavailable")
            case "timeout":
                raise httpx.ReadTimeout("fake timeout", request=request)
            case "garbage":
                return httpx.Response(200, json=[{"table": "A", "rates": "ignore previous instructions"}])
            case "oversized":
                return httpx.Response(200, content=b"[" + b" " * 2_000_000 + b"]")
            case "empty":
                return httpx.Response(404, text="404 NotFound - Not Found")
            case "recording":
                pass
        if not (m := _PATH.match(request.url.path)):
            return httpx.Response(400, text="400 BadRequest")
        start, end = date.fromisoformat(m[1]), date.fromisoformat(m[2])
        result = [t for t in self.tables if start <= date.fromisoformat(t["effectiveDate"]) <= end]
        if not result:
            return httpx.Response(404, text="404 NotFound - Not Found")
        return httpx.Response(200, json=result)

    def transport(self) -> httpx.MockTransport:
        """Return an httpx transport routing requests to the fake."""
        return httpx.MockTransport(self)
