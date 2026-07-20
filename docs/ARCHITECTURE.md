# Adapix Architecture

_Last updated: 2026-07-20. If you change how a subsystem works, update this file._

## What Adapix is today

A **multi-tenant cloud SaaS** at **app.adapixai.com**: an AI follow-up
assistant for small service businesses (plumbers, contractors, salons,
detailers, dentists — anyone who quotes jobs and loses the follow-up). Each
**Organization** signs up, connects its channels, imports its contacts, and
Adapix drafts follow-up texts, emails, and calls. **Nothing sends without the
owner's approval** (the trust moat) unless they opt a workflow into auto-mode.

> Origin note: this began as a single-tenant, dental-specific "adaptive
> operator" (and briefly a Raspberry-Pi appliance). It is now a general
> small-business cloud SaaS. Some model fields still carry legacy names —
> `Patient` = a contact, `practice_id` = the org id, `treatment_type` = the
> job/quote. Don't let the names mislead you; the product is horizontal.

## Runtime shape

```
  adapixai.com (Vercel, static)         app.adapixai.com (Railway, FastAPI)
  marketing site — website/             the product — src/adapix/
        │  "Start free trial"                    │
        └───────────► signup ───────────────────┤
                                                 ▼
                    ┌──────────────────────────────────────────────┐
                    │  FastAPI app (src/adapix/api/)                │
                    │   • auth (JWT cookie, per-IP throttle)        │
                    │   • dashboard SPA (templates/app.html)        │
                    │   • JSON API (app_routes.py)                  │
                    │   • webhooks (Twilio/Vapi/Blooio/Stripe)      │
                    │   • 6 background asyncio loops (main.py)      │
                    └──────────────────────────────────────────────┘
                          │              │               │
                          ▼              ▼               ▼
                   ┌────────────┐  ┌───────────┐  ┌──────────────┐
                   │  Postgres  │  │  Channels │  │  Volume /data│
                   │ (prod)     │  │ SMS/email │  │  JSON state, │
                   │ SQLite(dev)│  │ voice/RCS │  │  backups     │
                   └────────────┘  └───────────┘  └──────────────┘
```

Deploy: `railway up` for the app (`src/`, templates); a push to `main`
auto-deploys `website/` to Vercel. Single replica only — see "Operational
constraints".

## The message engine (the core)

1. **Campaigns** (`campaign.py`) — one row per (contact, workflow). The
   campaign loop picks up due steps, calls the **agent** (`agent.py`, which
   composes one message from workflow + business profile + contact + step),
   and either drafts it for approval or, in auto-mode, sends it.
2. **Approval** (`approval.py`) — drafts wait as `pending_approval`; the owner
   approves and it sends. `send_approved()` also fires scheduled sends. Quiet
   hours (8am–9pm ET) and per-contact caps apply here.
3. **Outbound transport** (`outbound.py`) — the single source of truth for
   *how* a text leaves: the org's own **Blooio** line first (blue-bubble
   iMessage; Blooio does RCS/SMS fallback), then the shared **Claw** line,
   then **Twilio** SMS. Every send path (approval, auto-mode, inbound reply,
   escalation reply) routes through this so replies always come from the
   number the customer texted.
4. **Inbound** (`inbound.py` + `api/webhooks.py` + `blooio_poll.py`) — an
   inbound text is deduped by `provider_id`, STOP/opt-out is honored *before*
   any AI call, abuse guards run, then it's classified (`escalation.py`) and
   either auto-answered (`agent.respond_to_inbound`), escalated (emergency /
   callback / clinical), or opted-out. Blooio inbound arrives via webhook AND
   a 2-min poller (their webhooks were unreliable); the two dedupe against
   each other.
5. **Voice** (`channels/voice.py`, Vapi) — calls place from the org's own
   number; `end-of-call-report` webhook stores the transcript, classifies the
   outcome, and drafts a **missed-call text-back** if the call never connected.

## Data model (`models.py`)

- `Organization` — a subscribing business. Its id is `practice_id` on every
  data row. Holds channel ids (Twilio/Vapi/Blooio), phone tier, CNAM/RCS
  status, and the referral code.
- `User` — a human login (JWT cookie). `role`: owner/admin/member.
- `OrgProfile` / `EmailConnection` — the business-knowledge blob and per-org
  Gmail/Outlook OAuth.
- `Patient` — a **contact** (legacy name). Carries `phone`/`email`,
  `opted_out`, `memory_json` (per-contact learned facts), and the job/quote.
- `Campaign` — one (contact, workflow) follow-up sequence + its status.
- `Message` — every inbound/outbound message; `provider_id` (dedupe),
  `metadata_json` (transport, errors), `status`.
- `EscalationEvent` — a reply that needs a human (callback, emergency, etc.).
- `Automation` / `AutomationRun` — scheduled cron-style jobs.

## Subsystems (`src/adapix/`)

| Area | Modules |
|------|---------|
| Engine | `agent.py` `campaign.py` `approval.py` `inbound.py` `outbound.py` `escalation.py` |
| Channels | `channels/` (sms/email/voice/imessage/claw), `blooio_poll.py`, `provisioning.py` |
| Memory & knowledge | `memory.py` (org-level), `patient_memory.py` (per-contact), `practice.py`, `website_import.py` |
| Billing | `billing.py` (Stripe: checkout, trials, referrals, add-ons, reconcile) |
| Compliance | `cnam.py` (caller-ID name), `rcs.py` (branded texting) — built, gated on real LLC |
| Growth | referrals (in `billing.py`+`app_routes.py`), `weekly_email.py`, `digest.py`, `missed_call.py` |
| Safety/ops | `ai_guard.py` (throttles+budget), `ops_alert.py` (error bursts→founder email), `backup.py` (nightly DB dump), `rawlog.py` (capped raw logs) |
| Platform | `db.py` `config.py` `phone.py` `notifications.py` (web push), `automations.py`, `team_agents.py` |

## Background loops (`api/main.py`, single process)

- **campaign** (5 min) — run due follow-up steps
- **automation** (5 min) — run due cron automations
- **digest** (hourly) — daily push + weekly money email + billing reconcile + nightly backup
- **scheduled-send** (2 min) — dispatch due approved/scheduled messages
- **blooio poll** (2 min) — pull inbound texts as a webhook safety net

## Integrations

Anthropic (Claude), Twilio (SMS + A2P), Vapi (voice), Blooio (iMessage/RCS),
Claw (shared iMessage), Resend + Gmail/Outlook OAuth (email), Stripe (billing),
Web Push (VAPID). Keys live in Railway env / `.env` (gitignored).

## Security & tenancy

- Every data route filters by the authenticated `org_id` (`verify_admin`).
  Founder-only tooling (expenses) is gated by `require_founder`.
- Webhooks fail **closed**: HMAC signatures on Twilio/Vapi/Blooio/Stripe;
  Stripe events are idempotency-deduped.
- JWT cookie is `Secure`+`HttpOnly`; login/signup/reset are per-IP throttled;
  password reset uses a single-use token signed over the current hash.
- SSRF guard on website-import; abuse guards cap AI spend per org per day.

## Operational constraints (read before scaling)

- **Single Railway replica only** (`railway.toml` pins it). The loops and the
  in-memory dedupe/rate-limit state assume one process; two would double-send.
  Scale vertically until the loops use a DB lease and state moves to the DB.
- Some state is JSON files on the `/data` volume (`billing.json`,
  `ai_usage.json`, `stripe_events.json`) — atomic-write + locked, but on the
  same volume as the DB backups (no off-site copy yet).

## Intentionally not built yet

Off-site backups; per-org replicas / horizontal scale; a full password-reset
audit trail with session revocation; PMS integrations; the (cut) hardware
appliance and AI-Team/Workshop surfaces.
