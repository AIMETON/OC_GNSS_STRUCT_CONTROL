# Engineering Preview operator workspace taxonomy

The Preview UI is organized by operator task, not by implementation module.

## Scenarios
Contains only:
- active scenario overview;
- constellation summary and geometry;
- constellation composition editor.

It must not contain source adapters, force-model controls, robustness tools, run telemetry or raw YAML editing.

## Inputs
Contains only ways to create initial scenario state:
- primary IGS/BKG RINEX NAV intake;
- explicit osculating-state input;
- Walker constellation synthesis;
- XLS/XLSX bulk spacecraft-state input;
- spacecraft/correction catalog input.

The normal IGS workflow is two-stage. Network intake asks only for start date and GNSS system and must not depend on the active ScenarioConfig or Orekit availability. Scenario construction is a separate step that requires an explicitly selected template scenario for force-model, frame/time-scale, integrator and spacecraft authority. All source-specific low-level adapters are Expert tools.

## Design
Contains:
- Earth gravity / force-model selection;
- closed-loop control configuration;
- design workflow;
- optimal operations workspace.

## Robustness
Contains:
- constellation perturbation generator;
- robustness workflow / Monte Carlo validation.

## Run & Results
Contains:
- operations summary;
- calculation progress;
- completed-run promotion;
- mass/propellant state;
- drift physical consistency and result-oriented diagnostics.

## Expert
Contains:
- full ScenarioConfig YAML editor;
- normalized/raw representations;
- source-specific legacy/diagnostic adapters (IAC, NAVCEN, GSC, YUMA/SEM, TLE/OMM);
- legacy embedded gravity editor retained only for compatibility.

No new card may be added to the root Preview DOM without an explicit destination in this taxonomy.
