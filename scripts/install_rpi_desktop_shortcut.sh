#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Nexus HIL Web"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_SCRIPT="$REPO_DIR/tools/rpi_digital_hil/nexus_sync_rpi_digital_hil_web.py"
CONFIG_PATH="$REPO_DIR/rpi_digital_hil_config.json"
DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
LOCAL_APPS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/Nexus HIL Web.desktop"
APP_FILE="$LOCAL_APPS_DIR/nexus-hil-web.desktop"
LAUNCHER_FILE="$REPO_DIR/scripts/run_rpi_digital_hil_web.sh"
ICON_FILE="$REPO_DIR/assets/branding/sieza_logo_light.png"
LOG_DIR="$REPO_DIR/logs"
PORT="${NEXUS_HIL_WEB_PORT:-8080}"

if [[ ! -f "$WEB_SCRIPT" ]]; then
  echo "ERROR: Web HIL script not found: $WEB_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "ERROR: Config file not found: $CONFIG_PATH" >&2
  exit 1
fi

if [[ ! -f "$ICON_FILE" ]]; then
  echo "ERROR: Branding icon not found: $ICON_FILE" >&2
  exit 1
fi

mkdir -p "$DESKTOP_DIR" "$LOCAL_APPS_DIR" "$LOG_DIR"
cp "$ICON_FILE" "$DESKTOP_DIR/SIEZA Logo.png"

cat > "$LAUNCHER_FILE" <<EOF
#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="$REPO_DIR"
WEB_SCRIPT="$WEB_SCRIPT"
CONFIG_PATH="$CONFIG_PATH"
LOG_DIR="$LOG_DIR"
PORT="$PORT"
cd "\$REPO_DIR"
mkdir -p "\$LOG_DIR"
if pgrep -f "nexus_sync_rpi_digital_hil_web.py.*--port \$PORT" >/dev/null 2>&1; then
  echo "Nexus HIL Web already running on port \$PORT"
  echo "Open: http://\$(hostname -I | awk '{print \$1}'):\$PORT"
  exit 0
fi
nohup python3 "\$WEB_SCRIPT" --config "\$CONFIG_PATH" --host 0.0.0.0 --port "\$PORT" >> "\$LOG_DIR/web_service.log" 2>&1 &
echo \$! > "\$LOG_DIR/web_service.pid"
sleep 1
echo "Nexus HIL Web started"
echo "Open: http://\$(hostname -I | awk '{print \$1}'):\$PORT"
echo "Log: \$LOG_DIR/web_service.log"
EOF
chmod +x "$LAUNCHER_FILE"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Start Nexus HIL web service
Path=$REPO_DIR
Exec=$LAUNCHER_FILE
Icon=$ICON_FILE
Terminal=true
Categories=Development;Engineering;
StartupNotify=true
EOF

cp "$DESKTOP_FILE" "$APP_FILE"
chmod +x "$DESKTOP_FILE" "$APP_FILE"
rm -f "$DESKTOP_DIR/Nexus Sync Digital HIL.desktop" "$LOCAL_APPS_DIR/nexus-sync-digital-hil.desktop"

if command -v gio >/dev/null 2>&1; then
  gio set "$DESKTOP_FILE" metadata::trusted true >/dev/null 2>&1 || true
fi

echo "Desktop shortcut created:"
echo "  $DESKTOP_FILE"
echo "Branding logo copied:"
echo "  $DESKTOP_DIR/SIEZA Logo.png"
echo
echo "Start service from the desktop shortcut or with:"
echo "  $LAUNCHER_FILE"
echo
echo "Open from your computer:"
echo "  http://$(hostname -I | awk '{print $1}'):$PORT"
