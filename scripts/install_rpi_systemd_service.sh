#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="nexus-hil-web.service"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_SCRIPT="$REPO_DIR/tools/rpi_digital_hil/nexus_sync_rpi_digital_hil_web.py"
CONFIG_PATH="$REPO_DIR/rpi_digital_hil_config.json"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
PORT="${NEXUS_HIL_WEB_PORT:-8080}"

if [[ ! -f "$WEB_SCRIPT" ]]; then
  echo "ERROR: Web HIL script not found: $WEB_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "ERROR: Config file not found: $CONFIG_PATH" >&2
  exit 1
fi

sudo tee "$SERVICE_PATH" >/dev/null <<EOF
[Unit]
Description=Nexus HIL Web Service
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=$REPO_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $WEB_SCRIPT --config $CONFIG_PATH --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

if pgrep -f "nexus_sync_rpi_digital_hil_web.py.*--port $PORT" >/dev/null 2>&1; then
  sudo pkill -f "nexus_sync_rpi_digital_hil_web.py.*--port $PORT" || true
  sleep 1
fi

sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "Systemd service installed and started:"
echo "  $SERVICE_NAME"
echo
echo "Open from your computer:"
echo "  http://$(hostname -I | awk '{print $1}'):$PORT"
echo
echo "Status:"
systemctl --no-pager --full status "$SERVICE_NAME" || true
