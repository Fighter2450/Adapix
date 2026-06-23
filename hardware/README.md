# Adapt 1.0 — Hardware

Parametric 3D-printed enclosure for a Raspberry Pi 5. The case is generated from a Python script (`build_stl.py`) using `trimesh` + `shapely`. Edit a constant, re-run, get a new STL.

---

## Files

| File | What |
|---|---|
| `build_stl.py` | The case generator. Edit constants near the top, run with `python build_stl.py`, three STLs land in this folder. |
| `adapix_bottom.stl` | The bottom shell. Pi sits inside on a foam pad in the back-right corner. |
| `adapix_top.stl` | The lid. Engraved logo + vent slots + fan mount holes. |
| `adapix_assembled.stl` | Both shells combined, for visual reference. |
| `quick_render.py` | Renders the assembled case from 4 angles for review. |
| `HARDWARE_SHOPPING_LIST.md` | Everything you need to assemble one device. |
| `adapix_logo.svg` | The brand mark, used for engraving on the top of the case. |
| `adapix_mark.svg` | Same mark, icon-only (no wordmark below). |

---

## Specs

| | |
|---|---|
| **Footprint** | 150 × 150 mm square with chamfered front-left corner |
| **Height** | 50 mm body + 5 mm central pedestal foot underneath |
| **Corner radius** | 6 mm (three corners), 38 × 38 mm diagonal chamfer (front-left) |
| **Wall thickness** | 2.5 mm |
| **Lid thickness** | 4 mm |
| **Snap-fit line** | Z = 35 mm (clears all port cutouts) |
| **Material** | Matte black PLA or PETG works best — pops against the cyan underglow |

### Ports (cut into the side walls)

- **Back wall**: 2 × USB-A + RJ45 (one continuous pill cutout)
- **Right wall**: USB-C + 2 × micro-HDMI (pill cutouts)

The Pi sits in the **back-right corner** of the bottom shell on a 10 mm foam pad. Stuck down with double-sided tape — no standoffs needed.

### Top face engravings

- **Adapix A logo** (cyan-blue circuit-board style, color-separated SVG paths so the A body and circuit traces engrave as distinct features)
- **"ADAPT 1.0"** wordmark in the bottom-right
- **10 vent slots** cut **all the way through** the 4 mm lid for fan airflow
- **4 × Ø4 mm M3 screw holes** in a 32 × 32 mm pattern for a 40 mm fan

### Underglow

A 9.65 mm wide channel runs around the perimeter on the underside of the bottom shell. Drop in a standard WS2812B LED strip — it lights the desk surface with a halo of cyan.

A small **wire-feed slot** through the back wall lets the LED data and power leads come up into the cavity where the Pi is.

### Central pedestal foot

The bottom shell has a single central pedestal that drops 5 mm below the body. This lifts the LED channel off the desk so the underglow shines outward, and gives the device one solid point of contact instead of four wobbly feet.

---

## Print orientation

| Part | How |
|---|---|
| **Bottom shell** | Flat on the build plate, central pedestal pointing **down**. Tree supports under the LED channel area; everything else is overhang-friendly. |
| **Top shell** | **Top face on the build plate (upside-down)** for the cleanest engraving finish. No supports needed. |

Print at 0.2 mm layer height with 3 perimeters / 20% infill. Matte black PLA looks best with the cyan underglow.

---

## Generating new STLs

```bash
cd hardware/
python3 build_stl.py
```

Three files get written:

```
adapix_bottom.stl
adapix_top.stl
adapix_assembled.stl     ← top + bottom merged, visual reference only
```

Every CSG step in `build_stl.py` asserts `is_volume` and `is_watertight` so the output is always print-ready.

If you only changed the constant for an existing feature (corner radius, vent count, fan hole diameter, etc.) the diff in the STL will be tight. If you added a new boolean (new port, new engraving, new cutout) re-render and visually verify with `quick_render.py` before printing.

---

## Customizing the case

Open `build_stl.py` and look at the constants block near the top:

```python
BODY_W = 150.0          # case width  (left-right)
BODY_D = 150.0          # case depth  (front-back)
BODY_H = 50.0           # case height
CORNER_R = 6.0          # rounded corner radius
CHAMFER_X = 38.0        # chamfered corner width
CHAMFER_Y = 38.0        # chamfered corner depth
WALL = 2.5              # wall thickness
LID = 4.0               # lid thickness
SNAP_Z = 35.0           # snap-fit height
# ...
```

Bump anything, re-run. The build never crashes on invalid geometry — if you make the wall too thin or the cavity too small, the CSG fails an assertion before exporting.

---

## Hero render for marketing / Higgsfield references

The `*_reference.png` files in this folder are **gitignored** — they're regenerated from the STL + logo SVG sources as needed for product photography or AI video pipelines. The render scripts that produce them live in the project history.

To regenerate a hero render:

```bash
python3 -c "
import trimesh, matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
mesh = trimesh.load('adapix_assembled.stl')
# ... see project history for the full render script
"
```

---

## Versioning

The case is at **v35**. Each version is a single point change (vent slots cut through, fan-hole diameter bumped from 3.2 to 4 mm, central pedestal added, power button hole removed, etc.). Major version history lives in the git log and the task tracker.

Whenever you change `build_stl.py`, bump a slice in `quick_render.py`, run both, and visually verify the result before printing. A 5-second visual check has caught more bugs than the CSG assertions.
