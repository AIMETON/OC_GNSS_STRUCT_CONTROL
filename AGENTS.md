# AGENTS.md — OC_GNSS_STRUCT_CONTROL

## Scope

These rules apply to the whole repository. Narrower `AGENTS.md` files may strengthen but not weaken them.

Canonical AIMETON-wide governance: `AIMETON/aimeton-architecture/AGENTS.md`.

## Mission rule

Work is performed as an evidence-driven engineering mission, not as isolated code generation. Keep the system objective visible across commits, issues, tests and reports.

Before substantial work read the current README/status/engineering-preview docs, active P0 issue, relevant scenarios/contracts, current PR/CI state and exact `main` SHA. For cross-repository work read every touched repository's root `AGENTS.md` before the first mutation.

For runner/deployment/runtime infrastructure facts use `Dimar4713/aimeton-infrastructure`; for normative AIMETON principles use `AIMETON/aimeton-architecture`.

## 3×3 Reality Check

Before blocker, root-cause, physics/model conclusion, fidelity claim, release/compatibility claim, security/cost decision or consequential write, treat the first explanation as a hypothesis.

Check at least:

- architecture/lifecycle;
- alternatives/control paths;
- history/live;
- source/contract;
- runtime/live;
- independent numerical/engineering evidence;
- falsification attempt.

Claims such as `Orekit is unavailable`, `Python backend is equivalent`, `design validated`, `Windows package works`, `no access`, or `only path` are provisional until the applicable gate is complete.

## GitHub / execution fallback

Before requesting manual owner action, check:

`GitHub connector/API → AIMETON GitHub MCP/router → REST/GraphQL/gh through trusted AIMETON server → owner`.

A limitation of one runner/token/workflow/connector is not a system-level AIMETON limitation. Never expose secret values or unpublished engineering data.

## Required execution loop / Motor State

```text
READ → DECIDE → ACTION → READ-BACK → EVIDENCE → NEXT SAFE ACTION
```

1. Read the parent mission and active P0 issue.
2. Make the smallest coherent architectural/engineering change that advances an acceptance gate.
3. Add or update automated evidence: test, schema validation, artifact, numerical comparison or CI gate.
4. Inspect the actual result.
5. Continue to the next safe step; do not stop at local success while an immediately actionable gate remains.
6. Record unresolved physical/technical uncertainty explicitly rather than hiding it behind defaults.
7. Keep a current → next → following action queue.

Absence of a new owner message is not a blocker. Before ending a tool session perform MOTOR-CHECK and STOP-CHECK. PR open/merge, GREEN CI, successful scenario or packaged release are state transitions, not automatic mission completion.

## Physics safety rules

- Never optimise secular drift using instantaneous osculating `a`.
- Never compare mean elements produced by different force-model definitions without explicit conversion.
- Never label screening output as design/validation evidence.
- Never silently fall back from Orekit to a lower-fidelity backend.
- Never invent operational constellation parameters in code; scenario values belong to configuration.
- Hard safety constraints fail closed.
- Frames, time scales, units, force-model definition, integrator and output step must remain explicit where they affect interpretation.
- Numerical authority and UI/convenience layers must remain distinguishable.

## Model / evidence truth gate

Explicitly distinguish:

`scenario/input → mathematical model → numerical backend → implementation → runtime result → engineering validation`.

One layer cannot silently substitute for another. Synthetic/smoke scenarios prove execution contracts, not flight/design validity. A successful Orekit run does not by itself validate modelling assumptions or acceptance ranges.

Reference datasets/scenarios must have provenance and immutable/versioned inputs where practical. Do not loosen tolerances, change force models or alter scenarios merely to restore GREEN without documenting the engineering reason.

## Change-management rule

Preserve working behaviour unless a change has a stated acceptance reason and regression evidence. Refactors must not silently alter physical semantics, units, frames, time scales or mean-element definition.

Operator UI must not introduce hidden control/search/robustness defaults. Derived scenarios must preserve parent/provenance and perturbation metadata.

## Windows / package truth gate

Clean-machine Windows 10/11 package behavior, legacy/stale sidecar recovery, Orekit data revision/hash verification and operator UI behavior require actual package-level evidence; development-venv CI is not a substitute.

If a Windows-specific lane or other specialized runner is needed, runner lifecycle/topology remains owned by `aimeton-infrastructure`; do not create a local runner controller or hardcode a new physical identity as architecture.

## Cross-repository source-of-truth

Infrastructure runner/provider state belongs to canonical AIMETON repositories. Generated projections must pin canonical repository, exact source SHA, source path, immutable blob/object id and/or digest; drift must fail closed.

## Authority boundary

Without owner authorization do not create new paid compute/provider spend, make irreversible production/provider mutations, weaken physical/numerical safety gates, change legal/license boundaries, or publish private engineering/secrets material.

## Evidence before closure

An issue is closed only when code, tests/docs and CI/runtime evidence satisfy its acceptance criteria. A PR description must list assumptions, unresolved limitations and the next fidelity gate.

Applicable Definition of Done also requires the next safe action to be executed or an exact objective blocker to be recorded, and strong conclusions to pass 3×3.
