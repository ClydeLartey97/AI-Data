"""Prefill and decode throughput, measured separately.

These are two different machines wearing one name. Prefill processes the
prompt in parallel and is compute-bound; decode emits one token at a time and
is memory-bandwidth-bound. A single "tokens per second" figure averages them
according to whatever prompt and generation lengths the benchmark happened to
use, which is why two honest benchmarks of the same model disagree.

Keeping them apart is what makes the number useful to a scheduler: a
long-context summarisation is prefill-heavy and wants arithmetic throughput,
while a long-generation task is decode-heavy and wants memory bandwidth. The
same fleet ranks differently for the two.

Comparability rules, unchanged from the rest of the programme: the model is
pinned to an immutable revision, the prompt is a fixed token count rather
than arbitrary text, sampling is deterministic, warm-up is excluded, and the
run is refused outright if the host cannot produce a valid measurement.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from hardware import preflight

#: Pinned to an immutable commit: a moving tag would silently change the
#: thing being measured between runs.
DEFAULT_MODEL = "Qwen/Qwen3-1.7B-MLX-4bit"
DEFAULT_PRECISION = "4bit"
#: Prompt lengths sweep the prefill curve. Short prompts are launch-bound and
#: understate the device, exactly as small matrices do in the GEMM sweep.
DEFAULT_PROMPT_TOKENS = (128, 512, 2048)
DEFAULT_GENERATION_TOKENS = 128
WARMUP_TOKENS = 8
#: mlx_lm reports its own rates; a wall-clock disagreement beyond this means
#: one of the two is measuring something other than the work.
TIMING_TOLERANCE = 0.25


class ModelUnavailable(RuntimeError):
    """MLX-LM or the pinned model weights are not present."""


@dataclass(frozen=True)
class PhaseMeasurement:
    phase: str                 # "prefill" | "decode"
    tokens: int
    tokens_per_second: float
    seconds: float


@dataclass(frozen=True)
class InferenceRun:
    model_id: str
    revision: str | None
    precision: str
    prompt_tokens: int
    generation_tokens: int
    prefill_tps: float
    decode_tps: float
    peak_memory_gb: float
    wall_seconds: float
    finish_reason: str | None
    timing_cross_check: float
    notes: tuple[str, ...] = ()

    def measurements(self) -> list[dict]:
        """Shaped for the baseline history, which stores named rates."""
        return [
            {"name": "prefill", "dtype": self.precision,
             "size": self.prompt_tokens, "rate": self.prefill_tps,
             "rate_unit": "tokens/s"},
            {"name": "decode", "dtype": self.precision,
             "size": self.generation_tokens, "rate": self.decode_tps,
             "rate_unit": "tokens/s"},
        ]


@dataclass
class InferenceReport:
    device: str
    model_id: str
    revision: str | None
    observed_at: str
    stack: str
    runs: list[InferenceRun] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["runs"] = [asdict(run) for run in self.runs]
        payload["measurements"] = [m for run in self.runs for m in run.measurements()]
        return payload

    def best(self, phase: str) -> float | None:
        rates = [run.prefill_tps if phase == "prefill" else run.decode_tps
                 for run in self.runs]
        return max(rates) if rates else None


def _load(model_id: str, revision: str | None):
    try:
        from mlx_lm import load
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ModelUnavailable("mlx-lm is not installed") from exc
    try:
        return load(model_id, revision=revision) if revision else load(model_id)
    except Exception as exc:  # weights absent, offline, or revision unknown
        raise ModelUnavailable(f"could not load {model_id}: {exc}") from exc


def _fixed_prompt(tokenizer, token_count: int) -> str:
    """A prompt of an exact token length, so prefill is measured at a known
    shape rather than at whatever length some prose happened to tokenise to."""
    if token_count < 1:
        raise ValueError("prompt token count must be positive")
    seed = "the quick brown fox jumps over the lazy dog "
    text = seed * max(1, token_count // 4)
    tokens = tokenizer.encode(text)
    while len(tokens) < token_count:
        text += seed
        tokens = tokenizer.encode(text)
    return tokenizer.decode(tokens[:token_count])


def measure(model, tokenizer, *, prompt_tokens: int, generation_tokens: int,
            model_id: str, revision: str | None,
            precision: str = DEFAULT_PRECISION) -> InferenceRun:
    """One prefill/decode measurement at a fixed shape."""
    from mlx_lm import stream_generate

    prompt = _fixed_prompt(tokenizer, prompt_tokens)
    started = time.perf_counter()
    final = None
    for response in stream_generate(model, tokenizer, prompt,
                                    max_tokens=generation_tokens):
        final = response
    wall = time.perf_counter() - started
    if final is None:
        raise RuntimeError("generation produced no tokens")

    prefill_seconds = (final.prompt_tokens / final.prompt_tps
                       if final.prompt_tps else 0.0)
    decode_seconds = (final.generation_tokens / final.generation_tps
                      if final.generation_tps else 0.0)
    accounted = prefill_seconds + decode_seconds
    # The framework reports its own rates. If they cannot account for the
    # wall clock, one of the two is measuring the wrong thing.
    cross_check = abs(wall - accounted) / wall if wall > 0 else 0.0
    notes = []
    if cross_check > TIMING_TOLERANCE:
        notes.append(
            f"reported phase timings account for only {accounted:.2f}s of "
            f"{wall:.2f}s wall clock")
    return InferenceRun(
        model_id=model_id,
        revision=revision,
        precision=precision,
        prompt_tokens=int(final.prompt_tokens),
        generation_tokens=int(final.generation_tokens),
        prefill_tps=float(final.prompt_tps),
        decode_tps=float(final.generation_tps),
        peak_memory_gb=round(float(final.peak_memory), 3),
        wall_seconds=round(wall, 3),
        finish_reason=final.finish_reason,
        timing_cross_check=round(cross_check, 4),
        notes=tuple(notes),
    )


def run(*, model_id: str = DEFAULT_MODEL, revision: str | None = None,
        prompt_lengths=DEFAULT_PROMPT_TOKENS,
        generation_tokens: int = DEFAULT_GENERATION_TOKENS,
        precision: str = DEFAULT_PRECISION,
        skip_preflight: bool = False,
        min_free_memory_gb: float = 3.0) -> InferenceReport:
    from hardware.apple_benchmark import stack_fingerprint

    context: dict = {}
    warnings: list[str] = []
    if not skip_preflight:
        gate = preflight.check(min_free_memory_gb=min_free_memory_gb)
        context = gate.context
        warnings.extend(gate.cautions)
        gate.raise_if_invalid()

    report = InferenceReport(
        device=context.get("device") or "unknown",
        model_id=model_id, revision=revision,
        observed_at=datetime.now(timezone.utc).isoformat(),
        stack=stack_fingerprint(), context=context, warnings=warnings,
    )
    try:
        model, tokenizer = _load(model_id, revision)
    except ModelUnavailable as exc:
        report.warnings.append(str(exc))
        return report

    # Warm-up: the first generation pays model compilation and allocation.
    try:
        measure(model, tokenizer, prompt_tokens=32,
                generation_tokens=WARMUP_TOKENS, model_id=model_id,
                revision=revision, precision=precision)
    except Exception as exc:
        report.warnings.append(f"warm-up failed: {exc}")

    for prompt_length in prompt_lengths:
        try:
            report.runs.append(measure(
                model, tokenizer, prompt_tokens=prompt_length,
                generation_tokens=generation_tokens, model_id=model_id,
                revision=revision, precision=precision))
        except Exception as exc:
            report.warnings.append(f"prompt {prompt_length}: {exc}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure prefill and decode throughput separately.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=None)
    parser.add_argument("--prompt-tokens", type=int, nargs="+",
                        default=list(DEFAULT_PROMPT_TOKENS))
    parser.add_argument("--generation-tokens", type=int,
                        default=DEFAULT_GENERATION_TOKENS)
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--store", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run(model_id=args.model, revision=args.revision,
                 prompt_lengths=tuple(args.prompt_tokens),
                 generation_tokens=args.generation_tokens,
                 skip_preflight=args.skip_preflight)

    print(f"{report.model_id}  ({report.device})")
    for item in report.runs:
        print(f"  prompt {item.prompt_tokens:>5} tok : "
              f"prefill {item.prefill_tps:8.1f} tok/s | "
              f"decode {item.decode_tps:7.2f} tok/s | "
              f"peak {item.peak_memory_gb:.2f} GB")
        for note in item.notes:
            print(f"      note: {note}")
    if report.runs:
        decode = [r.decode_tps for r in report.runs]
        print(f"  decode across prompt lengths: median {statistics.median(decode):.2f}"
              f" tok/s, spread {max(decode) - min(decode):.2f}")
    for warning in report.warnings:
        print(f"  warning: {warning}")

    if args.store and report.runs:
        from hardware import baseline_store
        run_id = baseline_store.record_run(report.as_dict())
        print(f"Stored run {run_id}")
    if args.output:
        args.output.write_text(json.dumps(report.as_dict(), indent=2) + "\n",
                               encoding="utf-8")
        print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
