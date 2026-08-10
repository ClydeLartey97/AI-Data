# Commercial readiness

This document separates a credible product prototype from a production system
and from a valuable company. Those are different thresholds.

## Current product stage

The repository is a **sellable pilot foundation**, not yet a production-grade
control plane. It can demonstrate the complete decision path on real GB and
CAISO data, produce an exact recommendation, preserve the inputs and signal
snapshot, and score the recommendation later. It must not yet be trusted to
launch or defer a customer's workload.

| Capability | Current evidence | Stage | Production exit criterion |
|---|---|---|---|
| Market data | Live GB national/regional, CAISO nodal and NYISO zonal price plus correctly scoped carbon | Pilot | Remaining US operators, contracted feeds, freshness SLOs, reconciliation and failover |
| Placement algorithm | Exact single-job search plus bounded exact portfolio scheduling with half-hour facility capacity, hard total cost/carbon policies and deterministic tie-breaking | Pilot | Time-indexed production solver, published optimality gap and independent validation on customer traces |
| Workload estimation | Architecture-aware estimates plus a modality-neutral measured evidence contract for language, vision, speech and training work units | Prototype | Apple-device benchmark collection, prediction intervals and drift monitoring |
| Model routing | Quality gate before placement, with measured comparable evaluation scores required | Prototype | Customer-approved evaluation suites and governed model registry |
| Decision evidence | Immutable request, response and signal snapshot plus realised-score attachment | Pilot | Tenant-scoped append-only store, retention policy and signed exports |
| Operator product | AI Operations home plus linked Fleet Lab, Placement Lab, Sites & Grid terminal and Decision Journal | Pilot | Real workload/fleet ingestion, user research, accessibility audit and supported browser matrix |
| Workload execution | No launcher or admission controller | Not built | Idempotent execution adapter, cancellation, retries and rollback |
| Security | Loopback-only service, body limits and restrictive browser headers | Development | Identity, role policy, tenant isolation, secrets management and security review |
| Reliability | Unit/integration tests and explicit failures | Development | Service-level objectives, telemetry, alerting, backup/restore and incident runbooks |
| Deployment | Local Python process | Development | Reproducible signed package, migrations, environment configuration and upgrade path |

## Claims that are supportable now

- The planner examines every feasible hardware and half-hour placement in its
  declared search space.
- GB and CAISO location controls have different, explicitly labelled spatial
  precision. Carbon is not represented as nodal where the source is only
  balancing-area or regional.
- A saved recommendation is reproducible from its inputs and decision-time
  signal snapshot.
- The browser planner is a responsive preview. The persisted server decision
  is authoritative.
- Historical replay demonstrates technical behaviour. It is not a forecast,
  a tariff quote, a contractual saving or evidence of future performance.

## Claims that are not supportable yet

- Guaranteed energy or carbon savings.
- Production safety for customer workloads.
- Measured power or throughput for catalogue devices that remain labelled
  `SPEC` or `ESTIMATED`.
- Nodal carbon intensity in CAISO.
- Carbon-credit generation.
- A specific company valuation.

## Route to a paid pilot

1. Calibrate at least two supported accelerator families using repeatable
   workloads and wall-power or trusted device telemetry. Publish error bands.
2. Ingest forecast and realised signals automatically and run shadow decisions
   for four to eight weeks without controlling workloads.
3. Agree one customer success metric, such as cost saved subject to a carbon
   ceiling and deadline-completion SLO.
4. Add one execution integration behind a dry-run and approval gate. Preserve
   idempotency, cancellation and a complete action log.
5. Add identity, tenant isolation, encrypted configuration, observability,
   backup/restore and a deployment package before processing customer data.
6. Convert shadow evidence into a paid pilot with a defined baseline,
   exclusions, support boundary and no unsupported savings guarantee.

## Commercial value test

A seven-figure valuation cannot be guaranteed by software completeness. A
credible case would normally require evidence that the system controls or
advises a meaningful compute budget, produces repeatable net savings after
operational costs, retains customers, and has defensible data or workflow
integration. The next commercial milestone is therefore a paid or strongly
committed design-partner pilot, not another valuation claim.
