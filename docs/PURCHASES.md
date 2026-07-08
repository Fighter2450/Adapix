# Adapix — Purchase & Subscription Checklist

Everything Adapix still needs bought or subscribed to, in priority order.
Already covered: the **adapixai.com** domain and the **Anthropic API** (the AI brain — usage-based, roughly cents per follow-up).

After each purchase, tell Claude — the wiring on the software side (keys, numbers, config, deploys) is handled from there.

---

## 1. ~~Railway Hobby plan~~ — DONE 2026-07-06 ($5/month)

**What it is:** The server Adapix runs on, 24/7.

**Status:** Upgraded 2026-07-06. 24/7 uptime + custom domains unlocked
(app.adapixai.com runs on it).

**Where:** https://railway.com/workspace/plans → **Hobby** → add a payment method.
The $5 includes $5 of usage credits; the app currently fits inside that, so the real
cost is ~$5/month flat.

**After purchase:** Nothing to wire — it just keeps running.

---

## 2. ~~Twilio A2P 10DLC registration~~ — DONE 2026-07-06 (vetting round 2 in progress)

**What it is:** Registers your business with US phone carriers as a legitimate sender
of text messages.

**Status:** Brand APPROVED (sole prop, Dad as account holder). First campaign
submission rejected on CTA verification; resubmitted 2026-07-07 with the public
opt-in page (adapixai.com/sms-optin) — carrier vetting in progress.

**Where:** Twilio Console → Messaging → Regulatory Compliance → US A2P 10DLC.
You'll need: business name, address, and EIN (or sole-proprietor registration if no EIN).
- One-time: ~$4 brand registration + ~$15 campaign vetting
- Ongoing: ~$1.50–4/month campaign fee

**After purchase:** Tell Claude — the sending number gets linked to the registered
campaign, then a live deliverability test.

---

## 3. ~~Blooio iMessage line~~ → DONE via Claw Messenger ($5/month)

**Superseded 2026-07-07:** blue texts went live through Claw Messenger instead —
$5/mo for 250 messages with a dedicated line, vs Blooio's $39/mo. 7-day free
trial running now; first blue bubble confirmed on Rocco's iPhone.

<details><summary>Original Blooio plan (kept for reference)</summary>

## 3. Blooio iMessage line — $39/month (shared) or ~$98/month (dedicated)

**What it is:** Lets Adapix send **blue texts** — real iMessages instead of green SMS.

**Why it matters:** Green texts from unknown numbers read as spam on iPhones; blue
ones read as a real person. Better open rates, better reply rates. The iMessage code
is fully built and tested — it activates the same day the line exists.

**Recommendation:** Start with the **$39/month shared line** to prove it works, upgrade
to dedicated when there are real customers on it.

**Where:** https://blooio.com dashboard → purchase a line. (API key is already
configured in the app.)

**After purchase:** Tell Claude — the channel ID gets fetched and wired to your org,
then the first blue-text test goes out within the hour.

</details>

---

## 4. Dedicated Twilio number + CNAM — ~$1.15/month + small one-time fee

**What it is:** A real phone number owned by the business, with the **company name
showing on caller ID** (CNAM registration).

**Why it matters:** Right now calls come from a free Vapi number and caller ID shows
just digits. With CNAM, the customer's phone says the business name — dramatically
higher answer rates, and it lets the website honestly claim "caller ID shows your
company" again.

**Where:** Twilio Console → Phone Numbers → Buy a Number, then Trust Hub → CNAM.

**After purchase:** Tell Claude — the number gets imported into Vapi (A-level
STIR/SHAKEN attestation) and set as the org's calling line. The code path for this
swap already exists.

---

## Free, but needs an account created (owner action)

### Azure app registration — $0
Unlocks **one-click Outlook/Microsoft 365 connect** for users (Gmail already works).
Where: https://portal.azure.com → App registrations → New. Claude wires the two
credentials into Railway afterward and restores the "Outlook in one click" claim on
the website.

### Stripe — $0/month (~2.9% + 30¢ per transaction)
**Billing.** Needed only when it's time to charge customers for Adapix itself.
No monthly cost — park this until there's a trial user worth converting.

---

## Ongoing usage costs (no action needed)

| Service | Cost | What it does |
|---|---|---|
| Anthropic API | ~cents per follow-up | Writes drafts, answers questions, classifies replies |
| Vapi | ~$0.05–0.15/min of calls | AI phone calls, recordings, transcripts |
| Twilio SMS | ~$0.008/message | Text delivery for non-Apple recipients |
| Claw Messenger | $5/mo (250 msgs) | Blue-bubble iMessage line (20-contact cap — scale decision pending) |
| Resend | Free tier (3k emails/mo) | Backup email sending when no inbox is connected |
| Vercel | Free tier | Hosts adapixai.com (auto-deploys on git push; replaced Netlify 2026-07-07) |

---

## Bottom line

| Spend | What you get |
|---|---|
| **~$16/month today** (Railway $5 + Twilio ~$6 + Claw $5) | Always on, registered SMS (pending vetting), blue-bubble texts |
| **+~$2/month** (item 4, at first customer) | Company name on caller ID |
| **Pending decision** | iMessage at scale: Claw Agency tier vs Blooio $39/mo — awaiting Claw's reply |
