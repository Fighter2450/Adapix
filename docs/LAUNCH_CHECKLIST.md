# Adapix Launch Checklist

_The product is built and hardened. Launch is gated almost entirely on
business/legal steps only Rocco (and Dad, as account holder) can do. This is
the runbook, in order._

Last updated: 2026-07-21.

---

## The one-line truth

A stranger can sign up, get routed through the setup wizard, and use the whole
dashboard **today** — but they **cannot pay**, because Stripe is in test mode,
and Stripe can't go live without a real business entity. Everything below is
about crossing that gap. The code is ready; the paperwork isn't.

---

## GATE 1 — Form the business (blocks everything else)

- [ ] **Form Chenet Tech LLC** (or your chosen entity). This is the keystone —
      nothing downstream works without it.
- [ ] **Get an EIN** (IRS, free, ~15 min online once the LLC exists).
- [ ] **Open a business bank account** for the LLC (needed for Stripe payouts).

Until this gate is done, the rest cannot proceed. Everything after here takes
hours, not days — this is the long pole.

## GATE 2 — Turn on real payments (Stripe)

- [ ] **Activate the Stripe account** with the LLC's legal details, EIN, and
      bank account (Stripe dashboard → "Activate account"). Note: the account
      holder is Dad — this must be done under his identity + the LLC.
- [ ] **Recreate the products/prices in LIVE mode.** Test-mode price ids
      (`price_..._test`) don't exist in live. Recreate the $99/mo plan (and the
      $1.50/mo dedicated-line add-on) in live mode, and the FOUNDING promo code.
- [ ] **Swap the Railway env vars to live values:**
  - `STRIPE_SECRET_KEY` → `sk_live_...`
  - `STRIPE_PRICE_ID` → the live $99 price id
  - `STRIPE_DEDICATED_LINE_PRICE_ID` → the live add-on price id
  - `STRIPE_WEBHOOK_SECRET` → the signing secret of a **new live-mode webhook
    endpoint** pointing at `https://app.adapixai.com/webhooks/stripe`
      (events: `checkout.session.completed`, `customer.subscription.*`,
      `invoice.paid`, `invoice.payment_failed`).
- [ ] **Do a real end-to-end payment test** with your own real card: sign up a
      throwaway business → complete checkout → confirm the trial starts, the
      engine turns on, and (see Gate 3) a calling number provisions. Then
      cancel it.

> The billing engine itself is already hardened for live mode: event-id dedup,
> idempotency keys, referral credit only on real paid invoices, hourly
> reconciliation, duplicate-checkout refused, one-trial-per-card. No code
> changes needed here — just the account activation + key swap.

## GATE 3 — Telephony / deliverability

- [ ] **Re-file A2P 10DLC under the LLC.** The current registration was filed
      under "Dad, sole prop." Carriers need the registered brand to match the
      business actually sending — re-register the brand + campaign under
      Chenet Tech LLC so texts deliver instead of getting filtered.
- [ ] **Confirm calling-number provisioning works live.** `AUTO_PROVISION_NUMBERS`
      is now `true`, and provisioning fires when a trial starts (card on file).
      During the Gate-2 payment test, confirm the new org actually got a
      working number (check Settings → the calling line shows a real number).
- [ ] **(Optional, later) CNAM + RCS.** Business-name-on-caller-ID and branded
      texting are built but need the real LLC details submitted for carrier
      review (days, not instant). Not required to launch — ship without them
      and add later. The in-app forms are ready.

## GATE 4 — Pre-launch smoke test (do this as the final step)

Run through as a brand-new business, start to finish:

- [ ] Sign up → land in the setup wizard → complete it → reach the dashboard.
- [ ] Complete checkout with a real card → trial starts → engine is on.
- [ ] A calling number provisions (Settings → calling line).
- [ ] Import 2–3 contacts → Adapix drafts follow-ups → they wait in the Inbox.
- [ ] Approve one → it actually sends to a real phone → **you receive it.**
- [ ] Reply from that phone → the reply lands in the Inbox / conversation.
- [ ] Mark a job Won → the won-back counter updates.
- [ ] Cancel the throwaway subscription and delete the test data.

If all of that works with real money and real phones, you're live.

---

## Already done (no action needed)

- Product: signup, setup wizard, dashboard, contacts, drafting, approve-before-
  send, inbound handling, analytics, referrals, password reset — all working;
  empty-state and onboarding flow verified end-to-end 2026-07-21.
- Security: tenant isolation, webhook signatures fail-closed, SSRF guard,
  login/reset rate-limiting, Secure cookies, founder-only endpoints locked.
- Reliability: nightly DB backups, founder error-alert emails, AI-abuse
  budget/throttles, single-replica pin.
- Billing engine: hardened for live mode (see Gate 2 note).
- Vendor names scrubbed from all customer-facing surfaces.

## Known limitations to launch WITH (not blockers)

- **Blooio texting** is on a shared line (5 new contacts/day cap); the org's
  own dedicated line + CNAM/RCS come later. SMS via the A2P number is the
  reliable path at launch.
- **AI-Team / document generation** is cut from the nav — don't market it.
- A couple of medium messaging edge cases remain (a narrow approve-vs-sweep
  double-send window; shared-contact-across-two-orgs routing) — low frequency,
  documented on the founder board, safe to launch with.
- Backups live on the same volume as the DB — fine for launch; add an off-site
  copy (S3) when convenient.

## The honest bottom line

Code readiness: **high.** The blocker is **business formation**, and it
cascades: LLC → Stripe live → A2P → real launch. That path is ~a few days of
paperwork and waiting, not engineering. The day the LLC exists, launch is a
key-swap and a smoke test away.
