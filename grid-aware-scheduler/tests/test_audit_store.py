from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adapters.base_adapter import GridDataPoint
from core.audit_store import (get_decision, list_decisions, save_decision,
                              save_score)


def test_audit_store_persists_request_response_and_signal_snapshot(tmp_path):
    path = tmp_path / "audit.sqlite"
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    signals = [
        GridDataPoint(start + timedelta(minutes=30 * i), 100 + i, 50 + i)
        for i in range(2)
    ]
    decision_id = save_decision(
        market="GB", location="London", signal_mode="forecast",
        request={"workload": {"tokens": 100}},
        response={"selected": {"cost": 1.2}}, signals=signals, path=path,
    )
    saved = get_decision(decision_id, path)
    assert saved["request"]["workload"]["tokens"] == 100
    assert saved["response"]["selected"]["cost"] == 1.2
    assert len(saved["signals"]) == 2
    assert saved["signals"][0]["carbon"] == 100
    summary = list_decisions(path=path)[0]
    assert summary["id"] == decision_id
    assert summary["status"] == "awaiting_outturn"
    assert summary["cost"] == 1.2


def test_audit_store_attaches_realised_score(tmp_path):
    path = tmp_path / "audit.sqlite"
    decision_id = save_decision(
        market="GB", location="London", signal_mode="forecast",
        request={}, response={}, signals=[], path=path,
    )
    save_score(decision_id, {"cost_regret": 0.4}, path)
    assert get_decision(decision_id, path)["score"]["result"]["cost_regret"] == 0.4
    assert list_decisions(path=path)[0]["status"] == "scored"


def test_audit_store_rejects_score_for_unknown_decision(tmp_path):
    with pytest.raises(KeyError, match="unknown decision"):
        save_score("missing", {}, tmp_path / "audit.sqlite")
