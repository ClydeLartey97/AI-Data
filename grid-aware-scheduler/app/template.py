"""Page templates kept as files, filled by ``__TOKEN__`` substitution.

The HTML, CSS and JavaScript for each page live in ``app/templates/`` so they
can be read and edited as what they are. Python computes the values and fills
the tokens. Filling is one pass, so a value that happens to contain
``__SOMETHING__`` is never itself substituted, and it refuses a token with no
value or a value with no token: either is a page that would render wrong
without failing.
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"
_TOKEN = re.compile(r"__[A-Z][A-Z0-9_]*?__")


def load(name: str) -> str:
    """Read one template file exactly as written."""
    return (TEMPLATES / name).read_text(encoding="utf-8")


def fill(template: str, values: dict[str, object]) -> str:
    """Replace every ``__TOKEN__`` in ``template`` with ``str(values[TOKEN])``."""
    found = set(_TOKEN.findall(template))
    missing = sorted(found - values.keys())
    unused = sorted(values.keys() - found)
    if missing or unused:
        raise KeyError(f"template tokens without values: {missing}; "
                       f"values without tokens: {unused}")
    return _TOKEN.sub(lambda match: str(values[match.group()]), template)
