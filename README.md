# Adapix — Adapt 1.0

The AI follow-up assistant for any business, shipped as a plug-and-play hardware appliance.

You plug it into power, plug it into your computer, open `http://adapix.local` in a browser, and you're talking to your business's new AI staff member. Adapix learns your voice, runs follow-ups overnight, drafts replies for your approval, and adapts as you tell it more about the business.

The hardware is a Raspberry Pi 5 inside a 150 × 150 × 50 mm 3D-printed enclosure with a cyan LED underglow. The software is a FastAPI dashboard + an agent layer powered by Claude.

---

## Repo layout

```
adapix/
├── README.md                   ← you are here
├── requirements.txt            ← Python deps
├── .env.example                ← copy to .env, fill in ANTHROPIC_API_KEY
│
├── src/adapix/                 ← the application
│   ├── api/                    ← FastAPI dashboard + Jinja templates
│   ├── skills/                 ← Anthropic-style skill bundles (SKILL.md files)
│   ├── channels/               ← Twilio SMS + Resend email adapters
│   ├── agent.py                ← workflow-agnostic agent core
│   ├── chat.py                 ← in-product Adapix chat
│   ├── practice.py             ← business profile loader + prompt fragments
│   ├── dashboard.py            ← widget catalog (Adapix shapes its own UI)
│   ├── oauth.py                ← Google + Microsoft OAuth (email send-as)
│   ├── inbound.py              ← inbound SMS classification + dispatch
│   ├── escalation.py           ← emergency / clinical / pricing classifier
│   └── notifications.py        ← Web Push (VAPID)
│
├── hardware/                   ← case design, STLs, render scripts
│   ├── build_stl.py            ← parametric case generator (trimesh + shapely)
│   ├── adapix_assembled.stl    ← top + bottom combined, for visual reference
│   ├── adapix_top.stl          ← the lid (print upside-down)
│   ├── adapix_bottom.stl       ← the shell (Pi sits inside on a foam pad)
│   ├── quick_render.py         ← multi-angle review renderer
│   └── HARDWARE_SHOPPING_LIST.md
│
├── deploy/                     ← Pi 5 install scripts
│   ├── README.md               ← step-by-step flash + provision guide
│   ├── install.sh              ← one-shot installer (run on the Pi as root)
│   └── adapix.service          ← systemd unit
│
├── website/                    ← marketing site (Apple-feel landing page)
│   ├── index.html
│   ├── adapix_mark.svg         ← brand logo (transparent, icon-only)
│   └── adapix_logo_full.svg    ← brand logo + "Adapix" wordmark
│
├── config/                     ← per-business + per-workflow YAML configs
│   ├── workflows/
│   └── practices/
│
└── docs/
    ├── ARCHITECTURE.md
    ├── HIPAA-NOTES.md
    └── founding-strategy.md
```

---

## What it does

When the Pi appliance boots, it serves a dashboard at `http://adapix.local`. The dashboard is **shaped by the user's conversation with Adapix**, not by code. Widgets appear, reshuffle, or disappear based on what the business actually needs — Adapix mutates the layout via tool calls during the in-product chat.

The **welcome wizard** (`/welcome`) forks at the start:

- **"I'm running a business — help me run it"** → Adapix becomes a follow-up assistant. Pulls leads from your inbox, drafts reminder messages, watches for high-value replies, escalates anything urgent to a human.
- **"I'm starting a business — help me build it"** → Adapix becomes a co-founder. Brainstorms names, defines services, sets pricing, generates a launch checklist, lands the first 10 customers. Driven by the Skills system (`src/adapix/skills/new_business/`).

The wizard then asks for the **exact business type** from a searchable catalog of ~200 specific options (oral surgeon, coffee shop, plumber, SaaS startup, etc. — not just "Healthcare"). That precise type is fed into every prompt Adapix uses, so it never sounds like a generic AI — it sounds like *your* industry.

The **Workshop** tab is the unified working space: tools (skills you can run on demand), plug-ins (Gmail, Outlook, Twilio, Resend, Web Push), and a bench for active projects.

---

## Skills

Adapix uses an [Anthropic-style skill system](https://docs.claude.com/en/docs/agents/skills) — each skill lives in its own folder with a `SKILL.md` (YAML frontmatter + markdown body). The agent loads the catalog at startup, sees what's available, and decides when to invoke a skill based on the conversation.

Current new-business skills:

- `name-brainstorm` — 12 candidate names across 4 archetypes
- `service-catalog` — turn vague offering into a structured catalog
- `pricing-strategy` — pick a model and put real numbers on each offering
- `brand-voice` — define how the business talks; produces a reusable voice doc
- `launch-checklist` — tailored to-do list, ordered by long-pole
- `first-customer-plan` — concrete plan for the first 10 customers

Adding a new skill is: `mkdir src/adapix/skills/<mode>/<slug>/`, drop in a `SKILL.md`. The loader picks it up on next request. No code changes.

See [`src/adapix/skills/README.md`](src/adapix/skills/README.md) for the SKILL.md format.

---

## Quick start — local dev

Don't have the Pi yet? You can run the whole dashboard on your laptop.

```bash
# 1. Get the code
git clone https://github.com/Fighter2450/Adapix.git
cd Adapix

# 2. Make a venv and install deps
python3 -m venv venv
source venv/bin/activate            # Windows:  venv\Scripts\activate
pip install -r requirements.txt

# 3. Drop in your API key
cp .env.example .env                # then edit .env, set ANTHROPIC_API_KEY=sk-ant-...

# 4. Run the dashboard
set PYTHONPATH=src                  # Windows
# or:   export PYTHONPATH=src       # macOS / Linux
uvicorn adapix.api.main:app --port 8000

# 5. Open it
#    http://localhost:8000/welcome
```

The welcome wizard will run, you'll pick a business mode + type, then land on the dashboard. To reset and run the wizard again, delete `configured.flag` and `practice_profile.json`.

---

## Quick start — Pi 5 appliance

See [`deploy/README.md`](deploy/README.md). End-to-end: flash an SD card, copy this repo to `/opt/adapix`, run `sudo bash /opt/adapix/deploy/install.sh`, reboot. Total time ~20 minutes. The Pi auto-starts the dashboard on every boot and serves it at `http://adapix.local`.

---

## Hardware

The case is parametric — open `hardware/build_stl.py`, change a constant, run it, get a new STL. See [`hardware/README.md`](hardware/README.md).

Key dimensions:

- **150 × 150 × 50 mm** (a flat square, not a cube)
- **45° chamfered front-left corner** — orients the device visually
- **6 mm rounded corners** elsewhere
- **9.65 mm wide underglow channel** in the bottom shell — fits a standard WS2812B LED strip
- **40 mm fan grille** on the top with 4× M3 mounting holes
- **Snap-fit lid at Z = 35 mm**
- **Engraved Adapix A + circuit traces + "ADAPT 1.0" + vent slots** on top

Print orientation: bottom shell flat on the build plate (central pedestal pointing down), top shell upside-down for the cleanest engraving finish.

---

## Privacy & data

Everything is local-first. The Pi runs the dashboard, the SQLite database, and the OAuth tokens. The only thing that goes out over the internet is the Anthropic API call for the chat. No customer data is centralized.

Files that are per-device runtime state and **never checked in**:

- `practice_profile.json` — the business config
- `chat_history.json` — the in-product chat log
- `email_tokens.json` — Gmail / Outlook OAuth tokens
- `vapid_keys.json` — web push keys (auto-generated on first boot)
- `*.db` — SQLite

See `.gitignore` for the full list. If you ever see one of these about to be committed, **stop and ask**.

---

## Subdirectory READMEs

- [`src/adapix/README.md`](src/adapix/README.md) — application architecture
- [`src/adapix/skills/README.md`](src/adapix/skills/README.md) — SKILL.md format
- [`src/adapix/api/README.md`](src/adapix/api/README.md) — dashboard routes + templates
- [`hardware/README.md`](hardware/README.md) — case design + STL generation
- [`deploy/README.md`](deploy/README.md) — Pi flash + install guide
- [`website/README.md`](website/README.md) — marketing site

---

## License

Proprietary, all rights reserved. Pre-launch.
