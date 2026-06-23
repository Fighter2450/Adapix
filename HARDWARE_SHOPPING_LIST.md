# Adapix Adapt 1.0 — Hardware Shopping List

Everything you need to build the first pilot unit. Order all of this now so a
"yes" from a practice isn't blocked on shipping.

Approximate total: **$320–$380** depending on where you order.

---

## Core (required)

| Part | Recommended source | ~Price | Notes |
|---|---|---|---|
| **Raspberry Pi 5 — 8GB** | CanaKit, Adafruit, Amazon | $80 | The brain. Don't get 4GB — Claude calls + future LLM work want headroom. |
| **Official Pi 5 Active Cooler** | Same retailer as Pi | $5 | Mandatory on Pi 5. Snaps onto the SoC. |
| **Official Pi 5 USB-C PSU (27W)** | Same retailer as Pi | $12 | Anything weaker brownouts under load. |
| **32GB microSD (SanDisk Extreme A2)** | Amazon | $10 | For initial boot/install. You'll move the OS to NVMe later. |
| **Cat6 Ethernet cable (3 ft)** | Amazon | $5 | Office wifi works, but ethernet is more reliable for a follow-up appliance. |

**Core total: ~$112**

---

## Storage upgrade (recommended)

| Part | Recommended source | ~Price | Notes |
|---|---|---|---|
| **Pimoroni NVMe Base for Pi 5** | Pimoroni.com or Adafruit | $15 | What your STL is designed around. |
| **1TB NVMe M.2 2280 SSD** (e.g. Crucial P3, WD SN570) | Amazon | $60 | Patient logs, conversation history, future ML cache. |
| **Brass M2.5 standoffs — 5mm** (pack of 10) | Amazon | $7 | Mount Pi to the NVMe Base. |

**Storage total: ~$82**

---

## Power resilience + I/O (recommended for v1)

| Part | Recommended source | ~Price | Notes |
|---|---|---|---|
| **Waveshare UPS HAT for Pi 5** (with batteries) | Amazon or Waveshare.com | $45 | Survives brief power blips so a follow-up appliance doesn't go dark. |
| **WS2812B LED strip — 1m, 60 LEDs/m, 10mm wide** | Amazon ("BTF-LIGHTING WS2812B 10mm") | $12 | The underglow channel in your STL is 10.03mm. |
| **3M VHB 5952 tape — 1mm thick** | Amazon or McMaster | $10 | Mounts the Pi assembly. Your stack math depends on 3mm of VHB. |

**Power/IO total: ~$67**

---

## Optional polish (skip for pilot #1 unless you want them)

| Part | ~Price | Notes |
|---|---|---|
| **0.96" OLED SSD1306 128x64** | $8 | Status display through the OLED window in your STL. Add later. |
| **DS3231 RTC module** | $5 | Holds the clock during power loss. Mostly cosmetic for v1. |
| **PoE+ HAT for Pi 5** | $25 | Single-cable install (power + ethernet from a PoE switch). Skip unless target practice has PoE. |

---

## Already on hand (you should have)

- 3D printer + filament for the case (PETG recommended, ~200g of filament per unit)
- Soldering iron + flux (for LED strip leads to GPIO)
- Heat-shrink tubing or electrical tape
- Small Phillips screwdriver

---

## Suggested order, in priority

1. **Today:** Pi 5 + active cooler + PSU + microSD + ethernet cable (Core)
2. **Today (same cart):** NVMe Base + 1TB SSD + standoffs (Storage)
3. **This week:** UPS HAT + LED strip + VHB tape (Power/IO)
4. **Optional/later:** OLED, RTC, PoE HAT

If you order from Amazon Prime + Pimoroni (UK, takes ~7 days to ship to US), you should have everything within 7–10 business days.

---

## Print queue while parts ship

Start printing:
- 1× top shell (`build_stl.py` → `adapt_top.stl`)
- 1× bottom shell (`build_stl.py` → `adapt_bottom.stl`)
- 1× foot disc (already in build_stl.py)

PETG, 0.2mm layer, 30% infill. Each shell is ~6 hours on a Bambu A1 or Prusa MK4. Bottom shell with the LED channel may want supports under the channel overhang — check your slicer's preview.

---

## When everything arrives, the build order is:

1. Install Raspberry Pi OS (64-bit, Bookworm) onto microSD
2. Boot Pi, run `rpi-clone` or `dd` to copy OS onto NVMe SSD
3. Reboot from NVMe (remove microSD)
4. `git clone` adapix repo, `pip install`, configure `.env`
5. Mount Pi onto NVMe Base with M2.5 standoffs
6. VHB-tape the NVMe Base into the bottom shell at the engraved outline
7. Wire LED strip leads to GPIO 18 + 5V + GND
8. Wire UPS HAT (sits between Pi 5 and the NVMe Base if you stack, or off to the side)
9. Close shell, plug in PSU + ethernet
10. Boot, OAuth-connect practice email, demo
