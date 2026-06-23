# Adapix — Marketing Website

The pre-launch landing page for **Adapt 1.0**. Single static `index.html`, Apple-feel design system (Inter font, tight letter-spacing, 22 px rounded corners, frosted nav bar). Hero device mockup, problem framing, features, specs, pricing, reservation form.

Lives at the public Adapix domain when deployed.

---

## Files

| File | What |
|---|---|
| `index.html` | The whole page. CSS + HTML + a small bit of JS, single file. |
| `adapix_mark.svg` | Icon-only logo (no wordmark). Used in the nav bar, hero device, favicon. |
| `adapix_logo_full.svg` | Full logo with "Adapix" wordmark below. Available if needed elsewhere. |
| `adapix_logo.svg` | Original raw logo (auto-traced from a raster — keeps the wordmark too). |
| `adapix_device_render.png` | Hero render of Adapt 1.0 device. |
| `adapix_device_render.svg` | SVG version of the same render. |

---

## Design system

Apple-feel, copied (not literally) and tuned for Adapix:

- **Colors**: off-white `#F5F5F7`, ink `#1D1D1F`, cyan `#00D4FF` for the Adapix accent, 8 px hairlines for borders
- **Typography**: Inter, weights 300–900, tight letter-spacing (-.025em on display sizes)
- **Corners**: 22 px on cards, 28 px on big tiles
- **Shadows**: subtle (`0 1px 2px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.04)`) — nothing dramatic
- **Easing**: `cubic-bezier(.22,1,.36,1)` — the Apple ease-out feel
- **Logo**: defined once as an SVG `<symbol>`, referenced via `<use>` in nav + hero + favicon (data-URI in the favicon link)

---

## Sections (in order down the page)

1. **Nav bar** — frosted-glass top bar with Adapix mark + nav links + "Reserve" CTA
2. **Hero** — "Real AI. In your business." Headline + subhead + reserve CTA + device mockup
3. **Statement** — "A new staff member. Who never goes home."
4. **Bento (features)** — feature tiles in a grid
5. **Workflow** — case acceptance / recall / no-show recovery / financing follow-up
6. **Approval workflow** — visual showing the queue + draft + approve flow
7. **Specs** — the actual Adapt 1.0 hardware specs (150×150×50 mm, Pi 5 inside, etc.)
8. **Pricing** — Adapt 1.0 device + Adapix Pro software
9. **Reservation form** — capture early signups
10. **Footer**

---

## Editing

The site is one file. Open `index.html`, edit, save, refresh. No build step.

If you change copy:

- Headlines: search for `h-display` or `h1` classes
- CTA text: search for `Reserve Adapt 1.0`
- Pricing: search for `price-row`

If you change the logo, replace `adapix_mark.svg` (transparent, icon-only). The nav and hero both reference this file via `<img src="adapix_mark.svg">`. The favicon is a data-URI inside the `<head>` — also needs replacing if you change brand colors.

---

## Deploying

It's a static page. Drop it on any host:

- **GitHub Pages**: push the `website/` folder to a `gh-pages` branch, point a custom domain at it
- **Vercel / Netlify**: drag-and-drop deploy
- **S3 + CloudFront**: standard static site setup
- **Cloudflare Pages**: GitHub integration

Whichever host you pick, set `index.html` as the root document. No server, no Node, no build.

---

## Future

- Add an **interactive Workshop preview** that mirrors the actual dashboard UI
- Add a **demo embed** that shows a short Higgsfield-rendered ad clip on the hero
- Add a **"For" page** per business type (the searchable picker has ~200 types; we can SEO each one separately later)
