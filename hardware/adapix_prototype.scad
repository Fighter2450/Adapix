// ===================================================================
//  ADAPIX  -  Prototype enclosure  (v0.1)
// ===================================================================
//  An MVP enclosure for the Adapix device. Mac-mini-inspired form
//  factor: rounded square, low profile, engraved wordmark on top,
//  status-LED inset, side vents, rear port cutout, recessed feet.
//
//  Two parts (print separately, assemble with a friction-fit lip):
//    - top_shell    : print upside-down (logo against the print bed
//                     for crispest finish, no supports required)
//    - bottom_shell : print right-side-up, no supports
//
//  Parameters at the top - tweak freely. Default size targets a
//  consumer FDM printer with a 220x220 bed (Ender 3, Prusa Mini, etc.).
//  Bump W/D up to 197 if you want true Mac-mini dimensions and your
//  printer can handle it.
//
//  Open in OpenSCAD ( https://openscad.org , free ). Press F5 to
//  preview, F6 to render, then File -> Export -> STL.
// ===================================================================

$fn = 80;            // smoothness of curves (raise for final print)

// ---------- DIMENSIONS (mm) ----------
W            = 150;  // width
D            = 150;  // depth
H            = 35;   // total height
WALL         = 2.5;  // wall thickness
R            = 12;   // outer corner radius
TOP_H        = 30;   // height of top shell
BOT_H        = H - TOP_H;

// ---------- BRANDING ----------
LOGO_TEXT    = "ADAPIX";
LOGO_SIZE    = 16;   // font size (mm)
LOGO_DEPTH   = 1.2;  // engrave depth
LOGO_FONT    = "Liberation Sans:style=Bold";

// ---------- LED INDICATOR ----------
LED_DIAM     = 6;
LED_DEPTH    = 0.8;
LED_OFFSET_Y = -22; // distance from logo (negative = toward front)

// ---------- VENTS ----------
VENT_COUNT   = 6;
VENT_W       = 2;
VENT_H       = 14;
VENT_SPACING = 12;

// ---------- REAR PORT CUTOUT ----------
PORT_W       = 50;
PORT_H       = 9;
PORT_Z       = 4;    // distance from inner floor

// ---------- FEET ----------
FOOT_DIAM    = 9;
FOOT_DEPTH   = 1.5;
FOOT_INSET   = 14;   // from each corner

// ---------- ASSEMBLY LIP ----------
LIP_H        = 2.5;  // tall enough to seat solidly
LIP_GAP      = 0.4;  // friction fit clearance

// ---------- WHAT TO RENDER ----------
//   "top"      - just the top shell, ready to print
//   "bottom"   - just the bottom shell, ready to print
//   "both"     - both, side by side (multi-part plate)
//   "preview"  - assembled, for visualizing the final device
PART = "preview";

// ===================================================================

module rounded_box(w, d, h, r) {
    hull() {
        for (x = [r, w - r], y = [r, d - r])
            translate([x, y, 0]) cylinder(h = h, r = r);
    }
}

module top_shell() {
    difference() {
        // Outer body
        rounded_box(W, D, TOP_H, R);
        // Hollow interior
        translate([WALL, WALL, -0.1])
            rounded_box(W - 2 * WALL, D - 2 * WALL,
                        TOP_H - WALL + 0.1, max(R - WALL, 1));
        // Engraved logo (sits centered on the TOP face when assembled)
        translate([W / 2, D / 2 + 4, TOP_H - LOGO_DEPTH])
            linear_extrude(LOGO_DEPTH + 0.1)
                text(LOGO_TEXT, size = LOGO_SIZE,
                     halign = "center", valign = "center",
                     font = LOGO_FONT);
        // Status LED indent
        translate([W / 2, D / 2 + LED_OFFSET_Y, TOP_H - LED_DEPTH])
            cylinder(h = LED_DEPTH + 0.1, d = LED_DIAM);
        // Side vents (left + right walls)
        vent_total = (VENT_COUNT - 1) * VENT_SPACING;
        for (i = [0:VENT_COUNT - 1]) {
            y = D / 2 - vent_total / 2 + i * VENT_SPACING;
            translate([-0.1, y - VENT_W / 2, TOP_H / 2 - VENT_H / 2])
                cube([WALL + 0.2, VENT_W, VENT_H]);
            translate([W - WALL - 0.1, y - VENT_W / 2, TOP_H / 2 - VENT_H / 2])
                cube([WALL + 0.2, VENT_W, VENT_H]);
        }
        // Rear port cutout
        translate([W / 2 - PORT_W / 2, D - WALL - 0.1, PORT_Z])
            cube([PORT_W, WALL + 0.2, PORT_H]);
    }
}

module bottom_shell() {
    difference() {
        rounded_box(W, D, BOT_H, R);
        // Recessed feet (top side after print, sits on table)
        for (fx = [FOOT_INSET, W - FOOT_INSET],
             fy = [FOOT_INSET, D - FOOT_INSET])
            translate([fx, fy, -0.1])
                cylinder(h = FOOT_DEPTH + 0.1, d = FOOT_DIAM);
        // Subtle "Adapix prototype 0.1" stamp on bottom
        translate([W / 2, D / 2, BOT_H - 0.5])
            linear_extrude(0.6)
                text("Adapix Prototype 0.1", size = 5,
                     halign = "center", valign = "center",
                     font = LOGO_FONT);
    }
    // Assembly lip rising from top of bottom shell
    translate([WALL + LIP_GAP, WALL + LIP_GAP, BOT_H])
        difference() {
            rounded_box(W - 2 * (WALL + LIP_GAP),
                        D - 2 * (WALL + LIP_GAP),
                        LIP_H, max(R - WALL - LIP_GAP, 1));
            translate([WALL, WALL, -0.1])
                rounded_box(W - 2 * (WALL + LIP_GAP) - 2 * WALL,
                            D - 2 * (WALL + LIP_GAP) - 2 * WALL,
                            LIP_H + 0.2, max(R - 2 * WALL - LIP_GAP, 1));
        }
}

// ---------- RENDER SELECTOR ----------
if (PART == "top") {
    top_shell();
} else if (PART == "bottom") {
    bottom_shell();
} else if (PART == "preview") {
    bottom_shell();
    translate([0, 0, BOT_H]) top_shell();
} else {
    // "both" - lay them out side by side
    bottom_shell();
    translate([W + 25, 0, 0]) top_shell();
}
