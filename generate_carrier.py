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

# Half-lambert against this rig only spans roughly 0.50..0.96, so the raw value is
# remapped onto 0..1 before it is quantised. Without it the ramp's top step swallows
# every surface within 40 degrees of straight up.
SHADE_LO = 0.50
SHADE_GAIN = 1.0 / 0.46

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
            "rock": _ramp("3a3129", "5c4f3f", "82705a", "ab9679", "d0bb99"),
            "rock_dark": _ramp("241e19", "3a3129", "544738", "6f5f4b", "8d7a61"),
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
            "rock": _ramp("1a1f27", "2a323d", "3d4757", "525f72", "6b7a90"),
            "rock_dark": _ramp("0e1218", "191f27", "262f3a", "35404e", "465364"),
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


def _attach(prims, scale, origin, yaw=0.0):
    """Scale a sub-craft's primitives and drop them into a parent model's space."""
    out = []
    for pr in prims:
        axes = (yaw_vec(pr.ax, yaw), yaw_vec(pr.ay, yaw), yaw_vec(pr.az, yaw))
        c = vadd(origin, yaw_vec(vmul(pr.c, scale), yaw))
        out.append(Prim(pr.kind, c, vmul(pr.h, scale), pr.mat, axes, pr.power))
    return out


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
        # The launching craft is the actual starfighter model, scaled down, so the
        # carrier's air wing and the fighter sheets cannot drift apart.
        sub = base_state(st["frame"], 8)
        sub["throttle"] = 0.85
        sub["nav"] = fade
        craft = f_airframe(sub, None, simple=True) + f_engine_std(sub, None)
        p.extend(
            _attach(craft, 0.20, (x, by, z), yaw=math.radians(4 * (1 if by > 0 else -1)))
        )
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


CARRIER_MODULES = {
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

CARRIER_LOADOUTS = {
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


# --------------------------------------------------------------------------------------
# the starfighter
#
# A broad arrowhead flying wing: a swept delta built from chord-wise strips, a dorsal
# spine carrying a recessed intake grille, a notched trailing edge that leaves an aft
# prong either side of the engine, and a single nozzle on the centreline. Same
# primitives, same palette, same camera as the carrier — it is the carrier's air wing,
# so it has to agree with it by construction.
# --------------------------------------------------------------------------------------

# (inner y, outer y, leading edge x, trailing edge x, half thickness, z centre, roll deg).
# The leading edge sweeps hard aft as the strips move outboard; the trailing edge
# reaches furthest aft at mid span, which is what leaves the two prongs.
#
# The roll matters more than it looks. A flying wing seen from 30 degrees of elevation
# is almost entirely top surface, and axis-aligned strips all share one normal, so the
# whole planform quantises to a single ramp step and reads as a pale blob. Progressive
# anhedral gives each strip its own normal, so the wing facets across the span the way
# the reference does — and the two wings shade differently, which sells the volume.
# Roll is assigned in three groups rather than ramping per strip: six evenly-stepped
# rolls just produce six slivers that each round to the same ramp step. Three broad
# facets — inner, mid, outer — give three distinct tones across the span, which is how
# the reference reads, while the six chords keep the swept leading edge fine.
WING_STRIPS = [
    (0.24, 0.44, 1.05, -0.30, 0.072, 0.008, -4.0),
    (0.44, 0.62, 0.92, -0.70, 0.066, 0.002, -4.0),
    (0.62, 0.79, 0.78, -0.92, 0.056, -0.015, -19.0),
    (0.79, 0.95, 0.62, -0.95, 0.048, -0.035, -19.0),
    (0.95, 1.09, 0.46, -0.84, 0.040, -0.060, -40.0),
    (1.09, 1.20, 0.32, -0.66, 0.032, -0.085, -40.0),
]


def f_airframe(st, cfg, simple=False):
    """Fuselage, swept wings, dorsal spine and intake. The whole silhouette."""
    p = []
    pw = st["power"]

    # The airframe sits on the darker `deck` tone with the spine and panelling picked
    # out in the lighter hull colour. Bone over the whole planform washes out at 48px,
    # where nearly every visible pixel is top surface.
    p.append(box((0.18, 0.0, 0.010), (0.86, 0.26, 0.085), "deck"))
    p.append(box((-0.34, 0.0, 0.000), (0.34, 0.21, 0.072), "deck"))
    p.append(box((1.14, 0.0, 0.000), (0.20, 0.16, 0.060), "deck"))
    p.append(box((1.36, 0.0, 0.000), (0.10, 0.085, 0.038), "hull"))

    # Swept delta, one rolled strip at a time. Deliberately no per-strip edge highlight
    # or seam: repeated across six chords they stop reading as a leading edge and start
    # reading as corrugation. The three facet rolls carry the form on their own.
    for sy in (1.0, -1.0):
        for (yi, yo, xle, xte, hz, cz, roll) in WING_STRIPS:
            cx, hx = (xle + xte) * 0.5, (xle - xte) * 0.5
            cy, hy = (yi + yo) * 0.5, (yo - yi) * 0.5
            p.append(
                box((cx, sy * cy, cz), (hx, hy, hz), "deck",
                    axes_from_euler(roll=math.radians(roll * sy)))
            )

    # dorsal spine and canopy
    p.append(box((0.08, 0.0, 0.135), (0.60, 0.20, 0.055), "hull"))
    p.append(box((0.08, 0.0, 0.080), (0.64, 0.235, 0.014), "armor"))
    p.append(ell((0.60, 0.0, 0.125), (0.20, 0.125, 0.055), "window", power=0.55 * pw))

    if simple:
        return p

    # recessed intake grille on the spine
    p.append(box((0.06, 0.0, 0.186), (0.29, 0.175, 0.014), "armor"))
    for i in range(4):
        p.append(box((0.06, -0.126 + i * 0.084, 0.196), (0.26, 0.020, 0.010), "hull_dark"))

    for sy in (1.0, -1.0):
        # one painted flash per wing root, and one marker aft. At 48px a decal is two
        # or three pixels, so a handful of deliberate ones beats a scatter.
        p.append(
            box((0.34, sy * 0.40, 0.082), (0.16, 0.036, 0.008), "plate",
                axes_from_euler(roll=math.radians(-3.0 * sy)))
        )
        p.append(
            box((-0.42, sy * 0.68, 0.020), (0.10, 0.028, 0.008), "accent",
                axes_from_euler(roll=math.radians(-14.0 * sy)))
        )
        # wingtip navigation light: port red, starboard green
        p.append(
            ell((-0.30, sy * 1.18, -0.072), (0.045, 0.045, 0.040),
                "nav_red" if sy > 0 else "nav_green", power=st["nav"] * pw)
        )

    # squadron chevron on the port wing root
    for sy in (1.0, -1.0):
        p.append(
            box((-0.04, 0.44 + sy * 0.10, 0.084), (0.15, 0.033, 0.008), "hull_light",
                axes_from_euler(yaw=math.radians(34 * sy)))
        )
    return p


def _nozzle(p, x, y, z, r, st, plume_scale=1.0):
    thr = st["throttle"]
    p.append(cyl((x + 0.06, y, z), (x, y, z), r, "hull_dark"))
    p.append(cyl((x, y, z), (x - 0.03, y, z), r * 0.74, "engine", power=0.30 + 0.65 * thr))
    if thr > 0.02:
        ln = (0.20 + 0.85 * thr) * st["plume"] * plume_scale
        core = r * 0.56
        p.append(cyl((x - 0.03, y, z), (x - ln * 0.50, y, z), core, "engine",
                     power=0.55 + 0.40 * thr))
        p.append(cyl((x - ln * 0.45, y, z), (x - ln, y, z), core * 0.62, "engine",
                     power=0.22 + 0.30 * thr))


def f_engine_std(st, cfg):
    """Single centreline engine block."""
    p = []
    p.append(box((-0.60, 0.0, 0.050), (0.26, 0.185, 0.098), "hull"))
    p.append(box((-0.62, 0.0, 0.158), (0.20, 0.135, 0.020), "armor"))
    p.append(box((-0.60, 0.0, -0.052), (0.22, 0.150, 0.018), "plate"))
    _nozzle(p, -0.88, 0.0, 0.045, 0.115, st)
    return p


def f_engine_boosted(st, cfg):
    """Twin nozzles on a deeper block, for the heavier marks."""
    p = []
    p.append(box((-0.58, 0.0, 0.055), (0.30, 0.225, 0.105), "hull"))
    p.append(box((-0.60, 0.0, 0.170), (0.22, 0.160, 0.022), "armor"))
    p.append(box((-0.58, 0.0, -0.058), (0.26, 0.185, 0.018), "plate"))
    for sy in (1.0, -1.0):
        _nozzle(p, -0.90, sy * 0.115, 0.045, 0.088, st, plume_scale=1.15)
    return p


def f_cannons_light(st, cfg):
    """A pair of wing-root cannons that flash on the fire animation."""
    p = []
    fire = st["fire"]
    for sy in (1.0, -1.0):
        p.append(box((0.54, sy * 0.40, -0.048), (0.15, 0.055, 0.034), "hull_dark"))
        p.append(cyl((0.68, sy * 0.40, -0.048), (0.94, sy * 0.40, -0.048), 0.024, "armor"))
        if fire > 0.02:
            p.append(
                ell((0.99 + 0.06 * fire, sy * 0.40, -0.048),
                    (0.075 * fire + 0.03, 0.055, 0.050), "spark", power=fire)
            )
    return p


def f_cannons_heavy(st, cfg):
    """Four barrels and a chin pod."""
    p = f_cannons_light(st, cfg)
    fire = st["fire"]
    for sy in (1.0, -1.0):
        p.append(box((0.44, sy * 0.66, -0.052), (0.17, 0.060, 0.032), "hull_dark"))
        p.append(cyl((0.58, sy * 0.66, -0.052), (0.80, sy * 0.66, -0.052), 0.022, "armor"))
        if fire > 0.02:
            p.append(
                ell((0.85 + 0.05 * fire, sy * 0.66, -0.052),
                    (0.065 * fire + 0.025, 0.048, 0.044), "spark", power=fire)
            )
    p.append(box((0.72, 0.0, -0.070), (0.20, 0.10, 0.040), "armor"))
    return p


def f_torpedo_pods(st, cfg):
    """Underwing ordnance pods: the bomber's reason for existing."""
    p = []
    for sy in (1.0, -1.0):
        p.append(box((-0.06, sy * 0.62, -0.100), (0.34, 0.100, 0.072), "hull_light"))
        p.append(ell((0.32, sy * 0.62, -0.100), (0.11, 0.085, 0.070), "hull_light"))
        p.append(box((-0.06, sy * 0.62, -0.180), (0.30, 0.085, 0.018), "plate"))
        p.append(box((-0.34, sy * 0.62, -0.100), (0.06, 0.070, 0.055), "hull_dark"))
    return p


def f_wingtip_missiles(st, cfg):
    """Rail-mounted missiles outboard, plus a sensor pod on the spine."""
    p = []
    for sy in (1.0, -1.0):
        p.append(box((-0.18, sy * 1.06, 0.020), (0.26, 0.055, 0.030), "armor"))
        for i in range(2):
            y = sy * (1.02 + i * 0.10)
            p.append(cyl((0.10, y, 0.052), (-0.34, y, 0.052), 0.034, "hull_light"))
            p.append(ell((0.14, y, 0.052), (0.055, 0.032, 0.032), "plate"))
    p.append(box((-0.16, 0.0, 0.215), (0.13, 0.10, 0.030), "hull_dark"))
    p.append(ell((-0.16, 0.0, 0.250), (0.075, 0.065, 0.030), "window", power=0.60 * st["power"]))
    return p


def f_destruction(st, cfg):
    """
    Fireball and tumbling debris. Trajectories come from one fixed seed evaluated at
    the frame's blast value, so fragments fly on consistent arcs instead of jittering.
    """
    b = st["blast"]
    if b <= 0.0:
        return []
    p = []
    rng = Rng(7)
    fade = 1.0 - clamp((b - 0.55) / 0.45)
    if fade > 0.02:
        r = 0.16 + 0.62 * ease(min(1.0, b * 1.5))
        for i in range(3):
            rr = r * (1.0 - i * 0.24)
            p.append(
                ell((-0.10 + i * 0.06, 0.0, 0.03), (rr, rr * 0.82, rr * 0.70), "spark",
                    power=clamp((0.60 + 0.40 * math.sin(i * 2.0 + b * 6.0)) * fade))
            )
    for _ in range(8):
        ang = rng.range(0.0, math.tau)
        sp = rng.range(0.45, 1.05)
        d = b * sp
        s = rng.range(0.06, 0.13)
        p.append(
            box(
                (-0.10 + math.cos(ang) * d, math.sin(ang) * d, 0.03 + rng.range(-0.25, 0.45) * d),
                (s, s * 0.70, s * 0.35),
                "hull_dark",
                axes_from_euler(yaw=ang, roll=b * 4.0, pitch=b * 2.5),
            )
        )
    return p


FIGHTER_MODULES = {
    "airframe": f_airframe,
    "engine_std": f_engine_std,
    "engine_boosted": f_engine_boosted,
    "cannons_light": f_cannons_light,
    "cannons_heavy": f_cannons_heavy,
    "torpedo_pods": f_torpedo_pods,
    "wingtip_missiles": f_wingtip_missiles,
}

FIGHTER_LOADOUTS = {
    "interceptor": {
        "name": "Kite-class interceptor",
        "modules": ["airframe", "engine_std", "cannons_light"],
        "role": "escort",
    },
    "bomber": {
        "name": "Kite-class strike bomber",
        "modules": ["airframe", "engine_std", "cannons_light", "torpedo_pods"],
        "role": "anti-capital",
    },
    "elite": {
        "name": "Kite-class heavy interceptor",
        "modules": [
            "airframe",
            "engine_boosted",
            "cannons_heavy",
            "wingtip_missiles",
        ],
        "role": "superiority",
    },
}


def build_fighter(cfg, st):
    """Pose the fighter for one frame. Past the mid-point of a kill it is only debris."""
    if st["blast"] > 0.55:
        return f_destruction(st, cfg)
    prims = []
    for name in cfg["modules"]:
        prims.extend(FIGHTER_MODULES[name](st, cfg))
    prims.extend(f_destruction(st, cfg))
    return prims


def f_idle(frame, n):
    st = base_state(frame, n)
    ph = st["t"] * math.tau
    st["throttle"] = 0.20 + 0.06 * math.sin(ph * 2.0)
    st["nav"] = 0.50 + 0.50 * (0.5 + 0.5 * math.sin(ph))
    st["bob"] = math.sin(ph) * 0.030
    return st


def f_cruise(frame, n):
    st = base_state(frame, n)
    ph = st["t"] * math.tau
    st["throttle"] = 0.72 + 0.08 * math.sin(ph * 3.0)
    st["plume"] = 0.92 + 0.08 * math.sin(ph * 4.0 + 0.7)
    st["nav"] = 0.85
    return st


def f_boost(frame, n):
    st = base_state(frame, n)
    ph = st["t"] * math.tau
    st["throttle"] = 1.0
    st["plume"] = 1.15 + 0.18 * math.sin(ph * 5.0)
    st["nav"] = 1.0
    st["bob"] = math.sin(ph * 2.0) * 0.018
    return st


def f_fire(frame, n):
    st = base_state(frame, n)
    st["throttle"] = 0.70
    # two bursts per cycle, each one frame hot and one frame trailing off
    st["fire"] = (1.0, 0.45, 0.0, 1.0, 0.45, 0.0)[frame % 6]
    st["nav"] = 0.9
    st["bob"] = -0.012 * st["fire"]
    return st


def f_damage(frame, n):
    st = base_state(frame, n)
    ph = st["t"] * math.tau
    flick = 0.5 + 0.5 * math.sin(ph * 5.0)
    st["throttle"] = 0.34 * flick
    st["plume"] = 0.55
    st["damage"] = 0.8
    st["power"] = 0.35 + 0.65 * (1.0 if frame % 3 else 0.25)
    st["nav"] = 0.4
    st["bob"] = math.sin(ph * 3.0) * 0.028
    return st


def f_destroyed(frame, n):
    st = base_state(frame, n)
    t = frame / (n - 1.0)
    st["throttle"] = 0.30 * (1.0 - clamp(t * 3.0))
    st["power"] = 1.0 - clamp(t * 2.5)
    st["nav"] = 0.0
    st["blast"] = t
    return st


FIGHTER_ANIMS = [
    ("idle", f_idle, 8, 10, True),
    ("cruise", f_cruise, 8, 12, True),
    ("boost", f_boost, 8, 16, True),
    ("fire", f_fire, 6, 16, True),
    ("damage", f_damage, 8, 12, True),
    ("destroyed", f_destroyed, 10, 14, False),
]


def build_carrier(cfg, st):
    """Pose the whole ship for one frame, in ship-local space."""
    prims = []
    for name in cfg["modules"]:
        prims.extend(CARRIER_MODULES[name](st, cfg))
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
        "fire": 0.0,
        "blast": 0.0,
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


CARRIER_ANIMS = [
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


def render_frame(prims, scale, bob, size=SPRITE, shadow=False, light=None, bias=0):
    """
    Rasterise one posed, yawed model into an RGBA pixel buffer.

    `light` overrides the global key direction and `bias` shifts every lit surface
    down the ramp. The ships use the fixed studio rig, but map props are lit from
    wherever the map's sun actually is and dimmed by how far from it they sit, so the
    same geometry is baked per light direction and per falloff tier the way a ship is
    baked per facing. Biasing the ramp index rather than scaling colour keeps a dimmed
    prop inside the palette instead of inventing new colours for it.
    """
    key = light or LIGHT
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
                # Half-lambert keeps curved parts reading as balls at this size, but on
                # its own it only ever reaches the top two steps on surfaces facing the
                # key light — so a flat-topped hull quantises to one tone. Stretching
                # the band the lighting actually occupies across the full ramp is what
                # lets a shallow tilt read as a facet.
                v = 0.5 * vdot(n, key) + 0.5
                f = vdot(n, FILL)
                if f > 0.0:
                    v += 0.10 * f
                v = (v - SHADE_LO) * SHADE_GAIN
                k = int(v * nramp) + bias
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
# craft registry
#
# One entry per buildable craft. Everything downstream — fitting, rendering, sheet
# assembly — reads the craft from here, so adding a third hull means adding a row.
# --------------------------------------------------------------------------------------

CRAFT = {
    "carrier": {
        "display": "Lancer-class carrier",
        "sprite": 96,
        "loadouts": CARRIER_LOADOUTS,
        "animations": CARRIER_ANIMS,
        "build": build_carrier,
        "fit_state": {"throttle": 0.55, "door": 1.0},
    },
    "fighter": {
        "display": "Kite-class fighter",
        "sprite": 48,
        "loadouts": FIGHTER_LOADOUTS,
        "animations": FIGHTER_ANIMS,
        "build": build_fighter,
        "fit_state": {"throttle": 0.75},
    },
}


def margin_for(size):
    """The outline and bloom need room, but a fixed margin wastes a small cell."""
    return 3 if size <= 64 else 5


def fit_scale(craft_key, cfg):
    """
    One scale and one long-axis offset per loadout, shared by every facing and
    animation, so sprites stay registered across sheets. A hull is rarely balanced
    about its own origin, so it is first slid to centre it on the yaw axis —
    otherwise a chunk of the cell is spent on empty space behind the stern.
    """
    craft = CRAFT[craft_key]
    st = base_state(0, 8)
    st.update(craft["fit_state"])
    prims = craft["build"](cfg, st)

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
    size = craft["sprite"]
    half = size * 0.5 - margin_for(size)
    return min(half / ext_x, half / ext_y), offx


def render_cell(job):
    craft_key, cfg, anim_fn, frame, nframes, facing, scale, offx, shadow, palette = job
    # rebound explicitly so the render is identical whether pooled workers are
    # forked (inheriting globals) or spawned (starting from import state)
    use_palette(palette)
    craft = CRAFT[craft_key]
    st = anim_fn(frame, nframes)
    prims = craft["build"](cfg, st)
    a = FACING_YAW0 + facing * math.tau / 8.0
    prims = [p.placed(offx, a) for p in prims]
    size = craft["sprite"]
    img = render_frame(prims, scale, st["bob"] * scale, size, shadow)
    return (facing, frame, img.tobytes())


def build_sheets(craft_key, loadout_key, outdir, shadow=False, contact=False, jobs=None,
                 palette="crimson"):
    craft = CRAFT[craft_key]
    size = craft["sprite"]
    cfg = craft["loadouts"][loadout_key]
    scale, offx = fit_scale(craft_key, cfg)
    meta_anims = []

    for (aname, afn, nframes, fps, loop) in craft["animations"]:
        work = [
            (craft_key, cfg, afn, f, nframes, fc, scale, offx, shadow, palette)
            for fc in range(8)
            for f in range(nframes)
        ]
        if jobs and jobs > 1:
            with Pool(jobs) as pool:
                results = pool.map(render_cell, work, chunksize=4)
        else:
            results = [render_cell(w) for w in work]

        sheet = Image.new("RGBA", (size * nframes, size * 8), (0, 0, 0, 0))
        for (facing, frame, raw) in results:
            sheet.paste(Image.frombytes("RGBA", (size, size), raw), (frame * size, facing * size))

        base = f"{craft_key}_{loadout_key}_{aname}"
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
        "craft": craft_key,
        "loadout": loadout_key,
        "display_name": cfg["name"],
        "modules": cfg["modules"],
        "palette": palette,
        "frame_width": size,
        "frame_height": size,
        "projection": "orthographic dimetric, yaw 45 deg, pitch 30 deg (2:1)",
        "origin": "cell centre; the hull is registered identically across every sheet",
        "facings": [
            {
                "row": i,
                "name": FACING_NAMES[i],
                "yaw_deg": round(math.degrees(FACING_YAW0) + i * 45) % 360,
            }
            for i in range(8)
        ],
        "animations": meta_anims,
    }
    if "squadrons" in cfg:
        meta["squadron_capacity"] = cfg["squadrons"]
    if "role" in cfg:
        meta["role"] = cfg["role"]

    with open(os.path.join(outdir, f"{craft_key}_{loadout_key}.json"), "w") as fh:
        json.dump(meta, fh, indent=2)

    if contact:
        _contact_sheet(craft_key, loadout_key, outdir, meta_anims)
    return meta


def _contact_sheet(craft_key, loadout_key, outdir, meta_anims):
    """A single overview image, used for eyeballing changes between tweaks."""
    rows = [Image.open(os.path.join(outdir, a["sheet"])).convert("RGBA") for a in meta_anims]
    width = max(i.width for i in rows)
    height = sum(i.height for i in rows) + 12 * len(rows)
    sheet = Image.new("RGBA", (width, height), (18, 16, 14, 255))
    y = 0
    for img in rows:
        sheet.paste(img, (0, y), img)
        y += img.height + 12
    sheet.save(os.path.join(outdir, f"contact_{craft_key}_{loadout_key}.png"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--craft", action="append", choices=sorted(CRAFT), help="repeatable")
    ap.add_argument("--loadout", action="append", help="repeatable; filters within a craft")
    ap.add_argument("--out", default="sprites")
    ap.add_argument("--shadow", action="store_true", help="bake a deck drop shadow")
    ap.add_argument("--contact", action="store_true", help="also write contact sheets")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--palette", default="crimson", choices=sorted(PALETTES), help="paint scheme")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    use_palette(args.palette)

    atlas = []
    for craft_key in (args.craft or list(CRAFT)):
        craft = CRAFT[craft_key]
        keys = [k for k in craft["loadouts"] if not args.loadout or k in args.loadout]
        if not keys:
            continue
        entry = {"craft": craft_key, "display": craft["display"],
                 "sprite_size": craft["sprite"], "loadouts": []}
        for k in keys:
            print(f"{craft_key}/{k}: {craft['loadouts'][k]['name']}")
            entry["loadouts"].append(
                build_sheets(craft_key, k, args.out, args.shadow, args.contact,
                             args.jobs, args.palette)
            )
        atlas.append(entry)

    with open(os.path.join(args.out, "sprite_atlas.json"), "w") as fh:
        json.dump({"palette": args.palette, "craft": atlas}, fh, indent=2)
    n = sum(len(e["loadouts"]) for e in atlas)
    print(f"\nwrote {n} loadout(s) across {len(atlas)} craft to {args.out}/")


if __name__ == "__main__":
    sys.exit(main())
