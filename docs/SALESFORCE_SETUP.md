# Salesforce connector — setup

What it does once connected (per org, opt-in from **SMS & Email → Salesforce**):
- Pulls **open Opportunities** (and the last 30 days of closed ones) with their
  primary contact, hourly + on demand. Each becomes an Adapix contact/quote:
  Opportunity name → the job, Amount → the value, Closed Won → won,
  Closed Lost → declined. Stalled open ones become quiet quotes the engine chases.
- **Writes back**: every follow-up Adapix actually sends is logged in Salesforce
  as a completed **Task** on the Opportunity (and contact), so SF stays the
  system of record.
- Dedupes against existing contacts by phone/email; never resurrects someone
  the owner manually declined or paused... unless SF itself reopens the deal.

The feature is **inert** until the two env vars below are set — no UI shows,
no loop runs.

## One-time: create the Connected App (≈10 min, founder does this once)

1. You need any Salesforce org to HOST the app definition — a free
   [Developer Edition](https://developer.salesforce.com/signup) works and your
   customers' orgs connect through it.
2. In that org: **Setup → App Manager → New Connected App**
   - Name: `Adapix`
   - ✅ Enable OAuth Settings
   - Callback URL (both):
     `https://app.adapixai.com/oauth/salesforce/callback`
     `http://localhost:8000/oauth/salesforce/callback`
   - OAuth Scopes: **Manage user data via APIs (api)** and
     **Perform requests at any time (refresh_token, offline_access)**
   - ✅ Require secret for Web Server Flow
3. Save → wait ~10 min for propagation → copy **Consumer Key** and
   **Consumer Secret**.
4. Set env vars (Railway + local `.env`):
   ```
   SALESFORCE_CLIENT_ID=<Consumer Key>
   SALESFORCE_CLIENT_SECRET=<Consumer Secret>
   ```
5. Redeploy. The "Salesforce" card appears under SMS & Email; a customer (or
   you) clicks **Connect Salesforce**, logs into THEIR org, done.

## Notes / limits (v1)
- Polling, not streaming: changes land within the hour (or instantly via
  "Sync now").
- Opportunities without any Contact Role are skipped (nobody to text) — the
  sync result shows the count.
- Sandbox orgs use `test.salesforce.com`; v1 targets production/dev orgs on
  `login.salesforce.com`.
- Marketing may say "Connects to Salesforce" ONLY after this ships verified
  against a real org (MARKETING.md rule: nothing unshipped).

## Where the code lives
- `src/adapix/salesforce.py` — OAuth, SOQL sync, Task write-back
- `src/adapix/api/app_routes.py` — `/oauth/salesforce/*`, `/api/v1/salesforce/*`
- `src/adapix/api/main.py` — `_salesforce_sync_loop` (hourly)
- `src/adapix/approval.py` — write-back hook on successful send
- Settings UI card: `app.html` `loadSalesforceCard()` (hidden unless configured)
