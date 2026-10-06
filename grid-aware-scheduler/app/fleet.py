"""The site's hardware: what is connected, what it reads live, what is measured.

These sections used to sit on the Overview, which meant the facts about a site
were split across two pages. They live on Site setup now, beside the rest of
what the operator declares once. The markup, script and styles are
``app/templates/fleet.*``, so the page that shows them includes three strings.
"""
from __future__ import annotations

from app import template

FLEET_CSS = template.load("fleet.css")
FLEET_HTML = template.load("fleet.html")
FLEET_JS = template.load("fleet.js")
