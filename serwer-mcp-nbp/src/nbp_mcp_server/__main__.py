"""Allows running the server with ``python -m nbp_mcp_server``."""

import sys

from .server import main

sys.exit(main())
