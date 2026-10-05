"""MCP tool descriptions as a versioned artifact (REQ-29).

The agent reads these descriptions as instructions, so every change to their text is a change in
system behavior. Changing any description (including a field description, a limit or a parameter
type in ``server.py``) requires bumping ``DESCRIPTIONS_VERSION`` and adding the SHA-256 of the
``tools/list`` manifest to ``tests/test_descriptions.py``; otherwise the review test fails.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final

DESCRIPTIONS_VERSION: Final = "2.1.0"

SERVER_INSTRUCTIONS: Final = (
    "This server only reads NBP average exchange rates (Table A, api.nbp.pl). "
    "It has no tools that change state. Amounts and rates are returned as decimal text "
    "to avoid losing precision. A tool error means the data is missing: do not replace it with "
    "a number of your own."
)

GET_NBP_RATE: Final = (
    "Returns the NBP average exchange rate (Table A) of a foreign currency against PLN for a given day. "
    "When there was no quote on that day (weekend, public holiday), it returns the last earlier "
    "quote and gives its actual date in the quote_date field. "
    "Parameters: currency - ISO 4217 code from Table A (e.g. EUR, USD, CHF); "
    "date - day in YYYY-MM-DD format, not in the future; omitted means today. "
    "When there is no rate, the tool returns an error with a reason, never a default value."
)

CONVERT_TO_PLN: Final = (
    "Converts an amount in a foreign currency to PLN at the NBP average rate (Table A) of a given day, "
    "with forward-fill for days without a quote. Rounded ROUND_HALF_UP to the grosz (0.01 PLN). "
    "Parameters: amount - positive decimal number as text, e.g. '1250.50'; currency - ISO 4217 code "
    "from Table A or PLN; date - YYYY-MM-DD, not in the future; omitted means today."
)

PORTFOLIO_VALUE: Final = (
    "Values in PLN the portfolio given in the argument (list of positions: currency and amount) at "
    "NBP average rates (Table A) of a given day, with forward-fill. Returns the value of each position "
    "rounded ROUND_HALF_UP to the grosz, and the total: the exact sum of the unrounded values rounded "
    "once, so it may differ by a few grosz from the sum of the rounded positions. "
    "The server stores no portfolio. When any currency has no rate, the whole tool "
    "returns an error instead of a partial total. Amounts positive, at most 50 positions."
)

DESCRIPTIONS: Final[dict[str, str]] = {
    "instructions": SERVER_INSTRUCTIONS,
    "get_nbp_rate": GET_NBP_RATE,
    "convert_to_pln": CONVERT_TO_PLN,
    "portfolio_value": PORTFOLIO_VALUE,
}


def descriptions_digest(descriptions: dict[str, str] | None = None) -> str:
    """Return the SHA-256 of the canonical JSON form of the tool descriptions.

    Args:
        descriptions: Mapping of descriptions; defaults to the current ``DESCRIPTIONS``.

    Returns:
        Hex digest, pinned in the review test for the given ``DESCRIPTIONS_VERSION``.

    """
    data = json.dumps(descriptions if descriptions is not None else DESCRIPTIONS, sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
