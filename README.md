# Adapix

**The AI teammate that follows up so you don't have to.**

Adapix is a multi-tenant SaaS for small service businesses (plumbers,
contractors, salons, detailers, dentists — anyone who quotes jobs and loses
the follow-up game). It wins back the customers who went quiet by drafting the
follow-up texts, emails, and calls for you — from your own number and inbox —
and **nothing sends until you approve it.**

- Product: **https://app.adapixai.com**
- Marketing site: **https://adapixai.com**
- Stack: FastAPI + Claude on Railway (Postgres), static marketing site on Vercel.

> Legacy-name heads-up: the codebase started dental/single-tenant, so some
> model fields keep old names — `Patient` = a contact, `practice_id` = the org
> id, `treatment_type` = the job/quote. The product is horizontal small-business.

---

## Repo layout

```
adapix/
├── README.md                   ← you are here
├── CLAUDE.md                   ← working agreement + ownership lanes (read this)
├── requirements.txt            ← Python deps (pinned)
├── railway.toml                ← Railway deploy config (single replica — see ARCHITECTURE)
│
├── src/adapix/                 ← the product (Rocco's lane)
│   ├── api/                    ← FastAPI app, JSON API, webhooks, dashboard SPA
│   ├── channels/               ← SMS (Twilio), email (Resend/OAuth), voice (Vapi), iMessage (Blooio/Claw)
│   ├── agent.py                ← workflow-agnostic message composer
│   ├── campaign.py             ← follow-up scheduler
│   ├── approval.py             ← the approve-before-send queue
│   ├── outbound.py             ← single source of truth for send transport order
│   ├── inbound.py              ← inbound classification + dispatch (+ blooio_poll.py)
│   ├── billing.py              ← Stripe: checkout, trials, referrals, reconcile
│   ├── ai_guard.py             ← anti-abuse throttles + per-org daily AI budget
│   ├── backup.py / ops_alert.py ← nightly DB backup + founder error alerts
│   └── …                       ← see docs/ARCHITECTURE.md for the full map
│
├── website/                    ← adapixai.com marketing site (Ben's lane — auto-deploys on push)
├── admin/                      ← founder board (admin/index.html)
├── config/                     ← per-workflow + per-business YAML
└── docs/                       ← ARCHITECTURE, MARKETING, OUTREACH, RCS_REGISTRATION, …
```

---

## What it does

1. **Sign up** → a business gets its own dashboard, a dedicated calling number,
   and a 14-day trial (card up front, $0 charged today).
2. **Import contacts** (CSV or one-by-one) and **teach Adapix** the business —
   services, pricing, hours, FAQs (or auto-import from the business's website).
3. **Adapix drafts follow-ups** — text, email, or an AI phone call — naming the
   real job and quote. Every draft waits in the **Inbox** for one-tap approval.
4. **Customers reply** and Adapix holds the conversation, answering from what it
   was taught and escalating anything that needs a human.
5. **Mark a job Won** and it logs the recovered revenue (the "$X won back"
   counter) and can ask the happy customer for a review.

Full subsystem map, data model, background loops, and operational constraints:
**[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).**

---

## Quick start — local dev

```bash
git clone https://github.com/Fighter2450/Adapix.git
cd Adapix
python3 -m venv venv
source venv/bin/activate            # Windows:  venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                # set ANTHROPIC_API_KEY=sk-ant-...
export PYTHONPATH=src               # Windows:  set PYTHONPATH=src
uvicorn adapix.api.main:app --port 8000
#   → http://localhost:8000/signup
```

Local dev uses SQLite (`adapix.db`); production uses Postgres via
`DATABASE_URL`. To log in over plain http locally, set
`ADAPIX_COOKIE_INSECURE=1`. The dev-only inbound simulator at
`/webhooks/dev/sms` is off unless `ADAPIX_ENABLE_DEV_SMS=1`.

---

## Deploy

- **App** (`src/`, templates): `railway up` → Railway (project `adapix`,
  service `adapix-web`). Then `railway logs --build` to confirm the
  healthcheck. **One replica only** — the schedulers assume a single process.
- **Marketing site** (`website/`): push to `main` → Vercel auto-deploys in
  ~1 min. Keep unfinished site work on a branch.

---

## Working agreement

Two people share this repo — **Rocco** (Founder & CEO, owns `src/`) and
**Ben** (CMO, owns `website/`). Before shipping anything marketable, read
`docs/MARKETING.md` and follow its hard rules. Don't edit the other lane's
files — add a handoff task to the founder board (`admin/index.html`) instead.
Full rules in **[`CLAUDE.md`](CLAUDE.md)**.

---

## Data hygiene

Per-deployment runtime state is **never checked in** (see `.gitignore`):
`.env`, `*.db`, `jwt_secret`, `billing.json`, `ai_usage.json`,
`stripe_events.json`, OAuth tokens, VAPID keys, the `/data` volume contents.
If you see one of these about to be committed, **stop.**

---

## License

Proprietary, all rights reserved. Pre-launch.
