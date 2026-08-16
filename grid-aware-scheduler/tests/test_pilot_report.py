"""The pilot report is the artefact a funding or procurement conversation
tests hardest, so these tests are mostly about what it refuses to say.
"""
from __future__ import annotations

from core import pilot_report
from core.audit_store import save_decision, save_score


def _decision(path, *, market="GB", currency="GBP", immediate=10.0,
              scheduled=6.0, oracle=5.0, carbon_saved=1.5, carbon_regret=0.3,
              delay_hours=6.0, hardware="H100 SXM", scored=True,
              cost_error=0.2, carbon_error=0.05):
    decision_id = save_decision(
        market=market, location="national", signal_mode="forecast",
        request={"workload": {"model_key": "llama-8b"}},
        response={"selected": {"cost": scheduled, "currency": currency}},
        signals=[], path=path)
    if not scored:
        return decision_id
    save_score(decision_id, {
        "realised_selected": {"cost": scheduled, "currency": currency,
                              "hardware": hardware, "delay_hours": delay_hours,
                              "carbon_kg": 2.0},
        "realised_immediate": {"cost": immediate, "currency": currency,
                               "carbon_kg": 2.0 + carbon_saved},
        "realised_oracle": {"cost": oracle, "currency": currency},
        "cost_saved": immediate - scheduled,
        "carbon_saved_kg": carbon_saved,
        "cost_forecast_error": cost_error,
        "carbon_forecast_error_kg": carbon_error,
        "cost_regret": scheduled - oracle,
        "carbon_regret_kg": carbon_regret,
    }, path)
    return decision_id


def test_an_empty_journal_claims_nothing_rather_than_zero(tmp_path):
    """"Saved 0.00" reads as a measurement. "Nothing scored" is the truth."""
    report = pilot_report.build(path=tmp_path / "audit.sqlite")
    assert report["claimable"]["state"] == "no_measured_result"
    assert "no saving can be claimed" in report["claimable"]["statement"]
    assert report["cost_by_currency"] == []


def test_unscored_recommendations_are_excluded_from_the_saving(tmp_path):
    """They have no realised outcome; averaging them in would invent one."""
    path = tmp_path / "audit.sqlite"
    _decision(path)
    for _ in range(3):
        _decision(path, scored=False)
    report = pilot_report.build(path=path)
    assert report["coverage"]["decisions_recorded"] == 4
    assert report["coverage"]["decisions_scored"] == 1
    assert report["coverage"]["awaiting_outturn"] == 3
    assert report["coverage"]["scored_percent"] == 25.0
    assert report["cost_by_currency"][0]["scored_decisions"] == 1


def test_two_currencies_are_never_added_together(tmp_path):
    """A pilot spanning GB and MISO holds pounds and dollars.

    One combined total would be a number with no unit and no meaning.
    """
    path = tmp_path / "audit.sqlite"
    _decision(path, market="GB", currency="GBP", immediate=10.0, scheduled=6.0)
    _decision(path, market="MISO", currency="USD", immediate=20.0, scheduled=15.0)
    report = pilot_report.build(path=path)
    by_currency = {row["currency"]: row for row in report["cost_by_currency"]}
    assert set(by_currency) == {"GBP", "USD"}
    assert by_currency["GBP"]["cost_saved"] == 4.0
    assert by_currency["USD"]["cost_saved"] == 5.0
    assert "4.00 GBP" in report["claimable"]["statement"]
    assert "5.00 USD" in report["claimable"]["statement"]


def test_a_recommendation_that_lost_money_is_counted_not_dropped(tmp_path):
    """Reporting only the wins is how a backtest flatters itself."""
    path = tmp_path / "audit.sqlite"
    _decision(path, immediate=10.0, scheduled=6.0)
    _decision(path, immediate=10.0, scheduled=13.0)
    row = pilot_report.build(path=path)["cost_by_currency"][0]
    assert row["decisions_worse_than_immediate"] == 1
    assert row["worst_single_loss"] == -3.0
    # And the loss reduces the headline rather than sitting beside it.
    assert row["cost_saved"] == 1.0


def test_capture_percent_states_how_much_of_the_prize_was_taken(tmp_path):
    """Saving is meaningless without the best that was available.

    Saved 4 with 1 of regret means perfect hindsight would have saved 5, so
    the scheduler captured 80% of what the day actually offered.
    """
    path = tmp_path / "audit.sqlite"
    _decision(path, immediate=10.0, scheduled=6.0, oracle=5.0)
    row = pilot_report.build(path=path)["cost_by_currency"][0]
    assert row["regret_against_perfect_hindsight"] == 1.0
    assert row["capture_percent"] == 80.0


def test_a_small_sample_is_labelled_indicative_not_measured(tmp_path):
    path = tmp_path / "audit.sqlite"
    _decision(path)
    claim = pilot_report.build(path=path)["claimable"]
    assert claim["state"] == "indicative"
    assert "not a statistically settled result" in claim["statement"]


def test_a_full_campaign_is_labelled_measured(tmp_path):
    path = tmp_path / "audit.sqlite"
    for _ in range(30):
        _decision(path)
    claim = pilot_report.build(path=path)["claimable"]
    assert claim["state"] == "measured"
    assert "30 scored decisions" in claim["statement"]


def test_forecast_error_is_reported_as_a_median_absolute_value(tmp_path):
    """Signed errors cancel. An operator wants the typical miss, not the net."""
    path = tmp_path / "audit.sqlite"
    _decision(path, cost_error=0.4)
    _decision(path, cost_error=-0.4)
    row = pilot_report.build(path=path)["cost_by_currency"][0]
    assert row["median_absolute_forecast_error"] == 0.4


def test_the_delay_the_saving_cost_is_reported_beside_it(tmp_path):
    """A saving bought with an unacceptable delay is not a saving."""
    path = tmp_path / "audit.sqlite"
    _decision(path, delay_hours=4.0)
    _decision(path, delay_hours=12.0)
    delay = pilot_report.build(path=path)["delay"]
    assert delay["median_hours"] == 8.0
    assert delay["max_hours"] == 12.0


def test_every_report_carries_its_disclosures(tmp_path):
    """Including the empty one — the limits do not depend on the result."""
    report = pilot_report.build(path=tmp_path / "audit.sqlite")
    text = " ".join(report["disclosures"])
    assert "never launches" in text
    assert "70-85%" in text          # optimistic multi-accelerator scaling
    assert "balancing-area" in text  # carbon scope
    assert report["mode"] == "shadow"


def test_since_excludes_earlier_decisions(tmp_path):
    path = tmp_path / "audit.sqlite"
    _decision(path)
    report = pilot_report.build(path=path, since="2099-01-01T00:00:00+00:00")
    assert report["coverage"]["decisions_recorded"] == 0


def test_the_rendered_text_leads_with_coverage_not_the_saving(tmp_path):
    """A reader must meet the sample before they meet the headline."""
    path = tmp_path / "audit.sqlite"
    _decision(path)
    text = pilot_report.render(pilot_report.build(path=path))
    assert text.index("Coverage") < text.index("Cost (GBP)")
    assert "Disclosures" in text
    assert "Saved" in text
