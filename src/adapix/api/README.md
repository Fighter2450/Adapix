# Adapix API & Dashboard

FastAPI app that serves the Adapix dashboard, the welcome wizard, the in-product chat, and all the JSON endpoints the browser hits.

Runs on `http://0.0.0.0:80` on the Pi appliance (via systemd; see `deploy/`). Locally during dev, run on port 8000.

---

## Module map

```
src/adapix/api/
├── main.py             ← FastAPI factory: routers, /static mount, root pages
├── app_routes.py       ← /app PWA, /welcome wizard, /chat, /api/v1/* JSON
├── webhooks.py         ← /webhooks/twilio/sms, /webhooks/dev/sms
├── auth.py             ← HTTP Basic auth helper for JSON endpoints
├── static/             ← brand assets served at /static/*
│   └── adapix_mark.svg
└── templates/          ← Jinja2 dashboards
    ├── welcome.html    ← 8-step welcome wizard
    ├── app.html        ← main dashboard (Home / Queue / Workshop / Settings)
    ├── chat.html       ← in-product chat surface
    ├── approvals.html  ← human-in-the-loop approval queue
    ├── expenses.html   ← expenses widget detail view
    └── index.html      ← admin-only root (auth-gated)
```

---

## Routes

### HTML pages (no auth)

| Path | What | Template |
|---|---|---|
| `/welcome` | First-boot setup wizard | `welcome.html` |
| `/app` | Main dashboard (redirects to `/welcome` if not yet configured) | `app.html` |
| `/chat` | In-product chat with Adapix | `chat.html` |
| `/static/*` | Static assets (logo, favicon) | n/a |

### HTML pages (admin auth required)

| Path | What | Template |
|---|---|---|
| `/` | Admin index (legacy) | `index.html` |
| `/approvals` | Approval queue UI | `approvals.html` |
| `POST /approvals/{id}/approve` | Approve + send | redirect |
| `POST /approvals/{id}/reject` | Reject with reason | redirect |

### JSON endpoints

Most are auth-gated via `Depends(verify_admin)`. The dashboard's HTML shells load **without** auth (no PHI in them), then the JSON fetches trigger Basic auth from the browser — credentials get cached after first prompt.

| Path | Method | What |
|---|---|---|
| `/api/v1/setup/save` | POST | Persist welcome wizard config |
| `/api/v1/setup/status` | GET | Has the wizard been completed? |
| `/api/v1/feed` | GET | Dashboard feed: escalations + pending approvals + digest |
| `/api/v1/patients` | GET | (legacy name) Customers list |
| `/api/v1/chat/history` | GET | Conversation transcript + suggested follow-ups |
| `/api/v1/chat/opener` | POST | Generate Adapix's first message |
| `/api/v1/chat/send` | POST | Send user message, get reply |
| `/api/v1/memory` | GET | Structured long-term facts |
| `/api/v1/memory/{id}` | DELETE | Forget a fact |
| `/api/v1/skills` | GET | List skills (`?mode=new\|existing`) |
| `/api/v1/skills/{slug}` | GET | Full SKILL.md for one skill |
| `/api/v1/connectors` | GET | Status of every connector (Gmail, Outlook, Twilio, Resend, push) |
| `/api/v1/oauth/google/start` | GET | Begin Gmail OAuth |
| `/api/v1/oauth/google/callback` | GET | Finish Gmail OAuth |
| `/api/v1/oauth/microsoft/start` | GET | Begin Outlook OAuth |
| `/api/v1/oauth/microsoft/callback` | GET | Finish Outlook OAuth |
| `/api/v1/oauth/disconnect` | POST | Revoke a connector |
| `/api/v1/email/test` | POST | Send a test email |
| `/api/v1/sms/test` | POST | Send a test SMS |
| `/api/v1/notify/vapid-public-key` | GET | Web push public key for the browser to subscribe |
| `/api/v1/notify/subscribe` | POST | Save a push subscription |
| `/api/v1/notify/unsubscribe` | POST | Drop a subscription |
| `/api/v1/notify/test` | POST | Fire a test push |
| `/api/v1/approvals/{id}/approve` | POST | Approve via API |
| `/api/v1/approvals/{id}/reject` | POST | Reject via API |
| `/api/v1/escalations/{id}/resolve` | POST | Mark an escalation handled |

### Webhooks (signature-gated, no admin auth)

| Path | Source |
|---|---|
| `/webhooks/twilio/sms` | Twilio inbound SMS (signed request) |
| `/webhooks/dev/sms` | Local dev simulator (no signature; bypasses Twilio) |

---

## The dashboard

`app.html` is a single-page PWA. The browser hits `GET /app`, gets the HTML shell, and from there everything is JavaScript fetching the `/api/v1/*` JSON endpoints.

Key tabs:

- **Home** — feed of escalations, approvals, today's stats
- **Queue** — approval queue with inline edit
- **Customers** — recent customers (renamed from "Patients" since Adapix isn't dental-only)
- **Studio** — Adapix's concierge mode for one-off requests
- **Workshop** — tools (skills you can run) + plug-ins (connectors). See the welcome wizard's mode fork; this surface adapts per mode.
- **Settings** — link to chat ("Tell Adapix what you want changed")

The Workshop is the most distinctive surface: numbered sections (`01 / BENCH`, `02 / TOOLS`, `03 / PLUG-INS`), monospace eyebrows, corner ticks framing the canvas, one-chamfered-corner cards. Designed to feel like a precision workshop without being kitschy.

---

## The welcome wizard (`welcome.html`)

8 steps, single page, transitions between them:

1. **Boot** — Hi / I'm Adapix / Let's set you up
2. **Fork** — Creating a business vs. running a business
3. **Business name**
4. **Owner name**
5. **Voice/tone** — Warm Professional / Casual Friendly / Direct & Formal
6. **Real problems** — free-form textarea
7. **Business type** — searchable picker over ~200 specific types
8. **Done** — Adapix is configured for {business}, voice is set, workflows are armed

On the last step, the wizard POSTs to `/api/v1/setup/save` which writes `practice_profile.json` and `configured.flag`. The browser then redirects to `/app`.

To rerun the wizard during testing:

```bash
rm /opt/adapix/configured.flag /opt/adapix/practice_profile.json
sudo systemctl restart adapix.service
```

---

## Running

```bash
PYTHONPATH=src uvicorn adapix.api.main:app --reload --port 8000
# open http://localhost:8000/welcome
```

The Pi appliance runs this on port 80 via the `adapix.service` systemd unit.
