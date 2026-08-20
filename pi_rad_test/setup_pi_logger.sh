#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="chronocat-telemetry"
INTERFACE="eth0"
STATIC_IP="192.168.1.10/24"
PORT="5005"
CONFIGURE_ETH=0
START_SERVICE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
  TARGET_USER="${SUDO_USER}"
else
  TARGET_USER="$(id -un)"
fi

if command -v getent >/dev/null 2>&1; then
  TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
else
  TARGET_HOME="$(eval echo "~${TARGET_USER}")"
fi
LOG_DIR="${TARGET_HOME}/chronocat_logs"
VENV_DIR="${REPO_DIR}/.venv-pi"

usage() {
  cat <<EOF
Usage: sudo ./pi_rad_test/setup_pi_logger.sh [options]

Installs the Chronocat UDP telemetry recorder as a systemd boot service.
The Python package is installed from the current repo checkout:
  ${REPO_DIR}

Options:
  --configure-eth0        Configure a static IPv4 address on eth0.
  --interface IFACE       Interface for static IP config, default: ${INTERFACE}
  --ip CIDR               Static Pi IP/CIDR, default: ${STATIC_IP}
  --port PORT             UDP listen port, default: ${PORT}
  --log-dir DIR           CSV output directory, default: ${LOG_DIR}
  --venv-dir DIR          Python venv directory, default: ${VENV_DIR}
  --service-name NAME     systemd service name, default: ${SERVICE_NAME}
  --no-start              Install and enable service, but do not start it now.
  -h, --help              Show this help.

Examples:
  sudo ./pi_rad_test/setup_pi_logger.sh
  sudo ./pi_rad_test/setup_pi_logger.sh --configure-eth0
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --configure-eth0)
      CONFIGURE_ETH=1
      shift
      ;;
    --interface)
      INTERFACE="$2"
      shift 2
      ;;
    --ip)
      STATIC_IP="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --log-dir)
      LOG_DIR="$2"
      shift 2
      ;;
    --venv-dir)
      VENV_DIR="$2"
      shift 2
      ;;
    --service-name)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --no-start)
      START_SERVICE=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "error: this setup script is intended for Linux/Raspberry Pi OS" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "error: run with sudo so the script can install the systemd service" >&2
  exit 1
fi

if [[ ! -f "${REPO_DIR}/pyproject.toml" ]]; then
  echo "error: expected pyproject.toml in ${REPO_DIR}" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is not installed" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"
chown "${TARGET_USER}:${TARGET_USER}" "${LOG_DIR}"

echo "Creating Python venv: ${VENV_DIR}"
sudo -u "${TARGET_USER}" python3 -m venv "${VENV_DIR}"
sudo -u "${TARGET_USER}" "${VENV_DIR}/bin/python" -m pip install --upgrade pip
sudo -u "${TARGET_USER}" "${VENV_DIR}/bin/python" -m pip install -e "${REPO_DIR}"

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
echo "Installing systemd service: ${UNIT_PATH}"
cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=Chronocat UDP telemetry CSV logger
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${TARGET_USER}
WorkingDirectory=${LOG_DIR}
ExecStart=${VENV_DIR}/bin/chronocat_telemetry --bind 0.0.0.0 --port ${PORT} --quiet --geiger-only
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

if [[ "${CONFIGURE_ETH}" -eq 1 ]]; then
  echo "Configuring ${INTERFACE} static IPv4: ${STATIC_IP}"
  if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager; then
    CONNECTION_NAME="chronocat-${INTERFACE}"
    if ! nmcli -t -f NAME connection show | grep -Fxq "${CONNECTION_NAME}"; then
      nmcli connection add type ethernet ifname "${INTERFACE}" con-name "${CONNECTION_NAME}"
    fi
    nmcli connection modify "${CONNECTION_NAME}" ipv4.method manual ipv4.addresses "${STATIC_IP}" ipv6.method disabled connection.autoconnect yes
    nmcli connection up "${CONNECTION_NAME}" || true
  elif [[ -f /etc/dhcpcd.conf ]]; then
    if ! grep -q "# chronocat ${INTERFACE}" /etc/dhcpcd.conf; then
      cat >> /etc/dhcpcd.conf <<EOF

# chronocat ${INTERFACE}
interface ${INTERFACE}
static ip_address=${STATIC_IP}
EOF
    fi
  else
    echo "warning: could not auto-configure static IP; no active NetworkManager or /etc/dhcpcd.conf found" >&2
  fi
fi

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

if [[ "${START_SERVICE}" -eq 1 ]]; then
  systemctl restart "${SERVICE_NAME}.service"
fi

echo
echo "Chronocat Pi logger setup complete."
echo "Repo:       ${REPO_DIR}"
echo "Logs:       ${LOG_DIR}"
echo "Service:    ${SERVICE_NAME}.service"
echo "UDP port:   ${PORT}"
echo
echo "Check status:"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo
echo "Follow logs:"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
echo
echo "CSV files:"
echo "  ls -lh ${LOG_DIR}"
