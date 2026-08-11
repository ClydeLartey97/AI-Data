from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from adapters.miso import LOCATIONS, _parse_price_csv, location
from app.markets import market_locations

FIXTURE = Path(__file__).parent / "fixtures" / "miso_da_expost_lmp.csv"


def test_miso_parser_selects_hub_lmp_and_converts_est_to_utc():
    parsed = _parse_price_csv(FIXTURE.read_text(), date(2026, 8, 8),
                              LOCATIONS["indiana"])
    assert len(parsed) == 24
    # HE 1 is the hour ending 01:00 EST: interval start midnight EST = 05:00 UTC.
    assert parsed[datetime(2026, 8, 8, 5, tzinfo=timezone.utc)] == 33.36
    # The MCC component row and the AECI.ALTW loadzone row must not leak in:
    # HE 1 for Indiana would read 2.67 (MCC) or 30.13 (loadzone) otherwise.
    assert 2.67 not in parsed.values()
    assert 30.13 not in parsed.values()


def test_miso_parser_is_fixed_offset_not_daylight_saving():
    # MISO market hours-ending are EST year-round. A July day must still be
    # UTC-5, not the UTC-4 that America/New_York would apply.
    payload = FIXTURE.read_text()
    parsed = _parse_price_csv(payload, date(2026, 7, 1), LOCATIONS["minnesota"])
    assert datetime(2026, 7, 1, 5, tzinfo=timezone.utc) in parsed


def test_miso_parser_returns_empty_for_missing_header():
    assert _parse_price_csv("no,such,file", date(2026, 8, 8),
                            LOCATIONS["indiana"]) == {}


def test_miso_exposes_all_eight_hubs_and_rejects_unknowns():
    choices = market_locations("MISO")
    assert len(choices) == 8
    assert {choice.key for choice in choices} == set(LOCATIONS)
    assert location("Indiana").node == "INDIANA.HUB"
    with pytest.raises(ValueError, match="unknown MISO hub"):
        location("houston")
