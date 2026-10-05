"""Shared test configuration: asyncio event loop via the anyio pytest plugin."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Async tests run on asyncio (same as the server)."""
    return "asyncio"
