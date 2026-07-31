# Graphical Klipper Calibration Center (GKCC)

GKCC is a lightweight browser-based configuration, calibration, and as-built documentation application for Klipper / Moonraker printers and ERCF / Happy Hare installations.

## Blobifier commissioning (v0.4.0)

GKCC now includes a full first-run workflow for an installed Blobifier:

- Rebase and preserve the official `mmu/addons/blobifier.cfg` and `blobifier_hw.cfg` files shipped with the installed Happy Hare version.
- Build and edit the servo, bucket switch, tray, brush, purge, blob, and shaker variables without replacing the official macro body.
- Show an interactive rear-left geometry diagram and a 17-step commissioning checklist.
- Test tray in/out, brush motion, shaker motion, and a confirmation-gated heated test blob.
- Preview exact changes, back up every affected file, apply live, restart and validate, and roll back on rejection.

Blobifier actions are supervised and PIN locked. The first heated test requires a safety confirmation and typing `BLOB`.

## Current release

Version: **v0.4.0**

### Transactional live configuration updates

GKCC can now update live Klipper and Happy Hare configuration files through Moonraker.

The live workflow is deliberately transactional:

1. Load or rebase the editable project from the current live files.
2. Preserve the raw source text so comments, macros, formatting, and unparsed continuation lines remain intact.
3. Preview a unified diff for every file that would change.
4. Refuse stale-baseline writes if a file was edited elsewhere after it was loaded.
5. Refuse writes while a print is printing or paused.
6. Create a ZIP backup of every original file before uploading anything.
7. Upload changed `.cfg` files through Moonraker's writable `config` root.
8. Optionally restart Klipper and wait for it to return `ready`.
9. Automatically restore the backup and restart again if Klipper rejects the proposed configuration.
10. Keep downloadable backups and provide a guided restore action. A new safety backup is created before a manual restore.

The operator must unlock machine controls with the GKCC PIN and type `APPLY` before a live update. Backup restoration requires typing `RESTORE`.

### Configuration builder

- Imports one or more known-good Klipper or Happy Hare `.cfg` files.
- Can rebase the current project from live `printer.cfg` and Happy Hare files.
- Parses every normal section and scalar option while preserving the original raw file text.
- Provides interactive printer, toolhead-filament-path, and ERCF diagrams.
- Walks through core `printer.cfg` hardware variables in a controlled order.
- Includes an advanced section editor for board-specific drivers, macros, extra Z steppers, CAN devices, LEDs, and future Klipper options.
- Tracks an Ellis-style tuning path in the recommended order.
- Tracks an ERCF / Happy Hare setup and calibration path.
- Checks toolhead geometry consistency using physical and configured distances.
- Exports a review ZIP containing:
  - `printer.cfg`
  - `mmu/base/mmu_hardware.cfg`
  - `mmu/base/mmu_parameters.cfg`
  - `mmu/base/mmu_macro_vars.cfg`
  - `GKCC_PROJECT.json`
  - `GKCC_BUILD_NOTES.json`

### Existing calibration features

- Live Klipper, print, position, temperature, MMU, and sensor status.
- PIN unlock for machine actions.
- ERCF / Happy Hare filament movement using `MMU_TEST_MOVE`.
- Gear, extruder, gear+extruder, and extruder+gear motor modes.
- Fixed movement buttons through ±50 mm plus a custom movement field.
- Measurement markers and repeated-run records.
- Live Klipper configuration snapshot capture.
- Printable HTML calibration/specification manual.
- JSON export of recorded data.
- GitHub/Moonraker update-manager support.

## Safety limits

- Machine and live-file actions are blocked while a print is printing or paused.
- Live-file actions require PIN unlock and explicit typed confirmation.
- Moonraker's `config` root must report write permission.
- Only relative `.cfg` paths inside the Moonraker config root are accepted.
- Stale live baselines are blocked instead of overwriting newer external edits.
- Full-file replacement without a raw live baseline requires a separate explicit override; patch mode is preferred.
- Every live transaction creates a ZIP backup before the first upload.
- Upload failure triggers immediate restoration of the transaction backup.
- Optional restart validation automatically restores the backup if Klipper returns `error`, `shutdown`, or does not return ready before the timeout.
- An automatic backup cannot prove that pins, thermistors, currents, heaters, travel limits, or imported values are safe for the connected hardware.
- Each individual manual filament move remains limited by the backend to 100 mm and 100 mm/s.
- Hotend targets remain limited to 290 °C.

## First installation

```bash
cd ~
git clone https://github.com/thatmanyouknow/GKCC.git
cd GKCC
sudo bash install.sh
```

Open:

```text
http://mainsailos.local:7128
```

The installer:

- Runs the service directly from `~/GKCC`.
- Creates `gkcc.service`.
- Stores local configuration, builder projects, records, and backups in `~/printer_data/config/gkcc/`.
- Migrates data from an older `~/voron_calibration_center/` installation when found.
- Adds the GKCC updater configuration to Moonraker.
- Authorizes Moonraker to restart the `gkcc` service.

## Updates

Push the updated tracked files to the `main` branch. In Mainsail, open **Update Manager** and press **Update** beside **gkcc**.

Do not edit tracked files directly in `~/GKCC` on the printer. Local changes make the repository dirty and can stop normal updater operation.

## Local files preserved during updates

```text
~/printer_data/config/gkcc/
├── config.json
├── backups/
│   └── YYYYMMDD-HHMMSS-xxxxxx.zip
└── data/
    ├── profile.json
    ├── records.json
    ├── printer_snapshot.json
    └── config_builder_project.json
```

Each backup ZIP contains `MANIFEST.json`, the original files under `config/`, proposed files when available under `proposed/`, and the GKCC builder project when the backup came from a live apply.

## Configuration switches

Existing installations automatically receive defaults when these keys are absent:

```json
{
  "allow_live_config_writes": true,
  "live_restart_timeout_seconds": 45
}
```

Set `allow_live_config_writes` to `false` in `~/printer_data/config/gkcc/config.json` to disable all live configuration writes while retaining the builder and export features.

## Service commands

```bash
sudo systemctl status gkcc
sudo systemctl restart gkcc
sudo journalctl -u gkcc -n 80 --no-pager
```

## Source guides used by the built-in workflow

- Klipper configuration reference: https://www.klipper3d.org/Config_Reference.html
- Ellis’ Print Tuning Guide: https://ellis3dp.com/Print-Tuning-Guide/
- Happy Hare wiki: https://github.com/moggieuk/Happy-Hare/wiki
- Moonraker File Management API: https://moonraker.readthedocs.io/en/latest/external_api/file_manager/
- Moonraker Printer Administration API: https://moonraker.readthedocs.io/en/latest/external_api/printer/

## Uninstall

```bash
cd ~/GKCC
sudo bash uninstall.sh
```

The uninstaller removes the service and updater entry but preserves local records, builder projects, and configuration backups in `~/printer_data/config/gkcc/`.
