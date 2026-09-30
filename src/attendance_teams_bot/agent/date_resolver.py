"""Local date safety checks; date interpretation belongs to the constrained model loop."""

from __future__ import annotations

import re

_AMBIGUOUS_NUMERIC_DATE = re.compile(r"(?<![\d./-])\d{1,2}[./-]\d{1,2}(?![./-]\d)")


def has_ambiguous_numeric_date(message: str) -> bool:
    """Reject numeric day/month forms rather than silently guessing their order."""
    return isinstance(message, str) and bool(_AMBIGUOUS_NUMERIC_DATE.search(message))
