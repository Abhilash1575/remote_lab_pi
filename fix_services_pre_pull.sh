#!/bin/bash
# Run this ON EACH OTHER LAB PI, from inside its repo root (~/lab-pi),
# BEFORE running `git pull`. It does three things:
#
#   1. Migrates per-Pi runtime state (admin password, UI config, uploads,
#      default firmware, SOP PDFs) out of the old lab-pi/ subfolder into
#      the new data/ folder at the repo root. git pull will NOT do this
#      for you -- these files are untracked/gitignored, so a plain pull
#      only moves the code, leaving your customized admin settings
#      orphaned at the old path.
#
#   2. Pre-patches the two installed systemd units that reference the
#      old nested lab-pi/lab-pi/... path (vlab-lab-pi.service and, if
#      present, audio_stream.service) so they point at where the code
#      will live AFTER you pull. This is safe to do now: editing the
#      unit file + `daemon-reload` does not restart anything that's
#      currently running -- it only affects the *next* start/restart.
#
#   3. Installs `av` (PyAV) into this Pi's venv, pinned to the version
#      range aiortc actually supports. The pulled code implements real
#      WebRTC audio via aiortc, which hard-requires av at import time --
#      without this, audio_stream.service will crash-loop after restart.
#      A prebuilt aarch64 wheel exists on PyPI; no compilation needed.
#      Independent of the pull, safe to do at any point, idempotent.
#
# Nothing here touches .env, and no service is left stopped when the
# script exits (vlab-lab-pi.service is briefly stopped mid-script for
# the data migration, then NOT restarted -- see the final instructions).
#
# Usage:
#   cp fix_services_pre_pull.sh ~/lab-pi/
#   cd ~/lab-pi
#   ./fix_services_pre_pull.sh
#   git pull
#   sudo systemctl start vlab-lab-pi.service
#   sudo systemctl restart audio_stream.service
#   sudo journalctl -u audio_stream.service -n 20 --no-pager   # confirm mic auto-detected

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$(whoami)"

echo "=== Project dir: $PROJECT_DIR (user: $CURRENT_USER) ==="

if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo "ERROR: $PROJECT_DIR does not look like the repo root (no .git dir)."
    echo "Copy this script into ~/lab-pi and run it from there."
    exit 1
fi

# ----------------------------------------------------------------------
# Step 1: Stop the Flask app service before touching its data files.
# ----------------------------------------------------------------------
if systemctl list-unit-files | grep -q "^vlab-lab-pi.service"; then
    echo "=== Stopping vlab-lab-pi.service for the data migration ==="
    sudo systemctl stop vlab-lab-pi.service
    STOPPED_APP=1
else
    echo "=== vlab-lab-pi.service not installed here, skipping stop ==="
    STOPPED_APP=0
fi

# ----------------------------------------------------------------------
# Step 2: Migrate per-Pi runtime data from lab-pi/* into data/*
# Idempotent: only moves if the old path exists and the new one doesn't,
# so re-running this script is safe.
# ----------------------------------------------------------------------
OLD_DIR="$PROJECT_DIR/lab-pi"
DATA_DIR="$PROJECT_DIR/data"
mkdir -p "$DATA_DIR"

migrate() {
    local src="$1" dst="$2"
    if [ -e "$src" ] && [ ! -e "$dst" ]; then
        echo "Migrating: $src -> $dst"
        mv "$src" "$dst"
    elif [ -e "$src" ] && [ -e "$dst" ]; then
        echo "SKIP (both exist, not overwriting): $src already have $dst"
    else
        echo "SKIP (nothing at $src)"
    fi
}

echo "=== Migrating per-Pi data out of lab-pi/ into data/ ==="
migrate "$OLD_DIR/ui_config.json"        "$DATA_DIR/ui_config.json"
migrate "$OLD_DIR/admin_password.hash"   "$DATA_DIR/admin_password.hash"
migrate "$OLD_DIR/uploads"               "$DATA_DIR/uploads"
migrate "$OLD_DIR/default_fw"            "$DATA_DIR/default_fw"
migrate "$OLD_DIR/static/sop"            "$DATA_DIR/sop"

# ----------------------------------------------------------------------
# Step 3: Pre-patch installed systemd units for the post-pull layout.
# Handles both known broken variants seen in the field:
#   - literal <LOCAL_USER> / unresolved %h,%i placeholders
#   - doubled lab-pi/lab-pi/... path segment
# ----------------------------------------------------------------------
patch_unit() {
    local unit="$1"
    local path="/etc/systemd/system/$unit"
    if [ ! -f "$path" ]; then
        echo "=== $unit not installed here, skipping ==="
        return
    fi
    echo "=== Patching $unit ==="
    echo "--- before ---"
    grep -E "WorkingDirectory|ExecStart|User=" "$path" || true
    sudo sed -i \
        -e "s#<LOCAL_USER>#${CURRENT_USER}#g" \
        -e "s#%h#/home/${CURRENT_USER}#g" \
        -e "s#User=%i#User=${CURRENT_USER}#g" \
        -e "s#/home/${CURRENT_USER}/lab-pi/lab-pi/#/home/${CURRENT_USER}/lab-pi/#g" \
        "$path"
    echo "--- after ---"
    grep -E "WorkingDirectory|ExecStart|User=" "$path" || true
}

patch_unit "vlab-lab-pi.service"
patch_unit "audio_stream.service"

sudo systemctl daemon-reload

# ----------------------------------------------------------------------
# Step 4: Install av (PyAV), pinned to aiortc's supported range.
# Requires this Pi to already have a venv (created by install-lab-pi.sh).
# ----------------------------------------------------------------------
if [ -x "$PROJECT_DIR/venv/bin/pip" ]; then
    echo "=== Installing av (PyAV) for real WebRTC audio ==="
    "$PROJECT_DIR/venv/bin/pip" install "av>=14.0.0,<17.0.0"
else
    echo "=== WARNING: no venv found at $PROJECT_DIR/venv, skipping av install ==="
    echo "    Run manually after setting up the venv: pip install \"av>=14.0.0,<17.0.0\""
fi

# ----------------------------------------------------------------------
# Done. Do NOT restart yet -- app.py doesn't exist at the new root path
# until you actually pull. vlab-lab-pi.service is currently STOPPED;
# leave it that way until after the pull.
# ----------------------------------------------------------------------
echo ""
echo "=== Pre-pull fixes applied ==="
echo "vlab-lab-pi.service is currently STOPPED (was stopped for the migration)."
echo "Next steps:"
echo "  1. cd $PROJECT_DIR && git pull"
echo "  2. sudo systemctl start vlab-lab-pi.service"
echo "  3. sudo systemctl restart audio_stream.service"
echo "  4. sudo systemctl status vlab-lab-pi.service audio_stream.service --no-pager"
echo "  5. sudo journalctl -u audio_stream.service -n 20 --no-pager"
echo "     Look for: '[WebRTC] Using audio capture device: ...'"
echo "     If it says 'default' instead of 'plughw:N,M', or the wrong mic got"
echo "     picked, add AUDIO_INPUT_DEVICE=plughw:N,M to that Pi's .env (find"
echo "     N,M via: arecord -l) and restart audio_stream.service again."
