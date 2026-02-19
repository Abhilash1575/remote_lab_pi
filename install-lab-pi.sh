#!/bin/bash

# ============================================================================
# Virtual Lab - Lab Pi Installation Script
# ============================================================================
# This script sets up a Raspberry Pi as a Lab Pi (Experiment Node)
# It clones the repository and configures the Lab Pi to connect to Master Pi
#
# Usage:
#   wget -O install-lab-pi.sh <install-script-url>
#   chmod +x install-lab-pi.sh
#   ./install-lab-pi.sh
#
# Required Environment Variables:
#   LAB_PI_ID       - Unique ID for this Lab Pi (e.g., lab-001)
#   LAB_PI_NAME     - Display name (e.g., "LED Blinky Lab")
#   LAB_PI_MAC      - MAC address of the Pi
#   EXPERIMENT_ID   - Which experiment this Pi handles
#   MASTER_URL      - URL of Master Pi (e.g., http://192.168.1.100:5000)
#   MASTER_API_KEY  - Optional API key for authentication
# ============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Virtual Lab - Lab Pi Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${YELLOW}⚠️  Running as root is not recommended. Run as regular user with sudo.${NC}"
fi

# Get MAC address if not provided
get_mac_address() {
    # Try to get MAC address from eth0 or wlan0
    for iface in eth0 wlan0 end0; do
        if ip link show "$iface" &>/dev/null; then
            mac=$(ip link show "$iface" | grep link | awk '{print $2}')
            if [ -n "$mac" ]; then
                echo "$mac"
                return 0
            fi
        fi
    done
    echo ""
    return 1
}

# Detect hostname
get_hostname() {
    hostname -s 2>/dev/null || echo "raspberrypi"
}

# ============================================================================
# Step 1: Get Configuration
# ============================================================================
echo -e "${YELLOW}Step 1: Configuration${NC}"
echo ""

# Get hostname for default values
DETECTED_HOSTNAME=$(get_hostname)

# Lab Pi ID - use hostname as default
if [ -z "$LAB_PI_ID" ]; then
    read -p "Enter Lab Pi ID (default: lab-$DETECTED_HOSTNAME): " LAB_PI_ID
    LAB_PI_ID=${LAB_PI_ID:-lab-$DETECTED_HOSTNAME}
fi

# Lab Pi Name - use hostname as default
if [ -z "$LAB_PI_NAME" ]; then
    read -p "Enter Lab Pi Name (default: Lab Pi $DETECTED_HOSTNAME): " LAB_PI_NAME
    LAB_PI_NAME=${LAB_PI_NAME:-Lab Pi $DETECTED_HOSTNAME}
fi

# MAC Address
if [ -z "$LAB_PI_MAC" ]; then
    echo "MAC address not provided. Attempting to detect..."
    DETECTED_MAC=$(get_mac_address)
    if [ -n "$DETECTED_MAC" ]; then
        echo -e "Detected MAC: ${GREEN}$DETECTED_MAC${NC}"
        read -p "Use this MAC address? (Y/n): " USE_DETECTED
        if [ "$USE_DETECTED" != "n" ] && [ "$USE_DETECTED" != "N" ]; then
            LAB_PI_MAC="$DETECTED_MAC"
        fi
    fi
    if [ -z "$LAB_PI_MAC" ]; then
        read -p "Enter MAC address (optional, press Enter to skip): " LAB_PI_MAC
    fi
fi

# Experiment ID - default to 1
if [ -z "$EXPERIMENT_ID" ]; then
    read -p "Enter Experiment ID (default: 1): " EXPERIMENT_ID
    EXPERIMENT_ID=${EXPERIMENT_ID:-1}
fi

# Master URL - use common default
if [ -z "$MASTER_URL" ]; then
    read -p "Enter Master Pi URL (default: http://192.168.1.5:5000): " MASTER_URL
    MASTER_URL=${MASTER_URL:-http://192.168.1.5:5000}
fi

# Master API Key (optional)
if [ -z "$MASTER_API_KEY" ]; then
    read -p "Enter Master API Key (optional, press Enter to skip): " MASTER_API_KEY
fi

# Location (optional)
if [ -z "$LOCATION" ]; then
    read -p "Enter Location (optional, e.g., Lab Room 101): " LOCATION
fi

echo ""
echo -e "${GREEN}Configuration Summary:${NC}"
echo "  Lab Pi ID: $LAB_PI_ID"
echo "  Lab Pi Name: $LAB_PI_NAME"
echo "  MAC Address: $LAB_PI_MAC"
echo "  Experiment ID: $EXPERIMENT_ID"
echo "  Master URL: $MASTER_URL"
echo "  Location: $LOCATION"
echo ""

# ============================================================================
# Step 2: Update System
# ============================================================================
echo -e "${YELLOW}Step 2: Updating system packages...${NC}"
sudo apt update && sudo apt upgrade -y

# ============================================================================
# Step 3: Install Dependencies
# ============================================================================
echo -e "${YELLOW}Step 3: Installing system dependencies...${NC}"
sudo apt install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    git \
    curl \
    wget \
    swig \
    liblgpio-dev \
    portaudio19-dev \
    libasound2-dev \
    esptool \
    avrdude \
    openocd \
    alsa-utils \
    libportaudio2 \
    ffmpeg

# ============================================================================
# Step 5: DFRobot UPS will be installed after project setup
# ============================================================================
echo -e "${YELLOW}Step 5: DFRobot UPS will be installed after project setup...${NC}"

# ============================================================================
# Step 6: Clone/Pull Repository
# ============================================================================
echo -e "${YELLOW}Step 6: Setting up project...${NC}"

PROJECT_DIR="$HOME/lab-pi"
if [ -d "$PROJECT_DIR" ]; then
    echo "Project directory already exists. Pulling latest changes..."
    cd "$PROJECT_DIR"
    # Use --no-rebase to handle divergent branches (merge strategy)
    git pull --no-rebase || {
        echo "Git pull failed due to divergent branches. Attempting to reset..."
        git fetch origin
        git reset --hard origin/main
    }
else
    echo "Cloning repository..."
    # Replace with your actual repository URL
    REPO_URL=${REPO_URL:-"https://github.com/Abhilash1575/remote_lab_pi.git"}
    git clone "$REPO_URL" "$PROJECT_DIR"
    cd "$PROJECT_DIR"
fi

# ============================================================================
# Step 7: Install DFRobot UPS support (Raspberry Pi only)
# ============================================================================
echo -e "${YELLOW}Step 7: Installing DFRobot UPS support...${NC}"

# Check if we're running on Raspberry Pi
if [ "$(uname -m)" = "armv7l" ] || [ "$(uname -m)" = "aarch64" ]; then
    if [ -f "/proc/device-tree/model" ] && grep -q "Raspberry" "/proc/device-tree/model"; then
        echo -e "${GREEN}✅ Detected Raspberry Pi - Installing DFRobot UPS support${NC}"
        
        # Copy UPS script
        if [ -f "$PROJECT_DIR/install/rpi_dfrobot_ups_all_in_one.sh" ]; then
            REAL_USER=$(whoami) bash "$PROJECT_DIR/install/rpi_dfrobot_ups_all_in_one.sh"
        else
            echo -e "${YELLOW}⚠️ DFRobot UPS script not found${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️ Not a Raspberry Pi - Skipping DFRobot UPS installation${NC}"
    fi
else
    echo -e "${YELLOW}⚠️ Not ARM architecture - Skipping DFRobot UPS installation${NC}"
fi

# ============================================================================
# Step 8: Create Configuration
# ============================================================================
echo -e "${YELLOW}Step 8: Creating Lab Pi configuration...${NC}"

# Create .env file for Lab Pi
cat > "$PROJECT_DIR/.env" << EOF
# Lab Pi Configuration
VLAB_PI_TYPE=lab
VLAB_PI_ID=$LAB_PI_ID
VLAB_PI_NAME="$LAB_PI_NAME"
VLAB_PI_MAC="$LAB_PI_MAC"
EXPERIMENT_ID=$EXPERIMENT_ID
MASTER_URL=$MASTER_URL
MASTER_API_KEY=$MASTER_API_KEY
LOCATION="$LOCATION"

# Server settings
LAB_PORT=5001
LAB_HOST=0.0.0.0
LAB_DEBUG=False
EOF

echo "Configuration saved to $PROJECT_DIR/.env"

# ============================================================================
# Step 6: Setup Python Environment
# ============================================================================
echo -e "${YELLOW}Step 8: Setting up Python environment...${NC}"

cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Install PyAudio for audio capture (requires portaudio)
# Try to install pre-built wheel first, otherwise build from source
echo "Installing PyAudio for audio capture..."
if ! pip show pyaudio >/dev/null 2>&1; then
    # Install system dependencies for PyAudio
    sudo apt-get update -qq
    sudo apt-get install -y -qq portaudio19-dev python3-pyaudio libasound2-dev 2>/dev/null || true
    pip install pyaudio || echo "Warning: PyAudio installation failed. Audio features will be disabled."
fi

# ============================================================================
# Step 7: Create Systemd Service
# ============================================================================
echo -e "${YELLOW}Step 9: Creating systemd service...${NC}"

sudo tee /etc/systemd/system/vlab-lab-pi.service > /dev/null << EOF
[Unit]
Description=Virtual Lab - Lab Pi Node
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/lab_pi_app.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/vlab-lab-pi.log
StandardError=append:/var/log/vlab-lab-pi.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable vlab-lab-pi.service

# ============================================================================
# Step 8: Hardware Setup (GPIO)
# ============================================================================
echo -e "${YELLOW}Step 10: Setting up GPIO permissions...${NC}"
sudo usermod -a -G gpio $USER

# ============================================================================
# Step 11: Final Summary
# ============================================================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Lab Pi has been configured with:"
echo "  - ID: $LAB_PI_ID"
echo "  - Name: $LAB_PI_NAME"
echo "  - Experiment: $EXPERIMENT_ID"
echo "  - Master: $MASTER_URL"
echo ""
echo "To start the Lab Pi service:"
echo "  sudo systemctl start vlab-lab-pi.service"
echo ""
echo "To check status:"
echo "  sudo systemctl status vlab-lab-pi.service"
echo ""
echo "To view logs:"
echo "  journalctl -u vlab-lab-pi.service -f"
echo ""
echo -e "${YELLOW}IMPORTANT: After starting, check Master Pi to verify registration!${NC}"
