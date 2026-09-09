# RINEX NAV as the primary external GNSS navigation format

OC GNSS STRUCT CONTROL treats **RINEX Navigation** as the canonical external interchange format for GNSS broadcast navigation data.

## Source priority

1. IGS / BKG GNSS Data Center RINEX NAV for stable archival and repeatable network intake.
2. Constellation-specific official sources (IAC GLONASS, Galileo GSC, NAVCEN, etc.) as independent authority/cross-check channels.
3. YUMA, SEM, operator XML/HTML/table formats remain compatibility adapters and are normalized before entering the scenario pipeline.

## GLONASS chain

The initial RINEX-first implementation is:

BKG/IGS HTTPS BRDC
→ daily GLONASS `*_RN.rnx.gz`
→ immutable local cache
→ SHA-256 of downloaded gzip and decompressed RINEX
→ Orekit `RinexNavigationParser`
→ nearest GLONASS broadcast ephemeris selected for each satellite subject to an explicit operator `max_ephemeris_age_s`
→ Orekit `GLONASSNumericalPropagator` to the explicit target epoch
→ force-model-consistent DSST mean conversion
→ project `MeanOrbit`
→ immutable derived `ScenarioConfig`
→ standard application runner.

The source scenario contributes only explicit simulation authority that is not contained in RINEX: force model, integrator, spacecraft physical model, frame/time-scale contract and constraints. It is never overwritten.

## Cache and provenance

Network input is persisted below `data/cache/rinex/igs-bkg/brdc/YYYY/DDD/`.

For every source the cache records:
- source HTTPS URL;
- exact source date and filename;
- SHA-256 of the downloaded compressed artifact;
- SHA-256 of the decompressed RINEX file;
- paths of both files;
- cache-manifest identity.

A filename collision with a different digest fails closed.

Derived scenarios record `rinex_nav_import` lineage with the source URL, source SHA-256, parent config hash and the explicit propagation/ephemeris-age settings.

## Numerical-authority invariant

Python does not reimplement GLONASS broadcast dynamics. RINEX parsing and broadcast propagation are delegated to Orekit 13.1.7. The resulting osculating state is converted to the project's force-model-consistent DSST mean-element authority before it becomes runnable scenario state.

No fallback may silently substitute IAC/YUMA/XML data when the requested RINEX source is unavailable.
