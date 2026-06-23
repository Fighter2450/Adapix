# Adapix Hardware v1.0 — Specification

*Locked: SBC + BOM + manufacturing path. v0 form factor (3D-printed shell) proves the design before any of this is committed.*

---

## SBC: Raspberry Pi 5 (8GB)

**Decision rationale:**
- $80 retail, $70 at volume — fits our cost envelope
- Runs Python/FastAPI/SQLite stack natively (no compatibility surprises)
- Massive community + documentation = junior engineers can support it
- Built-in WiFi 5 (2.4 + 5 GHz) + Bluetooth 5 + Gigabit Ethernet
- Heat manageable with passive aluminum case as heatsink
- Adapix workload is light (LLM runs on Anthropic's servers, we just orchestrate) — Pi 5 is comfortably overspec'd, which means it'll run cool and last

**Alternatives we rejected:**
- **Pi Compute Module 5** — better for embedded, but requires $30-50K NRE for a custom carrier PCB. Defer to v2.0.
- **Mac mini board** — $600, runs macOS, branding overlap is a trap.
- **Intel NUC** — $300+, x86 overkill for our Python workload.
- **Off-brand ARM (Orange Pi, Rock Pi)** — saving $20 isn't worth the worse software ecosystem.

---

## Full electronic hardware list (everything that goes inside)

### Compute + storage
| Item | Spec | Cost (vol 1K) | Why |
|---|---|---|---|
| Raspberry Pi 5 | 8GB RAM | $70 | The brain |
| **NVMe SSD** | 128GB M.2 2230 | $18 | **Replaces SD card.** SD wears out under 24/7 SQLite writes. NVMe lasts 10× longer. |
| Pi 5 NVMe HAT | Official or third-party | $12 | Adapter to mount NVMe to Pi 5 |
| Coin cell battery | CR2032 | $0.30 | Powers Pi 5's built-in RTC across power loss. Critical for cron-style campaigns. |

### Power
| Item | Spec | Cost | Why |
|---|---|---|---|
| AC adapter | USB-C PD, 27W, FCC/CE certified | $14 | Pi 5 needs 5V/5A for full performance |
| Internal USB-C cable | Right-angle, 15cm | $2 | Pi USB-C port → rear panel cutout |
| Polyfuse | 3A resettable | $0.20 | Protects against shorts on power input |
| Ferrite bead | Snap-on, EMI suppression | $0.30 | Helps with FCC compliance |

### User interface
| Item | Spec | Cost | Why |
|---|---|---|---|
| RGB status LED | WS2812B (single, addressable) | $0.40 | Shows: booting / idle / processing / error / waiting-for-approval |
| Light pipe | Cast acrylic, 4mm dia | $0.50 | Carries LED through the case |
| Tactile button | 12mm, momentary, panel-mount | $1.00 | Front-panel reset / factory-reset / pairing |
| Button cap | Aluminum-look, color-matched | $0.50 | Cosmetic |

### Cooling
| Item | Spec | Cost | Why |
|---|---|---|---|
| Thermal pad | 1mm silicone, 14×14mm | $0.30 | Pi 5 SoC → aluminum top shell |
| Aluminum top shell | (counted under mechanical, but acts as heatsink) | $32 | Passive cooling, no fan = silent |
| **Optional fan** | 30mm low-noise (Noctua-grade) | $5 | Only if thermal testing shows we need it. Likely don't with aluminum top. |

### Connectivity
| Item | Spec | Cost | Why |
|---|---|---|---|
| WiFi/Bluetooth | Built into Pi 5 | $0 | Default for office WiFi |
| Ethernet | Built into Pi 5 (Gigabit) | $0 | Wired option for security-paranoid practices |
| External WiFi antenna | u.FL pigtail + RP-SMA | $1.50 | **Optional.** Only if signal in target offices is weak (orthos often have lots of metal). Adds an ugly antenna nub — defer to a SKU variant if needed. |

### Expansion / glue
| Item | Spec | Cost | Why |
|---|---|---|---|
| Custom GPIO HAT | Tiny PCB | $4 (NRE: $3-5K) | Holds LED driver, button connector, fan PWM, RTC battery socket. Cleaner than a rats' nest of jumpers. |
| Standoffs + screws | M2.5, 8mm, brass | $1.20 | Mount Pi to bottom shell |
| Threaded inserts | Heat-set M2.5 | $0.30 | Embedded in plastic bottom shell |

### Safety / EMC (compliance)
| Item | Spec | Cost | Why |
|---|---|---|---|
| EMC gasket | Conductive foam, between shells | $0.80 | FCC Part 15 — keeps RF inside |
| ESD-protection diodes | TVS on USB and Ethernet | (on Pi 5 already) | $0 |
| Tamper-evident seal | Sticker over case seam | $0.15 | Optional, useful for medical/HIPAA optics |

---

## What's NOT in the BOM (deliberate)

| Component | Why we're skipping |
|---|---|
| Custom motherboard | Saves $25/unit at 10K but $50-100K NRE. Wait for v2. |
| Cellular modem (LTE) | Most offices have WiFi. Save +$40/unit + carrier deals for premium SKU later. |
| Battery backup / UPS | Practice has wall power. Add for hospitals later if relevant. |
| Microphone | Not needed for v1 (we're SMS/email, not voice). Add when voice channel ships. |
| Speaker | Same as mic. |
| Display / screen | Not needed — admin UI is on phone/laptop. Don't add visual UI to the device. |
| GPS | Overkill — practices don't move. |
| Hardware Security Module (HSM) | Overkill for v1. Encrypted SSD + good key management is sufficient. |
| Camera | Privacy nightmare in a healthcare practice. Hard no. |

---

## Per-unit cost rollup (volume 1000 units)

| Category | Cost |
|---|---|
| Mechanical (top + bottom shell + feet + light pipe + hardware) | $37 |
| **Electronic** (Pi + NVMe + LED + button + fan + RTC + cabling + safety) | **~$118** |
| Packaging (box + foam + cards) | $6 |
| Assembly + QA + burn-in | $16 |
| Tooling amortization (over 5K units) | $5 |
| Shipping to warehouse | $4 |
| **Total per unit** | **~$186** |

**Retail target:** $1,899 device + $250/mo software → **~91% hardware gross margin**

---

## The full electronic block diagram

```
                 ┌──────────────────────────┐
                 │  USB-C PD power input    │
                 │  (27W, FCC/CE certified) │
                 └────────────┬─────────────┘
                              │ + polyfuse + ferrite
                              ▼
       ┌────────────────────────────────────────────┐
       │           Raspberry Pi 5 (8GB)             │
       │  • SoC (BCM2712, 4×ARM Cortex-A76 @ 2.4GHz)│
       │  • Built-in WiFi 5, BT5, Gigabit Ethernet  │
       │  • Built-in RTC (no battery)               │
       │  • USB-C power, 4× USB ports, GPIO         │
       └─────┬──────────────┬──────────┬────────────┘
             │              │          │
        NVMe HAT         GPIO HAT    USB/Ethernet
             │              │       (out rear panel)
             ▼              ▼
        ┌─────────┐    ┌──────────┐
        │ 128 GB  │    │  RGB LED │──→ light pipe → top of case
        │  NVMe   │    │  Button  │←── front panel
        │  SSD    │    │  RTC bat │
        └─────────┘    │  Fan PWM │── (only if needed)
                       └──────────┘

  ┌─────────────────────────────────────────────────┐
  │  Aluminum top shell = passive heatsink          │
  │  (thermal pad → SoC, plus aluminum bulk)        │
  └─────────────────────────────────────────────────┘
```

---

## What you should buy RIGHT NOW (to validate the choice)

If you want to de-risk the SBC pick before committing to anything else:

1. **Raspberry Pi 5, 8GB** — ~$80 from Adafruit, Pi Hut, CanaKit, or PiShop.us
2. **27W USB-C power supply** (official Pi 5 one) — $12
3. **128GB NVMe SSD + Pi 5 NVMe HAT** — Geekworm or Pimoroni, ~$30 combined
4. **microSD card to bootstrap** — any 32GB, $8

Total: ~$130. Boot it up, install Raspberry Pi OS, clone the Adapix repo, run `python -m adapix.cli init-db` and confirm everything works on Pi. If yes, the Pi is locked. If no, we know within a weekend.

---

## Roadmap from here (parallel to software)

| When | Hardware milestone |
|---|---|
| **Now** | 3D-printed PLA shell + Pi 5 dev kit on the desk |
| **6 months** | Custom GPIO HAT designed (LED + button + RTC + fan), $3-5K NRE |
| **12 months** | First 50 pilot units: vacuum-cast urethane top + Pi 5 + custom HAT, hand-assembled |
| **18 months** | Tooling for injection-molded bottom + first CNC aluminum top batch (500 units) |
| **24 months** | FCC + UL certification for retail-ready production |
| **30 months** | Volume CM in US or China, 1000+ unit runs |

**Don't spend a dollar on tooling before you have 3 paying pilot customers.** That's the discipline.
