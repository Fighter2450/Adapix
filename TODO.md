# Adapix — Next Steps

## 🟢 Remaining SaaS pieces

### 4. Deploy to Railway
- Switch SQLite → PostgreSQL (one env var change)
- Set `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, Twilio creds in Railway env
- One `railway up` command

### 5. Billing — Stripe
- Stripe Checkout for trial → paid conversion
- Webhook handler for subscription events (paid, cancelled, past_due)
- Plan enforcement: block campaign sends if trial expired and no active subscription
- `stripe_customer_id` and `stripe_subscription_id` already on `Organization` model

### 6. Password reset flow
- `/forgot-password` link exists on login page but goes nowhere
- Add `POST /auth/forgot-password` → send reset email via Resend
- Add `GET /auth/reset-password?token=...` page + `POST` handler

---

## ✅ Fixed

- ✅ Practice profile → per-tenant DB (`org_profiles` table, wizard saves/loads per `org_id`)
- ✅ Campaign runner → org-aware (`run_all_campaigns` queries `organizations JOIN org_profiles`)
- ✅ `JWT_SECRET_KEY` set in `.env` (random 64-char hex)
- ✅ Browser automations — switched to `async_playwright`, pinned `greenlet<3.4.0` to fix DLL error
- ✅ Chat business-agnostic — removed all dental/medical language, added real Adapix value props
- ✅ Chat aware of Workshop automations feature
- ✅ Signup 500 error — passlib incompatible with bcrypt 4.x, replaced with direct bcrypt calls
- ✅ `fetch` URL parse error — `location.origin` returned `"null"` in sandboxed iframe, fixed with `document.baseURI`
- ✅ JWT auth replaces HTTP Basic — `User` + `Organization` models, JWT cookies, all routes tenant-scoped
- ✅ CSV contact import — drag-and-drop web UI, preview mode, success screen
- ✅ Login + Signup pages — two-column layout, password toggle, accessible, loading states
- ✅ Revenue calculator — public `/calculator` page with ROI vs Adapix cost
