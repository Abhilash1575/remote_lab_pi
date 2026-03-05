#!/bin/bash
# DFRobot UPS - Raspberry Pi 5
# Fully automated for remote lab usage

set -e

echo "========================================"
echo "🔋 DFRobot UPS All-in-One Setup"
echo "========================================"

### 1️⃣ EEPROM CONFIG ###
echo "⚙️ Configuring EEPROM..."

TMP=$(mktemp)
sudo rpi-eeprom-config > "$TMP"

sed -i 's/^POWER_OFF_ON_HALT=.*/POWER_OFF_ON_HALT=1/' "$TMP" || true
grep -q POWER_OFF_ON_HALT "$TMP" || echo "POWER_OFF_ON_HALT=1" >> "$TMP"

grep -q PSU_MAX_CURRENT "$TMP" || echo "PSU_MAX_CURRENT=5000" >> "$TMP"

sudo rpi-eeprom-config --apply "$TMP"
rm -f "$TMP"

echo "✅ EEPROM done"

### 2️⃣ ENABLE I2C ###
echo "🔌 Enabling I2C..."

sudo raspi-config nonint do_i2c 0

for m in i2c-dev i2c-bcm2835; do
    lsmod | grep -q $m || sudo modprobe $m
done

echo "✅ I2C enabled"

### 3️⃣ DEPENDENCIES ###
echo "📦 Installing dependencies..."

sudo apt update
sudo apt install -y \
    i2c-tools \
    python3-smbus \
    python3-pip \
    python3-rpi-lgpio \
    python3-gpiozero

pip3 install --break-system-packages smbus2

echo "✅ Packages installed"

### 4️⃣ I2C SCAN ###
echo "🔍 Scanning I2C bus..."
i2cdetect -y 1 || true

### 5️⃣ UPS MONITOR ###
echo "🧠 Installing UPS monitor..."

# Get project and home directories
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR=$(dirname "$PROJECT_DIR")
REAL_USER=$(basename "$HOME_DIR")

UPS_PY="$PROJECT_DIR/dfrobot_ups.py"

# Ensure the Python file has executable permissions
sudo chmod +x "$UPS_PY"

echo "✅ UPS monitor installed"

### 6️⃣ SYSTEMD SERVICE ###
echo "⚙️ Creating service..."

SERVICE_FILE="$PROJECT_DIR/services/dfrobot-ups.service"
SYSTEMD_SERVICE="/etc/systemd/system/dfrobot-ups.service"

sudo cp "$SERVICE_FILE" "$SYSTEMD_SERVICE"

# Update the service file with correct paths and user
PROJECT_DIR_ESC=$(printf '%s\n' "$PROJECT_DIR" | sed -e 's/[\/&]/\\&/g')
HOME_DIR_ESC=$(printf '%s\n' "$HOME_DIR" | sed -e 's/[\/&]/\\&/g')

sudo sed -i "s|%h|$HOME_DIR_ESC|g" "$SYSTEMD_SERVICE"
sudo sed -i "s|User=%i|User=$REAL_USER|g" "$SYSTEMD_SERVICE"
sudo chmod 644 "$SYSTEMD_SERVICE"

sudo systemctl daemon-reload
sudo systemctl enable dfrobot-ups.service

echo "========================================"
echo "🎉 DFRobot UPS READY"
echo "🔁 Reboot required"
echo "========================================"