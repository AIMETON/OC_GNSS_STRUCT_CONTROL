# Engineering Preview 0.2.16

This release candidate consolidates the stacked GNSS intake work from PRs #209, #212, #214 and #215 into PR #216 targeting `main`.

Release gates for the final candidate are evaluated on the exact PR #216 head against `main` and include:

- `ci`
- `preview-package-compat`
- `preview-release-package`
- `preview-version-guard`
- `rf-source-diagnostic`

Field fixes included in 0.2.16:

- deterministic cache handling for uncompressed RINEX sources while accepting matching legacy 0.2.15 cache content;
- RINEX target epoch defaults to the selected source day and cross-day target epochs fail before Orekit conversion;
- RF source diagnostics cover the qualified IAC/FCND/WHU runtime paths, with Galileo RF qualification kept explicit.

The older stacked PRs are superseded by #216 once this exact head is GREEN and merged.
