#!/usr/bin/env python3
"""
generate_carrier.py — procedural isometric sprite renderer for a modular sci-fi carrier.

The sprites are not drawn frame by frame. A carrier is described once as a small
3D model made of primitives (boxes, ellipsoids, cylinders, quads) grouped into
swappable *modules*. Each animation is a function that poses the model for a given
frame; the posed model is then rotated in 45 degree steps to produce all 8 facings
from that single pose, projected through a fixed isometric camera, depth resolved,
and drawn straight to pixels with hard edges, a limited palette and ball-style
shading. A 1px silhouette outline pass, an engine bloom pass and an optional baked
drop shadow finish each frame. Frames are then tiled into sprite sheets at 1x and 2x.

Dependencies: Python 3 + Pillow. Nothing else.

Usage:
    python3 generate_carrier.py                   # render every loadout
    python3 generate_carrier.py --loadout mk1     # one loadout
    python3 generate_carrier.py --contact         # also write contact sheets
    python3 generate_carrier.py --shadow          # bake a ground drop shadow
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from functools import partial
from multiprocessing import Pool

from PIL import Image

# --------------------------------------------------------------------------------------
# vector / basis math
# --------------------------------------------------------------------------------------


def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vmul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def vnorm(a):
    m = math.sqrt(vdot(a, a)) or 1.0
    return (a[0] / m, a[1] / m, a[2] / m)


def yaw_vec(v, a):
    """Rotate a vector about +z by `a` radians."""
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2])


def axes_from_euler(pitch=0.0, yaw=0.0, roll=0.0):
    """Local x/y/z axes expressed in world space (applied roll, then pitch, then yaw)."""
    ax, ay, az = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    if roll:
        c, s = math.cos(roll), math.sin(roll)
        ay, az = vadd(vmul(ay, c), vmul(az, s)), vadd(vmul(az, c), vmul(ay, -s))
    if pitch:
        c, s = math.cos(pitch), math.sin(pitch)
        ax, az = vadd(vmul(ax, c), vmul(az, -s)), vadd(vmul(az, c), vmul(ax, s))
    if yaw:
        ax, ay = yaw_vec(ax, yaw), yaw_vec(ay, yaw)
        az = yaw_vec(az, yaw)
    return (ax, ay, az)


def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def lerp(a, b, t):
    return a + (b - a) * t


def ease(t):
    """Smoothstep, for door travel and similar."""
    t = clamp(t)
    return t * t * (3.0 - 2.0 * t)


class Rng:
    """Tiny deterministic PRNG so every render of a frame is byte-identical."""

    def __init__(self, seed):
        self.s = (seed * 1103515245 + 12345) & 0x7FFFFFFF

    def next(self):
        self.s = (self.s * 1103515245 + 12345) & 0x7FFFFFFF
        return self.s / 0x7FFFFFFF

    def range(self, a, b):
        return a + (b - a) * self.next()


# --------------------------------------------------------------------------------------
# isometric camera
#
# World axes: +x bow, +y port, +z up. The camera is a fixed orthographic dimetric
# rig: yaw 45 degrees, pitch 30 degrees, which gives the classic 2:1 pixel ratio.
# R / U are the screen right and screen up axes in world space, D is the depth axis
# pointing back towards the camera.
# --------------------------------------------------------------------------------------

CAM_YAW = math.radians(45.0)
CAM_PITCH = math.radians(30.0)

CAM_R = (math.sin(CAM_YAW), -math.cos(CAM_YAW), 0.0)
CAM_U = (
    math.cos(CAM_YAW) * math.sin(CAM_PITCH),
    math.sin(CAM_YAW) * math.sin(CAM_PITCH),
    math.cos(CAM_PITCH),
)
CAM_D = vcross(CAM_R, CAM_U)

# Key light sits over the camera's left shoulder; the fill lifts the shadow side just
# enough to keep the lower ramp steps readable against the outline.
LIGHT = vnorm((-0.35, -0.55, 0.86))
FILL = vnorm((0.62, 0.48, 0.12))

SPRITE = 96  # 1x cell size, in pixels
MARGIN = 5  # keeps the outline + bloom inside the cell
FACING_NAMES = ["S", "SE", "E", "NE", "N", "NW", "W", "SW"]
FACING_YAW0 = math.radians(225.0)  # row 0 points screen-south


def project(p):
    """World point -> (screen x, screen y) in world units, y already flipped."""
    return (vdot(p, CAM_R), -vdot(p, CAM_U))


# --------------------------------------------------------------------------------------
# palette
#
# Hand-authored ramps, dark to light. Every surface in the model resolves to one of
# these steps, so the whole fleet shares a palette by construction.
# --------------------------------------------------------------------------------------

def _ramp(*hexes):
    return [tuple(int(h[i : i + 2], 16) for i in (0, 2, 4)) + (255,) for h in hexes]


# Geometry names materials semantically, so a paint scheme is a swappable table in the
# same way a hangar is a swappable module. `plate` is the bold painted armour block,
# `trench` the recessed strip lighting, `runlight` the marker lights along the deck.
PALETTES = {
    # Bone hull, crimson plating, amber-lit recesses, green running lights.
    "crimson": {
        "outline": (15, 11, 9, 255),
        "shadow": (12, 9, 7, 110),
        "glow_near": (255, 176, 72, 150),
        "glow_far": (240, 128, 32, 70),
        "bloom": {"engine", "spark", "trench", "bay"},
        "ramps": {
            "hull": _ramp("423c33", "635b4e", "8b8172", "b2a795", "d2c7b3"),
            "hull_dark": _ramp("29251f", "38332c", "4c463c", "645c4e", "7d7462"),
            "hull_light": _ramp("4e483e", "6f675a", "958b7a", "b8ad9a", "dbd1bd"),
            "deck": _ramp("332f28", "4a453c", "665f52", "857c6b", "a89d88"),
            "plate": _ramp("400a0a", "6e1010", "9c1818", "c62222", "de4038"),
            "armor": _ramp("1a1815", "262320", "35312c", "47423a", "5c5649"),
            "accent": _ramp("6b4a0c", "a06f10", "d4991c", "f0bb3c", "ffd97a"),
            "engine": _ramp("2a1408", "8c3a0d", "e0631a", "ff8a2c", "ffc074"),
            "window": _ramp("2a1e08", "6b4a0c", "c08a14", "ffc83c", "fff0b0"),
            "bay": _ramp("2a1a06", "7a5209", "cc8f12", "ffbe33", "ffe49a"),
            "trench": _ramp("2a1a06", "8a5c0a", "d69a14", "ffc63c", "ffe9a8"),
            "runlight": _ramp("07240f", "0f5a24", "1c9440", "3fe07a", "b8ffd2"),
            "nav_red": _ramp("2a0a0a", "6b1414", "b62020", "ff3b3b", "ffa0a0"),
            "nav_green": _ramp("07240f", "0f5a24", "1c9440", "3fe07a", "b8ffd2"),
            "spark": _ramp("30160a", "8a3c10", "e0731c", "ffb055", "fff0c8"),
        },
    },
    # The original cool scheme: steel blue hull, cyan bay lighting.
    "steel": {
        "outline": (10, 14, 22, 255),
        "shadow": (8, 11, 18, 110),
        "glow_near": (255, 154, 60, 150),
        "glow_far": (255, 110, 32, 70),
        # cool emitters stay crisp: a warm halo around a cyan light reads as a bug
        "bloom": {"engine", "spark"},
        "ramps": {
            "hull": _ramp("1b2432", "2c3a4e", "43566e", "5f7590", "8296ad"),
            "hull_dark": _ramp("10151e", "1a2230", "273244", "36455c", "4a5c76"),
            "hull_light": _ramp("2b3547", "445269", "62748f", "8494ad", "a8b6c9"),
            "deck": _ramp("12171f", "1d2532", "28323f", "364356", "4a5a70"),
            "plate": _ramp("10151e", "1a2230", "273244", "36455c", "4a5c76"),
            "armor": _ramp("14181f", "21272f", "2f3841", "404b57", "56636f"),
            "accent": _ramp("53270f", "7d3d15", "a4521c", "d9822b", "f2b45c"),
            "engine": _ramp("2a1408", "8c3a0d", "e0631a", "ff8a2c", "ffc074"),
            "window": _ramp("0d2029", "17495a", "2b8fa8", "5fcbe0", "b5f2ff"),
            "bay": _ramp("0b1c22", "12414f", "1f7d94", "3fb6ce", "7fe4f4"),
            "trench": _ramp("0b1c22", "12414f", "1f7d94", "3fb6ce", "7fe4f4"),
            "runlight": _ramp("0b1c22", "12414f", "1f7d94", "3fb6ce", "7fe4f4"),
            "nav_red": _ramp("2a0a0a", "6b1414", "b62020", "ff3b3b", "ffa0a0"),
            "nav_green": _ramp("07240f", "0f5a24", "1c9440", "4dff8a", "b8ffd2"),
            "spark": _ramp("30160a", "8a3c10", "e0731c", "ffb055", "fff0c8"),
        },
    },
}

# Emissive materials bypass the lighting: their ramp step comes from the intensity the
# animation sets, which is how one geometry reads as powered, idling or dead.
EMISSIVE = {"engine", "window", "bay", "trench", "runlight", "nav_red", "nav_green", "spark"}

MATERIALS = {}
BLOOMING = set()
OUTLINE = SHADOW = GLOW_NEAR = GLOW_FAR = None


def use_palette(name):
    """Bind one paint scheme for the rest of the run."""
    global MATERIALS, BLOOMING, OUTLINE, SHADOW, GLOW_NEAR, GLOW_FAR
    pal = PALETTES[name]
    MATERIALS = pal["ramps"]
    BLOOMING = pal["bloom"]
    OUTLINE = pal["outline"]
    SHADOW = pal["shadow"]
    GLOW_NEAR = pal["glow_near"]
    GLOW_FAR = pal["glow_far"]


use_palette("crimson")


# --------------------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------------------


class Prim:
    """One convex primitive: a box, ellipsoid, cylinder (local z axis) or flat quad."""

    __slots__ = ("kind", "c", "ax", "ay", "az", "h", "mat", "power")

    def __init__(self, kind, c, h, mat, axes=None, power=1.0):
        self.kind = kind
        self.c = c
        self.h = h
        self.mat = mat
        self.power = power  # emissive intensity, 0..1; ignored for lit materials
        self.ax, self.ay, self.az = axes or ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

    def yawed(self, a):
        p = Prim.__new__(Prim)
        p.kind, p.h, p.mat, p.power = self.kind, self.h, self.mat, self.power
        p.c = yaw_vec(self.c, a)
        p.ax, p.ay, p.az = yaw_vec(self.ax, a), yaw_vec(self.ay, a), yaw_vec(self.az, a)
        return p

    def corners(self):
        """World-space corners of the primitive's local bounding box."""
        hx, hy, hz = self.h
        out = []
        for sx in (-hx, hx):
            for sy in (-hy, hy):
                for sz in (-hz, hz):
                    out.append(
                        vadd(
                            self.c,
                            vadd(vadd(vmul(self.ax, sx), vmul(self.ay, sy)), vmul(self.az, sz)),
                        )
                    )
        return out


def box(c, h, mat, axes=None, power=1.0):
    return Prim("box", c, h, mat, axes, power)


def ell(c, r, mat, axes=None, power=1.0):
    return Prim("ell", c, r, mat, axes, power)


def cyl(p0, p1, r, mat, power=1.0):
    """Cylinder spanning p0..p1; built with its local z along the axis."""
    d = vsub(p1, p0)
    ln = math.sqrt(vdot(d, d))
    if ln < 1e-9:
        ln = 1e-9
    az = vmul(d, 1.0 / ln)
    tmp = (0.0, 0.0, 1.0) if abs(az[2]) < 0.9 else (1.0, 0.0, 0.0)
    ax = vnorm(vcross(tmp, az))
    ay = vcross(az, ax)
    return Prim("cyl", vmul(vadd(p0, p1), 0.5), (r, r, ln * 0.5), mat, (ax, ay, az), power)


def quad(c, h, mat, axes=None, power=1.0):
    return Prim("quad", c, (h[0], h[1], 0.0), mat, axes, power)


# --------------------------------------------------------------------------------------
# the carrier model
#
# Everything below is one ship described once. Modules are independent part lists,
# so a loadout is just a set of module names; swapping the hangar or the engines
# re-renders every facing and every animation with no other edits.
# --------------------------------------------------------------------------------------


def m_chassis(st, cfg):
    """Hull body, belly, prow taper, painted plating, trench lighting and greebles."""
    p = []
    pw = st["power"]
    # core hull: everything else hangs off this
    p.append(box((0.0, 0.0, -0.02), (1.80, 0.58, 0.24), "hull"))
    # belly plate, inset so the hull sides read as a step above it
    p.append(box((-0.15, 0.0, -0.34), (1.50, 0.46, 0.12), "hull_dark"))
    # prow: two shrinking steps then a nose cap. The bow block carries paint.
    p.append(box((1.96, 0.0, -0.02), (0.20, 0.20, 0.19), "plate"))
    p.append(box((2.26, 0.0, -0.02), (0.16, 0.15, 0.14), "hull_light"))
    p.append(ell((2.44, 0.0, -0.02), (0.14, 0.14, 0.10), "hull_light"))
    # bow sensor blister
    p.append(ell((2.40, 0.0, 0.10), (0.09, 0.07, 0.06), "window", power=0.72 * pw))

    for sy in (1.0, -1.0):
        fy = sy * 0.58  # the hull flank plane

        # Painted blocks fore and aft, laid on the flank as thin panels. Two-tone
        # plating is what carries the scheme, so it is geometry, not a texture.
        p.append(box((1.28, fy + sy * 0.008, -0.02), (0.44, 0.012, 0.205), "plate"))
        p.append(box((-1.24, fy + sy * 0.008, -0.02), (0.50, 0.012, 0.205), "plate"))
        p.append(box((0.02, fy + sy * 0.008, -0.20), (0.72, 0.012, 0.055), "plate"))

        # Recessed strip light between them: a dark inset with the lit strip proud of it.
        p.append(box((0.02, fy + sy * 0.004, 0.04), (0.76, 0.012, 0.05), "armor"))
        p.append(
            box((0.02, fy + sy * 0.012, 0.04), (0.70, 0.010, 0.022), "trench", power=0.85 * pw)
        )

        # Flank ribs, alternating depth and material so the side reads as built up
        for i in range(5):
            x = -1.34 + i * 0.62
            deep = i % 2 == 0
            p.append(
                box(
                    (x, sy * (0.60 + (0.03 if deep else 0.0)), -0.09),
                    (0.13 if deep else 0.08, 0.05, 0.12),
                    "armor" if deep else "hull_dark",
                )
            )
        # Small hull greebles: vents and conduit boxes
        for (gx, gz, gl, gh) in ((0.72, 0.12, 0.10, 0.04), (-0.44, 0.10, 0.07, 0.05),
                                 (1.62, -0.14, 0.09, 0.05), (-1.66, 0.06, 0.12, 0.06)):
            p.append(box((gx, sy * 0.605, gz), (gl, 0.030, gh), "hull_light"))

        # ventral pylons
        p.append(
            box(
                (-0.70, sy * 0.30, -0.52),
                (0.40, 0.07, 0.14),
                "armor",
                axes_from_euler(pitch=math.radians(6)),
            )
        )
    p.append(box((-1.30, 0.0, -0.40), (0.35, 0.05, 0.20), "armor"))
    return p


def m_bridge(st, cfg):
    """Island: command tower offset to starboard, window band, mast and strobe."""
    p = []
    pw = st["power"]
    iy = -0.58  # offset to starboard, like a wet-navy carrier
    # base plinth ties the tower into the deck instead of letting it float
    p.append(box((-1.00, iy, 0.40), (0.40, 0.26, 0.12), "hull"))
    p.append(box((-1.02, iy, 0.66), (0.31, 0.21, 0.26), "hull_light"))
    p.append(box((-1.06, iy, 0.94), (0.22, 0.15, 0.05), "hull_dark"))
    # painted band around the tower, matching the hull plating
    p.append(box((-1.02, iy, 0.86), (0.315, 0.215, 0.035), "plate"))
    # forward window band, wrapping onto the outboard flank
    p.append(box((-0.695, iy, 0.74), (0.02, 0.17, 0.07), "window", power=0.88 * pw))
    p.append(box((-1.00, iy - 0.215, 0.74), (0.22, 0.02, 0.06), "window", power=0.66 * pw))
    p.append(box((-1.00, iy, 0.545), (0.24, 0.215, 0.03), "window", power=0.45 * pw))
    # mast + strobe
    p.append(cyl((-1.10, iy, 0.98), (-1.14, iy, 1.42), 0.045, "hull_dark"))
    p.append(box((-1.12, iy, 1.20), (0.03, 0.16, 0.03), "hull_dark"))
    p.append(ell((-1.15, iy, 1.48), (0.075, 0.075, 0.075), "nav_red", power=st["strobe"] * pw))
    return p


# Deck plate geometry per hangar module: (half length, half width, centre x). Other
# modules look this up so that armour and turrets mount to whichever deck is fitted.
DECKS = {
    "hangar_small": (1.62, 0.80, 0.02),
    "hangar_large": (1.74, 1.14, 0.06),
}
DECK_TOP = 0.37


def deck_of(cfg):
    for name in cfg["modules"]:
        if name in DECKS:
            return DECKS[name]
    raise ValueError(f"loadout {cfg['name']!r} has no hangar module")


def _hangar(st, cfg, deck_half_x, deck_half_y, deck_x, sponsons=()):
    """
    Flight deck plus the launch bays. The deck is the ship's hero shape, so the
    hangar module owns it: a bigger bay module means a bigger, wider deck.
    """
    p = []
    pw = st["power"]
    door = ease(st["door"])

    # sponsons: outboard hull extensions that carry the outer bays
    for (sx, sy, shx, shy) in sponsons:
        p.append(box((sx, sy, -0.04), (shx, shy, 0.20), "hull"))
        p.append(box((sx - 0.10, sy, -0.24), (shx * 0.8, shy * 0.7, 0.06), "hull_dark"))

    # flight deck plate
    p.append(box((deck_x, 0.0, 0.29), (deck_half_x, deck_half_y, 0.08), "deck"))
    # raised edge rails, so the plate reads as a deck and not a lid
    for sy in (1.0, -1.0):
        p.append(box((deck_x, sy * (deck_half_y + 0.02), 0.34), (deck_half_x, 0.035, 0.05), "hull"))
        # green marker lights along the rail
        for i in range(5):
            lx = deck_x - deck_half_x + 0.28 + i * (deck_half_x * 2.0 - 0.56) / 4.0
            p.append(
                ell(
                    (lx, sy * (deck_half_y + 0.02), 0.40),
                    (0.028, 0.028, 0.028),
                    "runlight",
                    power=(0.35 + 0.50 * door) * pw,
                )
            )

    # Painted deck blocks fore and aft. These read at 96px far better than fine
    # markings do, and they are what makes the scheme legible from every facing.
    # The livery is bone-dominant: paint claims the bow apron and the stern block,
    # and the long middle of the deck stays hull colour.
    p.append(
        box((deck_x + deck_half_x * 0.80, 0.0, 0.372),
            (deck_half_x * 0.20, deck_half_y * 0.96, 0.008), "plate")
    )
    p.append(
        box((deck_x - deck_half_x * 0.84, 0.0, 0.372),
            (deck_half_x * 0.16, deck_half_y * 0.96, 0.008), "plate")
    )
    # painted landing strip down the bone section
    p.append(box((deck_x - 0.05, 0.14, 0.374), (deck_half_x * 0.50, 0.05, 0.008), "accent"))

    # Fleet insignia: a chevron struck across the bone panel, in the same paint as
    # the plating so the ship reads as one livery.
    ins_x, ins_y = deck_x - deck_half_x * 0.10, -deck_half_y * 0.40
    for sy in (1.0, -1.0):
        p.append(
            box((ins_x, ins_y + sy * 0.15, 0.375), (0.26, 0.055, 0.008), "plate",
                axes_from_euler(yaw=math.radians(36 * sy)))
        )

    # recessed, lit deck trenches either side of the strip
    for sy in (1.0, -1.0):
        ty = sy * deck_half_y * 0.72
        p.append(box((deck_x - 0.05, ty, 0.368), (deck_half_x * 0.44, 0.055, 0.012), "armor"))
        p.append(
            box((deck_x - 0.05, ty, 0.374), (deck_half_x * 0.40, 0.030, 0.008),
                "trench", power=(0.45 + 0.45 * door) * pw)
        )

    # elevator pads and deck-side superstructure blocks
    for ex in (deck_x - deck_half_x * 0.42, deck_x + deck_half_x * 0.16):
        p.append(box((ex, -deck_half_y * 0.36, 0.372), (0.17, 0.15, 0.008), "hull_dark"))
    for (bx, by, bl, bw, bh) in (
        (deck_x - deck_half_x * 0.30, deck_half_y * 0.50, 0.20, 0.11, 0.07),
        (deck_x + deck_half_x * 0.24, deck_half_y * 0.62, 0.13, 0.09, 0.05),
        (deck_x - deck_half_x * 0.66, -deck_half_y * 0.66, 0.15, 0.10, 0.06),
    ):
        p.append(box((bx, by, DECK_TOP + bh), (bl, bw, bh), "hull_light"))
        p.append(box((bx, by, DECK_TOP + bh * 2.0), (bl * 0.6, bw * 0.6, 0.02), "armor"))
    # sensor masts
    for (mx, my, mh) in ((deck_x + deck_half_x * 0.52, deck_half_y * 0.30, 0.34),
                         (deck_x - deck_half_x * 0.88, deck_half_y * 0.20, 0.26)):
        p.append(cyl((mx, my, DECK_TOP), (mx, my, DECK_TOP + mh), 0.022, "armor"))
        p.append(ell((mx, my, DECK_TOP + mh), (0.035, 0.035, 0.035), "nav_red",
                     power=st["strobe"] * pw))

    # launch bays: recessed mouths in a forward-facing hull face
    for (by, front_x, bz) in cfg["bays"]:
        # capped short of the ramp's top step: a fully open bay should read as deep
        # amber light, not a blown-out white hole in the hull
        inner = 0.25 + 0.55 * door
        p.append(box((front_x - 0.18, by, bz), (0.18, 0.20, 0.12), "bay", power=inner * pw))
        # two-piece iris door: upper half retracts up, lower half drops
        travel = 0.24 * door
        p.append(box((front_x + 0.02, by, bz + 0.062 + travel), (0.035, 0.22, 0.065), "hull"))
        p.append(box((front_x + 0.02, by, bz - 0.062 - travel), (0.035, 0.22, 0.065), "hull"))
        # approach lights either side of the mouth
        for sy in (1.0, -1.0):
            p.append(
                ell(
                    (front_x - 0.01, by + sy * 0.235, bz),
                    (0.032, 0.032, 0.032),
                    "bay",
                    power=(0.25 + 0.75 * door) * pw,
                )
            )
    # navigation lights on the deck corners: port red, starboard green
    p.append(
        ell((deck_x + deck_half_x - 0.1, deck_half_y + 0.02, 0.40), (0.05, 0.05, 0.05),
            "nav_red", power=st["nav"] * pw)
    )
    p.append(
        ell((deck_x + deck_half_x - 0.1, -deck_half_y - 0.02, 0.40), (0.05, 0.05, 0.05),
            "nav_green", power=st["nav"] * pw)
    )
    return p


def m_hangar_small(st, cfg):
    """Two-squadron bay: the starting module."""
    hx, hy, dx = DECKS["hangar_small"]
    return _hangar(st, cfg, hx, hy, dx)


def m_hangar_large(st, cfg):
    """Four-squadron bay: wider, longer deck carried on a pair of sponsons."""
    hx, hy, dx = DECKS["hangar_large"]
    return _hangar(
        st, cfg, hx, hy, dx,
        sponsons=[(0.60, 0.80, 0.92, 0.24), (0.60, -0.80, 0.92, 0.24)],
    )


def _engine(p, x0, x1, y, z, r, st, big=True):
    """One nacelle: housing, nozzle disc, and a plume whose length tracks throttle."""
    thr = st["throttle"]
    p.append(cyl((x0, y, z), (x1, y, z), r, "hull"))
    p.append(cyl((x1 - 0.06, y, z), (x1 - 0.02, y, z), r * 1.08, "hull_dark"))
    # painted band and an intake ring, so the nacelle carries the livery too
    p.append(cyl((x0 - 0.16, y, z), (x0 - 0.02, y, z), r * 1.04, "plate"))
    p.append(cyl((x0 + 0.06, y, z), (x0 + 0.10, y, z), r * 1.05, "hull_light"))
    p.append(cyl((x1 - 0.02, y, z), (x1 + 0.005, y, z), r * 0.70, "engine", power=0.30 + 0.62 * thr))
    if thr > 0.02:
        # the plume is a short bright core plus a longer, dimmer, narrower tail
        ln = (0.18 + 0.60 * thr) * st["plume"]
        core = r * (0.46 if big else 0.40)
        p.append(cyl((x1 + 0.005, y, z), (x1 - ln * 0.50, y, z), core, "engine",
                     power=0.52 + 0.40 * thr))
        p.append(cyl((x1 - ln * 0.45, y, z), (x1 - ln, y, z), core * 0.66, "engine",
                     power=0.22 + 0.28 * thr))
    return p


def m_engines_basic(st, cfg):
    """Two main nacelles plus a pair of dorsal manoeuvring thrusters."""
    p = []
    for sy in (1.0, -1.0):
        _engine(p, -1.92, -2.62, sy * 0.60, 0.02, 0.25, st)
    p.append(box((-2.05, 0.0, 0.06), (0.28, 0.46, 0.28), "hull_dark"))
    for sy in (1.0, -1.0):
        p.append(
            cyl((-2.12, sy * 0.16, 0.34), (-2.34, sy * 0.16, 0.34), 0.075, "hull_light")
        )
        p.append(
            cyl(
                (-2.34, sy * 0.16, 0.34),
                (-2.38, sy * 0.16, 0.34),
                0.06,
                "engine",
                power=0.25 + 0.45 * st["throttle"],
            )
        )
    return p


def m_engines_uprated(st, cfg):
    """Four nacelles: the inboard pair grows, an outboard pair is added."""
    p = []
    for sy in (1.0, -1.0):
        _engine(p, -1.88, -2.70, sy * 0.52, 0.02, 0.28, st)
        _engine(p, -1.72, -2.34, sy * 1.02, -0.10, 0.18, st, big=False)
        # outboard pylon tying the small nacelle to the hull
        p.append(box((-1.70, sy * 0.80, -0.06), (0.16, 0.24, 0.06), "armor"))
    p.append(box((-2.05, 0.0, 0.08), (0.30, 0.44, 0.30), "hull_dark"))
    return p


def m_sensor_array(st, cfg):
    """Dish and search bar on the island."""
    p = []
    spin = st["t"] * math.tau
    ax = axes_from_euler(pitch=math.radians(-58), yaw=spin)
    p.append(cyl((-1.53, 0.0, 1.02), (-1.53, 0.0, 1.10), 0.05, "hull_dark"))
    p.append(Prim("cyl", (-1.53, 0.0, 1.16), (0.20, 0.20, 0.022), "hull_light", ax))
    p.append(
        box(
            (-0.95, 0.0, 0.50),
            (0.05, 0.34, 0.05),
            "hull_dark",
            axes_from_euler(yaw=spin * 0.5),
        )
    )
    return p


def m_weapons_pods(st, cfg):
    """Four twin-barrel turrets on deck-edge sponsons, where they stay readable."""
    p = []
    hx, hy, dx = deck_of(cfg)
    sweep = math.sin(st["t"] * math.tau) * 0.18
    mounts = [
        ((dx + hx * 0.55, hy + 0.06, DECK_TOP + 0.05), math.radians(60)),
        ((dx + hx * 0.55, -hy - 0.06, DECK_TOP + 0.05), math.radians(-60)),
        ((dx - hx * 0.62, hy + 0.06, DECK_TOP + 0.05), math.radians(130)),
        ((dx - hx * 0.62, -hy - 0.06, DECK_TOP + 0.05), math.radians(-130)),
    ]
    for (c, ang) in mounts:
        # plinth first, so the turret does not look glued to the deck rail
        p.append(box((c[0], c[1], c[2] - 0.10), (0.17, 0.15, 0.07), "armor"))
        ax = axes_from_euler(yaw=ang + sweep)
        p.append(ell(c, (0.14, 0.14, 0.10), "hull_light", ax))
        for sy in (1.0, -1.0):
            off = vadd(c, vmul(ax[1], sy * 0.05))
            p.append(
                cyl(vadd(off, vmul(ax[0], 0.04)), vadd(off, vmul(ax[0], 0.30)), 0.030, "hull_dark")
            )
    return p


def m_armor_belt(st, cfg):
    """Ablative plating: a belt under the deck rim, prow shoulders and a keel plate."""
    p = []
    hx, hy, dx = deck_of(cfg)
    for sy in (1.0, -1.0):
        # segmented belt hugging the underside of the deck rim
        for i in range(5):
            x = dx - hx * 0.80 + i * (hx * 1.60) / 4.0
            p.append(box((x, sy * (hy - 0.01), 0.17), (hx * 0.16, 0.075, 0.10), "armor"))
        # prow shoulder plates, angled in towards the nose
        p.append(
            box(
                (1.70, sy * 0.56, 0.00),
                (0.34, 0.075, 0.20),
                "armor",
                axes_from_euler(yaw=math.radians(-9 * sy)),
            )
        )
    p.append(box((-0.20, 0.0, -0.47), (1.50, 0.44, 0.05), "armor"))
    return p


def m_fighters(st, cfg):
    """Squadron craft, only present while a launch is in progress."""
    p = []
    launch = st["launch"]
    if launch <= 0.0:
        return p
    for i, (by, front_x, bz) in enumerate(cfg["bays"]):
        # stagger the squadrons so they leave the deck one after another
        t = clamp((launch - i * 0.10) / 0.62)
        if t <= 0.0:
            continue
        fade = 1.0 - clamp((t - 0.72) / 0.28)
        if fade <= 0.02:
            continue
        # travel is kept short enough that the squadron never leaves the cell
        x = lerp(front_x - 0.06, front_x + 0.75, ease(t))
        z = lerp(bz, bz + 0.38, ease(t))
        ax = axes_from_euler(pitch=math.radians(-11), yaw=math.radians(4 * (1 if by > 0 else -1)))
        p.append(ell((x, by, z), (0.22, 0.10, 0.07), "hull_light", ax))
        for sy in (1.0, -1.0):
            p.append(
                quad(
                    (x - 0.07, by + sy * 0.15, z - 0.01),
                    (0.13, 0.10),
                    "hull",
                    axes_from_euler(roll=math.radians(12 * sy), yaw=math.radians(8 * sy)),
                )
            )
        # lit canopy and a hot thruster: at this size they are what makes a fighter
        # legible against the hull it is flying over
        p.append(ell((x + 0.04, by, z + 0.05), (0.06, 0.045, 0.035), "window", power=0.90 * fade))
        p.append(ell((x - 0.22, by, z), (0.065, 0.055, 0.05), "engine", power=0.60 + 0.40 * fade))
    return p


def m_damage(st, cfg):
    """Sparks and vented plasma, driven by a per-frame deterministic RNG."""
    p = []
    dmg = st["damage"]
    if dmg <= 0.0:
        return p
    hx, hy, dx = deck_of(cfg)
    rng = Rng(st["frame"] * 977 + 31)
    # a breach in the deck, always in the same place so the damage reads as a wound
    bx, by = dx - hx * 0.30, hy * 0.45
    p.append(box((bx, by, DECK_TOP - 0.03), (0.26, 0.20, 0.05), "hull_dark"))
    p.append(
        box((bx - 0.22, by + 0.16, DECK_TOP + 0.04), (0.16, 0.05, 0.12), "hull_dark",
            axes_from_euler(roll=math.radians(26)))
    )
    # fire in the breach, flickering on the frame clock
    for i in range(3):
        f = 0.55 + 0.45 * math.sin(st["t"] * math.tau * 3.0 + i * 2.1)
        p.append(
            ell((bx + (i - 1) * 0.13, by, DECK_TOP + 0.02 + 0.05 * f),
                (0.09, 0.08, 0.06 + 0.05 * f), "spark", power=(0.55 + 0.45 * f) * dmg)
        )
    # sparks thrown clear of the hull
    for _ in range(int(4 + 5 * dmg)):
        x = rng.range(bx - 0.5, bx + 0.7)
        y = by + rng.range(-0.35, 0.45)
        z = rng.range(DECK_TOP - 0.05, DECK_TOP + 0.55)
        r = rng.range(0.035, 0.070)
        p.append(ell((x, y, z), (r, r, r), "spark", power=rng.range(0.50, 1.0)))
    return p


MODULES = {
    "chassis": m_chassis,
    "bridge_std": m_bridge,
    "hangar_small": m_hangar_small,
    "hangar_large": m_hangar_large,
    "engines_basic": m_engines_basic,
    "engines_uprated": m_engines_uprated,
    "sensor_array": m_sensor_array,
    "weapons_pods": m_weapons_pods,
    "armor_belt": m_armor_belt,
}

# A bay is (lateral offset, the x of the hull face it opens through, its height).
BAYS_2 = [(0.38, 1.80, -0.02), (-0.38, 1.80, -0.02)]
BAYS_4 = BAYS_2 + [(0.80, 1.52, -0.04), (-0.80, 1.52, -0.04)]

LOADOUTS = {
    "mk1": {
        "name": "Lancer-class escort carrier",
        "modules": ["chassis", "bridge_std", "hangar_small", "engines_basic"],
        "squadrons": 2,
        "bays": BAYS_2,
    },
    "mk2": {
        "name": "Lancer-class, expanded bay refit",
        "modules": ["chassis", "bridge_std", "hangar_large", "engines_uprated", "sensor_array"],
        "squadrons": 4,
        "bays": BAYS_4,
    },
    "mk3": {
        "name": "Lancer-class battlecarrier",
        "modules": [
            "chassis",
            "bridge_std",
            "hangar_large",
            "engines_uprated",
            "sensor_array",
            "weapons_pods",
            "armor_belt",
        ],
        "squadrons": 4,
        "bays": BAYS_4,
    },
}


def build_model(cfg, st):
    """Pose the whole ship for one frame, in ship-local space."""
    prims = []
    for name in cfg["modules"]:
        prims.extend(MODULES[name](st, cfg))
    prims.extend(m_fighters(st, cfg))
    prims.extend(m_damage(st, cfg))
    return prims


# --------------------------------------------------------------------------------------
# animations
#
# Each entry poses the model for frame i of n. `bob` is applied at render time as a
# whole-ship vertical offset so the hull keeps its pixel registration between sheets.
# --------------------------------------------------------------------------------------


def base_state(frame, n):
    t = frame / n
    return {
        "frame": frame,
        "t": t,
        "throttle": 0.0,
        "plume": 1.0,
        "door": 0.0,
        "launch": 0.0,
        "damage": 0.0,
        "power": 1.0,
        "nav": 1.0,
        "strobe": 0.0,
        "bob": 0.0,
    }


def a_idle(frame, n):
    st = base_state(frame, n)
    ph = st["t"] * math.tau
    st["throttle"] = 0.16 + 0.05 * math.sin(ph * 2.0)
    st["nav"] = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(ph))
    st["strobe"] = 1.0 if frame % n < 2 else 0.12
    st["bob"] = math.sin(ph) * 0.035
    return st


def a_cruise(frame, n):
    st = base_state(frame, n)
    ph = st["t"] * math.tau
    st["throttle"] = 0.88 + 0.12 * math.sin(ph * 3.0)
    st["plume"] = 0.90 + 0.10 * math.sin(ph * 4.0 + 1.1)
    st["nav"] = 0.8
    st["strobe"] = 1.0 if frame % 4 == 0 else 0.10
    return st


def a_bay_open(frame, n):
    st = base_state(frame, n)
    st["throttle"] = 0.12
    st["door"] = clamp(frame / (n - 1.0))
    st["nav"] = 0.7
    st["strobe"] = 1.0 if frame % 3 == 0 else 0.12
    return st


def a_launch(frame, n):
    st = base_state(frame, n)
    t = frame / (n - 1.0)
    st["throttle"] = 0.20
    st["door"] = clamp(t / 0.25)
    st["launch"] = clamp((t - 0.16) / 0.84)
    st["nav"] = 0.9
    st["strobe"] = 1.0 if frame % 2 == 0 else 0.15
    return st


def a_damage(frame, n):
    st = base_state(frame, n)
    ph = st["t"] * math.tau
    flick = 0.5 + 0.5 * math.sin(ph * 5.0)
    st["throttle"] = 0.30 * flick
    st["plume"] = 0.55
    st["damage"] = 0.75 + 0.25 * flick
    st["power"] = 0.35 + 0.65 * (1.0 if frame % 3 else 0.25)
    st["nav"] = 0.4
    st["strobe"] = 1.0 if frame % 2 == 0 else 0.0
    st["bob"] = math.sin(ph * 3.0) * 0.03
    return st


def a_powerdown(frame, n):
    st = base_state(frame, n)
    t = frame / (n - 1.0)
    fade = 1.0 - ease(t)
    st["throttle"] = 0.55 * fade
    st["power"] = fade
    st["nav"] = fade
    st["strobe"] = fade if frame % 3 == 0 else 0.0
    return st


ANIMATIONS = [
    ("idle", a_idle, 8, 10, True),
    ("cruise", a_cruise, 8, 14, True),
    ("bay_open", a_bay_open, 8, 12, False),
    ("launch", a_launch, 12, 14, False),
    ("damage", a_damage, 8, 12, True),
    ("powerdown", a_powerdown, 8, 10, False),
]


# --------------------------------------------------------------------------------------
# rasteriser
#
# The camera is orthographic, so the view ray direction is constant for the whole
# frame. That means each primitive only needs its local-space ray direction computed
# once; per pixel we solve an exact intersection inside the primitive's projected
# bounding box and keep the nearest hit in a depth buffer. Hard pixel edges, no
# antialiasing, correct occlusion between interpenetrating parts.
# --------------------------------------------------------------------------------------


def _hit_box(ol, dl, inv, h):
    tmin, tmax, axis, sgn = -1e30, 1e30, 0, 1.0
    for i in range(3):
        if inv[i] is None:  # ray parallel to this slab
            if abs(ol[i]) > h[i]:
                return None
            continue
        t1 = (-h[i] - ol[i]) * inv[i]
        t2 = (h[i] - ol[i]) * inv[i]
        s = -1.0
        if t1 > t2:
            t1, t2 = t2, t1
            s = 1.0
        if t1 > tmin:
            tmin, axis, sgn = t1, i, s
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return None
    n = [0.0, 0.0, 0.0]
    n[axis] = sgn
    return tmin, (n[0], n[1], n[2])


def _hit_ell(ol, dl, h):
    ox, oy, oz = ol[0] / h[0], ol[1] / h[1], ol[2] / h[2]
    dx, dy, dz = dl[0] / h[0], dl[1] / h[1], dl[2] / h[2]
    a = dx * dx + dy * dy + dz * dz
    if a < 1e-18:
        return None
    b = ox * dx + oy * dy + oz * dz
    c = ox * ox + oy * oy + oz * oz - 1.0
    disc = b * b - a * c
    if disc < 0.0:
        return None
    sq = math.sqrt(disc)
    t = (-b - sq) / a
    px, py, pz = ol[0] + dl[0] * t, ol[1] + dl[1] * t, ol[2] + dl[2] * t
    n = vnorm((px / (h[0] * h[0]), py / (h[1] * h[1]), pz / (h[2] * h[2])))
    return t, n


def _hit_cyl(ol, dl, h):
    r, hz = h[0], h[2]
    a = dl[0] * dl[0] + dl[1] * dl[1]
    best = None
    if a > 1e-18:
        b = ol[0] * dl[0] + ol[1] * dl[1]
        c = ol[0] * ol[0] + ol[1] * ol[1] - r * r
        disc = b * b - a * c
        if disc >= 0.0:
            sq = math.sqrt(disc)
            for t in ((-b - sq) / a, (-b + sq) / a):
                z = ol[2] + dl[2] * t
                if -hz <= z <= hz:
                    n = vnorm(((ol[0] + dl[0] * t) / r, (ol[1] + dl[1] * t) / r, 0.0))
                    best = (t, n)
                    break
    if abs(dl[2]) > 1e-12:  # end caps
        for sz in (1.0, -1.0):
            t = (sz * hz - ol[2]) / dl[2]
            x, y = ol[0] + dl[0] * t, ol[1] + dl[1] * t
            if x * x + y * y <= r * r and (best is None or t < best[0]):
                best = (t, (0.0, 0.0, sz))
    return best


def _hit_quad(ol, dl, h):
    if abs(dl[2]) < 1e-12:
        return None
    t = -ol[2] / dl[2]
    x, y = ol[0] + dl[0] * t, ol[1] + dl[1] * t
    if abs(x) > h[0] or abs(y) > h[1]:
        return None
    return t, (0.0, 0.0, -1.0 if dl[2] > 0 else 1.0)


def render_frame(prims, scale, bob, size=SPRITE, shadow=False):
    """Rasterise one posed, yawed model into an RGBA pixel buffer."""
    w = h = size
    cx = cy = size * 0.5
    px = [None] * (w * h)  # colour
    zb = [1e30] * (w * h)  # depth (smaller = nearer the camera)
    glow = [0.0] * (w * h)

    # constant view ray direction, in world space
    ddir = vmul(CAM_D, -1.0)

    plan = []
    for pr in prims:
        ax, ay, az = pr.ax, pr.ay, pr.az
        # screen bounding box of the primitive's local AABB
        xs, ys = [], []
        for c in pr.corners():
            sx, sy = project(c)
            xs.append(sx * scale + cx)
            ys.append(sy * scale + cy + bob)
        x0 = max(0, int(math.floor(min(xs))) - 1)
        x1 = min(w - 1, int(math.ceil(max(xs))) + 1)
        y0 = max(0, int(math.floor(min(ys))) - 1)
        y1 = min(h - 1, int(math.ceil(max(ys))) + 1)
        if x1 < x0 or y1 < y0:
            continue
        dl = (vdot(ddir, ax), vdot(ddir, ay), vdot(ddir, az))
        inv = tuple((1.0 / d if abs(d) > 1e-12 else None) for d in dl)
        # ol = dot(base, axis) - dot(c, axis), with base = R*sx_w + U*sy_w
        ra = (vdot(CAM_R, ax), vdot(CAM_R, ay), vdot(CAM_R, az))
        ua = (vdot(CAM_U, ax), vdot(CAM_U, ay), vdot(CAM_U, az))
        ca = (vdot(pr.c, ax), vdot(pr.c, ay), vdot(pr.c, az))
        plan.append((pr, x0, x1, y0, y1, dl, inv, ra, ua, ca))

    inv_scale = 1.0 / scale
    for (pr, x0, x1, y0, y1, dl, inv, ra, ua, ca) in plan:
        kind, hh, mat = pr.kind, pr.h, pr.mat
        ramp = MATERIALS[mat]
        nramp = len(ramp)
        is_emis = mat in EMISSIVE
        if is_emis:
            pw = clamp(pr.power)
            if pw <= 0.02:
                continue
            ecol = ramp[int(round(pw * (nramp - 1)))]
            eglow = pw if mat in BLOOMING else 0.0
        for y in range(y0, y1 + 1):
            sy_w = (cy + bob - (y + 0.5)) * inv_scale
            row = y * w
            b0 = ua[0] * sy_w - ca[0]
            b1 = ua[1] * sy_w - ca[1]
            b2 = ua[2] * sy_w - ca[2]
            for x in range(x0, x1 + 1):
                sx_w = ((x + 0.5) - cx) * inv_scale
                ol = (
                    ra[0] * sx_w + b0,
                    ra[1] * sx_w + b1,
                    ra[2] * sx_w + b2,
                )
                if kind == "box":
                    hit = _hit_box(ol, dl, inv, hh)
                elif kind == "ell":
                    hit = _hit_ell(ol, dl, hh)
                elif kind == "cyl":
                    hit = _hit_cyl(ol, dl, hh)
                else:
                    hit = _hit_quad(ol, dl, hh)
                if hit is None:
                    continue
                t, nl = hit
                idx = row + x
                if t >= zb[idx]:
                    continue
                zb[idx] = t
                if is_emis:
                    px[idx] = ecol
                    glow[idx] = eglow
                    continue
                # local normal -> world normal
                n = (
                    pr.ax[0] * nl[0] + pr.ay[0] * nl[1] + pr.az[0] * nl[2],
                    pr.ax[1] * nl[0] + pr.ay[1] * nl[1] + pr.az[1] * nl[2],
                    pr.ax[2] * nl[0] + pr.ay[2] * nl[1] + pr.az[2] * nl[2],
                )
                # half-lambert keeps curved parts reading as balls at this size
                v = 0.5 * vdot(n, LIGHT) + 0.5
                f = vdot(n, FILL)
                if f > 0.0:
                    v += 0.20 * f
                k = int(v * nramp)
                px[idx] = ramp[0 if k < 0 else (nramp - 1 if k >= nramp else k)]
                glow[idx] = 0.0

    return _finish(px, glow, w, h, shadow)


def _finish(px, glow, w, h, shadow):
    """Silhouette outline, engine bloom and the optional baked drop shadow."""
    out = [(0, 0, 0, 0)] * (w * h)

    if shadow:
        # squash the silhouette onto a notional deck plane and offset it down-right
        for y in range(h):
            for x in range(w):
                if px[y * w + x] is None:
                    continue
                sy = int(h * 0.62 + (y - h * 0.5) * 0.30) + 6
                sx = x + 5
                if 0 <= sx < w and 0 <= sy < h and px[sy * w + sx] is None:
                    out[sy * w + sx] = SHADOW

    # 1px silhouette outline
    for y in range(h):
        for x in range(w):
            i = y * w + x
            if px[i] is not None:
                continue
            if (
                (x > 0 and px[i - 1] is not None)
                or (x < w - 1 and px[i + 1] is not None)
                or (y > 0 and px[i - w] is not None)
                or (y < h - 1 and px[i + w] is not None)
            ):
                out[i] = OUTLINE

    # engine / bay bloom: two fixed halo colours only, to keep the palette tight
    for y in range(h):
        for x in range(w):
            g = glow[y * w + x]
            if g < 0.55:
                continue
            for dy in (-2, -1, 0, 1, 2):
                for dx in (-2, -1, 0, 1, 2):
                    d = abs(dx) + abs(dy)
                    if d == 0 or d > 3:
                        continue
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < w and 0 <= ny < h):
                        continue
                    j = ny * w + nx
                    if px[j] is not None:
                        continue
                    cur = out[j]
                    cand = GLOW_NEAR if d <= 1 else GLOW_FAR
                    if cur == (0, 0, 0, 0) or cur == OUTLINE or cur == SHADOW:
                        out[j] = cand
                    elif cand[3] > cur[3]:
                        out[j] = cand

    for i in range(w * h):
        if px[i] is not None:
            out[i] = px[i]

    img = Image.new("RGBA", (w, h))
    img.putdata(out)
    return img


# --------------------------------------------------------------------------------------
# fitting + sheet assembly
# --------------------------------------------------------------------------------------


def fit_scale(cfg):
    """
    One scale per loadout, shared by every facing and animation, so sprites stay
    registered. Fitting uses the model's radius in the xy plane, which is facing
    independent by construction.
    """
    st = base_state(0, 8)
    st["throttle"] = 0.55  # leave room for a moderate plume
    st["door"] = 1.0
    prims = build_model(cfg, st)
    ext_x = ext_y = 1e-6
    for f in range(8):
        a = FACING_YAW0 + f * math.tau / 8.0
        for pr in prims:
            for c in pr.corners():
                sx, sy = project(yaw_vec(c, a))
                ext_x = max(ext_x, abs(sx))
                ext_y = max(ext_y, abs(sy))
    half = SPRITE * 0.5 - MARGIN
    return min(half / ext_x, half / ext_y)


def render_cell(job):
    key, cfg, anim_fn, frame, nframes, facing, scale, shadow, palette = job
    # rebound explicitly so the render is identical whether pooled workers are
    # forked (inheriting globals) or spawned (starting from import state)
    use_palette(palette)
    st = anim_fn(frame, nframes)
    prims = build_model(cfg, st)
    a = FACING_YAW0 + facing * math.tau / 8.0
    prims = [p.yawed(a) for p in prims]
    bob = st["bob"] * scale
    img = render_frame(prims, scale, bob, SPRITE, shadow)
    return (facing, frame, img.tobytes())


def build_sheets(loadout_key, outdir, shadow=False, contact=False, jobs=None, palette="crimson"):
    cfg = LOADOUTS[loadout_key]
    scale = fit_scale(cfg)
    meta_anims = []

    for (aname, afn, nframes, fps, loop) in ANIMATIONS:
        work = [
            (loadout_key, cfg, afn, f, nframes, fc, scale, shadow, palette)
            for fc in range(8)
            for f in range(nframes)
        ]
        if jobs and jobs > 1:
            with Pool(jobs) as pool:
                results = pool.map(render_cell, work, chunksize=4)
        else:
            results = [render_cell(w) for w in work]

        sheet = Image.new("RGBA", (SPRITE * nframes, SPRITE * 8), (0, 0, 0, 0))
        for (facing, frame, raw) in results:
            cell = Image.frombytes("RGBA", (SPRITE, SPRITE), raw)
            sheet.paste(cell, (frame * SPRITE, facing * SPRITE))

        base = f"carrier_{loadout_key}_{aname}"
        p1 = os.path.join(outdir, base + ".png")
        p2 = os.path.join(outdir, base + "@2x.png")
        sheet.save(p1)
        sheet.resize((sheet.width * 2, sheet.height * 2), Image.NEAREST).save(p2)

        meta_anims.append(
            {
                "name": aname,
                "frames": nframes,
                "fps": fps,
                "loop": loop,
                "sheet": os.path.basename(p1),
                "sheet_2x": os.path.basename(p2),
            }
        )
        print(f"  {base}: {nframes} frames x 8 facings -> {sheet.width}x{sheet.height}")

    meta = {
        "loadout": loadout_key,
        "display_name": cfg["name"],
        "modules": cfg["modules"],
        "palette": palette,
        "squadron_capacity": cfg["squadrons"],
        "frame_width": SPRITE,
        "frame_height": SPRITE,
        "projection": "orthographic dimetric, yaw 45 deg, pitch 30 deg (2:1)",
        "origin": "cell centre; the hull is registered identically across every sheet",
        "facings": [
            {"row": i, "name": FACING_NAMES[i], "yaw_deg": round(math.degrees(FACING_YAW0) + i * 45) % 360}
            for i in range(8)
        ],
        "animations": meta_anims,
    }
    with open(os.path.join(outdir, f"carrier_{loadout_key}.json"), "w") as fh:
        json.dump(meta, fh, indent=2)

    if contact:
        _contact_sheet(loadout_key, outdir, meta_anims)
    return meta


def _contact_sheet(loadout_key, outdir, meta_anims):
    """A single overview image, used for eyeballing changes between tweaks."""
    rows = []
    for a in meta_anims:
        img = Image.open(os.path.join(outdir, a["sheet"])).convert("RGBA")
        rows.append((a["name"], img))
    width = max(i.width for _, i in rows)
    height = sum(i.height for _, i in rows) + 12 * len(rows)
    sheet = Image.new("RGBA", (width, height), (18, 22, 30, 255))
    y = 0
    for _, img in rows:
        sheet.paste(img, (0, y), img)
        y += img.height + 12
    sheet.save(os.path.join(outdir, f"contact_{loadout_key}.png"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loadout", action="append", choices=sorted(LOADOUTS), help="repeatable")
    ap.add_argument("--out", default="sprites")
    ap.add_argument("--shadow", action="store_true", help="bake a deck drop shadow")
    ap.add_argument("--contact", action="store_true", help="also write contact sheets")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--palette", default="crimson", choices=sorted(PALETTES), help="paint scheme")
    args = ap.parse_args()

    keys = args.loadout or list(LOADOUTS)
    os.makedirs(args.out, exist_ok=True)
    use_palette(args.palette)

    atlas = []
    for k in keys:
        print(f"{k}: {LOADOUTS[k]['name']}")
        atlas.append(
            build_sheets(k, args.out, args.shadow, args.contact, args.jobs, args.palette)
        )

    with open(os.path.join(args.out, "carrier_atlas.json"), "w") as fh:
        json.dump(
            {"sprite_size": SPRITE, "palette": args.palette, "loadouts": atlas}, fh, indent=2
        )
    print(f"\nwrote {len(atlas)} loadout(s) to {args.out}/")


if __name__ == "__main__":
    sys.exit(main())
