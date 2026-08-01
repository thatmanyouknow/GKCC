# Changelog

## v0.5.2 — coordinated repair release

- Repackages every frontend, backend, schema, guide, and checksum from one verified source tree.
- Adds pre-install checksum and JSON-structure validation so a mixed or misnamed upload is rejected before the service is changed.
- Preserves v0.5.1 protected-path detection, changed-files-only apply planning, and precise rollback reporting.
- Removes compiled `__pycache__` artifacts from the release package.
- Stops treating the harmless legacy `download` artifact as an installation-health warning.

## v0.5.1 — protected-path transaction safety

- Detects configuration files and directories that are symlinked outside Moonraker's config root.
- Marks Happy Hare, Mainsail, KAMP, Timelapse, Obico, and other externally managed files read-only before apply.
- Shows the resolved target and exact protection reason beside the proposed diff.
- Uploads only files whose proposed bytes differ from the loaded live baseline.
- Adds **Reload live clean** to discard stale draft cfg edits while preserving workflow progress, notes, and taught positions.
- Blocks the entire transaction before backup when any changed target is updater-managed.
- Restores only files that the current transaction successfully wrote; zero-write failures no longer trigger rollback.
- Reports the exact failed file, full Moonraker HTTP response, files written before failure, and rollback result.
- Adds safe cleanup for untracked obsolete `download` artifacts and clearer instructions for deleting a tracked copy from GitHub.

## v0.5.0

- Consolidated the frontend, backend, builder schema, Ellis guide, ERCF guide, Blobifier guide, workflows, defaults, installer, and checksums into one coordinated release.
- Repaired the mixed-file upload that caused `Cannot read properties of undefined (reading 'flatMap')`.
- Added defensive schema handling so a missing `groups` or `steps` array produces an installation-health error instead of a JavaScript crash.
- Added an Installation health page and startup banner showing component versions, JSON structure checks, and critical-file checksums.
- Blocked live configuration apply and backup restore whenever the coordinated release health check fails.
- Preserved v0.4.4-v0.4.6 Happy Hare section migration and live I/O / motion-test features.
- Removed obsolete packaged artifacts such as `download` and `__pycache__`.

## v0.4.6

- Added a live I/O and motion test bench to the ERCF / Happy Hare page.
- Added one-second indicators for configured endstops, `gcode_button` inputs, filament sensors, probe status, and Happy Hare sensor fields.
- Added loaded-configuration pin matching and per-input digital transition counters.
- Added guarded Happy Hare servo positions and direct-angle testing.
- Added Happy Hare gear, selector, and servo buzz tests, selector homing, gate selection, and motor release.
- Added Klipper `STEPPER_BUZZ` controls restricted to steppers reported by `motion_report`.
- Added an immediate Moonraker emergency-stop control.
- Added backend validation and typed confirmations for higher-risk movement commands.

## v0.4.5

- Fixed the Happy Hare parameter page to read and edit the real `[mmu]` section used by `mmu/base/mmu_parameters.cfg`.
- Added automatic migration of draft values created by older GKCC releases under the incorrect synthetic `[mmu_parameters]` section.
- Corrected configuration-window targeting so existing Happy Hare lines highlight in place instead of proposing a duplicate `[mmu_parameters]` section at the bottom of the file.
- Corrected review/export routing so the `[mmu]` section remains in `mmu/base/mmu_parameters.cfg` rather than being treated as a printer or MMU-hardware section.

## v0.4.4

- Fixed guided-page population for electronics fans configured as either `[heater_fan controller_fan]` or `[controller_fan controller_fan]`.
- Added section-alias resolution so imported/live values are edited in the section that actually exists instead of creating a duplicate section.
- Added guided fields for electronics-fan kick start, trigger heater, shutoff temperature, and fan speed.
- Renamed `Bed control` to `Bed heater control algorithm` to distinguish it from the bed-triggered electronics fan.

## v0.4.3

- Added a reusable **Teach machine positions** workflow for Blobifier, Klicky, and custom hardware locations.
- Added live G-code and machine-coordinate display, supervised X/Y/Z jogging, selectable 10/1/0.1/0.01 mm steps, independent XY and Z speeds, homing checks, print-state blocking, and Klipper travel-limit checks.
- Added one-click capture of the current G-code position and a location notebook stored with the GKCC project.
- Added Blobifier presets that convert captured coordinates into `brush_start`, `brush_width`, `brush_y_offset`, `purge_x`, `y_offset`, `brush_top`, and `tray_top` draft values.
- Added Klicky and custom XYZ targets with exact section and option names.
- Writing a captured location updates only the draft, opens the configuration viewer, and highlights the affected line before the normal backup/diff/live-apply workflow.

## v0.4.2

- Added an always-visible configuration file window beside the guided variable forms.
- Focusing a guided field now opens the actual imported/live `.cfg` file and highlights the exact section and option being edited.
- Added line numbers, current-versus-draft status, added-line highlighting, file selection, automatic scrolling, and a large modal editor view.
- Blobifier servo and bucket pin fields now open `blobifier_hw.cfg` directly and highlight the corresponding `pin:` line while typing.
- The viewer preserves raw comments and macro bodies; live writes still require preview, backup, approval, and validation.

## v0.4.1

- Added a visible **Hardware pins** panel directly to Blobifier Commissioning.
- Servo and bucket-switch pins populate automatically from imported or live `blobifier_hw.cfg`.
- Added controller/physical-connector notes for the as-built manual.
- Added generated `blobifier_hw.cfg` preview and exact duplicate-pin checks.
- Added direct save and live-diff controls beside the pin fields.

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
