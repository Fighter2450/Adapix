# Adapix Brand System (Locked)

## The logo

Bold custom 'A' letterform in navy with cyan circuit traces flowing in from the left, through the mark, and out the right. Reads as **data flowing through the brain**.

## The system

The mark and the wordmark are **separate, independent files**. Use them together when you want a full lockup. Use them apart when one or the other is enough.

| File | What it is | Where it goes |
|---|---|---|
| `adapix_mark_light.svg` | Mark only (logo, no text) — navy + cyan | Favicons, app icons, splash screens, the device |
| `adapix_mark_dark.svg` | Mark on dark backgrounds | Dark UI surfaces, hero images |
| `adapix_mark_mono.svg` | Mark in single navy color (no cyan) | 3D-print engraving, embossing, single-color uses |
| `adapix_a_only_light.svg` | Just the A (no circuits) | Tight spaces, favicon, status indicator |
| `adapix_a_only_dark.svg` | Just the A on dark | Same on dark |
| `adapix_wordmark_light.svg` | "Adapix" text only | Document headers, business cards, letterhead |
| `adapix_wordmark_dark.svg` | "Adapix" text on dark | Dark UI |
| `adapix_lockup_horizontal_light.svg` | Mark + wordmark, side-by-side | Web headers, slide deck masters |
| `adapix_lockup_horizontal_dark.svg` | Same on dark | Same on dark |
| `adapix_lockup_stacked_light.svg` | Mark above wordmark (matches reference) | Hero shots, t-shirts, app splash |
| `adapix_lockup_stacked_dark.svg` | Same on dark | Same on dark |

## Color palette (locked)

| Role | Name | Hex | Usage |
|---|---|---|---|
| Primary | Adapix Navy | `#0d2c5d` | A letterform, wordmark, primary text |
| Accent | Adapix Cyan | `#00b8e6` | Circuit traces, status indicators, hover states |
| Surface | White | `#ffffff` | Light backgrounds |
| Surface (dark) | Off-black | `#0a0c14` | Dark backgrounds |
| Subtle | Off-white | `#f6f7fa` | Section backgrounds, cards |

CSS:
```css
:root {
  --adapix-navy: #0d2c5d;
  --adapix-cyan: #00b8e6;
  --adapix-white: #ffffff;
  --adapix-black: #0a0c14;
  --adapix-surface: #f6f7fa;
}
```

## Typography

**Wordmark**: Inter Bold (font-weight 800), letter-spacing `-0.04em` (visually tight). Free at https://rsms.me/inter/.

For final brand assets, the wordmark should eventually be drawn as custom paths (so it doesn't depend on Inter being installed). v0 uses Inter as a placeholder — fine for pilot, replace before any printed marketing.

## Usage rules

**Do:**
- Use the lockup when introducing the brand for the first time on a surface
- Use the mark alone for repeat appearances, app icons, favicons, the device
- Use the wordmark alone in document bodies and footers
- Maintain a clearspace around the mark equal to the height of the cyan junction dot
- Use mono-navy for engraving on the 3D-printed device

**Don't:**
- Don't recolor the cyan or navy — those are the brand
- Don't add the cyan-violet gradient (that was an earlier exploration; this is the final palette)
- Don't squish, skew, or rotate the mark
- Don't add a stroke around the A — it's solid fill only
- Don't put the mark on a busy photographic background without a solid color block under it

## Engraving on the device

For the 3D-printed enclosure, use `adapix_mark_mono.svg`. Convert to a closed path, scale to fit the logo plate cutout in the top shell (~64×16mm), import into your slicer, engrave at 1mm depth.

I'll wire this into `build_stl.py` next session — it'll embed the SVG directly into the OpenSCAD/trimesh model so future prints have the engraved logo by default.

## Status

Brand system **LOCKED** as of May 7, 2026. Logo is final unless we deliberately revisit. Next steps below.

## Next steps

- [ ] Convert wordmark from Inter text to custom-drawn SVG paths (no font dependency)
- [ ] Generate PNG exports at common sizes (1024, 512, 256, 128, 64, 32)
- [ ] Generate `.ico` favicon bundle for web
- [ ] Apple/Android app icon (rounded square, 1024×1024 PNG)
- [ ] Update `build_stl.py` to engrave the mark into the 3D model's logo plate
- [ ] Update the admin UI HTML templates to use the real logo (currently text only)
- [ ] One-page brand guidelines PDF for handoff to anyone who later touches Adapix files
