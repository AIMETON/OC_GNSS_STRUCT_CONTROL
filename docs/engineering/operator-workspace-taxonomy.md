# Engineering Preview operator workspace taxonomy

The Preview UI is organized by the engineering mission, not by implementation modules, source formats or backend adapters.

The normal research lifecycle is:

`Mission -> baseline -> scenario variants -> experiments -> comparison/results -> next hypothesis`

## AIMETON execution echelons

All workspaces operate on the same ScenarioConfig, lineage, provenance and run model. The three echelons are execution policies, not separate applications.

### Manual
The operator explicitly chooses every engineering-significant source, physical modelling authority, parameter and action. No hidden substitutions are allowed. Manual is also the only echelon allowed to establish a new physical spacecraft/modelling authority when no previously trusted profile exists. Expert tools remain available for raw YAML, source-specific adapters and diagnostics.

### Assisted
This is the recommended default. The application may propose or prefill safe values and the next action, but the operator confirms engineering-significant transitions. Provenance and modelling authority remain visible. Assisted may recommend only an authority that is already traceable to trusted GNSS source lineage for the requested constellation.

### Automatic
The application may execute only governed, unambiguous steps already permitted by policy. It must preserve full provenance and stop fail-closed on missing authority, incompatible inputs or ambiguity. Synthetic smoke/test profiles are never promoted automatically merely because they contain Orekit or DESIGN settings. The operator can always descend from Automatic to Assisted or Manual without changing the underlying mission state.

## Mission
The normal entry point for modelling and research.

Contains:
- mission objective;
- execution echelon;
- baseline system/date;
- one primary path to create a baseline from real constellation data;
- next recommended action.

The normal real-constellation baseline uses the unified IGS/BKG RINEX NAV intake. Network acquisition asks only for date and GNSS system and must not depend on the active ScenarioConfig or Orekit. Scenario construction is separate and uses explicit physical modelling authority.

For Assisted/Automatic baseline creation, the authority resolver is constellation-specific and fail-closed:
1. use the active ScenarioConfig if it is an eligible DESIGN/VALIDATION scenario with Orekit, spacecraft parameters and traceable RINEX GNSS lineage for the requested constellation;
2. otherwise use another eligible trusted scenario for the same constellation;
3. otherwise stop and require Manual establishment/selection of physical authority.

A GLONASS-derived spacecraft authority must never be silently reused for GPS/Galileo/BeiDou. A synthetic smoke scenario must never become an automatic physical authority simply because it is runnable.

Baseline scenario identity includes source system/date plus the modelling authority's force-model mode, gravity degree/order and parent config-hash tag. Therefore the same source epoch can coexist under different physical/model authorities. An identical repeat request is idempotent and may reuse an existing matching baseline only when provenance, source SHA-256, parent config hash and force-model fingerprint agree.

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

The target interaction model is baseline -> parameter variants/sweep -> governed batch execution -> comparison.

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
- legacy `Other YAML inputs` selector;
- source-specific legacy/diagnostic adapters (IAC, NAVCEN, GSC, YUMA/SEM, TLE/OMM);
- legacy embedded gravity editor retained only for compatibility;
- transport/source diagnostics and manual authority tools.

Expert is a controlled descent into implementation detail, not the normal entry point.

## UI invariant

No new card may be added to the root Preview DOM without an explicit mission-role destination in this taxonomy. Backend modules must never define the primary navigation. The primary navigation is fixed around `Mission / Scenarios / Experiments / Results / Expert`.
