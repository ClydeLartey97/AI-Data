from __future__ import annotations

from datetime import datetime, timezone

import pytest

from adapters.nyiso import LOCATIONS, _parse_price_csv, location
from app.markets import market_locations


def _csv(rows: str) -> str:
    return (
        "Time Stamp,Name,PTID,LBMP ($/MWHr),"
        "Marginal Cost Losses ($/MWHr),Marginal Cost Congestion ($/MWHr)\n"
        + rows
    )


def test_nyiso_parser_selects_exact_zone_and_converts_eastern_to_utc():
    payload = _csv(
        "08/08/2026 00:00,CAPITL,61757,58.12,1.53,0.00\n"
        "08/08/2026 00:00,N.Y.C.,61761,72.25,0.10,4.00\n"
    )
    parsed = _parse_price_csv(payload, LOCATIONS["nyc"])
    assert parsed == {datetime(2026, 8, 8, 4, tzinfo=timezone.utc): 72.25}


def test_nyiso_parser_preserves_both_fall_back_hours():
    payload = _csv(
        "11/01/2026 01:00,N.Y.C.,61761,40.00,0,0\n"
        "11/01/2026 01:00,N.Y.C.,61761,41.00,0,0\n"
    )
    parsed = _parse_price_csv(payload, LOCATIONS["nyc"])
    assert parsed[datetime(2026, 11, 1, 5, tzinfo=timezone.utc)] == 40
    assert parsed[datetime(2026, 11, 1, 6, tzinfo=timezone.utc)] == 41


def test_nyiso_exposes_all_eleven_zones_and_rejects_unknowns():
    choices = market_locations("NYISO")
    assert len(choices) == 11
    assert {choice.key for choice in choices} == set(LOCATIONS)
    with pytest.raises(ValueError, match="unknown NYISO zone"):
        location("california")
