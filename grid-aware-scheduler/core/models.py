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


#: How much the parameter count can be trusted. Frontier labs stopped
#: publishing sizes around GPT-4, so anything current is either reported by
#: third parties or simply unknown — and a simulator that quietly treats a
#: rumour as a datasheet is worthless. PUBLISHED means the authors stated it.
CONFIDENCE = ("PUBLISHED", "REPORTED", "UNPUBLISHED")


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    family: str
    params_b: float                      # total, drives MEMORY
    active_params_b: float | None = None  # MoE only; drives COMPUTE
    precision: str = "bf16"
    notes: str = ""
    confidence: str = "PUBLISHED"
    open_weights: bool = True

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


def _m(key, name, family, params, active=None, notes="",
       confidence="PUBLISHED", open_weights=True):
    return Model(key=key, name=name, family=family, params_b=params,
                 active_params_b=active, notes=notes,
                 confidence=confidence, open_weights=open_weights)


CATALOGUE: dict[str, Model] = {m.key: m for m in [
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
    for m in CATALOGUE.values():
        if m.family not in seen:
            seen.add(m.family)
            out.append(m.family)
    return out


def get(key: str) -> Model:
    if key not in CATALOGUE:
        raise KeyError(f"unknown model {key!r}")
    return CATALOGUE[key]


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

# --- appended: wider catalogue ------------------------------------------
CATALOGUE.update({m.key: m for m in [
    # Llama earlier + Llama 4
    _m("llama2-7b", "Llama 2 7B", "Llama", 6.7),
    _m("llama2-13b", "Llama 2 13B", "Llama", 13.0),
    _m("llama2-70b", "Llama 2 70B", "Llama", 69.0),
    _m("llama4-scout", "Llama 4 Scout", "Llama", 109.0, 17.0, "MoE, 16 experts"),
    _m("llama4-maverick", "Llama 4 Maverick", "Llama", 400.0, 17.0, "MoE, 128 experts"),

    # Qwen 3
    _m("qwen3-0.6b", "Qwen3 0.6B", "Qwen", 0.6),
    _m("qwen3-4b", "Qwen3 4B", "Qwen", 4.0),
    _m("qwen3-8b", "Qwen3 8B", "Qwen", 8.2),
    _m("qwen3-14b", "Qwen3 14B", "Qwen", 14.8),
    _m("qwen3-32b", "Qwen3 32B", "Qwen", 32.8),
    _m("qwen3-30b-a3b", "Qwen3 30B-A3B", "Qwen", 30.5, 3.3, "MoE"),
    _m("qwen3-235b-a22b", "Qwen3 235B-A22B", "Qwen", 235.0, 22.0, "MoE"),

    # DeepSeek
    _m("deepseek-v2", "DeepSeek-V2 236B", "DeepSeek", 236.0, 21.0, "MoE"),
    _m("deepseek-coder-33b", "DeepSeek Coder 33B", "DeepSeek", 33.0),

    # Gemma 3
    _m("gemma3-1b", "Gemma 3 1B", "Gemma", 1.0),
    _m("gemma3-4b", "Gemma 3 4B", "Gemma", 4.3),
    _m("gemma3-12b", "Gemma 3 12B", "Gemma", 12.2),
    _m("gemma3-27b", "Gemma 3 27B", "Gemma", 27.4),

    # Phi 4
    _m("phi4", "Phi-4 14B", "Phi", 14.7),
    _m("phi4-mini", "Phi-4 mini 3.8B", "Phi", 3.8),

    # Code models
    _m("codellama-34b", "Code Llama 34B", "Code", 33.7),
    _m("codellama-70b", "Code Llama 70B", "Code", 69.0),
    _m("starcoder2-15b", "StarCoder2 15B", "Code", 15.5),

    # Other open weights
    _m("grok-1", "Grok-1 314B", "Grok", 314.0, 86.0, "MoE, open weights"),
    _m("olmo2-13b", "OLMo 2 13B", "OLMo", 13.7, notes="Fully open training data"),
    _m("nemotron-340b", "Nemotron-4 340B", "Nemotron", 340.0),
    _m("dbrx", "DBRX 132B", "DBRX", 132.0, 36.0, "MoE"),

    # --- Frontier models -------------------------------------------------
    # Parameter counts here are NOT datasheet facts. GPT-3 was published in
    # the paper; GPT-4's shape is widely reported but never confirmed; the
    # current frontier models from OpenAI, Anthropic and Google publish
    # nothing at all. Rather than invent numbers, the unpublished ones are
    # listed with a placeholder size and marked UNPUBLISHED so the UI can say
    # so plainly. Use them to explore shape, never to quote a figure.
    _m("gpt3-175b", "GPT-3 175B", "Frontier", 175.0,
       notes="Published in the GPT-3 paper", open_weights=False),
    _m("gpt4-reported", "GPT-4 (reported ~1.8T MoE)", "Frontier", 1760.0, 280.0,
       notes="Widely reported as a ~1.8T MoE with ~280B active. Never confirmed "
             "by OpenAI — treat as a shape, not a fact.",
       confidence="REPORTED", open_weights=False),
    _m("frontier-unpublished", "Frontier model (size unpublished)", "Frontier", 500.0, 60.0,
       notes="OpenAI, Anthropic and Google no longer publish parameter counts. "
             "This is a placeholder for exploring scale, not an estimate of any "
             "specific model.",
       confidence="UNPUBLISHED", open_weights=False),
]})


# --------------------------------------------------------------------------
# Architecture — what the KV cache actually depends on
# --------------------------------------------------------------------------
"""
Parameter count alone cannot size an inference deployment.

The KV cache holds one key and one value vector per token, per layer, for
every sequence in flight. Its size is:

    bytes = 2 x layers x kv_heads x head_dim x context x batch x bytes_per_elem

None of that is derivable from the parameter count, and at long context it
stops being a rounding error: a 70B model at bf16 is 141 GB of weights, but
serving 32 concurrent sequences at 128k context needs *more KV cache than
weights*. A simulator using a flat 1.25x multiplier — as this one did — will
tell you a job fits on hardware that would immediately run out of memory.

Grouped-query attention is the other half. Modern models share key/value
heads across query heads (Llama 3 70B has 64 query heads but only 8 KV
heads), which cuts the cache by 8x. Ignoring GQA overstates cache by nearly
an order of magnitude on exactly the models people actually serve.
"""


@dataclass(frozen=True)
class Architecture:
    layers: int
    hidden: int
    heads: int
    kv_heads: int          # < heads means grouped-query attention
    estimated: bool = False

    @property
    def head_dim(self) -> int:
        return self.hidden // self.heads if self.heads else 128

    @property
    def gqa_ratio(self) -> float:
        return self.heads / self.kv_heads if self.kv_heads else 1.0

    def kv_bytes_per_token(self, precision: str = "fp16") -> float:
        """Both K and V, across every layer, for one token of one sequence."""
        elem = BYTES_PER_PARAM.get(precision, 2)
        return 2 * self.layers * self.kv_heads * self.head_dim * elem


#: Published architectures for the models most likely to be served. Values are
#: from the model cards / config.json.
ARCHITECTURES: dict[str, Architecture] = {
    "llama32-1b":   Architecture(16, 2048, 32, 8),
    "llama32-3b":   Architecture(28, 3072, 24, 8),
    "llama31-8b":   Architecture(32, 4096, 32, 8),
    "llama31-70b":  Architecture(80, 8192, 64, 8),
    "llama31-405b": Architecture(126, 16384, 128, 8),
    "llama2-7b":    Architecture(32, 4096, 32, 32),   # pre-GQA: cache is 4x bigger
    "llama2-13b":   Architecture(40, 5120, 40, 40),
    "llama2-70b":   Architecture(80, 8192, 64, 8),
    "mistral-7b":   Architecture(32, 4096, 32, 8),
    "mixtral-8x7b": Architecture(32, 4096, 32, 8),
    "mixtral-8x22b": Architecture(56, 6144, 48, 8),
    "qwen25-7b":    Architecture(28, 3584, 28, 4),
    "qwen25-14b":   Architecture(48, 5120, 40, 8),
    "qwen25-32b":   Architecture(64, 5120, 40, 8),
    "qwen25-72b":   Architecture(80, 8192, 64, 8),
    "qwen3-8b":     Architecture(36, 4096, 32, 8),
    "qwen3-32b":    Architecture(64, 5120, 64, 8),
    "gemma2-9b":    Architecture(42, 3584, 16, 8),
    "gemma2-27b":   Architecture(46, 4608, 32, 16),
    "phi3-mini":    Architecture(32, 3072, 32, 32),
    "phi4":         Architecture(40, 5120, 40, 10),
    "deepseek-v3":  Architecture(61, 7168, 128, 128),
    "deepseek-r1":  Architecture(61, 7168, 128, 128),
    "command-r":    Architecture(40, 8192, 64, 64),
    "falcon-40b":   Architecture(60, 8192, 128, 8),
    "falcon-180b":  Architecture(80, 14848, 232, 8),
    "yi-34b":       Architecture(60, 7168, 56, 8),
    "grok-1":       Architecture(64, 6144, 48, 8),
    "dbrx":         Architecture(40, 6144, 48, 8),
}


def estimate_architecture(params_b: float) -> Architecture:
    """Infer a plausible shape when the real one is not catalogued.

    Uses the aspect ratios transformers actually cluster around rather than a
    formula, and assumes 8 KV heads because essentially every model of
    consequence released since 2023 uses grouped-query attention. Flagged
    ``estimated`` so the UI can say the KV figure is a guess about the model's
    shape, not just about its size.
    """
    table = [
        (1.5, 16, 2048, 16), (4, 28, 3072, 24), (9, 32, 4096, 32),
        (16, 40, 5120, 40), (35, 48, 6144, 48), (80, 80, 8192, 64),
        (200, 96, 12288, 96), (1e9, 126, 16384, 128),
    ]
    for ceiling, layers, hidden, heads in table:
        if params_b <= ceiling:
            return Architecture(layers, hidden, heads, min(8, heads), estimated=True)
    return Architecture(126, 16384, 128, 8, estimated=True)


def architecture_for(key: str, params_b: float) -> Architecture:
    return ARCHITECTURES.get(key) or estimate_architecture(params_b)


def kv_cache_gb(arch: Architecture, context: int, batch: int,
                precision: str = "fp16") -> float:
    """KV cache for ``batch`` sequences each holding ``context`` tokens."""
    return arch.kv_bytes_per_token(precision) * context * batch / 1e9
