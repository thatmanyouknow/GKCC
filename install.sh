#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash install.sh"
  exit 1
fi

CALLING_USER="${SUDO_USER:-admin}"
CALLING_HOME="$(getent passwd "${CALLING_USER}" | cut -d: -f6)"
if [[ -z "${CALLING_HOME}" ]]; then
  echo "Could not determine home folder for ${CALLING_USER}"
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_REPO="${CALLING_HOME}/GKCC"
LOCAL_DIR="${CALLING_HOME}/printer_data/config/gkcc"
MOONRAKER_CONF="${CALLING_HOME}/printer_data/config/moonraker.conf"
UPDATER_CONF="${CALLING_HOME}/printer_data/config/gkcc-update-manager.conf"
ALLOWED_SERVICES="${CALLING_HOME}/printer_data/moonraker.asvc"
SERVICE_NAME="gkcc.service"
OLD_SERVICE="voron-calibration-center.service"
OLD_DIR="${CALLING_HOME}/voron_calibration_center"

if [[ "${SOURCE_DIR}" != "${EXPECTED_REPO}" ]]; then
  echo "WARNING: This checkout is at ${SOURCE_DIR}."
  echo "Moonraker's included updater expects ${EXPECTED_REPO}."
  echo "The service will use the current path, but move/clone it to ~/GKCC before enabling updates."
fi

systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
systemctl stop "${OLD_SERVICE}" 2>/dev/null || true

mkdir -p "${LOCAL_DIR}/data" "${LOCAL_DIR}/backups" "${LOCAL_DIR}/app-backups"

# Remove obsolete untracked artifacts without making a Git checkout dirty. A
# tracked artifact must be deleted in GitHub so the updater can remove it cleanly.
if [[ -e "${SOURCE_DIR}/download" ]]; then
  if git -C "${SOURCE_DIR}" ls-files --error-unmatch download >/dev/null 2>&1; then
    echo "NOTICE: tracked obsolete file 'download' remains; delete it from the GitHub repository in the same v0.5.1 commit."
  else
    rm -rf "${SOURCE_DIR}/download"
    echo "Removed obsolete untracked artifact: download"
  fi
fi

# Preserve the currently installed application files before service/install changes.
if [[ -f "${SOURCE_DIR}/VERSION" ]]; then
  APP_BACKUP="${LOCAL_DIR}/app-backups/gkcc-app-$(date +%Y%m%d-%H%M%S).tar.gz"
  tar --exclude=.git --exclude=__pycache__ -czf "${APP_BACKUP}" -C "${SOURCE_DIR}" .
  chown "${CALLING_USER}:${CALLING_USER}" "${APP_BACKUP}"
  echo "Existing GKCC application backup: ${APP_BACKUP}"
fi

mkdir -p "${LOCAL_DIR}/data" "${LOCAL_DIR}/backups"

# Migrate user settings and records from the ZIP-installed v0.1.0 layout.
if [[ -d "${OLD_DIR}" ]]; then
  if [[ ! -f "${LOCAL_DIR}/config.json" && -f "${OLD_DIR}/config.json" ]]; then
    cp -a "${OLD_DIR}/config.json" "${LOCAL_DIR}/config.json"
  fi
  if [[ -d "${OLD_DIR}/data" ]]; then
    cp -an "${OLD_DIR}/data/." "${LOCAL_DIR}/data/" 2>/dev/null || true
  fi
fi

if [[ ! -f "${LOCAL_DIR}/config.json" ]]; then
  cp -a "${SOURCE_DIR}/config.default.json" "${LOCAL_DIR}/config.json"
fi

chown -R "${CALLING_USER}:${CALLING_USER}" "${LOCAL_DIR}"
chmod 700 "${LOCAL_DIR}"
chmod 600 "${LOCAL_DIR}/config.json"

cat > "/etc/systemd/system/${SERVICE_NAME}" <<UNIT
[Unit]
Description=GKCC Calibration Center
After=network-online.target moonraker.service
Wants=network-online.target

[Service]
Type=simple
User=${CALLING_USER}
Group=${CALLING_USER}
WorkingDirectory=${SOURCE_DIR}
Environment=GKCC_DATA_DIR=${LOCAL_DIR}
ExecStart=/usr/bin/python3 ${SOURCE_DIR}/calibration_center.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

# Retire the older service name after creating the replacement.
systemctl disable "${OLD_SERVICE}" 2>/dev/null || true
rm -f "/etc/systemd/system/${OLD_SERVICE}"

mkdir -p "$(dirname "${ALLOWED_SERVICES}")"
touch "${ALLOWED_SERVICES}"
if ! grep -Fxq "gkcc" "${ALLOWED_SERVICES}"; then
  printf '%s\n' "gkcc" >> "${ALLOWED_SERVICES}"
fi
chown "${CALLING_USER}:${CALLING_USER}" "${ALLOWED_SERVICES}" 2>/dev/null || true

cat > "${UPDATER_CONF}" <<UPDATER
[update_manager gkcc]
type: git_repo
channel: dev
path: ${EXPECTED_REPO}
origin: https://github.com/thatmanyouknow/GKCC.git
primary_branch: main
managed_services: gkcc
info_tags:
    desc=GKCC Calibration Center
UPDATER
chown "${CALLING_USER}:${CALLING_USER}" "${UPDATER_CONF}"

if [[ -f "${MOONRAKER_CONF}" ]]; then
  if ! grep -Eq '^\[include[[:space:]]+gkcc-update-manager\.conf\][[:space:]]*$' "${MOONRAKER_CONF}"; then
    printf '\n[include gkcc-update-manager.conf]\n' >> "${MOONRAKER_CONF}"
  fi
  chown "${CALLING_USER}:${CALLING_USER}" "${MOONRAKER_CONF}" 2>/dev/null || true
else
  echo "WARNING: ${MOONRAKER_CONF} was not found."
  echo "Add this line to moonraker.conf manually: [include gkcc-update-manager.conf]"
fi

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
systemctl restart moonraker.service 2>/dev/null || true
sleep 2

if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
  echo "GKCC failed to start. Recent log:"
  journalctl -u "${SERVICE_NAME}" -n 80 --no-pager
  exit 1
fi

echo
echo "GKCC Calibration Center v0.5.1 installed."
echo "Open: http://mainsailos.local:7128"
echo "Local data: ${LOCAL_DIR}"
echo "Service: sudo systemctl status gkcc"
echo "Log: sudo journalctl -u gkcc -n 80 --no-pager"
if [[ "${SOURCE_DIR}" != "${EXPECTED_REPO}" ]]; then
  echo
  echo "Updater warning: clone the repository at ${EXPECTED_REPO}, then run its install.sh again."
fi
