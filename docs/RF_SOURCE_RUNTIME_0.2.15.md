# Engineering Preview 0.2.15 — RF source runtime recovery

Field evidence from Preview 0.2.14 shows that the packaged code contains the configured Russian sources, but two runtime adapters do not reproduce the previously qualified source paths:

- IAC FTP reaches the configured archive but discovery reports no RINEX-like files.
- FCND API responds but the current catalogue mapping reports no RINEX-like GNSS files for the requested day.
- BKG is reset by the field network path.
- WHU rejects the configured daily subdirectory.

0.2.15 is reserved for correcting the actual source paths/schema handling and adding executed network qualification evidence. A source is not declared operational for a product until the same runtime adapter can fetch and validate that product.
