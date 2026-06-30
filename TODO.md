# Adapix — Next Steps

> **Status (2026-06-30):** The core AI engine is **verified working** — it composes
> context-aware, on-brand follow-up drafts against the live Anthropic key
> (`claude-sonnet-4-6`, confirmed via `demo.py`). The real gap is **delivery**:
> Twilio + Resend are placeholder credentials, so Adapix can *think and draft* but
> can't yet *send or receive*. Fix sending before touching billing/deploy.

---

## 🔴 Blockers — "does it actually work for a customer?" (do these FIRST)

### 1. Real sending credentials
- Twilio is placeholder: `ACCOUNT_SID` 5 chars (real = 34, starts `AC`), `AUTH_TOKEN` 3 chars (real = 32), `FROM_NUMBER` 5 chars → **SMS cannot send**.
- Resend is placeholder: `RESEND_API_KEY` 6 chars (real ≈ 30+, starts `re_`) → **email cannot send**.
- Action: get real Twilio + Resend accounts, drop real keys in `.env`, send one real test SMS + email to your own phone/inbox.

### 2. Verify the full loop end-to-end
- add contact → start campaign → AI draft lands in Inbox → approve → **actually delivers**.
- The compose step is verified in isolation (`demo.py`). The web pipeline wiring (campaign runner → pending approval → inbox feed → send) is **not yet proven** end to end.

### 3. Inbound replies + escalation
- `PUBLIC_BASE_URL` is empty; no public webhook is reachable.
- Action: set a public URL + Twilio inbound webhook so customer replies come back and escalations fire.

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
