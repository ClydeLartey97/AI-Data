"""
Model catalogue.

Parameter counts and precision are all the estimator needs, which is why a
custom model is a two-field form rather than a special case: give it a size
and a precision and it works exactly like a catalogue entry.

**Mixture-of-experts models need two numbers, not one, and conflating them is
the most common way this maths goes wrong.** A MoE routes each token through a
fraction of its parameters, so:

- **Compute** scales with the *active* parameters — the experts a token
  actually visits.
- **Memory** scales with the *total* parameters — every expert must be
  resident, because routing is per token and you cannot know in advance which
  expert is needed.

DeepSeek-V3 is the clearest case: 671B total, ~37B active. Size it by compute
and it looks like a 37B model that trains fast; size it by memory and it needs
well over a terabyte. Both are true, and a simulator that tracks only one of
them will confidently mislead.

Parameter counts are as published by the model authors. They are stable facts
rather than benchmarks, so unlike the hardware catalogue there is little to
mis-measure — but a few MoE active-parameter figures are approximate and are
marked in ``notes``.
"""
from __future__ import annotations

from dataclasses import dataclass

BYTES_PER_PARAM = {"fp32": 4, "fp16": 2, "bf16": 2, "fp8": 1, "int8": 1, "int4": 0.5}

PRECISIONS = ["bf16", "fp16", "fp8", "int8", "int4", "fp32"]


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    family: str
    params_b: float                      # total, drives MEMORY
    active_params_b: float | None = None  # MoE only; drives COMPUTE
    precision: str = "bf16"
    notes: str = ""

    @property
    def compute_params_b(self) -> float:
        """Parameters a single token actually passes through."""
        return self.active_params_b or self.params_b

    @property
    def is_moe(self) -> bool:
        return self.active_params_b is not None and self.active_params_b < self.params_b

    def weight_bytes(self, precision: str | None = None) -> float:
        return self.params_b * 1e9 * BYTES_PER_PARAM.get(precision or self.precision, 2)

    def weight_gb(self, precision: str | None = None) -> float:
        return self.weight_bytes(precision) / 1e9


def _m(key, name, family, params, active=None, notes=""):
    return Model(key=key, name=name, family=family, params_b=params,
                 active_params_b=active, notes=notes)


CATALOG: dict[str, Model] = {m.key: m for m in [
    # --- Llama ------------------------------------------------------------
    _m("llama32-1b", "Llama 3.2 1B", "Llama", 1.2),
    _m("llama32-3b", "Llama 3.2 3B", "Llama", 3.2),
    _m("llama31-8b", "Llama 3.1 8B", "Llama", 8.0),
    _m("llama31-70b", "Llama 3.1 70B", "Llama", 70.6),
    _m("llama31-405b", "Llama 3.1 405B", "Llama", 405.0),

    # --- Mistral ----------------------------------------------------------
    _m("mistral-7b", "Mistral 7B", "Mistral", 7.3),
    _m("mistral-nemo", "Mistral NeMo 12B", "Mistral", 12.2),
    _m("mistral-small-24b", "Mistral Small 24B", "Mistral", 23.6),
    _m("mistral-large", "Mistral Large 123B", "Mistral", 123.0),
    _m("mixtral-8x7b", "Mixtral 8x7B", "Mistral", 46.7, 12.9,
       "MoE: 8 experts, 2 active per token"),
    _m("mixtral-8x22b", "Mixtral 8x22B", "Mistral", 141.0, 39.0,
       "MoE: 8 experts, 2 active per token"),

    # --- Qwen -------------------------------------------------------------
    _m("qwen25-0.5b", "Qwen2.5 0.5B", "Qwen", 0.49),
    _m("qwen25-1.5b", "Qwen2.5 1.5B", "Qwen", 1.54),
    _m("qwen25-3b", "Qwen2.5 3B", "Qwen", 3.09),
    _m("qwen25-7b", "Qwen2.5 7B", "Qwen", 7.62),
    _m("qwen25-14b", "Qwen2.5 14B", "Qwen", 14.7),
    _m("qwen25-32b", "Qwen2.5 32B", "Qwen", 32.8),
    _m("qwen25-72b", "Qwen2.5 72B", "Qwen", 72.7),

    # --- Gemma ------------------------------------------------------------
    _m("gemma2-2b", "Gemma 2 2B", "Gemma", 2.6),
    _m("gemma2-9b", "Gemma 2 9B", "Gemma", 9.2),
    _m("gemma2-27b", "Gemma 2 27B", "Gemma", 27.2),

    # --- Phi --------------------------------------------------------------
    _m("phi3-mini", "Phi-3 mini 3.8B", "Phi", 3.8),
    _m("phi3-small", "Phi-3 small 7B", "Phi", 7.4),
    _m("phi3-medium", "Phi-3 medium 14B", "Phi", 14.0),

    # --- DeepSeek ---------------------------------------------------------
    _m("deepseek-v3", "DeepSeek-V3 671B", "DeepSeek", 671.0, 37.0,
       "MoE: ~37B active per token. Compute of a 37B model, memory of a 671B one."),
    _m("deepseek-r1", "DeepSeek-R1 671B", "DeepSeek", 671.0, 37.0,
       "MoE, same shape as V3"),

    # --- Command R --------------------------------------------------------
    _m("command-r", "Command R 35B", "Cohere", 35.0),
    _m("command-r-plus", "Command R+ 104B", "Cohere", 104.0),

    # --- Falcon -----------------------------------------------------------
    _m("falcon-7b", "Falcon 7B", "Falcon", 7.2),
    _m("falcon-40b", "Falcon 40B", "Falcon", 41.8),
    _m("falcon-180b", "Falcon 180B", "Falcon", 180.0),

    # --- Yi ---------------------------------------------------------------
    _m("yi-6b", "Yi 6B", "Yi", 6.1),
    _m("yi-34b", "Yi 34B", "Yi", 34.4),

    # --- GPT-OSS ----------------------------------------------------------
    _m("gpt-oss-20b", "GPT-OSS 20B", "GPT-OSS", 21.0, 3.6, "MoE"),
    _m("gpt-oss-120b", "GPT-OSS 120B", "GPT-OSS", 117.0, 5.1, "MoE"),
]}


def families() -> list[str]:
    seen, out = set(), []
    for m in CATALOG.values():
        if m.family not in seen:
            seen.add(m.family)
            out.append(m.family)
    return out


def get(key: str) -> Model:
    if key not in CATALOG:
        raise KeyError(f"unknown model {key!r}")
    return CATALOG[key]


def custom(params_b: float, *, active_params_b: float | None = None,
           precision: str = "bf16", name: str = "Custom model") -> Model:
    """Build a model from parameters alone.

    There is no catalogue lookup involved — the estimator only ever needed a
    size and a precision, so anything not listed above is a first-class input
    rather than an unsupported case.
    """
    return Model(key="custom", name=name, family="Custom",
                 params_b=params_b, active_params_b=active_params_b,
                 precision=precision, notes="User-specified")
