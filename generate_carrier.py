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
# `trench` the recessed strip lighting, `runlight` the marker lights along the hull.
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

    def placed(self, dx, a):
        """Slide along the ship's long axis to centre it in the cell, then yaw."""
        p = Prim.__new__(Prim)
        p.kind, p.h, p.mat, p.power = self.kind, self.h, self.mat, self.power
        p.c = yaw_vec((self.c[0] + dx, self.c[1], self.c[2]), a)
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
    """
    The spine: a long keel carrying a stepped stack of armour plate, a chisel prow
    and a forward sensor spar. The ship's mass steps up towards a dorsal ridge —
    there is no flat top deck, the silhouette is layered blocks along an axis.
    """
    p = []
    pw = st["power"]

    # keel and belly
    p.append(box((0.0, 0.0, 0.0), (2.20, 0.40, 0.22), "hull"))
    p.append(box((-0.35, 0.0, -0.28), (1.85, 0.28, 0.09), "hull_dark"))

    # Stepped dorsal stack: each layer shorter and narrower than the one below, so
    # the hull reads as built-up plate rather than a lid.
    for (cx, cz, hx, hy, hz, mat) in (
        (-0.15, 0.28, 1.80, 0.34, 0.08, "hull"),
        (-0.42, 0.42, 1.35, 0.26, 0.07, "hull_light"),
        (-0.66, 0.55, 0.92, 0.19, 0.07, "hull"),
        (-0.86, 0.67, 0.55, 0.12, 0.06, "hull_light"),
    ):
        # a dark recess under each layer, proud of its footprint, so the stack reads
        # as separate plates with shadow gaps instead of one smooth mass
        p.append(box((cx, 0.0, cz - hz), (hx * 0.99, hy + 0.022, 0.018), "armor"))
        p.append(box((cx, 0.0, cz), (hx, hy, hz), mat))

    # Paint goes on as a few large blocks where a layer's top is actually exposed.
    # Striping every edge instead just reads as noise at this size.
    p.append(box((1.29, 0.0, 0.365), (0.34, 0.325, 0.009), "plate"))
    p.append(box((-0.02, 0.0, 0.625), (0.25, 0.180, 0.009), "plate"))
    p.append(box((-0.86, 0.0, 0.735), (0.50, 0.110, 0.009), "plate"))

    # chisel prow, then a slender sensor spar off the nose
    p.append(box((2.42, 0.0, 0.0), (0.22, 0.30, 0.17), "hull"))
    p.append(box((2.74, 0.0, 0.0), (0.14, 0.19, 0.11), "plate"))
    p.append(box((2.94, 0.0, 0.0), (0.09, 0.10, 0.06), "hull_light"))
    p.append(cyl((3.02, 0.0, 0.02), (3.42, 0.0, 0.02), 0.026, "hull_light"))
    p.append(box((3.13, 0.0, 0.02), (0.03, 0.055, 0.055), "armor"))
    p.append(ell((3.44, 0.0, 0.02), (0.034, 0.034, 0.034), "nav_red", power=st["strobe"] * pw))
    p.append(ell((2.60, 0.0, 0.15), (0.10, 0.08, 0.06), "window", power=0.72 * pw))

    for sy in (1.0, -1.0):
        fy = sy * 0.40  # keel flank plane

        # painted blocks fore and aft on the keel side
        p.append(box((-1.55, fy + sy * 0.008, 0.0), (0.58, 0.012, 0.185), "plate"))
        p.append(box((1.66, fy + sy * 0.008, 0.0), (0.46, 0.012, 0.185), "plate"))
        # recessed strip light between them
        p.append(box((0.05, fy + sy * 0.004, 0.07), (0.98, 0.012, 0.052), "armor"))
        p.append(
            box((0.05, fy + sy * 0.012, 0.07), (0.92, 0.010, 0.020), "trench", power=0.85 * pw)
        )
        # painted flanks on the stack layers, deep enough to read as blocks not pinstripes
        p.append(box((0.42, sy * 0.348, 0.28), (0.86, 0.010, 0.072), "plate"))
        p.append(box((-1.18, sy * 0.348, 0.28), (0.48, 0.010, 0.072), "plate"))
        p.append(box((-0.95, sy * 0.268, 0.42), (0.72, 0.010, 0.060), "plate"))

        # ribs down the keel, alternating depth
        for i in range(6):
            x = -1.85 + i * 0.66
            deep = i % 2 == 0
            p.append(
                box(
                    (x, sy * (0.42 + (0.03 if deep else 0.0)), -0.10),
                    (0.12 if deep else 0.07, 0.05, 0.10),
                    "armor" if deep else "hull_dark",
                )
            )
        # greebles clinging to the stack shoulders
        for (gx, gy, gz, gl, gh) in (
            (0.55, 0.365, 0.26, 0.09, 0.045),
            (-1.30, 0.365, 0.28, 0.11, 0.05),
            (-0.30, 0.285, 0.42, 0.09, 0.04),
            (-0.80, 0.205, 0.55, 0.07, 0.04),
        ):
            p.append(box((gx, sy * gy, gz), (gl, 0.032, gh), "hull_light"))

        # ventral fin
        p.append(
            box(
                (-1.20, sy * 0.28, -0.46),
                (0.45, 0.06, 0.16),
                "armor",
                axes_from_euler(pitch=math.radians(8)),
            )
        )
    return p


def m_bridge(st, cfg):
    """Command block set into the dorsal stack, with a mast and strobe above it."""
    p = []
    pw = st["power"]
    iy = -0.14  # nudged off the centreline so the ridge is not mirror-symmetric
    p.append(box((-1.10, iy, 0.66), (0.36, 0.20, 0.15), "hull_light"))
    p.append(box((-1.14, iy, 0.83), (0.26, 0.15, 0.04), "hull_dark"))
    p.append(box((-1.10, iy, 0.775), (0.362, 0.202, 0.028), "plate"))
    # forward window band, wrapping onto both flanks
    p.append(box((-0.755, iy, 0.70), (0.02, 0.15, 0.055), "window", power=0.88 * pw))
    for sy in (1.0, -1.0):
        p.append(box((-1.08, iy + sy * 0.205, 0.70), (0.20, 0.02, 0.045), "window",
                     power=0.62 * pw))
    # mast, yardarm, strobe
    p.append(cyl((-1.34, iy, 0.86), (-1.40, iy, 1.24), 0.034, "hull_dark"))
    p.append(box((-1.38, iy, 1.06), (0.026, 0.14, 0.026), "hull_dark"))
    p.append(ell((-1.41, iy, 1.30), (0.06, 0.06, 0.06), "nav_red", power=st["strobe"] * pw))
    # navigation lights on the keel shoulders: port red, starboard green
    p.append(ell((-1.62, 0.42, 0.20), (0.05, 0.05, 0.05), "nav_red", power=st["nav"] * pw))
    p.append(ell((-1.62, -0.42, 0.20), (0.05, 0.05, 0.05), "nav_green", power=st["nav"] * pw))
    return p


# Hangar mass per module: (centre x, half x, half y, half z). Other modules look this
# up so armour and turrets mount to whichever bay is fitted.
HULLS = {
    "hangar_small": (1.05, 0.80, 0.56, 0.26),
    "hangar_large": (1.00, 0.98, 0.76, 0.29),
}


def hull_of(cfg):
    for name in cfg["modules"]:
        if name in HULLS:
            return HULLS[name]
    raise ValueError(f"loadout {cfg['name']!r} has no hangar module")


def _hangar(st, cfg):
    """
    The bay is a mass built into the forward hull, stepped out from the keel, with
    the launch mouths cut into its bow face. A bigger bay is a bigger block, so the
    module changes the ship's silhouette and not just its interior.
    """
    p = []
    pw = st["power"]
    door = ease(st["door"])
    bx, bhx, bhy, bhz = hull_of(cfg)
    top = 0.02 + bhz

    p.append(box((bx, 0.0, 0.02), (bhx, bhy, bhz), "hull"))
    p.append(box((bx - 0.10, 0.0, top - 0.01), (bhx * 0.84, bhy * 0.78, 0.016), "armor"))
    p.append(box((bx - 0.10, 0.0, top + 0.05), (bhx * 0.82, bhy * 0.76, 0.055), "hull_light"))
    p.append(box((bx - 0.05, 0.0, -bhz - 0.02), (bhx * 0.70, bhy * 0.60, 0.05), "hull_dark"))
    # painted block across the aft half of the bay mass roof
    p.append(
        box((bx - bhx * 0.42, 0.0, top + 0.108), (bhx * 0.30, bhy * 0.74, 0.010), "plate")
    )

    for sy in (1.0, -1.0):
        fy = sy * bhy
        # painted band and a long lit slot down the flank of the bay mass
        p.append(box((bx - 0.05, fy + sy * 0.008, 0.02 + bhz * 0.55),
                     (bhx * 0.86, 0.012, bhz * 0.28), "plate"))
        p.append(box((bx - 0.05, fy + sy * 0.004, -0.03), (bhx * 0.80, 0.012, 0.055), "armor"))
        p.append(box((bx - 0.05, fy + sy * 0.012, -0.03), (bhx * 0.74, 0.010, 0.024),
                     "trench", power=(0.45 + 0.45 * door) * pw))
        # marker lights along the top edge
        for i in range(4):
            lx = bx - bhx * 0.70 + i * (bhx * 1.40) / 3.0
            p.append(ell((lx, fy, top), (0.028, 0.028, 0.028), "runlight",
                         power=(0.35 + 0.50 * door) * pw))

    # Fleet insignia struck across the top of the bay mass, in the hull's own paint.
    ins_x, ins_y = bx + bhx * 0.14, -bhy * 0.30
    for sy in (1.0, -1.0):
        p.append(box((ins_x, ins_y + sy * 0.14, top + 0.108), (0.24, 0.050, 0.010), "plate",
                     axes_from_euler(yaw=math.radians(36 * sy))))

    # launch mouths cut into the bow face of the mass
    for (by, front_x, bz) in cfg["bays"]:
        inner = 0.25 + 0.55 * door
        p.append(box((front_x - 0.18, by, bz), (0.18, 0.16, 0.11), "bay", power=inner * pw))
        # two-piece iris door: upper half retracts up, lower half drops
        travel = 0.20 * door
        p.append(box((front_x + 0.02, by, bz + 0.058 + travel), (0.035, 0.18, 0.055), "hull"))
        p.append(box((front_x + 0.02, by, bz - 0.058 - travel), (0.035, 0.18, 0.055), "hull"))
        for sy in (1.0, -1.0):
            p.append(ell((front_x - 0.01, by + sy * 0.195, bz), (0.030, 0.030, 0.030),
                         "bay", power=(0.25 + 0.60 * door) * pw))
    return p


def m_hangar_small(st, cfg):
    """Two-squadron bay: the starting module."""
    return _hangar(st, cfg)


def m_hangar_large(st, cfg):
    """Four-squadron bay: a longer, wider forward mass on outboard sponsons."""
    p = _hangar(st, cfg)
    bx, bhx, bhy, bhz = hull_of(cfg)
    for sy in (1.0, -1.0):
        p.append(box((bx - 0.30, sy * (bhy + 0.09), -0.06), (bhx * 0.55, 0.11, 0.13), "armor"))
    return p


def _thruster(p, x_front, x_back, y, z, r, st, plume_scale=1.0):
    """One engine: housing, painted collar, nozzle, and a throttle-driven plume."""
    thr = st["throttle"]
    p.append(cyl((x_front, y, z), (x_back, y, z), r, "hull"))
    p.append(cyl((x_front - 0.02, y, z), (x_front - 0.11, y, z), r * 1.06, "plate"))
    p.append(cyl((x_back + 0.05, y, z), (x_back + 0.02, y, z), r * 1.09, "hull_dark"))
    p.append(cyl((x_back + 0.02, y, z), (x_back - 0.01, y, z), r * 0.72, "engine",
                 power=0.30 + 0.62 * thr))
    if thr > 0.02:
        ln = (0.18 + 0.60 * thr) * st["plume"] * plume_scale
        core = r * 0.50
        p.append(cyl((x_back - 0.01, y, z), (x_back - ln * 0.50, y, z), core, "engine",
                     power=0.52 + 0.40 * thr))
        p.append(cyl((x_back - ln * 0.45, y, z), (x_back - ln, y, z), core * 0.66, "engine",
                     power=0.22 + 0.28 * thr))


def m_engines_basic(st, cfg):
    """Stern block with a four-nozzle cluster."""
    p = []
    p.append(box((-2.32, 0.0, 0.02), (0.28, 0.42, 0.26), "hull_dark"))
    p.append(box((-2.34, 0.0, 0.31), (0.22, 0.30, 0.05), "hull_light"))
    p.append(box((-2.32, 0.0, -0.26), (0.24, 0.34, 0.04), "armor"))
    for (y, z) in ((0.23, 0.15), (-0.23, 0.15), (0.23, -0.13), (-0.23, -0.13)):
        _thruster(p, -2.26, -2.66, y, z, 0.125, st)
    return p


def m_engines_uprated(st, cfg):
    """Six nozzles: the stern cluster plus a pair of outboard pods on pylons."""
    p = []
    p.append(box((-2.30, 0.0, 0.02), (0.32, 0.46, 0.28), "hull_dark"))
    p.append(box((-2.32, 0.0, 0.33), (0.24, 0.32, 0.05), "hull_light"))
    p.append(box((-2.30, 0.0, -0.28), (0.26, 0.36, 0.04), "armor"))
    for (y, z) in ((0.25, 0.17), (-0.25, 0.17), (0.25, -0.15), (-0.25, -0.15)):
        _thruster(p, -2.24, -2.72, y, z, 0.130, st)
    for sy in (1.0, -1.0):
        p.append(box((-1.92, sy * 0.54, -0.02), (0.30, 0.15, 0.085), "armor"))
        _thruster(p, -2.18, -2.54, sy * 0.62, -0.02, 0.155, st, plume_scale=0.85)
    return p


def m_sensor_array(st, cfg):
    """Dish on the mast head and a search bar turning on the dorsal ridge."""
    p = []
    spin = st["t"] * math.tau
    p.append(cyl((-1.40, -0.14, 0.98), (-1.40, -0.14, 1.06), 0.042, "hull_dark"))
    p.append(
        Prim("cyl", (-1.40, -0.14, 1.13), (0.18, 0.18, 0.020), "hull_light",
             axes_from_euler(pitch=math.radians(-58), yaw=spin))
    )
    p.append(
        box((-0.66, 0.0, 0.68), (0.045, 0.28, 0.040), "hull_dark",
            axes_from_euler(yaw=spin * 0.5))
    )
    return p


def m_weapons_pods(st, cfg):
    """Four twin-barrel turrets: two flanking the bay mass, two on the keel shoulders."""
    p = []
    bx, bhx, bhy, bhz = hull_of(cfg)
    sweep = math.sin(st["t"] * math.tau) * 0.18
    mounts = [
        ((bx + bhx * 0.24, bhy + 0.08, 0.06), math.radians(55)),
        ((bx + bhx * 0.24, -bhy - 0.08, 0.06), math.radians(-55)),
        ((-1.28, 0.44, 0.14), math.radians(125)),
        ((-1.28, -0.44, 0.14), math.radians(-125)),
    ]
    for (c, ang) in mounts:
        # plinth first, so the turret is not glued straight to the plating
        p.append(box((c[0], c[1], c[2] - 0.085), (0.14, 0.12, 0.055), "armor"))
        ax = axes_from_euler(yaw=ang + sweep)
        p.append(ell(c, (0.125, 0.125, 0.085), "hull_light", ax))
        for sy in (1.0, -1.0):
            off = vadd(c, vmul(ax[1], sy * 0.046))
            p.append(
                cyl(vadd(off, vmul(ax[0], 0.04)), vadd(off, vmul(ax[0], 0.27)), 0.027, "hull_dark")
            )
    return p


def m_armor_belt(st, cfg):
    """Ablative plating along the keel, over the bay shoulders and on the prow."""
    p = []
    bx, bhx, bhy, bhz = hull_of(cfg)
    for sy in (1.0, -1.0):
        for i in range(4):
            x = -1.95 + i * 0.62
            p.append(box((x, sy * 0.435, 0.0), (0.26, 0.07, 0.17), "armor"))
        p.append(box((bx - 0.08, sy * (bhy + 0.045), 0.10), (bhx * 0.72, 0.055, 0.11), "armor"))
        p.append(
            box((2.36, sy * 0.27, 0.0), (0.26, 0.07, 0.15), "armor",
                axes_from_euler(yaw=math.radians(-9 * sy)))
        )
    p.append(box((-0.45, 0.0, -0.40), (1.70, 0.30, 0.05), "armor"))
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
    cx, hx, hy, hz = hull_of(cfg)
    rng = Rng(st["frame"] * 977 + 31)
    # A breach torn in the top of the bay mass, always in the same place so the damage
    # reads as a wound. That face is exposed from every facing, unlike the keel flanks.
    bx, by, top = cx - hx * 0.30, hy * 0.40, 0.02 + hz
    p.append(box((bx, by, top + 0.02), (0.24, 0.18, 0.05), "hull_dark"))
    p.append(
        box((bx - 0.20, by + 0.15, top + 0.09), (0.15, 0.05, 0.11), "hull_dark",
            axes_from_euler(roll=math.radians(26)))
    )
    # fire in the breach, flickering on the frame clock
    for i in range(3):
        f = 0.55 + 0.45 * math.sin(st["t"] * math.tau * 3.0 + i * 2.1)
        p.append(
            ell((bx + (i - 1) * 0.12, by, top + 0.07 + 0.05 * f),
                (0.085, 0.075, 0.06 + 0.05 * f), "spark", power=(0.55 + 0.45 * f) * dmg)
        )
    # sparks thrown clear of the hull
    for _ in range(int(4 + 5 * dmg)):
        x = rng.range(bx - 0.5, bx + 0.7)
        y = by + rng.range(-0.35, 0.45)
        z = rng.range(top, top + 0.60)
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
BAYS_2 = [(0.30, 1.85, 0.0), (-0.30, 1.85, 0.0)]
BAYS_4 = [(0.26, 1.98, 0.0), (-0.26, 1.98, 0.0), (0.58, 1.98, 0.0), (-0.58, 1.98, 0.0)]

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
    One scale and one long-axis offset per loadout, shared by every facing and
    animation, so sprites stay registered across sheets. The ship is bow-heavy, so
    it is first slid to centre it on the yaw axis — otherwise half the cell is spent
    on empty space behind the stern.
    """
    st = base_state(0, 8)
    st["throttle"] = 0.55  # leave room for a moderate plume
    st["door"] = 1.0
    prims = build_model(cfg, st)

    xs = [c[0] for pr in prims for c in pr.corners()]
    offx = -(min(xs) + max(xs)) * 0.5

    ext_x = ext_y = 1e-6
    for f in range(8):
        a = FACING_YAW0 + f * math.tau / 8.0
        for pr in prims:
            for c in pr.corners():
                sx, sy = project(yaw_vec((c[0] + offx, c[1], c[2]), a))
                ext_x = max(ext_x, abs(sx))
                ext_y = max(ext_y, abs(sy))
    half = SPRITE * 0.5 - MARGIN
    return min(half / ext_x, half / ext_y), offx


def render_cell(job):
    key, cfg, anim_fn, frame, nframes, facing, scale, offx, shadow, palette = job
    # rebound explicitly so the render is identical whether pooled workers are
    # forked (inheriting globals) or spawned (starting from import state)
    use_palette(palette)
    st = anim_fn(frame, nframes)
    prims = build_model(cfg, st)
    a = FACING_YAW0 + facing * math.tau / 8.0
    prims = [p.placed(offx, a) for p in prims]
    bob = st["bob"] * scale
    img = render_frame(prims, scale, bob, SPRITE, shadow)
    return (facing, frame, img.tobytes())


def build_sheets(loadout_key, outdir, shadow=False, contact=False, jobs=None, palette="crimson"):
    cfg = LOADOUTS[loadout_key]
    scale, offx = fit_scale(cfg)
    meta_anims = []

    for (aname, afn, nframes, fps, loop) in ANIMATIONS:
        work = [
            (loadout_key, cfg, afn, f, nframes, fc, scale, offx, shadow, palette)
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
