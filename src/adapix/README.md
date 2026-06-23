# Adapix — Application Core

This is the Python package that runs on the Adapt 1.0 appliance. It's a FastAPI app with a Jinja2 dashboard, an agent layer powered by Claude, channel adapters for SMS / email / push, and a skill system.

Everything is local-first. The only external network call is to the Anthropic API.

---

## Module map

```
src/adapix/
├── api/                ← FastAPI app, routes, templates
│   ├── main.py         ← FastAPI factory, /static mount, auth, root + approvals
│   ├── app_routes.py   ← /app PWA, /welcome wizard, /chat, /api/v1/* JSON endpoints
│   ├── webhooks.py     ← Twilio inbound SMS webhook + dev simulator
│   ├── auth.py         ← HTTP Basic auth for /api/v1/* (creds in .env)
│   ├── static/         ← brand assets (adapix_mark.svg, favicons, fonts)
│   └── templates/      ← Jinja2 dashboards (welcome.html, app.html, chat.html, ...)
│
├── skills/             ← Anthropic-style skill bundles
│   ├── loader.py       ← parses SKILL.md (YAML frontmatter + body), filters by mode
│   └── new_business/   ← skills for "I'm starting a business" branch
│       ├── name-brainstorm/SKILL.md
│       ├── service-catalog/SKILL.md
│       ├── pricing-strategy/SKILL.md
│       ├── brand-voice/SKILL.md
│       ├── launch-checklist/SKILL.md
│       └── first-customer-plan/SKILL.md
│
├── channels/           ← outbound message adapters
│   ├── sms.py          ← Twilio (with dry-run)
│   └── email.py        ← Resend (with dry-run)
│
├── agent.py            ← workflow-agnostic agent core (composes a message)
├── chat.py             ← in-product Adapix chat (the /chat conversation)
├── practice.py         ← business profile loader + prompt fragments
├── dashboard.py        ← widget catalog (Adapix mutates layout via tool calls)
├── memory.py           ← structured long-term facts Adapix has learned
├── notifications.py    ← Web Push (VAPID keys, subscriptions, send)
├── oauth.py            ← Google + Microsoft 365 OAuth (send email as the practice)
├── inbound.py          ← inbound SMS classifier + dispatcher
├── escalation.py       ← classifies an inbound reply (emergency / clinical / pricing / etc.)
├── campaign.py         ← orchestrates agent + channels for active campaigns
├── approval.py         ← human-in-the-loop approval queue
├── config.py           ← settings (env), workflow YAML, practice YAML
├── models.py           ← SQLAlchemy data models (Patient → Customer, Campaign, Message, EscalationEvent)
├── db.py               ← engine + session factory
└── cli.py              ← typer CLI (init-db, ingest, run, simulate-inbound, etc.)
```

---

## How a request flows

### The dashboard (`http://adapix.local`)

1. Browser hits `GET /app`.
2. `api/app_routes.py:app_shell` checks for `configured.flag`. If missing → redirects to `/welcome`.
3. The wizard at `/welcome` (template `welcome.html`) runs the 8-step setup:
   - Boot animation
   - Fork — "creating a business" vs "running a business"
   - Business name → owner name → voice tone → problems → exact business type (searchable picker over ~200 types) → done
4. Wizard `POST`s to `/api/v1/setup/save`, which writes `practice_profile.json` and `configured.flag`.
5. Browser is redirected back to `/app`. The dashboard loads.

The dashboard fetches data from `/api/v1/feed`, renders widgets, and lets the user click into Workshop / Chat / Approvals.

### A chat exchange (`/chat`)

1. Browser opens `/chat` → renders `chat.html`.
2. `GET /api/v1/chat/history` → returns conversation transcript + suggested follow-ups.
3. If empty, `POST /api/v1/chat/opener` is called to generate Adapix's first message.
4. User types something → `POST /api/v1/chat/send` → `chat.py:reply_to(text)` is called.
5. `reply_to` builds the system prompt by composing:
   - The role framing (depends on `profile.mode` — co-founder vs follow-up assistant)
   - The business profile block (name, type, voice, problems)
   - The skill index block (one line per available skill)
   - Missing topics Adapix is still learning about
6. Calls the Anthropic API with the full transcript.
7. Saves the response, returns it to the browser.

### An inbound SMS

1. Twilio POSTs to `/webhooks/twilio/sms` with signed headers.
2. `webhooks.py` verifies the signature (unless `SKIP_TWILIO_VERIFICATION=true` in `.env`).
3. `inbound.py` looks up the customer by `From` number, attaches the message to their active campaign.
4. `escalation.py` classifies the body (emergency / clinical question / callback request / decline / STOP / pricing / general).
5. Based on the classification:
   - Hard escalation → flag + push notification to the owner
   - Decline / STOP → mark campaign closed, send closeout
   - Everything else → `agent.py` composes a reply, queued for approval if `approval_mode: required`

---

## The mode fork

The single most important branch in the codebase. Set during the welcome wizard, stored in `practice_profile.json` under `mode`.

| `mode` | Adapix is… | System prompt framing | Skills loaded |
|---|---|---|---|
| `existing` | A follow-up assistant for a running business | "junior employee on day 4 who's learning the office" | (existing-business skills, when those exist) |
| `new` | A co-founder helping someone launch a business | "AI co-founder, push past planning-theater, ship things" | `skills/new_business/*` |

`chat.py:build_system_prompt` switches role + goals based on this. `skills/loader.py:list_skills(mode=…)` returns only skills marked for that mode.

---

## The business type field

Set during the welcome wizard's last step via a searchable picker over ~200 specific types (oral surgeon, coffee shop, plumber, SaaS startup, etc.).

Stored in `practice_profile.json` as **two fields**:

- `practice_type` — slug, e.g. `"oral_surgeon"`
- `practice_type_label` — human-readable, e.g. `"Oral surgeon"`

`practice.py:practice_type_fragment()` feeds the **label** directly into Adapix's prompt: *"This is a Coffee shop / café business. Use the tone, vocabulary, and customer expectations typical of that industry…"*

If the user picked **"Not listed"** at the wizard step, `practice_type = "other"` and `practice_type_custom` contains their free-form description. The prompt fragment uses that custom text instead.

---

## Skills

See [`skills/README.md`](skills/README.md) for the SKILL.md format and how the loader works.

TL;DR: each skill is a folder with `SKILL.md` (YAML frontmatter + markdown body). `loader.py:list_skills(mode="new")` returns the skills available for that mode. The agent gets a one-line index per skill in its system prompt; the full body is only loaded when the agent decides to invoke a specific skill.

Adding a skill = `mkdir` + drop a SKILL.md. No code changes.

---

## Configuration

Settings come from `.env` via `pydantic-settings`. Required at minimum:

- `ANTHROPIC_API_KEY`

Optional (depending on which channels you use):

- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` — SMS
- `RESEND_API_KEY`, `RESEND_FROM_EMAIL` — transactional email
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` — Gmail OAuth (send as the practice)
- `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET` — Outlook OAuth
- `ADMIN_USERNAME`, `ADMIN_PASSWORD` — HTTP Basic auth for the JSON API
- `PUBLIC_BASE_URL` — for Twilio webhook signature verification when behind a proxy

See `.env.example` for the full list.

Per-device runtime state (NEVER checked into git):

- `practice_profile.json` — business config from the welcome wizard
- `chat_history.json` — in-product chat log
- `configured.flag` — marks the wizard as completed
- `email_tokens.json` — OAuth refresh tokens
- `vapid_keys.json` — generated on first push subscription
- `*.db` — SQLite

---

## CLI

```bash
PYTHONPATH=src python -m adapix.cli --help
```

Commands:

- `init-db` — create the SQLite schema
- `ingest <csv> --practice <id>` — load customers from a CSV
- `start-campaigns --practice <id> --workflow <id>` — kick off campaigns for eligible customers
- `run --practice <id> --workflow <id> [--dry-run]` — compose and send (or queue) the next due messages
- `simulate-inbound --from <phone> --body <text>` — test the inbound pipeline without Twilio
- `pending-approvals` — list messages queued for human approval
- `approve <id>` / `reject <id> --reason <text>` — work the queue

---

## Testing

The smoke test:

```bash
python demo.py
```

Composes one message end-to-end without writing to the DB or sending anything. Validates the agent can talk to Claude and that the channel adapters work in dry-run.

For full functionality testing, run the FastAPI app locally (`uvicorn adapix.api.main:app --port 8000`) and click through the welcome wizard.
