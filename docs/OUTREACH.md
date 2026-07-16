# Adapix — Outreach List & Testimonial Kit

Working doc for the first 10–15 trial businesses (Birmingham, AL metro)
and the process for turning happy trial users into real testimonials.

Rules of the road:
- **Never publish an invented quote.** The site's testimonial section stays
  commented out until at least 3 real ones are collected (honesty rule in
  CLAUDE.md + FTC endorsement guides).
- Verify each contact (phone/email/owner name) on the business's own site
  before sending anything — the list below is researched but not yet verified.
- Track every touch in the table at the bottom.

---

## 1. Outreach list — first candidates

Criteria: local, small (owner answers the phone), quotes prices or books
appointments, visible follow-up pain. Ortho niche first per the board.

### Orthodontics / dental (the beachhead)

| # | Business | Location | Why they fit |
|---|----------|----------|--------------|
| 1 | Oak Mountain Orthodontics (Dr. Priscila Denny) | Birmingham + Helena | Independent, single-doctor — consults that don't book = lost cases. |
| 2 | Weissman Orthodontics (Dr. Weissman) | Crestline + Inverness | ~30 years established, two offices, independent. |
| 3 | Greystone Orthodontics (Dr. Jana Roberts) | Hoover / Greystone | Independent, 3 locations — recall + unscheduled-consult follow-up. |
| 4 | PT Orthodontics | Multiple Bham metro | Bigger (12 locations) — harder sell, but one office could pilot. |

### Med spas / aesthetics (quote-heavy, consult-driven)

| # | Business | Location | Why they fit |
|---|----------|----------|--------------|
| 5 | Spa Greystone | Greystone | Books consultations online — classic "let me think about it" pipeline. |
| 6 | Amae Med Spa (Birmingham Med Spa) | Birmingham | Consult-to-package conversion is their whole revenue model. |
| 7 | Seiler Skin Cosmetic Laser & Aesthetics | Homewood | Appointment-driven, high ticket sizes. |
| 8 | Nova Essence Medispa | Birmingham | Injectables/laser — repeat-visit recall is the money. |
| 9 | Infinity Med-I-Spa | Bham metro | Multi-location, consult-first model. |

### Home services (quotes that go cold — the Maria-the-fence-quote story)

| # | Business | Location | Why they fit |
|---|----------|----------|--------------|
| 10 | Superior Fence & Rail Birmingham | Birmingham | Fence quotes = the literal story on the website. (205) 431-3160. |
| 11 | Premier Fence of Birmingham | Birmingham / Gardendale | Residential + commercial quoting. |
| 12 | South Gate Fence Co. | Birmingham | 10+ years, small crew — owner does the quoting. |
| 13 | Olympic Fence Inc. | Maylene / Bham area | Since 1973, family-scale. |
| 14 | Woodford (exteriors/outdoor living) | Alabama | Est. 2019, small, high-ticket quotes. |

### Salons / personal care (recall + no-show recovery)

| # | Business | Location | Why they fit |
|---|----------|----------|--------------|
| 15 | Nails of Grace Spa | Birmingham 35242 | Online booking, no-show recovery + rebooking reminders. |

---

## 2. Outreach templates

Sender: a founder, from a real address (hello@adapixai.com). Short beats
clever. One CTA. **Dogfood rule: every follow-up to this list goes through
Adapix itself** — the product doing its own outreach is the demo.

### Cold email (v1 — the quote-chaser, for home services)

> Subject: the quotes you sent last month
>
> Hi {first name} — quick question: what happens to the quotes you send
> that nobody answers?
>
> We built Adapix for exactly that. It watches your customer list, writes
> the follow-up (text, email, or call — from YOUR number, not ours), and
> nothing sends until you tap approve. One contractor's "let me think
> about it" becomes a booked job while you're on a ladder.
>
> It's free for 14 days ($0 today), 5-minute setup: adapixai.com
>
> I'm local (Birmingham). Happy to set it up for you on a 10-minute call.
> — {founder name}, Adapix

### Cold email (v2 — the consult-closer, for ortho/med spa)

> Subject: the consults that never scheduled
>
> Hi {first name} — every practice has a folder of consults who said
> "we'll think about it" and never called back.
>
> Adapix follows up with every one of them automatically — texts and
> emails from your own number and inbox, in your voice, with your real
> prices. Your front desk approves every message before it sends, so
> nothing goes out you wouldn't say yourself.
>
> Free 14 days, nothing to install: adapixai.com
>
> We're Birmingham-based — I'll come set it up in person if you want.
> — {founder name}, Adapix

### Walk-in / phone script (30 seconds)

> "Hey — I run a small Birmingham software company. One question: when
> someone gets a quote/consult from you and goes quiet, who chases them?
> [pause] That's what we automate — it drafts the follow-up from your
> number, you approve it with one tap. Free for two weeks, I'll set it
> up for you right now on your phone. Worst case you delete it."

### Follow-up cadence (run it THROUGH Adapix)

- Day 0: cold email
- Day 3: text (if number is public) — "sent you a note about the quotes
  that go quiet — worth 10 minutes?"
- Day 7: last email — "closing the loop; if follow-up isn't a problem
  for you, ignore me and I'm gone."
- Reply at any point → offer the 10-minute setup call, same day.

---

## 3. Testimonial collection kit

The site section is built and commented out in `website/index.html`
(search `SOCIAL PROOF`). Three cards: name, business type, city, quote.

### When to ask
The moment something concrete happens — first recovered customer, first
booked job from a draft, end of week 1. Strike while it's specific.

### The ask (text or email, from the founder)

> "That job that came back through Adapix this week — would you be up
> for a two-sentence quote we can put on our site? Your name and
> business, nothing formal. I can draft it from what you tell me and
> you approve every word."

### Three questions that produce usable quotes
1. **The number:** "How many quiet customers did it reach out to, and how
   many came back?" → *"It recovered 9 cold leads in our first month. Two booked."*
2. **The before/after:** "What did follow-up look like before?" →
   *"Sunday nights used to be follow-up night. Now it's ten taps over coffee."*
3. **The fear:** "What worried you before you tried it, and what happened?" →
   *"I thought it'd sound robotic. My customers can't tell the difference."*

### Consent + mechanics
- Get explicit OK **in writing** (text/email reply is fine) to use their
  name, business name, and city on adapixai.com.
- They approve the final wording — send them the exact card text.
- Keep the written consent (screenshot into `docs/testimonials/`).
- When 3 are collected: uncomment the `#proof` section in
  `website/index.html`, fill in the cards, push (auto-deploys).

---

## 4. Tracking

| Business | Contact | Channel | Day 0 | Day 3 | Day 7 | Reply? | Trial? | Testimonial? |
|----------|---------|---------|-------|-------|-------|--------|--------|--------------|
| (fill as you go) | | | | | | | | |
