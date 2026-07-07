# Immersive 3D revamp for adapixai.com — handoff brief (Rocco → Ben)

**Date:** 2026-07-07 · **From:** Rocco's session · **For:** Ben (website/ is your lane)

Rocco wants adapixai.com to feel more immersive and 3D. A Higgsfield brand
film has been generated for this (logo + shipped features) — asset links
below. This doc is the design brief; the implementation in `website/` is
yours.

## The brand film (Higgsfield, generated 2026-07-07)

Silent 16:9 film in the site's exact palette (`#0b0b1c` bg, `#7c3aed`
purple, `#22d3ee` cyan). The story in one take: the navy Adapix "A" as a
3D monolith with pulsing cyan circuit traces → the branches grow out of
the A → holographic glass UI cards light up at the branch tips and spin
around it, showing the real product story: *a drafted follow-up → your
Approve button → goes out as your business (your number / your inbox)*.
All claims shown are shipped features, per the honesty rule.

**Assets** (hosted on Higgsfield's CDN — download from a normal browser;
Rocco's Claude session couldn't mirror them into the repo because its
network egress policy blocks the CDN):

- **MASTER — v5 (10s, 1280×720, silent), Rocco-approved direction.** One
  continuous take in the composition Rocco signed off on: opens on JUST
  the A + wordmark, cyan pulses breathe along its traces, then the branch
  stems grow out of the A one after another and six LARGE bold-lettered
  feature cards from the live site's copy materialize at the tips and
  revolve slowly, ending on the approved six-card ring (Approve draft ·
  Calls from your number · Emails from your inbox · Notices who went
  quiet · Answers while you can't · 57 AI specialists):
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_154222_d94dc760-5a29-4e07-b36c-2229f8db10ae.mp4>
- **Companion "loop" cut (10s)** — same scene with all six cards visible
  the whole time, slowly orbiting with pulses feeding them from the A;
  chain it after the master for a continuous hero background:
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_153519_cfb0f70b-dc7e-4ab0-af04-dd9023cb8e36.mp4>
- Poster still (the master's final frame):
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_153139_7f5cd09d-bfa8-4cd1-aad0-dcdd21250669.png>
- Clean "just the A" opening still (nice og:image / loading state):
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_154055_4f86a1ce-8689-4605-a9fd-cdc9b14b3f50.png>

Earlier takes (same palette, kept as alternates):

- v4 intro cut (10s, grows in from the older cinematic hero frame):
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_153303_82cdc3f3-00be-48d2-a100-c1fb894cc05f.mp4>
- v3 (10s, six cards, smaller/dimmer text — superseded for legibility):
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_152633_716ef677-3fe0-4a88-9470-cba0748cc44b.mp4>
- v2 continuous shot (10s, three cards, faster spin):
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_152051_9a05e5ba-1222-4db7-84eb-107aefc9f72c.mp4>

- Stitched film (~12s, 1280×720, silent):
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_151816_84e868e6-0ab2-46d8-bbbc-bbf9377cbe4f.mp4>
- Clip 1 — logo reveal (6s):
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_151425_a62a1915-8494-405b-a280-d1e79e8881df.mp4>
- Clip 2 — features stemming from the A (6s, per Rocco's direction):
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_151429_912bc4c5-aee1-47ce-9b39-49f3bc313c6a.mp4>
- Keyframe stills (posters / og:image material):
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_151203_5eaf2a7f-76e9-4ce9-9ff8-3e4c0582f1c9.png>
  <https://d8j0ntlcm91z4.cloudfront.net/user_3FGpqky25EoXR4BrSlSF8lPXHSf/hf_20260707_151207_1a3689b3-8e4f-4ac5-b3a7-1d85d973677f.png>

Clips are 720p (kept credits low: whole run ≈ 22 of the 194 credits on the
Higgsfield plan). If you want 1080p/2K for the hero, the account's
`upscale_video` (Topaz/ByteDance) can upscale these, or the clips can be
re-generated at higher res — ask Rocco's Claude or run it from your side.
Higgsfield job ids, if you need to re-open them in the app: keyframes
`5eaf2a7f`, `1a3689b3`, `18cb81f0`, `7f5cd09d` (bold six-card frame),
`4f86a1ce` (clean A opener); v1 clips `a62a1915`, `912bc4c5`; v1 stitch
`84e868e6`; v2 `9a05e5ba`; v3 `716ef677`; v4 intro `82cdc3f3`;
**master `d94dc760` + loop `cfb0f70b`**.

## Where to use it

- **Hero background or hero media slot** — muted, autoplay, loop,
  `playsinline`, with `poster` set to keyframe 1 so mobile/reduced-motion
  users get a still. Keep the H1 + CTA readable on top (dark gradient
  scrim over the video).
- The existing **DEMO VIDEO SLOT** in `index.html` is for the real product
  demo recording — don't burn it on this brand film; that task stays as-is.

## Heads-up: the last 3D hero was reverted

Board history: the previous 3D orbiting-A hero film test was reverted and
taken off the live site on 2026-07-07 at Rocco's request. He is now asking
for an immersive/3D direction again — the difference this time should be
**integration, not a bolt-on**: the film + 3D touches should live inside the
current animated UI (GSAP timeline, tilt cards, Station demo), not replace
it. Confirm the final hero treatment with Rocco before deploy.

## Immersive/3D direction (already in your toolchain)

Everything below uses libraries already vendored in `website/vendor/`
(three.module.min.js, gsap.min.js, ScrollTrigger.min.js, lenis.min.js,
vanilla-tilt.min.js):

1. **Hero film layer** — the Higgsfield film as the hero backdrop with a
   scrim; on scroll, GSAP scales/parallaxes it away under the content.
2. **Depth everywhere** — add `transformPerspective` + subtle `rotationX/Y`
   scroll-scrub to section cards (the tilt-card pattern already on the
   site, extended to sections).
3. **Three.js particle field** — a sparse cyan/violet particle drift behind
   the hero only (cheap: `<10k` points, `prefers-reduced-motion` disables
   it). This replaces the reverted orbiting-A idea with something subtler.
4. **Keep the lighthouse score** — lazy-load the film (`preload="none"` +
   IntersectionObserver play/pause), compress to ~2-4 MB webm/mp4, and keep
   the three.js scene behind `requestIdleCallback`.

## Honesty rule check

Everything depicted maps to shipped functionality: drafts you approve
before send, outreach from your own number/inbox/texts, follow-up engine.
No invented metrics, testimonials, or unshipped claims appear in the film.
