# Graphical Klipper Calibration Center

GKCC is a lightweight browser-based calibration application for a Klipper / Moonraker Voron and ERCF / Happy Hare installation.

## Current release

Version: **v0.1.1**

This release includes:

- Live Klipper, print, position, temperature, MMU, and sensor status.
- Guided calibration workflow framework.
- PIN unlock for machine actions.
- ERCF / Happy Hare filament baby-step controls using `MMU_TEST_MOVE`.
- Gear, extruder, gear+extruder, and extruder+gear motor modes.
- Measurement markers and repeated-run records.
- Live Klipper configuration snapshot capture.
- Printable HTML calibration/specification manual.
- JSON export of recorded data.
- GitHub/Moonraker update-manager support.

## Safety limits

- Machine actions are blocked while a print is printing or paused.
- Each filament move is limited to 100 mm.
- Move speed is limited to 100 mm/s.
- Hotend targets are limited to 290 °C.
- This release does not edit `printer.cfg` or Happy Hare files.
- The operator remains responsible for sensor state, nozzle temperature, filament path, cutter position, and mechanical clearance.

## First installation

Clone the repository on the printer and run the installer:

```bash
cd ~
git clone https://github.com/thatmanyouknow/GKCC.git
cd GKCC
sudo bash install.sh
```

Then open:

```text
http://mainsailos.local:7128
```

The installer:

- Runs the service directly from `~/GKCC`.
- Creates `gkcc.service`.
- Stores local configuration and records in `~/printer_data/config/gkcc/`.
- Migrates data from an older `~/voron_calibration_center/` installation when found.
- Adds the GKCC updater configuration to Moonraker.
- Authorizes Moonraker to restart the `gkcc` service.

## Updates

After installation, push a new commit to the `main` branch. In Mainsail, open **Update Manager** and press **Update** beside **gkcc**.

Do not edit tracked files directly in `~/GKCC` on the printer. Local changes make the repository dirty and can stop normal updater operation.

## Local files preserved during updates

```text
~/printer_data/config/gkcc/
├── config.json
└── data/
    ├── profile.json
    ├── records.json
    └── printer_snapshot.json
```

## Service commands

```bash
sudo systemctl status gkcc
sudo systemctl restart gkcc
sudo journalctl -u gkcc -n 80 --no-pager
```

## Uninstall

```bash
cd ~/GKCC
sudo bash uninstall.sh
```

The uninstaller removes the service and updater entry but preserves calibration records in `~/printer_data/config/gkcc/`.
