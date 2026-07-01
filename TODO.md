# Adapix — Next Steps

> **Status (2026-07-01):** **Calling works end-to-end, live, for real** —
> capstone test: `queue-call` → approve → AI called a real cell from Adapix's
> own number, had a real conversation, and the transcript flowed back through a
> Cloudflare tunnel to `/webhooks/vapi`, got classified, and created a real
> `callback_request` escalation in the Inbox. Every link in the calling chain is
> proven. **Texting/email are the opposite** — the pipeline is proven (ingest →
> campaign → AI compose → approval queue) but Twilio + Resend are still
> placeholder creds, so a live send returns `failed`. Fix those next, or move to
> deploy/billing now that the flagship channel (calling) is solid.
>
> **Dev-session-only setup, not persistent:** the local server + Cloudflare
> quick tunnel were restarted mid-session (they die when the shell/session
> ends). `PUBLIC_BASE_URL` in `.env` currently points at a temporary
> `trycloudflare.com` URL that changes every restart — fine for testing, but
> Railway deploy gives a permanent one.

---

## 🔴 Blockers — "does it actually work for a customer?" (do these FIRST)

### 1. Real sending credentials
- Twilio is placeholder: `ACCOUNT_SID` 5 chars (real = 34, starts `AC`), `AUTH_TOKEN` 3 chars (real = 32), `FROM_NUMBER` 5 chars → **SMS cannot send**.
- Resend is placeholder: `RESEND_API_KEY` 6 chars (real ≈ 30+, starts `re_`) → **email cannot send**.
- Action: get real Twilio + Resend accounts, drop real keys in `.env`, send one real test SMS + email to your own phone/inbox.

### 2. ✅ Full loop verified (CLI) — one link left: actual send
- Proven 2026-06-30: ingest → start-campaign → run (AI composes day-1 SMS + day-3 email) → both land in the **pending-approval queue** → approve. Only the final **send fails** on the placeholder Twilio creds (blocker #1). No code bug.
- Still TODO: prove the same loop through the **web UI** (Inbox feed → approve button → send), not just the CLI.

### 3. Inbound replies + escalation
- `PUBLIC_BASE_URL` is empty; no public webhook is reachable.
- Action: set a public URL + Twilio inbound webhook so customer replies come back and escalations fire.

---

## 🎙️ Calling (Vapi) — the priority channel

**Built (dry-run verified):** `channels/voice.py` (Vapi `POST /call/phone` adapter,
AI-disclosure + recording notice baked into every opening for TCPA/state-law
compliance), `POST /webhooks/vapi` (receives end-of-call transcript + summary),
`test-call` CLI command (voice analog of `demo.py`), config + `.env.example`.

**✅ Live-call verified 2026-06-30** — placed a real AI call to a cell via Vapi;
"sounded perfect." (Cloudflare 403 fixed with a real User-Agent; voice defaults
to Vapi's when none set.)

**Onboarding model — how businesses get a calling number (decided):**
Never a shared Adapix number. **Each business calls from its OWN dedicated
number** (its caller ID), so customers see the business they know. Default path:
**Adapix provisions + registers a local number per org at signup** — the business
touches no telephony. "Bring your own number" (port / verified caller ID) is an
advanced option, not the default. Whoever owns the number, Adapix manages its
reputation (Free Caller Registry, CNAM, STIR/SHAKEN) as a platform service.

**Pre-launch calling checklist (kill the "Spam Likely" label):**
1. Register the number at freecallerregistry.com (free; clears in 24–72h).
2. For production, use a **Twilio** number (A-level STIR/SHAKEN) imported into Vapi.
3. Register **CNAM** so it shows the business name.
4. Branded calling (Hiya/First Orion) later, for max answer rates.

**Still to build:**
- ✅ **Per-org calling number (done)** — `Organization` now stores `vapi_phone_number_id` / `phone_number` / `phone_status`; `ApprovalManager` looks up the calling org and places the call from **its** number, announcing **its** name (falls back to the global test number/name for the single-tenant CLI). Manual assign via `set-org-number` / `list-orgs`. Verified in dry-run. Additive DB migration in `init_db`.
- ✅ **Auto-provision at signup (done)** — signup schedules a background task (`ensure_org_number`) that buys a free Vapi number for the new org and stores it. Gated by `AUTO_PROVISION_NUMBERS` (off in dev so test signups don't each buy a number; on for production). On-demand path also works via the Settings button + `POST /api/v1/phone/provision`.
- ✅ **Settings "Your calling number" card (done)** — Messaging & channels settings shows the org's number + status, or a "Set up my calling line" button that provisions on demand (`GET /api/v1/phone`). Links to Free Caller Registry to clear the spam label.
- **Production numbers** — free Vapi numbers are limited per account and give weaker attestation; swap `create_vapi_number` for a Twilio buy + import (A-level STIR/SHAKEN + CNAM) before scaling to many businesses.
- **Auto-register** — wire Free Caller Registry / CNAM into provisioning so numbers aren't flagged as spam.
- ✅ **Approve/Inbox integration (done)** — a call is a `channel="call"` item in the same approval queue: `queue-call` creates it, it shows in the Inbox with a "Call plan" pill, and approving it routes through `ApprovalManager._send_one` → `VoiceChannel.place_call` (builds the assistant prompt from the approved plan + contact context). Verified via CLI in dry-run.
- ✅ **Transcript → action (done)** — the `/webhooks/vapi` end-of-call-report now runs `InboundProcessor.process_call_outcome`: logs the transcript on the contact, classifies it with the same engine as inbound SMS, and raises an `EscalationEvent` into the Inbox when it needs a human. The call carries `metadata` (patient/campaign/org) so the outcome links back. Verified end-to-end (a "call me back" transcript → `callback_request` escalation). Needs a public URL (`PUBLIC_BASE_URL` + ngrok/Railway) for Vapi to actually deliver the report.
- **Web approve button** — the Inbox *shows* pending items (incl. calls) but the approve/reject buttons aren't wired in the web UI yet for ANY channel; the approve endpoints exist (`POST /api/v1/approvals/{id}/approve|reject`). Wire them.
- **Compose a richer call plan** — `queue-call` stores the raw goal as the plan; have the agent draft a structured plan (goal + talking points) for the human to review.
- **Consent gating** — only call contacts who opted in (AI disclosure is done; opt-in isn't).
- **Verify** Vapi's Claude model id/provider naming against their supported list.

---

## 🟡 SaaS pieces (only matter once it can send)

### 4. Deploy to Railway
- SQLite → PostgreSQL (`DATABASE_URL` change).
- Set `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, real Twilio + Resend creds in Railway env.
- `railway up`.

### 5. Billing — Stripe
- Stripe Checkout for trial → paid conversion.
- Webhook handler (paid, cancelled, past_due).
- Plan enforcement: block sends if trial expired and no active subscription.
- `stripe_customer_id` / `stripe_subscription_id` already on `Organization`.

### 6. Password reset flow
- `/forgot-password` link exists on login page but goes nowhere.
- Add `POST /auth/forgot-password` → reset email via Resend; add `GET /auth/reset-password?token=...` page + handler.

### 7. Landing page (`website/index.html`)
- Confirm real pricing — **Growth tier $99/mo and message limits (150 / 750) are placeholders**.
- Confirm the `/signup` link target, then deploy to adapixai.com.

---

## ✅ Verified working
- **AI message composition** — context-aware, on-brand drafts. Live Anthropic key, `claude-sonnet-4-6`, confirmed `demo.py`.
- Web server runs; signup/login (JWT); single-contact add endpoint (`POST /api/v1/contacts/add`).

## ✅ Done this session (2026-06-30)
- **Pipeline test** — verified the whole engine end-to-end via CLI (compose → approval queue works; only send fails on fake creds).
- **AI voice-calling foundation** — Vapi adapter with baked-in AI disclosure, outcome webhook, `test-call` CLI, config + `.env.example` (dry-run verified).
- **Dashboard redesign** — dark neon vibrant UI + animation system (`app.html`).
- **Landing page rebuilt for SaaS reality** — removed all hardware copy (Adapt 1.0, $799 device, Raspberry Pi specs, "ships Q3 2026"); now free-trial + 3-tier SaaS framing; dark neon vibrant; deploy-proof inline logo.
- **Add-a-contact form** — add one customer without a CSV; pick Lead / Customer / Saved so buyers aren't treated as leads.
- **Plain-English copy pass** — de-jargoned Inbox / Results / Team (no more "flagged", "attributed", "escalations").
- **De-dental pass** — removed dental wording across the dashboard (Lead/Customer statuses, friendly escalation labels, generalized stats); deleted dead `renderHome`/`renderQueue`.

## ✅ Earlier fixes
- Practice profile → per-tenant DB (`org_profiles`, wizard saves/loads per `org_id`).
- Campaign runner → org-aware (`run_all_campaigns` queries `organizations JOIN org_profiles`).
- `JWT_SECRET_KEY` set in `.env` (random 64-char hex).
- Browser automations → `async_playwright`, pinned `greenlet<3.4.0`.
- Chat business-agnostic (removed dental/medical language; real Adapix value props).
- Chat aware of Workshop automations feature.
- Signup 500 — passlib/bcrypt 4.x incompatibility → direct bcrypt calls.
- `fetch` URL parse error — `document.baseURI` fix.
- JWT auth replaces HTTP Basic — `User` + `Organization` models, tenant-scoped routes.
- CSV contact import — drag-and-drop, preview, success screen.
- Login + Signup pages — two-column, password toggle, accessible.
- Revenue calculator — public `/calculator` ROI page.
