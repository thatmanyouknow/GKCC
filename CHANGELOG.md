# Changelog

## v0.4.0 — Guided Blobifier commissioning

- Added a dedicated Blobifier page, interactive geometry diagram, detected-installation panel, and 17-step commissioning manual.
- Added guided fields for Happy Hare purge integration, servo and bucket wiring, tray and brush geometry, purge shape, fan, bucket, and shaker tuning.
- Added PIN-locked tray, brush, shaker, and confirmation-gated supervised test-blob controls.
- Live rebase imports `mmu/addons/blobifier.cfg` and `blobifier_hw.cfg` when present.
- Exact diff, backup, restart validation, and rollback cover the Blobifier files.
- Export preserves and patches the official imported Happy Hare macro files instead of generating incomplete replacement macros.

## v0.3.0 — Transactional live configuration

- Added live Moonraker configuration-root detection and write-permission checks.
- Added **Load/rebase live files** for `printer.cfg` and the three primary Happy Hare user files.
- Raw imported/live file text is retained so comments, macros, formatting, and continuation lines survive targeted setting updates.
- Added stale-baseline conflict detection to prevent overwriting files changed elsewhere.
- Added unified diff preview for every proposed live file change.
- Added transactional ZIP backups containing original files, proposed files, manifest, and builder project.
- Added PIN-locked, typed-confirmation live apply.
- Added optional Klipper restart validation after upload.
- Added automatic rollback and a second restart when Klipper rejects a proposed configuration.
- Added downloadable backup history and manual restore with a pre-restore safety backup.
- Added optional deletion support for sections/options removed in the advanced editor.
- Added an explicit override for applying while guided required values remain incomplete.
- Added `allow_live_config_writes` and `live_restart_timeout_seconds` configuration settings.

## v0.2.0 — Configuration-builder foundation

- Added guided Klipper configuration builder.
- Added interactive printer, toolhead, and ERCF diagrams.
- Added known-good multi-file import and advanced section editor.
- Added Ellis and ERCF setup paths.
- Added review ZIP export.
