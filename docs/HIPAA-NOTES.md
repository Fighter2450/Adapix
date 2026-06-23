# Adapix HIPAA Notes

## Bottom line

**The current codebase is not HIPAA-compliant.** It is a development scaffold. Patient data must not touch this system in production until the items below are addressed.

## What's required before any real PHI flows

### Legal
- [ ] Adapix incorporated as a legal entity (Delaware C-Corp recommended)
- [ ] Business Associate Agreement (BAA) with Anthropic — requires Team or Enterprise tier
- [ ] BAA with Twilio (available on their Healthcare offering)
- [ ] BAA with Resend (verify availability; if not available, switch email provider — SendGrid offers BAAs on enterprise tier)
- [ ] BAA with each customer practice
- [ ] All BAA templates reviewed by a healthcare attorney before signing

### Technical
- [ ] Encryption at rest for the database (SQLCipher for SQLite, or move to Postgres with TDE / encrypted EBS)
- [ ] Encryption in transit (TLS everywhere — already true for API calls, must be enforced for the admin UI)
- [ ] Access controls on the admin UI (auth + per-practice authorization, not yet implemented)
- [ ] Audit log of every PHI access and every outbound message (the `messages` table is the start; need read-access logging too)
- [ ] Backups encrypted and access-controlled
- [ ] PHI redaction in any logs that go off the host (Sentry, etc.)
- [ ] Secret management — API keys must not live in `.env` files in production; use a secrets manager

### Operational policies (4 minimums)
- [ ] **Access Control Policy** — who can see what, how access is granted/revoked
- [ ] **Incident Response Plan** — what happens if PHI is exposed; 60-day breach notification rule
- [ ] **Data Retention & Destruction Policy** — how long PHI is kept, how it's destroyed
- [ ] **Workforce Training Log** — every person with PHI access has documented HIPAA training

## Penalties for non-compliance

$141 to $2.1M per violation depending on willfulness. Multiple settlements have exceeded $1M for BAA failures alone.

## Recommended posture for v0 development

While building:
- **Use synthetic data only** — no real patient names, phone numbers, or emails. The example patient in `demo.py` is fictional. The `example.yaml` practice is fictional.
- **Use the `--dry-run` flag** for any campaign work in development. No real SMS or email is sent.
- **Run locally only** — no cloud deployment until the legal + technical checklist above is complete.

When ready to pilot with a real practice:
1. Sign all BAAs (Anthropic, Twilio, Resend or alternative, the practice itself)
2. Implement the technical controls listed above
3. Document and sign off on the four operational policies
4. Have a healthcare attorney verify the deployment posture before flipping the switch

## Architectural choices that anticipate HIPAA

- All channel adapters support `dry_run` mode out of the box.
- The `Message` model logs every outbound message with provider id and status (audit-ready).
- No PHI is sent to any service the agent doesn't explicitly call. The agent only reads what is passed in.
- LLM calls go to Anthropic only; no third-party logging providers in the data path.
- The `metadata_json` field on Message is intended for non-PHI metadata only.
