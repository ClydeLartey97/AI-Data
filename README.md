# AI-Energy

Work on the energy cost and carbon intensity of AI compute.

## Projects

### [`grid-aware-scheduler/`](grid-aware-scheduler/)

A scheduler that decides **which GPU runs which job, and when**, by combining two signals:

1. **Hardware efficiency** — different accelerators have different power-to-throughput curves, so jobs are matched to the unit that suits their profile rather than treated as interchangeable.
2. **Grid timing** — using live or forecast electricity price and carbon intensity, flexible workloads shift into cheaper, cleaner half-hours. Urgent jobs run immediately regardless.

It is **market-agnostic** by construction: scheduling logic never touches market-specific code. Each electricity market sits behind an adapter that translates its API into one common format, so switching markets is a config change, not a code change.

**Status:** the GB market adapter is built and verified against live data (Carbon Intensity API + Elexon Insights, both public and keyless). The scheduling logic itself is not written yet.

**This is an applied synthesis, not a new category** — it combines published mechanisms (Zeus, Perseus, Google's carbon-intelligent computing) that had not been combined in one open, runnable system. It does **not** generate carbon credits. See [`grid-aware-scheduler/HANDOFF.md`](grid-aware-scheduler/HANDOFF.md) for prior art, the full design rationale, and current state.

## Working on this

Start with [`grid-aware-scheduler/HANDOFF.md`](grid-aware-scheduler/HANDOFF.md). It is the single source of truth for project state, decisions made, and what to do next — kept current at the end of every working session.
