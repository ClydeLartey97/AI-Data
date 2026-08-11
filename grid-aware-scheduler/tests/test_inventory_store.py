from __future__ import annotations

import pytest

from hardware import inventory_store


def _snapshot(**overrides):
    snapshot = {
        "provider": "redfish",
        "observed_at": "2026-08-11T10:00:00+00:00",
        "devices": [{
            "name": "Contoso 3500",
            "count": 1,
            "memory_gb": 96.0,
            "device_digest": "a" * 64,
        }],
        "warnings": [],
    }
    snapshot.update(overrides)
    return snapshot


def test_snapshot_roundtrip_and_summary(tmp_path):
    path = tmp_path / "inventory.sqlite"
    assert inventory_store.latest(path) is None
    first = inventory_store.record_snapshot(_snapshot(), path)
    second = inventory_store.record_snapshot(
        _snapshot(warnings=["endpoint x: partial"]), path)
    assert second > first

    newest = inventory_store.latest(path)
    assert newest["snapshot_id"] == second
    assert newest["devices"][0]["name"] == "Contoso 3500"
    assert newest["warnings"] == ["endpoint x: partial"]
    assert newest["recorded_at"]

    summary = inventory_store.summary(path)
    assert summary["snapshot_count"] == 2
    assert summary["latest_device_count"] == 1
    assert summary["latest_warning_count"] == 1


def test_snapshot_validation_fails_closed(tmp_path):
    path = tmp_path / "inventory.sqlite"
    with pytest.raises(ValueError, match="provider"):
        inventory_store.record_snapshot(_snapshot(provider=""), path)
    with pytest.raises(ValueError, match="timestamp"):
        inventory_store.record_snapshot(_snapshot(observed_at="whenever"), path)
    with pytest.raises(ValueError, match="devices"):
        inventory_store.record_snapshot(_snapshot(devices="not-a-list"), path)
    with pytest.raises(ValueError, match="warnings"):
        inventory_store.record_snapshot(_snapshot(warnings=[1]), path)
    assert inventory_store.summary(path)["snapshot_count"] == 0


def test_store_is_append_only_by_api():
    # The module deliberately exposes no update or delete operation.
    public = [name for name in dir(inventory_store) if not name.startswith("_")]
    assert not any("delete" in name or "update" in name for name in public)
