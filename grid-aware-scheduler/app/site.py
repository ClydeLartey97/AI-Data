"""The page a company declares its site on — plants, contracts, connection.

This is the input side of `facility-energy-v1`. Everything here is a field the
operator already knows from a connection agreement, a PPA or a datasheet; the
software does the arithmetic. Deliberately plain: the visual pass comes later,
and a form that is honest about what it is asking for beats a styled one that
is vague about it.
"""
from __future__ import annotations

from app import template
from app.fleet import FLEET_CSS, FLEET_HTML, FLEET_JS
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS, product_nav

_PAGE = template.load("site.html")


def render() -> str:
    """Return the self-contained declaration page."""
    return template.fill(_PAGE, {
        "__THEME_BOOTSTRAP__": THEME_BOOTSTRAP,
        "__THEME_CSS__": THEME_CSS,
        "__FLEET_CSS__": FLEET_CSS,
        "__FLEET_HTML__": FLEET_HTML,
        "__FLEET_JS__": FLEET_JS,
        "__PRODUCT_NAV__": product_nav("site"),
        "__THEME_CONTROL__": THEME_CONTROL,
    })
