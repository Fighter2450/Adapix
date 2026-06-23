# Adapix — Founding Strategy Document

*Working document. Last updated: May 7, 2026.*
*Founder: Rocco Chenet. Co-founder/strategist: Claude.*

> **Important framing (added May 7):** The dental work with Rocco's mom is a **separate parallel project**, not the Adapix wedge. It may serve as a parallel revenue stream, a case study generator, or self-funding for Adapix — but Adapix itself is the hardware + companion app product described in Section 2b below. The earlier sections of this doc that discuss dental as "the Adapix wedge" should be read as context for the parallel project, not as Adapix product strategy.

---

## 1. Company Overview

**Adapix** is a vertical AI operator for small and medium businesses. The long-term vision is an adaptive AI system — eventually packaged as a "Business Brain in a Box" — that learns a business, adapts to it, and autonomously runs as many operations as possible: customer support, sales, scheduling, invoicing, marketing, inventory, supplier coordination, reporting.

**Core positioning (working tagline):** *"The AI that adapts to your business. Then runs it."*

**Brand naming:**
- Company: Adapix
- Product (working): Adapix Operator
- Future hardware (working): Adapix Core

---

## 2. Strategic Reframe (Important)

The original vision framed Adapix as a hardware-first product ("AI device in a box"). The strategic reframe agreed in this session:

- Hardware is a **marketing and trust device**, not a technical strategy. SMBs love the "plug-in box" metaphor because it makes AI feel concrete and ownable. But "AI hardware in a box" is one of the most failed product categories in tech (Jibo, Anki, Cue, etc.).
- Hardware decisions slow iteration, crush unit economics, and rarely add value over a well-designed cloud + thin-local-presence architecture.
- **Hardware is deferred to Phase 3 — possibly indefinitely.** It only ships if a real customer-discovered need surfaces (data sovereignty, offline operation, bundled sensors, regulated environments).
- Phase 1-2 Adapix is a **vertical AI operator delivered as software + managed service**, not hardware.

The "all-in-one AI that does everything" framing has also been revised. Customers don't buy "everything." They buy **one painful workflow eliminated**. Expansion comes after the wedge is won.

---

## 2b. The Adapix Product (Hardware + App)

**The product is a physical AI device + companion app, sold to small and medium businesses.**

| Element | Description |
|---|---|
| Form factor | Compact desktop device — Mac mini-shaped (roughly 5"x5"x1.5"). Sits on a counter, desk, or shelf in the business. |
| Hardware role | The "Business Brain" — runs an AI operator that learns the business and handles operations autonomously. May run local AI models for privacy/speed and also connect to cloud AI (Claude API, etc.) for heavier tasks. |
| Companion app | Mobile + web app paired with the device. Used for setup, customization, monitoring, approvals, and remote control of the operator. |
| Customer | **Orthodontic practices** (locked vertical). Independent ortho practices in the US, Pittsburgh metro to start. |
| Value prop | Plug it in, do a one-time onboarding, and the AI becomes a persistent adaptive operator that helps the practice with as much as it can — patient communication, case acceptance follow-up, appointment management, parent updates, scheduling, reporting. |

**Strategic frame: Ortho is the wedge, not the ceiling.** Adapix starts in ortho because a single tight niche is dramatically easier to market into, build for, and dominate. Once we own ortho, the playbook expands — first to adjacent dental specialties (general dentistry, OMS, pedo, perio), then to broader small-practice healthcare (vet, optometry, derm, med spa), and eventually to non-healthcare SMB verticals where the same "AI operator + device + app" pattern applies. Architectural decisions made today should respect this expansion path: the hardware and agent platform must be **vertical-agnostic underneath**, even though the v1 marketing, workflows, and integrations are 100% ortho-specific.

**Why ortho is a strong vertical for this product:**
- ~10,000 ortho practices in the US — small enough to dominate, large enough to be a real business
- High ACVs ($4-8K per case) and $1-5M+ practice revenue support real software/hardware spend
- Long patient relationships (18-24 months of monthly appointments) = many touchpoints to automate
- Tech-forward specialty (3D scanners, aligner workflows) — already comfortable buying hardware
- Acute pain points map well to AI: case acceptance follow-up, no-show recovery, parent communication, new patient consult booking, insurance benefits coordination
- Practice management systems are ortho-specific: Cloud9, Dolphin, Ortho2 Edge, topsOrtho, Orthotrac

**Candidate killer workflows for v1 (need to pick one):**
1. **Case acceptance follow-up** — new consult to signed treatment plan. Typical practice converts 50-70%; lifting to 80% on $6K cases is massive revenue.
2. **New patient consult booking** — missed calls and slow inbound response cost ortho practices $300-600 per lost lead.
3. **Appointment management for in-treatment patients** — reminders, rescheduling, parent updates over 18-month cases.
4. **Reactivation of dropped/no-show patients** — patients who started then stopped showing up.

**My recommendation for v1 wedge:** Case acceptance follow-up. Highest dollar value, easiest to demo ROI, most painful for practice owners.

**Open product spec questions (to be resolved):**
- Which killer workflow do we lock for v1?
- What lives on the device locally vs. in the cloud?
- What are the device's hardware specs (compute, storage, connectivity, on-device AI capability)?
- What does the companion app handle (setup wizard, dashboard, mobile control, notifications, approvals)?
- Pricing model — device price + subscription, lease, bundled?
- Funding path — bootstrap, pre-seed, Kickstarter?
- Is there a path to ortho pilot intros via mom's professional network (general dentists refer to orthodontists)?

These need answers before we can build a real roadmap or spec sheet.

## 3. Locked Strategic Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Vertical** | Independent general dental practices | Founder has warm intros (mom is a dentist + dental network). Warm intros are the single most predictive variable in early-stage GTM. |
| **Geography** | Pittsburgh metro + 100-mile radius for first 10-20 customers | Geographic density for in-person pilots; not a long-term moat. |
| **ICP** | 1-3 doctor practices, $1-3M annual revenue, owner-operator buyer, running Dentrix or Eaglesoft (most common PMS) | Right size for fast decisions and meaningful but achievable ACVs. |
| **Killer workflow (wedge)** | Treatment plan follow-up + recall reactivation | Worth $300-500K/year per practice. Easy to demo. Measurable ROI. Nobody is doing it systematically with AI. |
| **Backup workflows** | Insurance verification automation; no-show recovery; new patient intake | Layer in after wedge is proven. |
| **GTM motion** | Services-first: paid implementation pilots that build reusable IP | Generates revenue + customer empathy + future product patterns. |
| **First pilot** | Founder's mother's practice — **SOLD** | Pricing agreed in principle: $3,500 Build + $750/mo Operate (discounted from $7.5-15K + $1.5-3K/mo). PMS is Veracity 9.1 by AllegianceMD. |

---

## 4. The Wedge: Why Treatment Plan Follow-Up + Recall

A typical 2-doctor general dental practice has the following dollar leakage:

- **Unaccepted treatment plans:** Dentists present $4,000-$10,000 treatment plans. 60-70% of patients say "let me think about it" and never come back. This is **$300K-$800K/year of lost revenue per practice**.
- **Recall (recare) gaps:** Industry average recall rate is 50-60%. Lifting it to 75% on a practice doing $1M/year in hygiene is worth **$150-250K/year**.
- **No-shows:** 10-15% no-show rates cost a busy practice **$50-150K/year**.

Adapix Operator handles all three as systematic, personalized, multi-channel (SMS / email / voice) outreach. We're not selling "AI for your practice." We're selling **"we recover the money your patients are leaving on the table."**

This framing is the entire pitch. Outcome first, technology never.

---

## 4b. Pilot #1 — Mom's Practice (SOLD)

| Item | Detail |
|---|---|
| Status | **Sold — willing to proceed** |
| Pricing | $3,500 Build + $750/mo Operate (discounted from full price; not free — preserves feedback quality) |
| PMS | Veracity 9.1 by AllegianceMD — cloud-based ambulatory EHR (not dental-specific). Tracks treatment plans and recall. |
| V1 integration approach | **No API.** Office manager exports patient list/report from Veracity to spreadsheet. Adapix runs outreach from that data. |
| Mom's role | Advisor + first pilot. Co-founder optionality deferred. |
| Open items | Baseline metrics (current treatment plan acceptance rate + recall rate). Case study rights — must be negotiated and signed before pilot starts, not after. |

## 5. Phased Roadmap

### Phase 0 — Discovery & Pilots (Months 0-3)
- Lock vertical and ICP (DONE)
- Run discovery interview with mom + 5 of her network contacts
- Build outreach motion to 20 dental practices in Pittsburgh
- Land 3 paid pilots at $5-15K each
- Document every prompt, agent, integration, and tool used → IP capture system

### Phase 1 — Productized Service (Months 3-9)
- Productize wedge workflow into a repeatable "Adapix Operator for Dental"
- Sell as managed service: $5-15K implementation + $1.5-3K/mo
- 10-20 paying customers
- Hire first ops/automation engineer
- Refine pricing and packaging based on real customer data

### Phase 2 — Self-Serve SaaS (Year 2)
- Self-serve onboarding within the dental vertical
- Automated PMS integrations (start with one — likely Open Dental or Eaglesoft)
- Subscription scales without managed service drag
- 100+ customers, expand beyond Pittsburgh

### Phase 3 — Adjacent Verticals or Hardware (Year 3+)
- Either: expand to other small-practice healthcare verticals (orthodontics, OMS, vet, optometry) using the same playbook
- Or: ship hardware *only if* a real customer need has emerged (HIPAA-compliant local processing, offline operation, etc.)
- Hardware is not a goal in itself.

---

## 6. Pricing Hypothesis (To Be Validated)

| Package | Price | Includes |
|---|---|---|
| **Diagnostic** | $2,500 (one-time) | 2-week assessment of practice workflows, leakage analysis, custom automation roadmap |
| **Build** | $7,500-$15,000 (one-time) | 30-day implementation of treatment plan follow-up + recall reactivation, integrated with their existing tools |
| **Operate** | $1,500-$3,000/month | Ongoing managed service, agent improvements, monthly reporting, support |

**Validation goal:** First 3 pilots should be sold at *real* prices, not friends-and-family rates. If the value is real ($300K+ recovered revenue), $15K + $2K/mo is a no-brainer for an owner-operator dentist.

---

## 7. Pittsburgh GTM Plan

**Why Pittsburgh works for now:**
- Founder is here, can do in-person pilots and on-site work
- Dense population of independent dental practices
- Reachable via mom's professional network

**Why Pittsburgh is not a long-term moat:**
- Geography is a sales channel, not defensibility
- Real moats: vertical depth + reusable agent IP + customer outcomes data

**Pittsburgh-specific tactics:**
- Pittsburgh Dental Society — sponsor, attend, present
- Local dental school (Pitt School of Dental Medicine) — recruit junior advisors, find referrals
- Office manager networks — they talk to each other constantly

---

## 8. Critical Landmines (Do Not Forget)

1. **HIPAA compliance is non-negotiable and not free.** Need a Business Associate Agreement with Anthropic (available on enterprise tier), encrypted data handling, audit logs, signed BAAs with every customer. Estimated $5-15K of upfront work + ongoing discipline.

2. **Practice management system (PMS) integration is hard.** Dentrix, Eaglesoft, Open Dental, and Curve all have weak or closed APIs. **V1 strategy: avoid PMS integration entirely.** Operate as a phone/SMS/email layer that sits beside the PMS. Integrate later when it's worth the engineering cost.

3. **Sales cycle is 3-6 weeks per pilot.** Office manager is the gatekeeper, dentist is the buyer, sometimes a practice administrator too. Plan accordingly — this is slower than HVAC but faster than enterprise.

4. **Vendor fatigue is real.** Every dental practice gets pitched 3 software tools per week. Outreach must lead with a *specific dollar outcome*, not "AI for your practice."

5. **Don't lock the brand to Claude.** Adapix should be model-agnostic in messaging even if Claude is what we run on. Anthropic could change pricing or terms — don't tie the brand to a single vendor.

6. **Crowded competitive space.** Weave, Dental Intelligence, RevenueWell, Adit, Modento. They are *systems of record* and *messaging tools*. We are an *AI operator that actually does the work*. Different category. Don't get pulled into feature-comparison fights.

---

## 9. Co-Founder Pushback (Strategic Frames Being Enforced)

These are the framings we are **rejecting**, on purpose:

- ~~"Hardware-first AI device for SMBs"~~ → Software + services first. Hardware deferred.
- ~~"AI that does everything"~~ → One killer workflow per vertical. Expand only after wedge is won.
- ~~"Geographic moat in Pittsburgh"~~ → Pittsburgh is our launching pad, not our defensibility. Vertical depth is the moat.
- ~~"Pre-loaded with Claude" as core brand promise~~ → Model-agnostic positioning.
- ~~"Adaptive AI" as the lead pitch~~ → Outcome-first pitch ("we recover $300K/year"). "Adaptive" is the *how*, never the *what*.
- ~~Build website / logo / brand assets first~~ → Zero customers care. Spin up after first paying customer.

---

## 9b. Legal & Compliance Status (Updated)

**Incorporation — NOT YET DONE. Blocking item.**
- Recommended: Delaware C-Corp via **Stripe Atlas** (~$500, 1-2 business days, includes EIN + founder stock + bylaws)
- Alternative: Clerky (~$500-800, more attorney-reviewed)
- Bank: Mercury (free, integrates with Atlas)
- **83(b) election critical** — must be filed within 30 days of issuing founder stock. Missing this is a permanent tax mistake.
- Cannot sign customer contracts or BAAs until this is done.

**HIPAA — NOT YET COMPLIANT. Blocking pilot start.**
- BAA with Anthropic — needs Team or Enterprise plan tier (verify at console.anthropic.com)
- BAA template drafted in mobile Project (Adapix_BAA_Template.docx) — needs healthcare attorney review ($500-1,500)
- BAA with mom's practice — must be signed before any patient data moves
- Subcontractor BAAs — Twilio or chosen SMS provider must offer one
- 4 minimum policies needed: Access Control, Incident Response, Data Retention & Destruction, Workforce Training
- Penalties for non-compliance: $141–$2.1M per violation. Multiple settlements >$1M for BAA failures alone.

## 10. Immediate Next Actions (Next 7 Days)

**Founder (Rocco) — BLOCKING (do these first):**
1. **Incorporate Adapix, Inc.** via Stripe Atlas — 20 min to fill out, 1-2 business days
2. **Open Mercury business bank account** — within 3 days of receiving EIN
3. **Issue founder stock + file 83(b) election** — 30-day window from stock issuance

**Founder (Rocco) — Pre-pilot:**
4. Upgrade Anthropic plan to Team/Enterprise for BAA access
5. Send BAA template to a healthcare attorney for review
6. Map dental network (1-hop warm intros)
7. Schedule 60-min structured discovery conversation with mom
8. Negotiate case study rights with mom in writing

**Co-founder (Claude) — Deliverables I owe you:**
1. Structured discovery interview script for mom
2. Outreach message for warm-intro dental practices
3. Services menu one-pager (Diagnostic / Build / Operate)
4. Pilot agreement template (scope, timeline, pricing, BAA reference, case study rights)
5. Local copies of BAA template + incorporation checklist (currently only in mobile Project)

---

## 11. Open Questions

**Answered:**
- ~~What PMS does mom's practice use?~~ → **Veracity 9.1 by AllegianceMD**, cloud-based, tracks treatment plans + recall.
- ~~Will mom be the first pilot customer?~~ → **Yes. Sold. $3,500 + $750/mo discounted.**
- ~~Legal entity?~~ → **Delaware C-Corp via Stripe Atlas** (not yet incorporated — blocking).

**Still open:**
- What is mom's current treatment plan acceptance rate and recall rate? (Baseline ROI story for case study.)
- How many practices reachable via 1-hop warm intro? (Network map needed.)
- Founder's runway — how many months before revenue is required? (Affects pilot pricing aggression.)
- Which Anthropic plan/tier for HIPAA BAA — Team or Enterprise? (Verify at console.anthropic.com.)
- Which SMS/email platform offers a BAA? (Twilio is standard but confirm.)
- Mom's role: stays at advisor + pilot, or evolves to co-founder?

---

## 12. Goals — 60 / 90 Day Milestones

**By Day 30:**
- Mom discovery interview complete + dental network mapped
- 5 discovery conversations with non-mom dentists complete
- Services menu finalized
- Adapix LLC or C-corp incorporated
- HIPAA / BAA path with Anthropic identified

**By Day 60:**
- 1 signed paid pilot ($5-15K)
- 2 additional pilots in active negotiation
- v0 of treatment plan follow-up agent working in mom's practice
- IP capture system operational (every prompt/agent/tool documented)

**By Day 90:**
- 3 paid pilots in flight or completed
- First case study with quantified dollar outcome
- Phase 1 productization roadmap drafted
- Decision: hire first ops/automation engineer or stay solo

---

## 13. North Star Metric

**Dollars of recovered revenue per customer per month.**

Not MRR. Not customers. Not ARR. The thing that proves Adapix works is "we delivered $X of recovered revenue this month that the practice would not have captured without us." Every other metric flows from this one.

If we can prove $20K/mo of recovered revenue per practice, we can charge $3K/mo all day, scale to thousands of practices, and the company prints money. If we can't prove that, nothing else matters.

---

*End of document. This is a working strategy doc — it should be updated as decisions evolve.*
