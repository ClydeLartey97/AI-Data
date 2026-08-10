# Quality-constrained routing

The project handoff originally described task-difficulty routing as selecting
hardware directly from semantic difficulty. That is not a defensible causal
model. A fixed model with the same token shape does not require more FLOPs or
memory bandwidth merely because the prompt is conceptually harder.

The commercially credible decision chain is:

```text
workload class
  -> measured quality floor
  -> eligible model deployments
  -> memory and deadline feasibility
  -> hardware, facility and half-hour optimisation
```

`core/routing.py` implements this policy boundary. Each candidate must carry a
normalised score from one named, versioned evaluation suite for the requested
workload class. Scores from different suites or versions cannot be compared.
Measured quality evidence is required by default. Estimated evidence is used
only when the caller opts in explicitly.

The router first rejects deployments below the quality floor. It then passes
only eligible deployments to the exact planner described in
[`planner.md`](planner.md). This means cost or carbon optimisation can never
silently select a cheaper model that fails the operator's quality requirement.

The module deliberately does not include a prompt classifier or benchmark
scores. Those must come from a customer-specific, versioned evaluation and be
auditable. Inventing scores would make the route look intelligent while
removing the evidence needed to trust it.
