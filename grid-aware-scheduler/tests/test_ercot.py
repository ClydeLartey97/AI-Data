"""ERCOT day-ahead price parsing, against real captured payloads.

Fixtures are a trimmed copy of the genuine article — one real DAM Settlement
Point Prices file cut to three settlement points, and a real fragment of the
MIS report listing — rather than something shaped by hand to pass. The
awkward parts of the format are the point.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from adapters.ercot import (CPT, LOCATIONS, ERCOTAdapter, _delivery_dates,
                            _hour_start, _parse_listing, _parse_price_csv,
                            _posting_date, location)

FIXTURES = Path(__file__).parent / "fixtures" / "ercot"


def _csv() -> str:
    return (FIXTURES / "dam_spp_sample.csv").read_text()


def _listing() -> str:
    return (FIXTURES / "report_listing.html").read_text()


# --- The listing, and the regex trap inside ERCOT's filenames ---

def test_the_listing_yields_posting_dates_and_document_ids():
    found = _parse_listing(_listing())
    assert found
    for posted, doc in found:
        assert isinstance(posted, date)
        assert doc.isdigit()


def test_the_report_id_is_not_mistaken_for_a_date():
    """The bug this guards: `cdr.00012331.0000000000000000.20260826....`

    The report id is also an eight-digit run and comes first. A pattern that
    takes the first match reads `00012331` as a date, fails to parse it, and
    silently drops every row in the listing — which looks exactly like ERCOT
    having published nothing.
    """
    name = "cdr.00012331.0000000000000000.20260826.124146390.DAMSPNP4190_csv.zip"
    assert _posting_date(name) == date(2026, 8, 26)


def test_only_the_csv_archive_is_selected():
    """Each report is published as CSV and XML behind separate ids."""
    listing = _listing()
    csv_ids = {doc for _, doc in _parse_listing(listing, suffix="_csv.zip")}
    xml_ids = {doc for _, doc in _parse_listing(listing, suffix="_xml.zip")}
    assert csv_ids
    assert not (csv_ids & xml_ids)


def test_a_filename_with_no_date_at_all_is_skipped_not_guessed():
    assert _posting_date("rpt.something.zip") is None


# --- Hour-ending, which is the easiest thing to get wrong ---

def test_hour_ending_one_starts_at_midnight():
    """HE 01:00 is the hour that *finishes* at 01:00, so it starts at 00:00.

    Reading it as the hour beginning at 01:00 shifts every price by an hour,
    which still produces a plausible-looking schedule.
    """
    stamp = _hour_start(date(2026, 8, 27), "01:00", "N")
    assert stamp.astimezone(CPT).hour == 0


def test_hour_ending_twenty_four_starts_at_twenty_three():
    stamp = _hour_start(date(2026, 8, 27), "24:00", "N")
    local = stamp.astimezone(CPT)
    assert local.hour == 23
    assert local.date() == date(2026, 8, 27)


def test_a_summer_hour_resolves_to_central_daylight_time():
    """Texas observes daylight saving; a fixed offset would be an hour out."""
    stamp = _hour_start(date(2026, 8, 27), "13:00", "N")
    assert stamp == datetime(2026, 8, 27, 17, 0, tzinfo=timezone.utc)


def test_a_winter_hour_resolves_to_central_standard_time():
    stamp = _hour_start(date(2026, 1, 15), "13:00", "N")
    assert stamp == datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)


def test_a_malformed_hour_is_dropped_rather_than_defaulted():
    assert _hour_start(date(2026, 8, 27), "", "N") is None
    assert _hour_start(date(2026, 8, 27), "not-an-hour", "N") is None
    assert _hour_start(date(2026, 8, 27), "99:00", "N") is None


# --- Parsing real rows ---

def test_the_selected_settlement_point_is_the_only_one_returned():
    houston = _parse_price_csv(_csv(), location("houston"))
    north = _parse_price_csv(_csv(), location("north"))
    assert houston and north
    assert houston != north


def test_a_full_day_yields_twenty_four_hourly_prices():
    prices = _parse_price_csv(_csv(), location("houston"))
    assert len(prices) == 24


def test_prices_are_read_despite_the_leading_whitespace_ercot_writes():
    """The real file writes ` 67.26`, with a space. Naive float() copes; a
    naive string comparison on the settlement point would not."""
    prices = _parse_price_csv(_csv(), location("houston"))
    assert all(isinstance(value, float) for value in prices.values())
    assert min(prices.values()) > 0


def test_the_delivery_date_is_read_from_the_file_not_inferred():
    """ERCOT posts day-ahead results the afternoon before delivery.

    Inferring the delivery date from the posting date is right almost always,
    and silently wrong whenever a market notice delays a posting past
    midnight. The fixture was posted on the 26th for delivery on the 27th.
    """
    prices = _parse_price_csv(_csv(), location("houston"))
    local_dates = {stamp.astimezone(CPT).date() for stamp in prices}
    assert local_dates == {date(2026, 8, 27)}


def test_a_repeated_hour_keeps_the_first_rather_than_overwriting():
    """On the autumn fold the local hour repeats and DSTFlag separates them.

    If the flag fails to disambiguate, losing one interval is recoverable;
    silently reporting the wrong hour's price is not.
    """
    doubled = (
        "DeliveryDate,HourEnding,SettlementPoint,SettlementPointPrice,DSTFlag\n"
        "11/01/2026,02:00,HB_HOUSTON, 10.00,N\n"
        "11/01/2026,02:00,HB_HOUSTON, 99.00,N\n"
    )
    prices = _parse_price_csv(doubled, location("houston"))
    assert len(prices) == 1
    assert list(prices.values()) == [10.00]


def test_the_daylight_flag_separates_the_two_passes_of_a_folded_hour():
    folded = (
        "DeliveryDate,HourEnding,SettlementPoint,SettlementPointPrice,DSTFlag\n"
        "11/01/2026,02:00,HB_HOUSTON, 10.00,Y\n"
        "11/01/2026,02:00,HB_HOUSTON, 99.00,N\n"
    )
    prices = _parse_price_csv(folded, location("houston"))
    assert len(prices) == 2
    first, second = sorted(prices)
    assert second - first == timedelta(hours=1)


def test_a_non_numeric_price_is_dropped_not_treated_as_zero():
    """A zero price is a cheap window. A missing price is not."""
    broken = (
        "DeliveryDate,HourEnding,SettlementPoint,SettlementPointPrice,DSTFlag\n"
        "08/27/2026,01:00,HB_HOUSTON,,N\n"
        "08/27/2026,02:00,HB_HOUSTON, 12.00,N\n"
    )
    prices = _parse_price_csv(broken, location("houston"))
    assert len(prices) == 1


# --- Locations ---

def test_houston_hub_and_houston_load_zone_are_different_points():
    assert LOCATIONS["houston"].settlement_point == "HB_HOUSTON"
    assert LOCATIONS["lz-houston"].settlement_point == "LZ_HOUSTON"


def test_an_unknown_settlement_point_is_refused():
    with pytest.raises(ValueError, match="unknown ERCOT"):
        location("nowhere")


def test_every_declared_location_is_a_real_ercot_point_name():
    """Guards a typo turning into an empty series that looks like an outage."""
    for loc in LOCATIONS.values():
        assert loc.settlement_point.startswith(("HB_", "LZ_"))


# --- Window handling ---

def test_delivery_dates_are_local_days_not_utc_days():
    """A UTC day is not an ERCOT operating day, and the gap matters.

    05:00 UTC is midnight in Central Daylight Time, so a window running
    06:00 UTC to 06:00 UTC starts an hour into one operating day and ends an
    hour into the next. Both files are needed; fetching only the UTC date
    would leave the tail of the window unpriced, and unpriced reads downstream
    as "never schedule here" — the same class of bug the GB settlement-day
    boundary produced.
    """
    start = datetime(2026, 8, 27, 6, tzinfo=timezone.utc)
    end = datetime(2026, 8, 28, 6, tzinfo=timezone.utc)
    assert _delivery_dates(start, end) == [date(2026, 8, 27), date(2026, 8, 28)]


def test_a_window_inside_one_operating_day_needs_only_that_day():
    start = datetime(2026, 8, 27, 6, tzinfo=timezone.utc)
    end = datetime(2026, 8, 27, 23, tzinfo=timezone.utc)
    assert _delivery_dates(start, end) == [date(2026, 8, 27)]


def test_an_inverted_window_is_refused():
    adapter = ERCOTAdapter("houston")
    with pytest.raises(ValueError, match="end must be after start"):
        adapter.get_data(datetime(2026, 8, 27, tzinfo=timezone.utc),
                         datetime(2026, 8, 26, tzinfo=timezone.utc))


def test_a_window_beyond_the_listing_depth_is_refused_with_the_reason():
    adapter = ERCOTAdapter("houston")
    with pytest.raises(ValueError, match="31 days"):
        adapter.get_data(datetime(2026, 1, 1, tzinfo=timezone.utc),
                         datetime(2026, 8, 27, tzinfo=timezone.utc))


def test_the_adapter_names_its_market():
    assert ERCOTAdapter("houston").market_name == "ERCOT"
