#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  Mini-DVR — Installation script
#  Tested on Ubuntu 22.04 / Debian 12
# ═══════════════════════════════════════════════════════════════════
set -e

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "Installing Mini-DVR from: $INSTALL_DIR"

# ── System packages ──────────────────────────────────────────────
echo ""
echo "==> Installing system dependencies..."
sudo apt-get update -q
sudo apt-get install -y \
  python3 python3-pip python3-venv \
  ffmpeg \
  nmap \
  netcat-openbsd \
  curl

# ── Python venv ──────────────────────────────────────────────────
echo ""
echo "==> Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/.venv"
source "$INSTALL_DIR/.venv/bin/activate"

pip install --upgrade pip -q
pip install -r "$INSTALL_DIR/requirements.txt" -q

echo ""
echo "==> Dependencies installed successfully."

# ── go2rtc binary ────────────────────────────────────────────────
echo ""
echo "==> Downloading go2rtc..."
GO2RTC_BIN="$INSTALL_DIR/go2rtc"
if [ ! -f "$GO2RTC_BIN" ]; then
  wget -q https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_amd64 \
    -O "$GO2RTC_BIN"
  chmod +x "$GO2RTC_BIN"
  echo "go2rtc downloaded."
else
  echo "go2rtc already present, skipping."
fi

# ── Buffer dir ───────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR/buffer"

# ── systemd service (optional) ───────────────────────────────────
echo ""
read -p "Install as systemd service (auto-start on boot)? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
  SERVICE_FILE="/etc/systemd/system/mini-dvr.service"
  sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Mini-DVR RTSP Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/uvicorn backend.server:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable mini-dvr
  sudo systemctl start mini-dvr
  echo "Service installed and started. Access: http://localhost:8080"
else
  echo ""
  echo "To start manually:"
  echo "  cd $INSTALL_DIR"
  echo "  ./scripts/run.sh"
fi

echo ""
echo "══════════════════════════════════════════════"
echo "  Mini-DVR installed! http://localhost:8080"
echo "══════════════════════════════════════════════"
