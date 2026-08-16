# `facility-energy-v1` — the operator-declared site document

An operator declares their site once, in one file, instead of typing it into a
form on every request. The software validates the declaration, derives what it
can calculate, and compiles it into the request the planner already consumes.

**Where it lives:** `data/site-profile.json` (gitignored — it carries real
meter and connection identities). Override with the `SITE_PROFILE` environment
variable. A complete, shareable example is `docs/site-profile.example.json`.

**Read it back:** `GET /api/v1/site-profile` returns the parsed declaration,
its per-field provenance and any warnings; `422` with the exact reason when the
document is invalid; `configured: false` when a site has not declared one —
which is a state, not a fault.

---

## Why a document rather than a form

The answers live in a connection agreement, a PPA and a meter. They are stable
for years, and they are known by someone who will never open a dashboard. A
form asks the wrong person the same questions every time; a document asks the
right person once and keeps their name on the answer.

## The three boundaries it enforces

**Declaration is not measurement.** Every figure carries how it is known:

| `evidence` | Means | Provenance |
|---|---|---|
| `metered` | A real meter, which must be identified | `MEASURED` |
| `contracted` | A signed agreement | `CONTRACTED` |
| `nameplate` | A datasheet rating | `SPEC` |
| `estimated` | An informed guess | `ESTIMATED` |

`metered` requires a `grid_connection_id`: claiming a meter reading without
naming a meter is exactly the failure the tier exists to prevent.

**A modelled shape is never better than `ESTIMATED`.** An operator may have
metered their array; nobody has metered tomorrow. Only
`availability_method: "series"` — an observed profile the operator supplies —
keeps the evidence tier it was given.

**A contract is not an electron.** `delivery_type` decides whether a source can
physically serve load. `onsite` and `dedicated_wire` can; `contractual` — a
virtual PPA, a certificate — is reported, counts toward market-based
accounting, and never satisfies the physical interval energy balance or raises
the power ceiling. A site declaring only contractual instruments is refused.

## Availability methods

| Method | Derives availability from | Use when |
|---|---|---|
| `flat` | Declared capacity, constant | Firm or dispatchable supply |
| `diurnal` | A modelled daily shape around `peak_hour` | No coordinates available |
| `weather` | A live forecast at the source's own coordinates | Solar or wind with a known location |
| `series` | Operator-supplied capacity factors | A measured or plant-modelled profile exists |

`weather` requires the source's own latitude and longitude, and uses the same
irradiance and wind-speed physics as `core/renewables.py`. A declared `series`
shorter than the horizon repeats daily rather than being zero-padded, because a
24-hour shape is the usual thing an operator has and a zeroed tail would read
as an outage the site is not having.

## The power envelope — why this is a throughput input, not only a price one

`power_envelope()` returns the ceiling on facility draw in each interval:

```
ceiling(t) = min(site electrical limit, max_import_kw + physical generation(t))
```

This is the mechanism that makes on-site generation improve performance rather
than only cost. A site can run more accelerators simultaneously while its own
generation is producing, so heavy work placed in a high window runs at full
power instead of being throttled or queued behind a flat ceiling — it finishes
sooner. `core/portfolio.py` enforces this ceiling per half hour, and a job is
rejected only if it exceeds the site's best interval, never its worst.

Two rules keep it honest: only physically delivered supply raises the ceiling,
and the result is capped by the site's absolute electrical limit. Generation
raises the usable ceiling toward the switchgear rating, never through it — the
wire is the wire. Declared `confidence` derates the contribution, so a forecast
nobody trusts cannot licence a schedule that depends on it.

## What this document does not do

It does not verify anything. Nothing here reads a meter, inspects a contract or
checks a nameplate against the equipment. It records what the operator asserts,
with their name and the date on it, so a later dispute is about a stated claim
rather than a number of unknown origin. Verification against metered data is a
separate step and is not built.
