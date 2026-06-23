"""Adapix Adapt 1.0 - v26 (original square form rebuild, no LCD).

Form (back to the original printed reference photo):
  * 150 x 150 x 50 mm square footprint, flat top
  * Front-left bottom corner chamfered with a diagonal cut (italic-A motif)
  * Adapix A logo engraved on top face (centered-left), SVG-traced
  * Vent slots on top face (top-right area)
  * "Adapt 1.0" engraved on top face (bottom-right)

Internals (kept from the v25 lozenge build):
  * Pi 5 sits in back-right corner of the cavity, taped to floor on a
    10 mm foam riser
  * USB-A x2 + RJ45 exit the back wall (Pi's short edge)
  * USB-C + 2x HDMI exit the right wall (Pi's long edge)
  * Two-piece snap fit at z = 25 mm
  * Wall 2.5 mm, lid 4 mm, sharp interior cavity so the Pi fits flush

Every CSG step asserts is_volume.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon, LineString, Point


# ============================================================
# Dimensions (mm)
# ============================================================
BODY_W = 150.0
BODY_D = 150.0
BODY_H = 50.0
CORNER_R = 6.0     # plan-view rounding on the three non-chamfered corners

# Chamfer on front-left bottom corner: cut size along front edge and left edge
CHAMFER_X = 38.0   # how far the cut extends along the front edge (X)
CHAMFER_Y = 38.0   # how far the cut extends along the left edge (Y)

WALL    = 2.5
LID     = 4.0
SNAP_Z  = 35.0   # above the tallest port cutout (USB-A top at z ~29.6)
LIP_H   = 5.0
LIP_GAP = 0.3

# Pi 5 - same layout as v25
MOUNT_H    = 10.0
PCB_THICK  = 1.4
PCB_BOTTOM = MOUNT_H
PCB_TOP    = MOUNT_H + PCB_THICK

PI_LONG  = 85.0
PI_SHORT = 56.0
PI_X = BODY_W - WALL - PI_SHORT     # PCB right edge AT inside right wall
PI_Y = BODY_D - WALL - PI_LONG      # PCB back edge AT inside back wall

LONG_EDGE_PORTS = [
    ("usbc",  11.2),
    ("hdmi",  25.8),
    ("hdmi",  39.2),
]
SHORT_EDGE_PORTS = [
    ("rj45", 10.0),
    ("usba", 28.0),
    ("usba", 46.0),
]

USBA_W, USBA_H = 13.4, 15.4
RJ45_W, RJ45_H = 15.5, 13.5
USBC_W, USBC_H = 9.0, 4.0
HDMI_W, HDMI_H = 8.0, 4.5

USBA_Z  = PCB_TOP + 8.0
RJ45_Z  = PCB_TOP + 7.0
USBC_Z  = PCB_TOP + 3.0
HDMI_Z  = PCB_TOP + 3.0

SD_W = 14.0
SD_D = 3.5

# Underglow LED channel (cut into the underside of the case floor)
UG_WIDTH  = 10.0   # LED strip width (9.65mm + 0.35mm clearance for friction fit)
UG_DEPTH  = 1.5    # depth into the floor (must be < WALL so floor isn't pierced)
UG_INSET  = 5.0    # gap from outer wall to the channel's outer edge
# Strip entry slot - a small notch through the back wall where the wire feeds in
UG_WIRE_SLOT_W = 4.0
UG_WIRE_SLOT_H = 2.5

# Feet (printed bumps on underside, so underglow has clearance to the desk)
FOOT_DIA   = 12.0
FOOT_H     = 5.0
FOOT_INSET = 22.0   # from outer wall (must clear the underglow channel)

# Power button access hole through the lid
# Pi 5's power button sits at PCB local (~5, 7) from the USB-C corner.
# In case coords (with our Pi orientation) that's roughly (140.5, 67.5).
PWR_BTN_DIA = 3.0
PWR_BTN_CX  = 140.5
PWR_BTN_CY  = 67.5

# 40 mm fan mount through the lid (4 M3 holes in a 32 mm square)
FAN_HOLE_PITCH = 32.0
FAN_SCREW_DIA  = 4.0     # bigger than M3 clearance so the through-cut is visually obvious
FAN_CX = 120.0           # match vent slot area
FAN_CY = 108.0

# Logo
LOGO_SVG_PKL    = "adapix_a_logo.pkl"
LOGO_HEIGHT     = 38.0    # mm (engraved on top, so this is its X-dim)
LOGO_DEPTH      = 1.2     # mm engraved depth
LOGO_CX         = BODY_W * 0.30   # placed on the LEFT half of the top
LOGO_CY         = BODY_D * 0.50

# Vent slots (top face, top-right area) - go ALL THE WAY THROUGH the lid
# so the fan can actually move air. Lid is LID=4mm so depth must exceed that.
VENT_COUNT      = 10
VENT_W          = 1.6      # along Y direction
VENT_L          = 26.0     # along X direction
VENT_PITCH      = 3.4
VENT_DEPTH      = LID + 2.0   # = 6mm, comfortably through the 4mm lid
VENT_AREA_CX    = BODY_W * 0.80
VENT_AREA_CY    = BODY_D * 0.72

# "Adapt 1.0" text (top face, bottom-right)
ADAPT_TEXT      = "ADAPT 1.0"
ADAPT_LETTER_W  = 4.5
ADAPT_LETTER_H  = 6.5
ADAPT_DEPTH     = 0.8
ADAPT_CX        = BODY_W * 0.78
ADAPT_CY        = BODY_D * 0.20

OUT = Path(__file__).parent


# ============================================================
# Helpers
# ============================================================
def rounded_rect(w, d, r, cx, cy, n=18):
    hw, hd = w / 2, d / 2
    centres = [
        (cx + hw - r, cy - hd + r, -math.pi / 2, 0.0),
        (cx + hw - r, cy + hd - r, 0.0, math.pi / 2),
        (cx - hw + r, cy + hd - r, math.pi / 2, math.pi),
        (cx - hw + r, cy - hd + r, math.pi, 3 * math.pi / 2),
    ]
    pts = []
    for cxr, cyr, t0, t1 in centres:
        for i in range(n + 1):
            t = t0 + (t1 - t0) * i / n
            pts.append((cxr + r * math.cos(t), cyr + r * math.sin(t)))
    poly = Polygon(pts)
    assert poly.is_valid
    return poly


def chamfered_square_poly():
    """Body footprint: rectangle with the front-left corner diagonally
    chamfered AND the other three corners rounded with CORNER_R radius."""
    r = CORNER_R
    n = 16    # arc resolution

    def arc(cx, cy, t0, t1):
        return [(cx + r * math.cos(t0 + (t1 - t0) * i / n),
                 cy + r * math.sin(t0 + (t1 - t0) * i / n))
                for i in range(n + 1)]

    pts = []
    # Start at front-edge end of chamfer
    pts.append((CHAMFER_X, 0))
    # Front edge to front-right rounded corner
    pts += arc(BODY_W - r, r, -math.pi / 2, 0.0)
    # Right edge to back-right rounded corner
    pts += arc(BODY_W - r, BODY_D - r, 0.0, math.pi / 2)
    # Back edge to back-left rounded corner
    pts += arc(r, BODY_D - r, math.pi / 2, math.pi)
    # Left edge down to chamfer start
    pts.append((0, CHAMFER_Y))
    # Diagonal chamfer back to start point (front edge after chamfer)
    p = Polygon(pts)
    assert p.is_valid, "chamfered+rounded footprint invalid"
    return p


def cavity_poly():
    """Interior cavity - same chamfered shape, inset by WALL."""
    # Use shapely's buffer with negative distance to inset
    return chamfered_square_poly().buffer(-WALL)


def extrude(poly, h, label=""):
    m = trimesh.creation.extrude_polygon(poly, height=h)
    if not m.is_volume:
        m.fix_normals(); m.process()
    assert m.is_volume, f"extrude({label}) not volume"
    return m


def box(extents, center, label=""):
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(center)
    assert m.is_volume, f"box({label}) not volume"
    return m


def cyl(r, h, center, axis="z", label=""):
    m = trimesh.creation.cylinder(radius=r, height=h, sections=48)
    if axis == "x":
        m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
    elif axis == "y":
        m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    m.apply_translation(center)
    assert m.is_volume, f"cyl({label}) not volume"
    return m


def stadium_polygon(w, h):
    r = min(w, h) / 2
    if w >= h:
        line = LineString([(-w / 2 + r, 0), (w / 2 - r, 0)])
    else:
        line = LineString([(0, -h / 2 + r), (0, h / 2 - r)])
    return line.buffer(r, resolution=18)


def pill(w, h, depth, center, axis, label=""):
    if axis == "x":
        poly = stadium_polygon(h, w)
        m = trimesh.creation.extrude_polygon(poly, height=depth)
        m.apply_translation([0, 0, -depth / 2])
        T = trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0])
        m.apply_transform(T)
    elif axis == "y":
        poly = stadium_polygon(w, h)
        m = trimesh.creation.extrude_polygon(poly, height=depth)
        m.apply_translation([0, 0, -depth / 2])
        T = trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0])
        m.apply_transform(T)
    m.apply_translation(center)
    if not m.is_volume:
        m.fix_normals(); m.process()
    assert m.is_volume, f"pill({label}) not volume"
    return m


def diff(a, b, label=""):
    assert a.is_volume
    assert b.is_volume
    r = a.difference(b)
    if not r.is_volume:
        r.fix_normals(); r.process()
    assert r.is_volume, f"POST diff ({label})"
    return r


def union(a, b, label=""):
    assert a.is_volume
    assert b.is_volume
    r = a.union(b)
    if not r.is_volume:
        r.fix_normals(); r.process()
    assert r.is_volume, f"POST union ({label})"
    return r


def merge(meshes, label="merge"):
    out = meshes[0]
    for i, m in enumerate(meshes[1:], 1):
        out = union(out, m, f"{label}-{i}")
    return out


# ============================================================
# Top-face engravings
# ============================================================
def _load_logo_polygon():
    """Load SVG-derived logo, scale to LOGO_HEIGHT, center at origin.
    Validate each piece INDEPENDENTLY so overlapping blue+cyan pieces aren't
    merged together by buffer(0)."""
    import pickle
    from shapely.geometry import MultiPolygon as _MP
    from shapely.affinity import scale as _scale, translate as _translate
    pkl_path = OUT / LOGO_SVG_PKL
    with open(pkl_path, "rb") as f:
        p = pickle.load(f)
    minx, miny, maxx, maxy = p.bounds
    s = LOGO_HEIGHT / (maxy - miny)
    cx_off = -(minx + maxx) / 2
    cy_off = -(miny + maxy) / 2

    pieces_in = list(p.geoms) if p.geom_type == "MultiPolygon" else [p]
    pieces_out = []
    for piece in pieces_in:
        q = piece.simplify(0.5, preserve_topology=True)
        q = _translate(q, xoff=cx_off, yoff=cy_off)
        q = _scale(q, xfact=s, yfact=-s, origin=(0, 0))
        if not q.is_valid:
            q = q.buffer(0)
        if hasattr(q, 'geoms'):
            for g in q.geoms:
                if g.area > 0.5:
                    pieces_out.append(g)
        elif not q.is_empty and q.area > 0.5:
            pieces_out.append(q)
    return pieces_out[0] if len(pieces_out) == 1 else _MP(pieces_out)


def _top_engrave_cutter(poly_2d, depth, cx, cy):
    """Build a cutter mesh that engraves `poly_2d` into the TOP face of the
    body (the surface at z = BODY_H)."""
    from shapely.affinity import translate as _translate
    # Clean up any self-intersections from auto-traced SVG
    if not poly_2d.is_valid:
        poly_2d = poly_2d.buffer(0)
    if hasattr(poly_2d, 'geoms'):
        poly_2d = max(poly_2d.geoms, key=lambda g: g.area)
    poly = _translate(poly_2d, xoff=cx, yoff=cy)
    m = trimesh.creation.extrude_polygon(poly, height=depth + 0.5)
    m.apply_translation([0, 0, BODY_H - depth])
    if not m.is_volume:
        m.fix_normals(); m.process()
    assert m.is_volume, "top engrave cutter not volume"
    return m


def adapix_logo_top_cutters():
    """Engrave the Adapix A on the top face. Each color-separated piece
    becomes its own cutter so the A and circuit traces stay distinct."""
    poly = _load_logo_polygon()
    cutters = []
    pieces = list(poly.geoms) if poly.geom_type == "MultiPolygon" else [poly]
    for g in pieces:
        if g.is_empty or g.area < 1.0:
            continue
        try:
            cutters.append(_top_engrave_cutter(g, LOGO_DEPTH, LOGO_CX, LOGO_CY))
        except AssertionError as e:
            print(f"      (logo piece skipped: {e})")
    return cutters


def vent_slot_cutters():
    """Horizontal vent slots cut through the top face."""
    cutters = []
    total_h = (VENT_COUNT - 1) * VENT_PITCH
    y_start = VENT_AREA_CY - total_h / 2
    for i in range(VENT_COUNT):
        y = y_start + i * VENT_PITCH
        b = box([VENT_L, VENT_W, VENT_DEPTH + 0.5],
                [VENT_AREA_CX, y, BODY_H - VENT_DEPTH / 2 + 0.25],
                label=f"vent-{i}")
        cutters.append(b)
    return cutters


def _letter_strokes(ch, w, h):
    """Block-letter strokes."""
    sw = w * 0.20
    if ch == "A":
        return [(0, 0, sw, h), (w - sw, 0, sw, h),
                (0, h - sw, w, sw), (0, h * 0.45, w, sw)]
    if ch == "D":
        return [(0, 0, sw, h), (0, 0, w - sw * 0.6, sw),
                (0, h - sw, w - sw * 0.6, sw),
                (w - sw, sw, sw, h - 2 * sw)]
    if ch == "P":
        return [(0, 0, sw, h), (0, h - sw, w, sw),
                (0, h * 0.45, w, sw), (w - sw, h * 0.45, sw, h * 0.55)]
    if ch == "T":
        return [(0, h - sw, w, sw),
                ((w - sw) / 2, 0, sw, h - sw)]
    if ch == "1":
        return [((w - sw) / 2, 0, sw, h),
                ((w - sw) / 2 - sw, h - sw - sw * 1.5, sw, sw)]
    if ch == "0":
        return [(0, 0, sw, h), (w - sw, 0, sw, h),
                (0, 0, w, sw), (0, h - sw, w, sw)]
    if ch == ".":
        return [(w * 0.35, 0, sw, sw)]
    return []


def central_pedestal_mesh():
    """A single solid pedestal extending downward from the bottom of the case,
    sized to fit INSIDE the underglow channel ring (so the LEDs around the
    perimeter still have desk clearance and aren't blocked). Same FOOT_H
    height as the original 4 corner feet."""
    inset_total = UG_INSET + UG_WIDTH + 1.5    # outside-wall to pedestal edge
    poly = chamfered_square_poly().buffer(-inset_total)
    if hasattr(poly, 'geoms'):
        poly = max(poly.geoms, key=lambda g: g.area)
    pedestal = extrude(poly, FOOT_H, "pedestal")
    pedestal.apply_translation([0, 0, -FOOT_H])
    return pedestal


def power_button_hole_cutter():
    """3 mm hole CUT FULLY THROUGH the lid above the Pi 5's PCB power button.
    Press through with a pen tip / paperclip. Generous overshoot so the
    cylinder definitely pierces every voxel of the lid."""
    # Cylinder spans z = (BODY_H - LID - 4) to z = (BODY_H + 4),
    # which is way more than the LID thickness in both directions.
    h = LID + 8.0
    cz = BODY_H - LID / 2 + 2.0   # centered above the lid midline
    return cyl(PWR_BTN_DIA / 2, h, [PWR_BTN_CX, PWR_BTN_CY, cz],
               label="pwr-btn")


def fan_mount_holes_cutters():
    """4 x M3 clearance holes CUT FULLY THROUGH the lid in a 32 x 32 mm
    square pattern (matches a standard 40 mm fan's mounting hole spacing).
    Generous Z overshoot so the cylinder pierces the entire lid."""
    cutters = []
    half = FAN_HOLE_PITCH / 2
    h = LID + 8.0
    cz = BODY_H - LID / 2 + 2.0   # well above lid midline
    for dx, dy in [(-half, -half), (half, -half), (-half, half), (half, half)]:
        cutters.append(cyl(FAN_SCREW_DIA / 2, h,
                           [FAN_CX + dx, FAN_CY + dy, cz],
                           label=f"fan-{dx},{dy}"))
    return cutters


def underglow_channel_cutter():
    """Channel on the UNDERSIDE of the bottom shell that holds a WS2812B LED
    strip facing down. Strip light bounces off the desk and creates a halo
    around the case."""
    outer = chamfered_square_poly()
    channel_outer = outer.buffer(-UG_INSET)
    channel_inner = channel_outer.buffer(-UG_WIDTH)
    if channel_inner.is_empty:
        # Fall back: not enough room, just give a thin band
        channel_inner = channel_outer.buffer(-UG_WIDTH * 0.5)
    ring = channel_outer.difference(channel_inner)
    if ring.geom_type != "Polygon":
        # MultiPolygon - take the biggest piece
        ring = max(ring.geoms, key=lambda g: g.area)
    cutter = extrude(ring, UG_DEPTH + 0.5)
    # Cutter sits with its top at z=UG_DEPTH+0.25 and bottom at z=-0.25, so
    # it overlaps the floor underside cleanly.
    cutter.apply_translation([0, 0, -0.25])
    return cutter


def underglow_wire_slot_cutter():
    """Small notch through the back wall so the LED strip wire can feed
    from inside the cavity (where the Pi is) down to the underglow channel."""
    # Located near the back wall, in the wall material between the cavity
    # and the underglow channel.
    cx = WALL + UG_INSET + UG_WIDTH / 2 + 4   # left-back corner area
    cy = BODY_D - WALL / 2
    cz = UG_DEPTH + UG_WIRE_SLOT_H / 2 + 0.5
    return box([UG_WIRE_SLOT_W, WALL + 4, UG_WIRE_SLOT_H],
               [cx, cy, cz], label="ug-wire-slot")


def adapt_text_cutters():
    """Engrave 'ADAPT 1.0' on the top face."""
    n = len(ADAPT_TEXT)
    space_w = ADAPT_LETTER_W * 0.5
    # Build x offsets
    x_offsets = []
    cur = 0.0
    for ch in ADAPT_TEXT:
        x_offsets.append(cur)
        if ch == " ":
            cur += space_w
        else:
            cur += ADAPT_LETTER_W
    total_w = cur

    cutters = []
    for ch, x_off in zip(ADAPT_TEXT, x_offsets):
        for (rx, ry, rw, rh) in _letter_strokes(ch, ADAPT_LETTER_W * 0.8, ADAPT_LETTER_H):
            poly = Polygon([
                (rx, ry), (rx + rw, ry),
                (rx + rw, ry + rh), (rx, ry + rh),
            ])
            cutters.append(_top_engrave_cutter(
                poly, ADAPT_DEPTH,
                ADAPT_CX - total_w / 2 + x_off,
                ADAPT_CY - ADAPT_LETTER_H / 2))
    return cutters


# ============================================================
# Bottom shell
# ============================================================
def build_bottom():
    print("  [B1] body lower (chamfered square footprint)")
    shell = extrude(chamfered_square_poly(), SNAP_Z, "body-lower")

    print("  [B2] hollow cavity")
    cav_h = SNAP_Z - WALL + 0.1
    cavity = extrude(cavity_poly(), cav_h, "cavity")
    cavity.apply_translation([0, 0, WALL])
    shell = diff(shell, cavity, "bottom-hollow")

    print("  [B3] Pi I/O cutouts on back + right walls")
    cutters = []
    back_y_centre = BODY_D - WALL / 2 + 0.5
    for kind, pcb_y in SHORT_EDGE_PORTS:
        case_x = PI_X + (PI_SHORT - pcb_y)
        if kind == "usba":
            cutters.append(box([USBA_W, WALL + 6, USBA_H],
                               [case_x, back_y_centre, WALL + USBA_Z],
                               label="B-usba"))
        else:
            cutters.append(box([RJ45_W, WALL + 6, RJ45_H],
                               [case_x, back_y_centre, WALL + RJ45_Z],
                               label="B-rj45"))
    right_x_centre = BODY_W - WALL / 2 + 0.5
    for kind, pcb_x in LONG_EDGE_PORTS:
        case_y = PI_Y + pcb_x
        if kind == "usbc":
            cutters.append(pill(USBC_W, USBC_H, WALL + 6,
                                [right_x_centre, case_y, WALL + USBC_Z],
                                axis="x", label="R-usbc"))
        elif kind == "hdmi":
            cutters.append(pill(HDMI_W, HDMI_H, WALL + 6,
                                [right_x_centre, case_y, WALL + HDMI_Z],
                                axis="x", label="R-hdmi"))
    shell = diff(shell, merge(cutters, "io"), "io-cutouts")

    print("  [B4] (microSD floor slot removed - swap SD via lid instead)")

    print("  [B4.4] central pedestal (inside underglow ring)")
    try:
        shell = union(shell, central_pedestal_mesh(), "pedestal")
    except AssertionError as e:
        print(f"      (pedestal skipped: {e})")

    print("  [B4.5] underglow LED channel (underside)")
    try:
        shell = diff(shell, underglow_channel_cutter(), "underglow-ring")
    except AssertionError as e:
        print(f"      (skipped underglow channel: {e})")
    try:
        shell = diff(shell, underglow_wire_slot_cutter(), "underglow-wire")
    except AssertionError as e:
        print(f"      (skipped wire slot: {e})")

    print("  [B5] snap-fit lip")
    cav = cavity_poly()
    lip_outer_poly = cav.buffer(-LIP_GAP)
    lip_inner_poly = lip_outer_poly.buffer(-WALL)
    if not lip_outer_poly.is_empty and not lip_inner_poly.is_empty:
        lo = extrude(lip_outer_poly, LIP_H, "lip-outer")
        lo.apply_translation([0, 0, SNAP_Z])
        li = extrude(lip_inner_poly, LIP_H + 0.2, "lip-inner")
        li.apply_translation([0, 0, SNAP_Z - 0.1])
        lip = diff(lo, li, "lip-hollow")
        shell = union(shell, lip, "shell+lip")

    return shell


# ============================================================
# Top shell with logo + vents + Adapt 1.0 engravings
# ============================================================
def build_top():
    top_h = BODY_H - SNAP_Z

    print("  [T1] body upper (chamfered)")
    upper = extrude(chamfered_square_poly(), top_h, "body-upper")
    upper.apply_translation([0, 0, SNAP_Z])

    print("  [T2] hollow top shell (keep LID-thick roof)")
    cav = cavity_poly()
    cav_h = top_h - LID + 0.1
    cavity = extrude(cav, cav_h, "top-cavity")
    cavity.apply_translation([0, 0, SNAP_Z])
    upper = diff(upper, cavity, "top-hollow")

    print("  [T3] Adapix A logo on top")
    for c in adapix_logo_top_cutters():
        try:
            upper = diff(upper, c, "logo-top")
        except AssertionError as e:
            print(f"      (skipped: {e})")

    print("  [T4] vent slots on top")
    for c in vent_slot_cutters():
        try:
            upper = diff(upper, c, "vent")
        except AssertionError as e:
            print(f"      (skipped: {e})")

    print("  [T5] 'Adapt 1.0' on top")
    for c in adapt_text_cutters():
        try:
            upper = diff(upper, c, "adapt-text")
        except AssertionError as e:
            print(f"      (skipped: {e})")

    # [T6] power button hole removed

    print("  [T7] 40mm fan mounting holes through lid")
    for c in fan_mount_holes_cutters():
        try:
            upper = diff(upper, c, "fan-screw")
        except AssertionError as e:
            print(f"      (skipped: {e})")

    return upper


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("Adapix v32 - feet, power button, fan mount")
    print(f"  body: {BODY_W} x {BODY_D} x {BODY_H} mm")
    print(f"  feet: {FOOT_DIA}mm dia x {FOOT_H}mm tall")
    print()
    print("BOTTOM SHELL")
    bot = build_bottom()
    print(f"  -> verts={len(bot.vertices)}, faces={len(bot.faces)}, "
          f"watertight={bot.is_watertight}, volume={bot.is_volume}")
    print()
    print("TOP SHELL")
    top = build_top()
    print(f"  -> verts={len(top.vertices)}, faces={len(top.faces)}, "
          f"watertight={top.is_watertight}, volume={top.is_volume}")
    bot.export(OUT / "adapix_bottom.stl")
    top.export(OUT / "adapix_top.stl")
    asm = trimesh.util.concatenate([bot, top])
    asm.export(OUT / "adapix_assembled.stl")
    print(f"  bounds: {asm.bounds.tolist()}")
    print("Done.")
