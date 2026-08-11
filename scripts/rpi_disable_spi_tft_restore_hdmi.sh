#!/usr/bin/env bash
set -euo pipefail

BOOT_CONFIG="/boot/firmware/config.txt"
RC_LOCAL="/etc/rc.local"
XORG_CONF_DIR="/etc/X11/xorg.conf.d"
LXDE_AUTOSTART="/home/pi/.config/lxsession/LXDE/autostart"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [[ ! -f "$BOOT_CONFIG" ]]; then
  BOOT_CONFIG="/boot/config.txt"
fi

if [[ ! -f "$BOOT_CONFIG" ]]; then
  echo "ERROR: Raspberry boot config not found" >&2
  exit 1
fi

sudo cp "$BOOT_CONFIG" "$BOOT_CONFIG.nexus-hdmi-backup-$STAMP"
if [[ -f "$RC_LOCAL" ]]; then
  sudo cp "$RC_LOCAL" "$RC_LOCAL.nexus-hdmi-backup-$STAMP"
fi

sudo python3 - "$BOOT_CONFIG" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text().splitlines()

disable_prefixes = (
    "dtoverlay=tft35a",
    "dtoverlay=spi1-",
    "dtparam=spi=on",
    "dtparam=i2c_arm=on",
    "enable_uart=1",
    "hdmi_group=",
    "hdmi_mode=",
    "hdmi_cvt",
    "hdmi_drive=",
)

out = []
has_kms = False
for line in lines:
    stripped = line.strip()
    if stripped == "dtoverlay=vc4-kms-v3d":
        has_kms = True
        out.append(line)
        continue
    if stripped == "#dtoverlay=vc4-kms-v3d":
        has_kms = True
        out.append("dtoverlay=vc4-kms-v3d")
        continue
    if any(stripped.startswith(prefix) for prefix in disable_prefixes):
        out.append(f"# disabled by Nexus HIL HDMI restore: {line}")
        continue
    out.append(line)

if not has_kms:
    out.append("dtoverlay=vc4-kms-v3d")

out.append("")
out.append("# Nexus HIL: SPI TFT disabled; HDMI restored; GPIO header freed for HIL.")
path.write_text("\n".join(out) + "\n")
PY

if [[ -f "$RC_LOCAL" ]]; then
  sudo sed -i 's/^[[:space:]]*fbcp[[:space:]]*&/# disabled by Nexus HIL HDMI restore: fbcp \&/' "$RC_LOCAL"
fi

for conf in 10-fbdev.conf 99-calibration.conf; do
  if [[ -f "$XORG_CONF_DIR/$conf" ]]; then
    sudo cp "$XORG_CONF_DIR/$conf" "$XORG_CONF_DIR/$conf.nexus-hdmi-backup-$STAMP"
    sudo mv "$XORG_CONF_DIR/$conf" "$XORG_CONF_DIR/$conf.disabled"
  fi
done

mkdir -p "$(dirname "$LXDE_AUTOSTART")"
touch "$LXDE_AUTOSTART"
if ! grep -q "xrandr --output HDMI-1 --mode 1920x1080" "$LXDE_AUTOSTART"; then
  echo "@xrandr --output HDMI-1 --mode 1920x1080 --rate 60" >> "$LXDE_AUTOSTART"
fi

sudo pkill -f '^fbcp$' >/dev/null 2>&1 || true

echo "SPI TFT disabled and HDMI restored in:"
echo "  $BOOT_CONFIG"
echo "Backups include suffix:"
echo "  nexus-hdmi-backup-$STAMP"
echo
echo "Reboot is required to release the GPIO overlays."
