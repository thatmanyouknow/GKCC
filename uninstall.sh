#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash uninstall.sh"
  exit 1
fi

CALLING_USER="${SUDO_USER:-admin}"
CALLING_HOME="$(getent passwd "${CALLING_USER}" | cut -d: -f6)"
MOONRAKER_CONF="${CALLING_HOME}/printer_data/config/moonraker.conf"
UPDATER_CONF="${CALLING_HOME}/printer_data/config/gkcc-update-manager.conf"
ALLOWED_SERVICES="${CALLING_HOME}/printer_data/moonraker.asvc"
LOCAL_DIR="${CALLING_HOME}/printer_data/config/gkcc"

systemctl disable --now gkcc.service 2>/dev/null || true
rm -f /etc/systemd/system/gkcc.service
systemctl daemon-reload

rm -f "${UPDATER_CONF}"
if [[ -f "${MOONRAKER_CONF}" ]]; then
  sed -i '/^\[include[[:space:]]\+gkcc-update-manager\.conf\][[:space:]]*$/d' "${MOONRAKER_CONF}"
fi
if [[ -f "${ALLOWED_SERVICES}" ]]; then
  sed -i '/^gkcc$/d' "${ALLOWED_SERVICES}"
fi
systemctl restart moonraker.service 2>/dev/null || true

echo "GKCC service and updater entry removed."
echo "Calibration records were preserved in: ${LOCAL_DIR}"
