"""Deterministic, content-free telemetry for a reference MLX language task.

The public evaluation prompts live in ``benchmarks/data``. Generated text is
held in memory only and is never written to the evidence store. The workload
emits aggregate token count and multiple-choice accuracy through the three-field
collector result contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from hardware.apple_benchmark import BenchmarkSpec

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "benchmarks" / "data" / "language_mcq_v1.json"
SUITE_NAME = "ai-energy-language-mcq"
LEADING_ANSWER_PATTERN = re.compile(
    r"^\s*(?:answer\s*[:=-]?\s*)?[\[(]?([A-D])(?:\b|[\])])",
    re.IGNORECASE,
)
NAMED_ANSWER_PATTERN = re.compile(
    r"\b(?:answer|option|choice)\s*(?:is|:|=)?\s*[\[(]?([A-D])(?:\b|[\])])",
    re.IGNORECASE,
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class EvaluationItem:
    item_id: str
    prompt: str
    expected: str

    def __post_init__(self) -> None:
        if not self.item_id.strip() or len(self.item_id) > 80:
            raise ValueError("evaluation item_id is required and must be at most 80 characters")
        if not self.prompt.strip() or len(self.prompt) > 4_000:
            raise ValueError("evaluation prompt is required and must be at most 4,000 characters")
        if self.expected not in {"A", "B", "C", "D"}:
            raise ValueError("evaluation expected answer must be A, B, C or D")


def dataset_digest(path: Path = DEFAULT_DATASET) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def suite_version(path: Path = DEFAULT_DATASET) -> str:
    return f"1.0+sha256.{dataset_digest(path)[:16]}"


def catalogue_entry(path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    items = load_evaluation(path)
    return {
        "benchmark_id": "mlx-language-mcq-v1",
        "name": "MLX language multiple-choice evaluation",
        "status": "runner_ready",
        "workload_class": "language_generation",
        "run_mode": "evaluation",
        "work_unit": "samples",
        "quality_metric": "multiple_choice_accuracy",
        "evaluation_suite": SUITE_NAME,
        "evaluation_suite_version": suite_version(path),
        "item_count": len(items),
        "requires": [
            "immutable model revision",
            "authorised Apple subsystem sampling or calibrated external meter",
            "three exact-fingerprint runs",
        ],
    }


def load_evaluation(path: Path = DEFAULT_DATASET) -> list[EvaluationItem]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("language evaluation dataset must be a non-empty JSON array")
    if len(payload) > 128:
        raise ValueError("language evaluation dataset cannot exceed 128 items")
    items: list[EvaluationItem] = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise ValueError(f"evaluation item {index} must be an object")
        try:
            items.append(EvaluationItem(
                item_id=str(row["item_id"]),
                prompt=str(row["prompt"]),
                expected=str(row["expected"]).upper(),
            ))
        except KeyError as exc:
            raise ValueError(f"evaluation item {index} is missing {exc.args[0]}") from exc
    identifiers = [item.item_id for item in items]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("evaluation item IDs must be unique")
    return items


def extracted_answer(response: str) -> str | None:
    value = response.strip().upper()[:256]
    match = LEADING_ANSWER_PATTERN.search(value) or NAMED_ANSWER_PATTERN.search(value)
    return match.group(1).upper() if match else None


def score_responses(items: list[EvaluationItem], responses: list[str]) -> float:
    if len(items) != len(responses):
        raise ValueError("one response is required for every evaluation item")
    correct = sum(
        extracted_answer(response) == item.expected
        for item, response in zip(items, responses)
    )
    return correct / len(items)


def _validated_revision(revision: str) -> str:
    revision = revision.strip().lower()
    if not COMMIT_PATTERN.fullmatch(revision):
        raise ValueError("model revision must be an immutable 40-character commit hash")
    return revision


def make_spec(
    *,
    model_id: str,
    revision: str,
    precision: str,
    device_key: str,
    compute_unit: str,
    max_tokens: int,
    dataset_path: Path = DEFAULT_DATASET,
) -> BenchmarkSpec:
    if not 1 <= max_tokens <= 256:
        raise ValueError("max_tokens must be between 1 and 256")
    revision = _validated_revision(revision)
    items = load_evaluation(dataset_path)
    shape = (
        f"mcq-{len(items)}_max-output-{max_tokens}_"
        f"dataset-sha256-{dataset_digest(dataset_path)[:16]}_batch-1"
    )
    return BenchmarkSpec(
        workload_class="language_generation",
        run_mode="evaluation",
        model_id=model_id,
        model_version=revision,
        precision=precision,
        device_key=device_key,
        compute_unit=compute_unit,
        shape_fingerprint=shape,
        work_unit="samples",
        quality_metric="multiple_choice_accuracy",
        quality_higher_is_better=True,
        evaluation_suite=SUITE_NAME,
        evaluation_suite_version=suite_version(dataset_path),
    )


def write_spec(spec: BenchmarkSpec, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(spec), indent=2) + "\n", encoding="utf-8")


def _assert_collector_contract(model_id: str, revision: str,
                               dataset_path: Path) -> None:
    expected = {
        "AI_ENERGY_MODEL_ID": model_id,
        "AI_ENERGY_MODEL_VERSION": revision,
        "AI_ENERGY_EVALUATION_SUITE": SUITE_NAME,
        "AI_ENERGY_EVALUATION_VERSION": suite_version(dataset_path),
    }
    for name, value in expected.items():
        supplied = os.environ.get(name)
        if supplied is not None and supplied != value:
            raise ValueError(f"{name} does not match the benchmark specification")


def _prompt(tokenizer: Any, content: str) -> str:
    apply_template = getattr(tokenizer, "apply_chat_template", None)
    if callable(apply_template):
        return apply_template(
            [{"role": "user", "content": content}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    return content


def run_evaluation(
    *,
    model_id: str,
    revision: str,
    max_tokens: int,
    dataset_path: Path = DEFAULT_DATASET,
) -> dict[str, float]:
    """Run MLX inference and return only aggregate collector metadata."""
    revision = _validated_revision(revision)
    if not 1 <= max_tokens <= 256:
        raise ValueError("max_tokens must be between 1 and 256")
    _assert_collector_contract(model_id, revision, dataset_path)
    result_path_raw = os.environ.get("AI_ENERGY_RESULT_PATH")
    if not result_path_raw:
        raise ValueError("AI_ENERGY_RESULT_PATH is required; run through the collector")
    result_path = Path(result_path_raw)
    items = load_evaluation(dataset_path)

    from mlx_lm import load, stream_generate

    model, tokenizer = load(model_id, revision=revision)
    responses: list[str] = []
    for item in items:
        segments: list[str] = []
        item_tokens = 0
        for response in stream_generate(
            model, tokenizer, _prompt(tokenizer, item.prompt),
            max_tokens=max_tokens,
        ):
            segments.append(response.text)
            item_tokens = max(item_tokens, int(response.generation_tokens))
        if item_tokens <= 0:
            raise RuntimeError(f"model generated no tokens for item {item.item_id}")
        responses.append("".join(segments))

    accuracy = score_responses(items, responses)
    result = {
        "work_amount": float(len(items)),
        "quality_value": accuracy,
        "quality_score": accuracy,
    }
    result_path.write_text(json.dumps(result), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Reference MLX language evaluation")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    prepare = subparsers.add_parser("prepare", help="Write a pinned benchmark spec")
    run = subparsers.add_parser("run", help="Run through hardware.apple_benchmark")
    for command in (prepare, run):
        command.add_argument("--model", required=True)
        command.add_argument("--revision", required=True)
        command.add_argument("--max-tokens", type=int, default=8)
        command.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    prepare.add_argument("--precision", choices=("int4", "int8", "fp16", "bf16"),
                         required=True)
    prepare.add_argument("--device-key", required=True)
    prepare.add_argument("--compute-unit", default="gpu")
    prepare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.mode == "prepare":
        write_spec(make_spec(
            model_id=args.model,
            revision=args.revision,
            precision=args.precision,
            device_key=args.device_key,
            compute_unit=args.compute_unit,
            max_tokens=args.max_tokens,
            dataset_path=args.dataset,
        ), args.output)
        return
    run_evaluation(
        model_id=args.model,
        revision=args.revision,
        max_tokens=args.max_tokens,
        dataset_path=args.dataset,
    )


if __name__ == "__main__":
    main()
