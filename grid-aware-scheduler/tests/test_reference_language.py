from __future__ import annotations

import json

import pytest

from hardware.reference_language import (DEFAULT_DATASET, catalogue_entry, extracted_answer,
                                         load_evaluation, make_spec,
                                         score_responses, suite_version,
                                         write_spec)


REVISION = "a" * 40


def test_public_language_evaluation_is_versioned_and_valid():
    items = load_evaluation()
    assert len(items) == 10
    assert suite_version().startswith("1.0+sha256.")
    assert all(item.expected in {"A", "B", "C", "D"} for item in items)
    assert catalogue_entry()["status"] == "runner_ready"


def test_answer_extraction_and_aggregate_scoring_store_no_content():
    items = load_evaluation()
    responses = [f"The answer is {item.expected}." for item in items]
    assert extracted_answer("Answer: c") == "C"
    assert extracted_answer("Stages A and B must run. The answer is C.") == "C"
    assert extracted_answer("Stages A and B must run.") is None
    assert extracted_answer("No selection") is None
    assert score_responses(items, responses) == 1


def test_prepared_spec_pins_model_dataset_shape_and_quality(tmp_path):
    spec = make_spec(
        model_id="public/reference-model",
        revision=REVISION,
        precision="int4",
        device_key="apple-m2-8gb",
        compute_unit="gpu",
        max_tokens=8,
    )
    assert spec.model_version == REVISION
    assert spec.work_unit == "samples"
    assert spec.evaluation_suite_version == suite_version(DEFAULT_DATASET)
    assert "dataset-sha256" in spec.shape_fingerprint
    output = tmp_path / "spec.json"
    write_spec(spec, output)
    payload = json.loads(output.read_text())
    assert payload["quality_metric"] == "multiple_choice_accuracy"
    assert not any(key in payload for key in ("prompt", "response", "content"))


def test_reference_workload_rejects_mutable_model_revision():
    with pytest.raises(ValueError, match="immutable 40-character"):
        make_spec(
            model_id="public/reference-model",
            revision="main",
            precision="int4",
            device_key="apple-m2-8gb",
            compute_unit="gpu",
            max_tokens=8,
        )
