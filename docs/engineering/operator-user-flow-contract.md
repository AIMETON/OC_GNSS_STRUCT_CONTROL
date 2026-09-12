# Operator user-flow contract

This document is the field contract for the Engineering Preview operator UI. It is intentionally mission-oriented and fail-closed.

## Global invariants

1. A user action must end in one of three visible states: `READY/RUNNABLE`, `BLOCKED` with a concrete reason, or `FAILED` with a concrete error. A click must never look like a no-op.
2. Data authority and modelling authority are separate concepts. External GNSS data never silently inherits force model, frame, time scale, spacecraft model, or Orekit endpoint from the currently active scenario.
3. A modelling authority must be selected explicitly when a source-to-scenario conversion needs one. Assisted/Auto may prepare a candidate, but must not silently promote the active scenario.
4. Creating a scenario is immutable: the source scenario is never overwritten. Success means a new YAML exists, `/api/scenarios` refreshes, the new scenario is selected, and `loadScenario()` succeeds.
5. Failed network, parse, conversion, validation, refresh, or browser-JS steps must leave the prior active scenario intact and expose the error to the operator.
6. Every external-source-derived ScenarioConfig records source identity, transport where applicable, SHA-256, transformation, parent config hash, and modelling authority in lineage/provenance.
7. Top-level workspace tab switching is navigation only. It must not trigger calculations, change authority, mutate scenario state, or terminate the preview process.

## Mission flow

`Mission objective -> GNSS system/date -> baseline data intake -> explicit modelling authority -> runnable baseline -> scenario variant/experiment -> run -> result`

Assisted mode may fill date/system and guide the next action. Auto mode may execute only after all engineering-significant choices are already explicit and unambiguous.

## External GNSS baseline flow

`cache -> reviewed primary source -> independent reviewed fallbacks -> offline import`

A source is eligible for automatic constellation fallback only when its data semantics are equivalent to the requested broadcast-navigation baseline. Station observation data or station-only NAV is not silently substituted for global BRDC-equivalent data.

## IAC GLONASS full-constellation flow

`IAC almanac -> normalized GLONASS data authority -> explicit supplementary time/health authority -> explicit modelling authority ScenarioConfig -> Orekit conversion -> new immutable ScenarioConfig -> refresh/select/load`

The active scenario is not an implicit modelling authority. A successful almanac download/parse is not reported as scenario success.

## Scenario editing flow

`selected scenario -> explicit edit/variant operation -> validation -> new scenario identity/YAML -> refresh/select/load`

Edits that change force-model fingerprint must not reuse mean elements derived for a different force model unless they are re-derived from an authoritative osculating/TLE/GNSS source.

## Experiment flow

`selected runnable baseline -> explicit model/control/robustness changes -> derived experiment configuration -> run -> progress -> completed result -> optional promotion`

Opening the Experiments tab is side-effect free. Missing optional cards or controls are tolerated. Browser exceptions and unhandled promise rejections are surfaced in the global operator runtime status rather than disappearing as a silent UI failure.

## Results flow

`run request -> visible progress -> terminal state -> artifacts/metrics -> comparison/promotion`

A completed calculation and a promoted scenario are distinct states. Promotion must remain explicit and reproducible.
