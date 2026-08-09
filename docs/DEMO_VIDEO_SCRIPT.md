# Adapix demo video — 90-second script + shot list

_Goal: a cold visitor watches this on the landing page and thinks "oh, that's all it is —
I approve texts and win back jobs." One take, screen recording + voiceover. No face needed._

**Recording setup:** OBS (free) at 1080p, record the browser on the demo account
(`demo@adapix.test` on the local or prod app — seeded with contacts + pending drafts).
Speak the VO live or record it after over the footage. Keep total under 95 seconds.

---

## The script

| # | Time | On screen | Voiceover |
|---|------|-----------|-----------|
| 1 | 0:00–0:08 | Landing page hero, slow scroll | "Every service business has quotes that went quiet. You sent the estimate… and never heard back. That's not a lost customer — that's a customer nobody followed up with." |
| 2 | 0:08–0:20 | Dashboard Home — point at "Waiting on you" and the stat cards | "This is Adapix. It watches your quotes, and when one goes quiet, it writes the follow-up for you. Not a template — a real message." |
| 3 | 0:20–0:38 | **The money shot.** Open Inbox. Hover a draft: "Hi Maria, it's Dave from Dave's Plumbing — still want that water heater in before winter?" | "Here's one it drafted. It names the actual customer, the actual job, the actual price. And here's the part that matters: **it hasn't sent.** Nothing sends until I tap approve." |
| 4 | 0:38–0:46 | Tap **Approve & send**. Toast appears. | "One tap. Done. That took four seconds, and it's the four seconds most businesses never get around to." |
| 5 | 0:46–0:58 | Open a contact → Conversation timeline (text → email → call → "booked") | "When they reply, Adapix answers — and everything lives in one timeline. Every text, email, and call. It even remembers the details, like the dog to lock up before the visit." |
| 6 | 0:58–1:10 | Contacts → tap **Won ✓** on a contact, enter amount → Home shows "Won back this month: $2,400" | "And when a quiet quote turns into a booked job, you log it — so you can see exactly what the follow-up is earning you. In dollars." |
| 7 | 1:10–1:25 | Back to Home, calm shot of the glass dashboard | "No funnels to build. No campaigns to configure. You import your customers, and drafts start showing up for your approval. That's the whole product." |
| 8 | 1:25–1:33 | Signup page | "It's $99 a month, everything included, free for 14 days. Bring your five deadest quotes and watch what happens. app.adapixai.com" |

---

## Rules while recording
- **Slow cursor.** Move like you're showing a friend, not speed-running.
- Shot 3 is the conversion moment — linger on the draft text so viewers can read it.
- Don't show Settings, Business Knowledge, or any empty view. Stay on the money path:
  Home → Inbox → approve → timeline → Won → Home.
- If a real send would fire (demo has no live Twilio), use the Reject/Edit buttons for
  shot 4's interaction, or approve an email draft — or just record the tap and cut
  before any error toast.
- No invented numbers in VO beyond what's on screen from seed data.

## Where it goes
1. Landing page: embed above the "See it in action" screenshots (YouTube unlisted embed
   or a self-hosted `<video>` — mp4 in `website/`, ~20 MB max).
2. Pinned post on @adapix_ai and @adapix.ai (cut a 60s vertical version for IG/Reels:
   shots 3–6 only).
3. The signup page, so half-convinced visitors get pushed over.

## The 60-second vertical cut (social)
Shots 3 → 4 → 5 → 6, cropped to 9:16, captions burned in (most people watch muted):
- 0:00 "This AI wrote a follow-up for a $2,400 water heater quote"
- 0:12 "But it CAN'T send it without the owner's approval"
- 0:20 "One tap. Sent."
- 0:30 "Every text, email & call — one timeline"
- 0:45 "Quiet quote → booked job → counted in dollars"
- 0:55 "$99/mo · 14-day free trial · link in bio"
