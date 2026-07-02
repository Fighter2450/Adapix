# Adapix — Next Steps

## 🚀 DEPLOYED TO RAILWAY (2026-07-02)

**Live at: https://adapix-web-production.up.railway.app** — permanent URL, Postgres-backed.

- Project `adapix` (service `adapix-web` + Postgres) on ChenetTech's Railway.
  Made room by deleting the old WebsiteBot project (user's call; NEXUS kept).
- All env vars pushed from local `.env`; `DATABASE_URL` references the Railway
  Postgres; a real `JWT_SECRET_KEY` was generated (it had been silently running
  on the dev fallback — never set despite older notes claiming it was).
- Build fixes: the hand-rolled nixpacks package list broke twice (bad
  `libxshmfence` name, then bare nix python312 has no pip). Now uses the
  nixpacks Python provider + `.python-version` = 3.12. The Playwright/Chromium
  stack for Workshop automations is intentionally NOT in the prod image.
- External URLs updated to the permanent domain (never need touching again):
  Google OAuth redirect URI (added in console) and the Twilio inbound-SMS
  webhook (set via API).
- Gotcha fixed along the way: appending to `.env` from Windows Python wrote a
  cp1252 em-dash that corrupted the file for UTF-8 readers (Settings() crashed
  everywhere). Keep `.env` ASCII-only.

**Prod starts with a FRESH database** — local test data stays local. First-run
on prod: sign up, connect Gmail (against the permanent redirect URI this time),
set up the calling line, import contacts.

**Post-deploy TODO:**
- Live smoke test on prod: signup → connect Gmail → send a draft → place a call.
- Point a custom domain (app.adapixai.com) at the Railway service eventually.
- Vapi call webhooks + Twilio inbound now resolve to the permanent URL via
  PUBLIC_BASE_URL — do a live inbound-SMS + end-of-call-report test on prod.


> **Status (2026-07-02): IT WORKS — every channel live-verified end to end.**
> - **Calls (Vapi):** real AI calls from the org's own number, recordings playable
>   in the dashboard, transcripts classified into Inbox escalations.
> - **SMS (Twilio):** real text delivered to a real cell (SM41f6…). Fixed two
>   .env bugs found during the test: RESEND_FROM_EMAIL pointed at adapix.com
>   (verified domain is adapixai.com) and TWILIO_FROM_NUMBER had spaces.
> - **Email, as-the-business (Gmail OAuth):** draft #68 approved through the
>   real API → delivered with Gmail message id 19f234a41921dbc5 — the dispatch
>   correctly preferred the org's connected Gmail over the shared sender.
> - **Email, fallback (Resend):** draft #67 sent via the real Inbox Send button
>   → delivered from hello@adapixai.com when no Gmail was connected. The
>   fallback chain behaved exactly as designed.
> - **Full product loop proven in the UI:** contact → draft → Send click in the
>   Inbox → delivery.
>
> **Fix that unblocked reconnects:** the OAuth callback used to require the
> session cookie, but it lands on PUBLIC_BASE_URL's origin (tunnel) while dev
> logins live on localhost — cookies don't cross origins, so connects silently
> died with "Not authenticated". Now /oauth/*/start embeds org_id in the
> server-side one-time state record and the callback needs no session.
>
> **Dev-session-only setup, not persistent:** the local server + Cloudflare
> quick tunnel die when the shell/session ends and get restarted with a NEW
> trycloudflare.com URL each time (PUBLIC_BASE_URL in .env is updated to
> match). Every tunnel rotation breaks two externally-registered URLs until
> updated: the Google OAuth redirect URI (Google Cloud Console → adapix
> client) and the Twilio inbound-SMS webhook. ALSO: uvicorn --reload sometimes
> misses file changes AND orphaned servers pile up on :8000 serving stale code
> — if something "doesn't work" after an edit, check
> `netstat -ano | findstr :8000` first. Railway deploy kills this whole class
> of problem and is the clear next step.
---

## ✅ Former blockers — ALL CLEARED 2026-07-02 (see status header)

1. ✅ **Real sending credentials** — real Twilio + Resend keys in `.env`; live SMS and email both delivered.
2. ✅ **Full loop through the web UI** — contact → draft → Inbox Send click → real delivery (Gmail id `19f234a41921dbc5`; Resend fallback also proven).
3. 🟡 **Inbound replies** — `PUBLIC_BASE_URL` points at the current tunnel and the Vapi call webhook works end to end; the Twilio inbound-SMS webhook is registered but breaks on every tunnel rotation (see header). Truly solved by Railway's permanent URL; a live inbound-SMS reply test is still to do after deploy.

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
- ✅ **Calls surfaced in the dashboard (done)** — Inbox escalations triggered by a call show a phone icon + the AI's plain-English call summary (not the raw transcript); the Results activity feed gives calls their own kinds/icons (`call_placed`, `call_outcome`, `call_failed`) instead of generic sent/reply labels. Verified against real capstone-test data.
- **Web approve button** — the Inbox *shows* pending items (incl. calls) but the approve/reject buttons aren't wired in the web UI yet for ANY channel; the approve endpoints exist (`POST /api/v1/approvals/{id}/approve|reject`). Wire them.
- **Compose a richer call plan** — `queue-call` stores the raw goal as the plan; have the agent draft a structured plan (goal + talking points) for the human to review.
- **Consent gating** — only call contacts who opted in (AI disclosure is done; opt-in isn't).
- **Verify** Vapi's Claude model id/provider naming against their supported list.

---

## 📧 Per-org email (OAuth Gmail/Outlook) — same "connect your own" model as calling

**Decided (mirrors calling):** never a shared Adapix sender — each business
connects its own Gmail or Microsoft 365 inbox via OAuth, and the OAuth login
itself is the ownership proof. Adapix then sends follow-ups **as them**
(their real from-address), falling back to the shared Resend sender only if
the org hasn't connected anything.

**✅ Built this session (2026-07-01):**
- **Per-org token storage (done)** — new `email_connections` table (`src/adapix/models.py`), keyed by `org_id`, replaces the old single flat-file `email_tokens.json`. `src/adapix/oauth.py`'s `load_tokens`/`save_tokens`/`get_provider`/`disconnect`/`google_access_token`/`microsoft_access_token`/`google_send`/`microsoft_send`/`send_email`/`status` all now take `org_id` and read/write that org's row.
- **OAuth routes (done)** — `GET /oauth/google/start`, `/oauth/google/callback`, `/oauth/microsoft/start`, `/oauth/microsoft/callback` in `src/adapix/api/app_routes.py`, all behind `verify_admin` (session cookie identifies the org on both the redirect-out and the callback — no extra CSRF header needed beyond the existing `state` nonce). Callback exchanges the code, pulls the real connected email (Google `userinfo`, Microsoft Graph `/me`), stores it per-org, redirects to `/app?tab=settings`.
- **Status/disconnect endpoints (done)** — `GET /api/v1/email/status` (mirrors `GET /api/v1/phone`: `configured`/`connected`/`provider`/`email`), `POST /api/v1/email/disconnect`.
- **Send-as-the-org wiring (done)** — `ApprovalManager._send_one` in `src/adapix/approval.py` now checks `oauth.is_connected(org_id)` for `channel == "email"`; if connected, sends via `oauth.send_email(org_id, ...)` as the business; otherwise falls back to the existing Resend `EmailChannel` (exact same fallback shape as the calling-number pattern).
- **Settings UI (done)** — new "Your email connection" card in Messaging & channels (`src/adapix/api/templates/app.html`, `loadEmailCard()`), mirroring `loadPhoneCard()`: shows "Connect Gmail" / "Connect Outlook" links (plain `<a href>` full-page nav, not fetch, since OAuth requires leaving the page) when not connected, or "Connected as {email}" + Disconnect button when connected. Shows "Email connect isn't set up yet" if neither `GOOGLE_CLIENT_ID` nor `MICROSOFT_CLIENT_ID` is configured (same graceful-degrade pattern as the Vapi phone card).
- Verified against a throwaway SQLite DB: table creation, save/load/disconnect round-trip, `/api/v1/email/status` + `/api/v1/email/disconnect` via `TestClient`, `/oauth/google/start` + `/oauth/microsoft/start` returning a clean 503 when unconfigured, and `ApprovalManager.send_approved` exercising both the OAuth-connected branch (attempts a real token refresh, fails cleanly with no real client ID configured — expected) and the no-connection fallback branch (goes to the existing dry-run Resend path).

**✅ Gmail — fully live and verified 2026-07-01:**
- Real Google Cloud OAuth client (`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` in `.env`), redirect URI registered on the tunnel domain.
- **Gotcha that cost a debugging round-trip:** the OAuth consent screen's "Scopes" step was originally skipped (assumed the code's own scope request in the auth URL was enough) — it is NOT. Google silently issues a token missing the scope if it isn't explicitly declared under **Data Access → Add or remove scopes** on the OAuth consent screen, even though the requested scope shows correctly in the auth URL. First real send attempt failed with `ACCESS_TOKEN_SCOPE_INSUFFICIENT`. Fix: add `https://www.googleapis.com/auth/gmail.send` under Data Access, then **disconnect + reconnect** (existing tokens don't retroactively gain the scope).
- Live click-through done end-to-end: connected `roccochenet95@gmail.com` via the real consent screen, "Connected as roccochenet95@gmail.com" rendered correctly in Settings, `POST /api/v1/email/test` returned `{"ok": true, "provider": "google", "provider_id": "19f20c8393d2d347"}` — a real Gmail message ID.
- The old May-30 client secret (never rotated, unusable since Google only shows secrets once) was replaced with a fresh one; the old one is still enabled on Google's side and can be deleted once the new one's been running a while without issue.

**✅ SMTP — the long-tail connector (built 2026-07-01):**
- Why: Gmail/Outlook OAuth covers ~85-90% of business email (custom domains hosted on Workspace/M365 use the same buttons), but iCloud/Yahoo/AOL/Zoho/etc. need a universal fallback. One generic SMTP connection with an app-specific password covers all of them — no per-provider integrations, ever.
- `provider="smtp"` on the same `email_connections` row (new `smtp_host`/`smtp_port`/`smtp_password` columns, additive migration applied to the live DB). `oauth.py` gains `SMTP_PRESETS` (iCloud/Yahoo/AOL/Verizon→AOL/Comcast/AT&T/Zoho/Fastmail + graceful `smtp.<domain>` fallback), `detect_smtp_settings`, `verify_smtp` (logs in before saving — a bad app password fails at connect time with a plain-English error, not on the first customer send), `save_smtp_connection`, `smtp_send` (465 = implicit TLS, else STARTTLS). `send_email` dispatch + `status`/`is_connected` now cover all three providers, so `ApprovalManager`'s email branch works unchanged.
- API: `GET /api/v1/email/smtp/detect` (prefills server/port from the address), `POST /api/v1/email/smtp/connect`. `GET /api/v1/email/status` now always reports `configured: true` (SMTP needs no app credentials) plus `oauth_configured` for the Gmail/Outlook buttons.
- Settings card: "Another email (iCloud, Yahoo…)" button expands an inline form — email, app password, auto-detected server/port (editable). Disconnect works the same for all providers.
- Verified on a throwaway DB: preset + fallback detection, storage round-trip, status/is_connected, clean error on unreachable host, disconnect. Endpoints registered on the live server (401 when unauthenticated). **Not yet live-tested against a real provider** — the natural test is an iCloud/Yahoo account with an app-specific password.
- Hardening note (applies to OAuth tokens too): SMTP passwords are stored plaintext in the DB; at-rest encryption is a pre-launch item.

**Microsoft/Outlook — explicitly deferred:**
- Signing in to Azure Portal with a personal (non-tenant) Microsoft account shows "no directory" — Microsoft deprecated creating App Registrations outside a directory. Getting one needs either the free M365 Developer Program (a sandbox tenant, no card needed) or a paid Azure subscription. User hit a paywall partway through and chose to defer Microsoft rather than pay — decision made 2026-07-01.
- Code fully supports it already (`MICROSOFT_CLIENT_ID`/`SECRET`, `/oauth/microsoft/start|callback`, the Settings "Connect Outlook" button) — zero rework needed, just drop in real Azure credentials whenever there's a real tenant.
- Disconnect currently allows reconnecting with a different provider (Google → Microsoft) by just running the other flow again — confirm that's the desired UX or add an explicit "switch provider" confirmation.

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
