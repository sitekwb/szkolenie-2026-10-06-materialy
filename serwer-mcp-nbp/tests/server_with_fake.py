"""Runs the real stdio server with a fake NBP instead of the network (e2e test only)."""

from __future__ import annotations

from fake_nbp import FakeNBP

from nbp_mcp_server.server import check_environment, run

if __name__ == "__main__":
    import os

    check_environment(os.environ)
    run(FakeNBP().transport())
