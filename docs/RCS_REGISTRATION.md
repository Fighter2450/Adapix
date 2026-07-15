# RCS Business Messaging — registration copy & assets

Reference doc for submitting Adapix's RCS branded-messaging registration
(Twilio Trust Hub: RCS Core → RCS US → RCS Google, in that order — see
`src/adapix/cnam.py` for the pattern; RCS itself isn't coded yet, this is
prep). Policy requirements were pulled live from Twilio's own
`/v1/Policies` API, not guessed.

## Hosted assets (live)

- Logo: https://adapixai.com/assets/rcs/logo.png
- Banner: https://adapixai.com/assets/rcs/banner.png
- Screenshots:
  - https://adapixai.com/assets/rcs/screenshots/home.png
  - https://adapixai.com/assets/rcs/screenshots/calls.png
  - https://adapixai.com/assets/rcs/screenshots/sms_email.png
- Demo video: https://adapixai.com/assets/rcs/demo.webm
  (real screen capture, test account, no customer data — convert to mp4
  before final submission if a reviewer requires it; no ffmpeg was
  available locally to do that conversion here)

## Draft written copy

### Use-case description
Adapix is an AI follow-up assistant for small service businesses (plumbers,
contractors, salons, and similar). It drafts and sends personalized
follow-up texts and emails to a business's own customers — reminding them
about a quote, a missed appointment, or an upcoming visit — and every
message is reviewed and approved by the business owner before it goes out
(nothing sends automatically without a human okay). When a customer
replies, Adapix reads the reply and continues the conversation using
information the business owner has taught it about their services,
pricing, and policies.

### Trigger event description
A message is triggered when: (1) a business owner adds a new customer
contact who is due for a follow-up, (2) enough time has passed since a
quote or appointment with no response, or (3) a customer replies to an
existing conversation and Adapix responds in kind. Every one-off or
automated message is queued for the business owner to approve (or is sent
only after the owner has enabled auto-approval for routine follow-ups).

### Messaging flow
Business owner adds/imports a contact with consent on file → Adapix drafts
a personalized follow-up → owner reviews and approves (or edits) the draft
→ message sends → if the customer replies, Adapix reads the reply and
continues the conversation, escalating to the owner whenever it can't
confidently answer.

### Opt-in description
Contacts are added only by the business owner, who confirms the customer
already gave their contact information directly to the business (e.g. by
requesting a quote or booking a service) — the same standing relationship
that qualifies for transactional/relationship messaging under TCPA and
CTIA guidelines. Adapix does not purchase, scrape, or otherwise acquire
contact lists.

### Opt-out description
Every message honors STOP automatically and permanently — replying STOP
(or an equivalent word Adapix recognizes) immediately halts all future
messages to that contact across every channel, with no owner action
required to enforce it.

### Access instructions
No app or account is required to receive messages — this is a standard
one-to-one business-to-consumer texting relationship. Customers simply
reply to the number/thread they already have.

### Sample HELP message
"This is Adapix, an AI assistant for [Business Name]. For help, contact
[business phone/email]. Msg & data rates may apply. Reply STOP to opt out."

### Sample STOP message
"You've been unsubscribed and won't receive further messages from
[Business Name]. Reply START to resubscribe."

### Campaign overview
Transactional/relationship follow-up messaging for small service
businesses — job quotes, appointment reminders, and post-service check-ins
with existing customers, not marketing blasts to purchased lists.

## Still needed before this can actually be submitted

- **Monthly message volume estimate** — a real number from Rocco, not
  invented. Needed for the RCS US "use_case" fields
  (`rbm_traffic_forecast_monthly`, `organic_website_traffic_monthly`).
- **Real business legal details** — same info as the CNAM registration
  (legal name, address, EIN, authorized representative). Not fabricated
  here; reuse whatever gets submitted for CNAM once that's done, or
  collect fresh via the same in-app flow.
- **Actual API submission code** — this doc is copy + assets only; the
  Twilio API calls to actually file RCS Core → US → Google registrations
  (mirroring `cnam.py`'s pattern) haven't been written yet.
