"""Operator-facing audit journal for persisted scheduling decisions."""
from __future__ import annotations

from app import template
from app.theme import THEME_BOOTSTRAP, THEME_CONTROL, THEME_CSS, product_nav

_PAGE = template.load("decisions.html")


def render() -> str:
    """Return a self-contained page backed by the versioned decision API."""
    return template.fill(_PAGE, {
        "__THEME_BOOTSTRAP__": THEME_BOOTSTRAP,
        "__THEME_CSS__": THEME_CSS,
        "__THEME_CONTROL__": THEME_CONTROL,
        "__PRODUCT_NAV__": product_nav("history"),
    })
