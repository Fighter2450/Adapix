# Deploying Adapix to a Raspberry Pi 5

This guide takes you from a fresh Pi 5 + blank SD card to a working **Adapt 1.0** appliance you can plug into a computer and open in a browser.

Total time: ~20 minutes (most of which is the Pi booting and apt-getting things).

---

## What you need

- Raspberry Pi 5
- microSD card, 16 GB or larger (32 GB recommended)
- microSD reader for your laptop
- Pi's USB-C **wall power adapter** (the official Pi 27W one, or any 5V/3A+ USB-C PD adapter)
- A second **USB-C cable** (the one you'll use to connect the Pi to a computer for data)
- Your Wi-Fi SSID and password (used as a one-time SSH fallback during setup — not required after that)
- An `ANTHROPIC_API_KEY` (you have one already)

---

## Step 1 — Flash Pi OS Lite (5 min)

1. Install **Raspberry Pi Imager** from https://www.raspberrypi.com/software/.
2. Insert the SD card into your laptop.
3. Open Imager.
4. **Choose Device**: `Raspberry Pi 5`.
5. **Choose OS**: `Raspberry Pi OS (other)` → `Raspberry Pi OS Lite (64-bit)`. Yes, *Lite*. We don't need a desktop.
6. **Choose Storage**: your SD card.
7. Click **Next**, then **Edit Settings** when it asks about OS customization.

In the settings panel, set:

- **General tab**
  - Set hostname: **`adapix`**
  - Set username and password: **`adapix`** / pick a password you'll remember
  - Configure wireless LAN: your Wi-Fi SSID + password (this is just a one-time fallback so we can SSH in if USB gadget mode hits a snag)
  - Set locale: your timezone and keyboard layout
- **Services tab**
  - Enable SSH: **Use password authentication**

Save the settings, then click **Yes** to write. It'll take 3-5 minutes.

When it's done, eject the SD card and put it in the Pi.

---

## Step 2 — First boot (5 min)

1. Insert the SD card into the Pi 5.
2. Plug the Pi's USB-C **wall adapter** into the Pi's USB-C port. (Not the cable to your computer yet — that comes later.)
3. The Pi will boot. The green LED will blink for a minute or two while it expands the filesystem.
4. Once it's quiet, find the Pi's IP address on your Wi-Fi network. Easiest way:
   - Open your router's admin page and look for a device named `adapix`
   - **OR** from your laptop terminal, try: `ssh adapix@adapix.local` (this works on macOS, modern Windows, and Linux because of mDNS)

If `ssh adapix@adapix.local` connects, great. Enter the password you set in Step 1 and you're in.

If it doesn't, find the Pi by IP (look in your router's connected-devices list) and try `ssh adapix@<that-ip>`.

---

## Step 3 — Copy the Adapix code to the Pi (3 min)

You have two options:

### Option A — clone from a git remote (if your repo is pushed)

```bash
ssh adapix@adapix.local
sudo mkdir -p /opt/adapix
sudo chown adapix:adapix /opt/adapix
cd /opt
git clone https://github.com/YOUR-ORG/adapix.git
```

### Option B — copy from your laptop (if the repo is local)

From your laptop, in the project directory:

```bash
# Mac / Linux / Windows PowerShell with OpenSSH
rsync -avz --exclude='venv' --exclude='__pycache__' --exclude='.git' \
    ./ adapix@adapix.local:/opt/adapix/

# If you don't have rsync, use scp:
scp -r ./ adapix@adapix.local:/opt/adapix/
```

---

## Step 4 — Run the installer (5 min)

SSH into the Pi:

```bash
ssh adapix@adapix.local
```

Run the installer:

```bash
sudo bash /opt/adapix/deploy/install.sh
```

This will:

1. Install system packages (Python, avahi for mDNS, dnsmasq for the USB DHCP)
2. Set the hostname to `adapix`
3. Configure the Pi 5 USB-C port for USB Ethernet gadget mode
4. Set up a tiny DHCP server on the gadget interface
5. Install all Python dependencies into `/opt/adapix/venv`
6. Install the `adapix.service` systemd unit so the dashboard auto-starts on every boot

It takes about 5 minutes. You'll see green log lines for each step. At the end it'll tell you to edit `.env` and reboot.

---

## Step 5 — Drop in your API key (1 min)

```bash
nano /opt/adapix/.env
```

Find this line:

```
ANTHROPIC_API_KEY=
```

Paste your key after the `=`. No quotes. Save with `Ctrl+O`, `Enter`, `Ctrl+X`.

---

## Step 6 — Reboot and connect (2 min)

```bash
sudo reboot
```

Wait 30 seconds for the Pi to come back up.

Now disconnect the wall USB-C adapter, then:

1. **Plug the Pi's USB-C wall adapter** back into the Pi (powering it)
2. **Plug a USB-C cable from the Pi's USB-A port (with a USB-A to USB-C adapter)** — wait, see the note below
3. Wait 10 seconds for the Pi to enumerate as a USB Ethernet device on your computer
4. Open a browser on the computer
5. Go to **http://adapix.local**

You should see the **welcome wizard** — Adapix is yours.

> **Note on cabling:** The Pi 5 has only one USB-C port, which is used for power. To enable USB-C *data* to your computer at the same time, the Pi needs to be powered through the GPIO pins instead, freeing the USB-C port for data. This requires either:
> - A PoE+ HAT (powers the Pi via Ethernet from a PoE switch — no second USB-C cable to your computer, but you connect via Ethernet instead)
> - A 5V GPIO power adapter (small board that takes a wall adapter and feeds 5V into the GPIO pins, leaving USB-C free for data to your computer)
>
> If you don't have either yet, **the fallback is Ethernet:** plug an RJ45 cable from the Pi's Ethernet port to your computer (use a USB-to-Ethernet adapter on the laptop side if needed). The Pi will be reachable at `http://adapix.local` over that cable too.

---

## Troubleshooting

### Can't SSH to `adapix.local` after first boot

- Make sure you set up Wi-Fi in the Imager step. If not, re-flash and check the box this time.
- Some networks block mDNS — try the Pi's IP directly: find it in your router's admin page.
- Make sure SSH was enabled in the Imager step.

### `adapix.local` works but the dashboard is blank or 500s

```bash
ssh adapix@adapix.local
sudo systemctl status adapix.service
sudo journalctl -u adapix.service -n 100
```

Look at the error and paste it back to me.

### "Couldn't reach the AI service"

The `ANTHROPIC_API_KEY` in `/opt/adapix/.env` is wrong, blank, or revoked. Edit it, then `sudo systemctl restart adapix.service`.

### USB gadget mode doesn't enumerate

- Make sure `/boot/firmware/config.txt` has `dtoverlay=dwc2,dr_mode=peripheral`
- Make sure `/boot/firmware/cmdline.txt` has `modules-load=dwc2,g_ether`
- Make sure the Pi is **NOT** being powered via the USB-C port that's now the data port (it needs alternate power)
- Reboot the Pi after any change to those files

### I want to reset the dashboard (re-run welcome wizard)

```bash
ssh adapix@adapix.local
sudo rm /opt/adapix/configured.flag /opt/adapix/practice_profile.json
sudo systemctl restart adapix.service
```

Then refresh `http://adapix.local/welcome` in your browser.

---

## What's running on the Pi

- **adapix.service** — uvicorn serving the FastAPI app on port 80
- **avahi-daemon** — broadcasts `adapix.local` via mDNS so any computer on the same link sees it
- **dnsmasq** — tiny DHCP server on the USB gadget interface
- **systemd-networkd** — handles `usb0` interface config

All four start on boot. None of them care about Wi-Fi or your home network — the Pi is fully self-contained.

---

## Updating the code later

```bash
ssh adapix@adapix.local
cd /opt/adapix
git pull          # or rsync from your laptop again
sudo systemctl restart adapix.service
```

Refresh the dashboard in your browser.
