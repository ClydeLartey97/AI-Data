"""Shared appearance and primary navigation for every product page."""

from __future__ import annotations

import html

from app import template

THEME_BOOTSTRAP = r"""<script>
(function(){try{var saved=localStorage.getItem("grid-aware-theme");
var theme=saved==="light"||saved==="dark"?saved:
(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");
document.documentElement.dataset.theme=theme;}catch(error){}})();
</script>"""

THEME_CSS = template.load("theme.css")
THEME_CONTROL = template.load("theme_control.html")


_NAVIGATION = (
    ("overview", "Overview", "/"),
    ("plan", "Plan work", "/planner"),
    ("hardware", "Hardware", "/simulator"),
    ("energy", "Energy", "/grid"),
    ("site", "Site setup", "/site"),
    ("history", "History", "/decisions"),
)


def product_nav(active: str, hrefs: dict[str, str] | None = None) -> str:
    """Render one task-labelled navigation contract across every page."""
    hrefs = hrefs or {}
    links = []
    for index, (key, label, default_href) in enumerate(_NAVIGATION):
        if index == 4:
            links.append('<span class="nav-divider" aria-hidden="true"></span>')
        current = ' class="on" aria-current="page"' if key == active else ""
        href = html.escape(hrefs.get(key, default_href), quote=True)
        links.append(f'<a href="{href}"{current}>{label}</a>')
    return '<nav class="product-nav" aria-label="Primary navigation">' + "".join(links) + "</nav>"
