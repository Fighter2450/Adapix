# Adapix Architecture

## Core thesis

Adapix is an **adaptive operator**. Behavior is *configured*, not *coded*. A new workflow (recall, no-show recovery, parent updates, supplier coordination, anything) is a new YAML file plus per-practice knowledge. The Python codebase stays small and stable; the YAMLs grow.

## Layers

```
                 ┌─────────────────────────────────────────────┐
                 │  Configs  (config/workflows, config/practices) │
                 │  — workflows define what to do                │
                 │  — practices define who we work for           │
                 └─────────────────────────────────────────────┘
                                    │
                                    ▼
                 ┌─────────────────────────────────────────────┐
                 │  Agent  (src/adapix/agent.py)                 │
                 │  — workflow-agnostic                          │
                 │  — composes one message given                 │
                 │    (workflow + practice + patient + step)     │
                 └─────────────────────────────────────────────┘
                                    │
                                    ▼
                 ┌─────────────────────────────────────────────┐
                 │  Campaign Runner  (src/adapix/campaign.py)    │
                 │  — picks up due steps                         │
                 │  — calls the agent                            │
                 │  — dispatches to channels                     │
                 │  — logs everything                            │
                 └─────────────────────────────────────────────┘
                       │                          │
                       ▼                          ▼
              ┌────────────┐              ┌─────────────┐
              │ Channels   │              │  Database   │
              │  SMS  Email│              │  SQLite v0  │
              │ (Twilio)   │              │  Postgres   │
              │ (Resend)   │              │   later     │
              └────────────┘              └─────────────┘

   Admin UI (FastAPI) reads from the database for monitoring + approvals.
```

## Why this architecture supports the long-term vision

- **Vertical expansion** (ortho → general dentistry → vet → optometry → HVAC) is new YAML configs + new domain knowledge in practice configs. The agent core does not change.
- **Hardware path** (Adapix device): the campaign runner can be packaged as a service that runs on the device. Database is portable. Channels are pluggable. The same code runs on a server today and a Mac mini-form-factor box tomorrow.
- **Companion app**: the FastAPI layer becomes the API the mobile/web app talks to. The HTML admin view is the v0 of that interface.
- **Local vs. cloud AI**: the `AdapixAgent` currently calls the Anthropic API. To run a local model on the device for privacy/latency, swap the LLM call inside the agent — one method change, no other code touched.

## Why workflows are YAML, not code

Three reasons:
1. **Non-engineers can edit workflows.** Tweaking the cadence or tone for a specific practice should not require a deploy.
2. **Diffability.** Workflow changes show up as readable diffs in git.
3. **Versioning.** Each workflow has a `version` field; we can run controlled experiments by versioning configs, not deploying code.

## Data model summary

- `Patient` — one row per consult patient. PHI-bearing.
- `Campaign` — one row per (patient, workflow) pair. Tracks progress through cadence.
- `Message` — one row per inbound or outbound message. Audit log + memory for the agent.

Future tables (planned): `Practice` (move from YAML once we have multi-tenancy), `Approval` (when a workflow step requires human sign-off), `EscalationEvent` (clinical questions, callbacks, declines), `IntegrationCredential` (per-practice Twilio/PMS/etc credentials, encrypted).

## What we are intentionally NOT building yet

- PMS integrations (Veracity, Dentrix, Cloud9, etc.) — V1 reads spreadsheet exports.
- Voice channel — text-first first.
- Multi-tenant SaaS — single-tenant per deployment for v0.
- Self-serve onboarding — managed deployment for first 1-3 pilots.
- Hardware abstraction layer — defer until the agent platform is proven.
