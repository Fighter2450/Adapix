# Adapix — Next Steps

> **Status (2026-06-30):** The core engine is **verified working end-to-end** — the
> full pipeline (ingest → campaign → AI compose → human approval queue) was proven
> via the CLI on a throwaway DB; it produces context-aware, on-brand drafts against
> the live Anthropic key (`claude-sonnet-4-6`). The **only broken link is delivery**:
> Twilio + Resend are placeholder credentials, so a live send returns `failed`. The
> **AI voice-calling foundation is now built** (Vapi adapter + webhook + `test-call`,
> dry-run verified) and is the priority channel. Fix real accounts + finish the
> calling flow before billing/deploy.

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

**To go live:** create a Vapi account, buy a number, set `VAPI_API_KEY` +
`VAPI_PHONE_NUMBER_ID` in `.env`, then `test-call --to +1… --live`.

**Still to build:**
- ✅ **Approve/Inbox integration (done)** — a call is a `channel="call"` item in the same approval queue: `queue-call` creates it, it shows in the Inbox with a "Call plan" pill, and approving it routes through `ApprovalManager._send_one` → `VoiceChannel.place_call` (builds the assistant prompt from the approved plan + contact context). Verified via CLI in dry-run.
- **Transcript → action** — the `/webhooks/vapi` handler logs the end-of-call transcript/summary; next is classifying the outcome (booked / escalate / not interested), attaching it to the contact, and firing escalations like inbound SMS.
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
