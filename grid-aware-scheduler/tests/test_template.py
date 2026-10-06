"""Template filling refuses to render a page with a hole or a stray value."""
from __future__ import annotations

import pytest

from app import template


def test_every_token_is_replaced_in_one_pass():
    assert template.fill("a __X__ b __Y__", {"__X__": 1, "__Y__": "two"}) == "a 1 b two"


def test_adjacent_tokens_are_read_as_two():
    assert template.fill("__A____B__", {"__A__": "£", "__B__": "5.00"}) == "£5.00"


def test_a_value_is_never_itself_substituted():
    """A value containing token-shaped text is inserted as written."""
    assert template.fill("__X__", {"__X__": "__Y__"}) == "__Y__"


def test_a_token_without_a_value_is_refused():
    with pytest.raises(KeyError, match="__MISSING__"):
        template.fill("__MISSING__", {})


def test_a_value_without_a_token_is_refused():
    with pytest.raises(KeyError, match="__UNUSED__"):
        template.fill("plain", {"__UNUSED__": "x"})


def test_every_template_file_loads():
    names = sorted(path.name for path in template.TEMPLATES.iterdir())
    assert "planner.html" in names
    for name in names:
        assert template.load(name)
