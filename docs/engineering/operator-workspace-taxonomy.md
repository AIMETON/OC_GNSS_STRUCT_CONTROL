# Engineering Preview operator workspace taxonomy

The Preview UI is organized by the engineering mission, not by implementation modules, source formats or backend adapters.

The normal research lifecycle is:

`Mission -> baseline -> scenario variants -> experiments -> comparison/results -> next hypothesis`

## AIMETON execution echelons

All workspaces operate on the same ScenarioConfig, lineage, provenance and run model. The three echelons are execution policies, not separate applications.

### Manual
The operator explicitly chooses every engineering-significant source, template, parameter and action. No hidden substitutions are allowed. Expert tools remain available for raw YAML, source-specific adapters and diagnostics.

### Assisted
This is the recommended default. The application may propose or prefill safe values and the next action, but the operator confirms engineering-significant transitions. Provenance and modelling authority remain visible.

### Automatic
The application may execute only governed, unambiguous steps already permitted by policy. It must preserve full provenance and stop fail-closed on missing authority, incompatible inputs or ambiguity. The operator can always descend from Automatic to Assisted or Manual without changing the underlying mission state.

## Mission
The normal entry point for modelling and research.

Contains:
- mission objective;
- execution echelon;
- baseline system/date;
- one primary path to create a baseline from real constellation data;
- next recommended action.

The normal real-constellation baseline uses the unified IGS/BKG RINEX NAV intake. Network acquisition asks only for date and GNSS system and must not depend on the active ScenarioConfig or Orekit. Scenario construction is separate and uses an explicit modelling authority. In Automatic mode the already active ScenarioConfig may be used as that authority only because the operator selected the automatic policy; incompatibility must stop the chain.

Source-specific low-level adapters do not belong in Mission.

## Scenarios
Contains:
- active scenario overview;
- constellation summary and geometry;
- constellation composition editor;
- fast immutable scenario variants with parent lineage;
- explicit osculating-state creation;
- Walker constellation synthesis;
- XLS/XLSX bulk input and spacecraft catalog tools.

The default correction workflow is `create variant`, not `edit parent`. Safe high-frequency parameters such as duration and output step are exposed directly. Changes that alter physical authority, such as gravity-model changes requiring mean-element re-derivation, remain specialized governed workflows rather than generic YAML edits.

Full raw YAML editing is Expert-only.

## Experiments
Combines the former Design and Robustness workspaces around the research question.

Contains:
- Earth gravity / force-model variants;
- closed-loop control configuration;
- design workflow;
- optimal operations workspace;
- constellation perturbations;
- robustness workflow / Monte Carlo validation.

The long-term interaction model is baseline -> parameter variants/sweep -> governed batch execution -> comparison.

## Results
Contains:
- operations summary;
- calculation progress;
- completed-run promotion;
- mass/propellant state;
- drift physical consistency;
- comparison-oriented engineering results.

Results should lead directly to the next variant or experiment rather than terminate the workflow at a run directory.

## Expert
Contains:
- full ScenarioConfig YAML editor;
- normalized/raw representations;
- source-specific legacy/diagnostic adapters (IAC, NAVCEN, GSC, YUMA/SEM, TLE/OMM);
- legacy embedded gravity editor retained only for compatibility;
- transport/source diagnostics and manual authority tools.

Expert is a controlled descent into implementation detail, not the normal entry point.

## UI invariant

No new card may be added to the root Preview DOM without an explicit mission-role destination in this taxonomy. Backend modules must never define the primary navigation. The primary navigation is fixed around `Mission / Scenarios / Experiments / Results / Expert`.
