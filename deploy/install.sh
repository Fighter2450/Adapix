#!/usr/bin/env bash
# ============================================================================
# Adapix Adapt 1.0 — one-shot Pi 5 installer.
#
# Run this ON THE PI after flashing Pi OS Lite and SSH'ing in:
#
#     curl -fsSL https://... /deploy/install.sh | sudo bash
#
# Or, if the code is already at /opt/adapix (you copied it manually):
#
#     sudo bash /opt/adapix/deploy/install.sh
#
# What this does, in order:
#   1. Installs system packages (python, avahi, dnsmasq, etc.)
#   2. Sets the hostname to "adapix" so adapix.local resolves
#   3. Configures USB-C peripheral mode so the Pi presents as a USB
#      network adapter when plugged into a computer
#   4. Sets up a tiny DHCP server on the gadget interface so the
#      connected computer gets an IP automatically
#   5. Installs the Adapix Python deps into a venv at /opt/adapix/venv
#   6. Installs and enables the adapix.service systemd unit
#   7. Reboots
#
# After this finishes and the Pi reboots:
#   - Power the Pi
#   - Plug a USB-C cable from the Pi to your computer
#   - Open http://adapix.local in any browser → welcome wizard
# ============================================================================

set -euo pipefail

# --- Configurable bits ----------------------------------------------------
APP_USER="${APP_USER:-adapix}"
APP_DIR="${APP_DIR:-/opt/adapix}"
HOSTNAME_DESIRED="${HOSTNAME_DESIRED:-adapix}"
GADGET_HOST_IP="10.55.0.1"           # the Pi's IP on the USB gadget link
GADGET_DHCP_RANGE="10.55.0.10,10.55.0.50,12h"

log()  { printf '\033[1;36m[adapix]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[adapix]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[adapix]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo. Try:  sudo bash $0"

# --------------------------------------------------------------------------
# 1. System packages
# --------------------------------------------------------------------------
log "Updating apt and installing base packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    git curl ca-certificates \
    avahi-daemon avahi-utils \
    dnsmasq \
    libffi-dev libssl-dev \
    sqlite3 \
    >/dev/null

# --------------------------------------------------------------------------
# 2. Hostname → "adapix" (so adapix.local resolves via mDNS)
# --------------------------------------------------------------------------
log "Setting hostname to '$HOSTNAME_DESIRED'…"
hostnamectl set-hostname "$HOSTNAME_DESIRED"
# Keep /etc/hosts in sync so sudo doesn't whine about hostname lookup
if ! grep -q "127\.0\.1\.1.*$HOSTNAME_DESIRED" /etc/hosts; then
    sed -i "/^127\.0\.1\.1/d" /etc/hosts
    echo "127.0.1.1   $HOSTNAME_DESIRED" >> /etc/hosts
fi

# --------------------------------------------------------------------------
# 3. USB-C gadget mode (g_ether)
#    The Pi 5's USB-C port is normally PWR_IN. When the gadget overlay is
#    active, the Pi presents itself as a USB Ethernet device to whatever
#    host computer it's plugged into. The Pi MUST be powered separately
#    (via a powered USB hub, PoE HAT, or the 5V GPIO pin) when this is
#    enabled — the gadget mode disables power-input on the USB-C port.
# --------------------------------------------------------------------------
log "Enabling dwc2 / g_ether USB gadget mode in /boot/firmware/config.txt…"
CONFIG_TXT=/boot/firmware/config.txt
CMDLINE_TXT=/boot/firmware/cmdline.txt
[[ -f $CONFIG_TXT  ]] || CONFIG_TXT=/boot/config.txt
[[ -f $CMDLINE_TXT ]] || CMDLINE_TXT=/boot/cmdline.txt

if ! grep -q "^dtoverlay=dwc2" "$CONFIG_TXT"; then
    {
        echo ""
        echo "# Adapix: enable USB-C peripheral (gadget) mode"
        echo "dtoverlay=dwc2,dr_mode=peripheral"
    } >> "$CONFIG_TXT"
fi
# cmdline.txt has to be ONE line — splice in our module loads if not already there
if ! grep -q "modules-load=dwc2,g_ether" "$CMDLINE_TXT"; then
    # insert right after rootwait, otherwise append
    if grep -q "rootwait" "$CMDLINE_TXT"; then
        sed -i 's/\(rootwait\)/\1 modules-load=dwc2,g_ether/' "$CMDLINE_TXT"
    else
        # cmdline is single line; append at the end (preserving newline)
        sed -i '$ s/$/ modules-load=dwc2,g_ether/' "$CMDLINE_TXT"
    fi
fi

# --------------------------------------------------------------------------
# 4. Network config for the gadget interface (usb0)
#    When the Pi boots with g_ether, a new interface "usb0" appears.
#    Give it a static IP (10.55.0.1) and run dnsmasq on it as a tiny
#    DHCP server so the connected computer gets 10.55.0.10 automatically.
# --------------------------------------------------------------------------
log "Writing systemd-networkd profile for usb0…"
mkdir -p /etc/systemd/network
cat > /etc/systemd/network/10-usb0.network <<NETEOF
[Match]
Name=usb0

[Network]
Address=$GADGET_HOST_IP/24
IPMasquerade=no
ConfigureWithoutCarrier=yes
NETEOF

log "Configuring dnsmasq for usb0…"
mkdir -p /etc/dnsmasq.d
cat > /etc/dnsmasq.d/adapix-usb0.conf <<DNSEOF
# Only serve DHCP on the USB gadget link — never on real LAN
interface=usb0
bind-interfaces
dhcp-range=$GADGET_DHCP_RANGE
# Push our hostname to the connected machine
dhcp-option=option:dns-server,$GADGET_HOST_IP
domain=local
expand-hosts
DNSEOF

# --------------------------------------------------------------------------
# 5. Python venv + deps for the Adapix dashboard
# --------------------------------------------------------------------------
log "Setting up Adapix Python environment at $APP_DIR/venv…"
# Make sure the app user exists (defaults to 'adapix' from Pi imager preset)
if ! id "$APP_USER" >/dev/null 2>&1; then
    warn "User '$APP_USER' does not exist; falling back to 'pi'."
    APP_USER="pi"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR" || true

sudo -u "$APP_USER" bash <<INNER
set -euo pipefail
cd "$APP_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
INNER

# Ensure the .env exists; if not, copy from example (the user will fill in keys later)
if [[ ! -f $APP_DIR/.env ]]; then
    if [[ -f $APP_DIR/.env.example ]]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
        warn "No .env found — copied .env.example. EDIT IT to add your ANTHROPIC_API_KEY."
    else
        warn "No .env or .env.example. Dashboard will fail to start until you create .env."
    fi
fi

# --------------------------------------------------------------------------
# 6. systemd unit for the Adapix dashboard (runs uvicorn on port 80)
# --------------------------------------------------------------------------
log "Installing adapix.service…"
install -m 0644 "$APP_DIR/deploy/adapix.service" /etc/systemd/system/adapix.service
# Substitute APP_USER / APP_DIR placeholders in the unit file
sed -i \
    -e "s|@APP_USER@|$APP_USER|g" \
    -e "s|@APP_DIR@|$APP_DIR|g" \
    /etc/systemd/system/adapix.service

# Let uvicorn bind to port 80 without being root
log "Granting Python the cap_net_bind_service capability (port 80)…"
PYBIN="$(readlink -f "$APP_DIR/venv/bin/python3")"
setcap 'cap_net_bind_service=+ep' "$PYBIN"

# --------------------------------------------------------------------------
# 7. Enable services
# --------------------------------------------------------------------------
log "Enabling services…"
systemctl daemon-reload
systemctl enable systemd-networkd
systemctl enable avahi-daemon
systemctl enable dnsmasq
systemctl enable adapix.service

log ""
log "================================================================"
log " Install complete."
log ""
log " Next steps:"
log "   1. Edit $APP_DIR/.env and set ANTHROPIC_API_KEY."
log "   2. Reboot:    sudo reboot"
log "   3. After reboot, plug the Pi into a computer via USB-C."
log "   4. Open http://adapix.local in a browser."
log "================================================================"
