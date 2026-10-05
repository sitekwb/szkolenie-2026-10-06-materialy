"""Wspólna konfiguracja testów: pętla asyncio przez wtyczkę pytest z anyio."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Testy asynchroniczne działają na asyncio (jak serwer)."""
    return "asyncio"
