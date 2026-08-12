#!/usr/bin/env python3
"""
generate_map.py — procedural isometric battle map for the Lancer fleet.

The arena is a square in world space, which the camera projects as a diamond: the
playfield itself is isometric, not just the things standing on it. A tactical grid is
drawn on the ecliptic plane along the world axes, so the diamond reads as ground.

Spawns sit at opposing corners of the diamond. Three routes connect them, and they run
along the isometric axes rather than across the screen:

    centre  the straight diagonal, shortest and most exposed
    north   out along one diamond edge, round the top corner, in along the next
    south   the same around the bottom corner

Dense asteroid and wreckage bands flank every lane, filling the ground between them.

Props are rendered with the same primitive rasteriser as the ships (generate_carrier),
so the map and the units on it share a camera, a palette and a light model. Each prop
variant is baked once per light direction (eight azimuths) and per falloff tier, then
placed with the pair matching its true bearing and distance to the sun — the same trick
that bakes a ship once per facing.

The layout has 180 degree rotational symmetry about the centre, which maps each spawn
corner onto the other, so neither side gets the better ground.

Outputs:
    sprites/prop_<kind>.png     variant columns x (light bucket, falloff tier) rows
    maps/battle_map.png         the composited map
    maps/battle_map.json        game data: arena, grid, spawns, lanes, props, sun

Usage:
    python3 generate_map.py
    python3 generate_map.py --palette steel
    python3 generate_map.py --seed 7 --no-units
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

from PIL import Image, ImageDraw

from generate_carrier import (
    FACING_NAMES, PALETTES, Rng, axes_from_euler, box, cyl, ell, project,
    render_frame, use_palette, vnorm,
)

# --------------------------------------------------------------------------------------
# arena
#
# The playfield is the world square |x| <= N, |y| <= N. Under this camera that projects
# to a 2:1 diamond, so lanes laid along the world axes run along the diamond's edges.
# --------------------------------------------------------------------------------------

MAP_W, MAP_H = 2800, 1440
MAP_SCALE = 12.9  # px per world unit, matching the ships' own fit scale

# Arena half-extent. Sized against the lanes, not picked for a nice canvas: three
# corridors of half-width H crossing a diamond of half-extent N take up roughly
# 4.3 * H / N of the playfield, so a 52-unit arena with 12-unit lanes left only 16%
# of the ground for the fields that are supposed to be walling those lanes in.
ARENA_N = 76.0
TILE = 4.0      # tactical grid pitch, world units
MAJOR = 4       # every Nth grid line is drawn brighter

SPAWN_INSET = 9.0
CORNER_INSET = 12.0
# Navigable half width. This has to be generous: the centre lane runs along the screen
# horizontal, whose perpendicular is the very axis the camera compresses 2:1, so a
# corridor there reads about half as wide on screen as the same corridor on a flank.
LANE_HALF = 10.0
WALL_BAND = 13.0     # dense field flanking each lane

SUN_XY = (3.3, 110.7)  # outside the arena, up and to the left on screen
SUN_R = 112
SUN_CORONA = 330

LIGHT_BUCKETS = 8
SUN_ELEVATION = math.radians(38.0)
FALLOFF_TIERS = 3
FALLOFF_EDGES = (95.0, 165.0)


def to_screen(x, y, z=0.0):
    sx, sy = project((x, y, z))
    return (MAP_W * 0.5 + sx * MAP_SCALE, MAP_H * 0.5 + sy * MAP_SCALE)


def light_for_bucket(k):
    az = k * math.tau / LIGHT_BUCKETS
    return vnorm((math.cos(az) * math.cos(SUN_ELEVATION),
                  math.sin(az) * math.cos(SUN_ELEVATION),
                  math.sin(SUN_ELEVATION)))


def bucket_towards_sun(x, y):
    az = math.atan2(SUN_XY[1] - y, SUN_XY[0] - x)
    return int(round(az / (math.tau / LIGHT_BUCKETS))) % LIGHT_BUCKETS


def falloff_tier(x, y):
    d = math.hypot(x - SUN_XY[0], y - SUN_XY[1])
    return 0 if d < FALLOFF_EDGES[0] else (1 if d < FALLOFF_EDGES[1] else 2)


def sheet_row(bucket, tier):
    return bucket * FALLOFF_TIERS + tier


# The diamond's corners, in world space.
CORNER_N = (ARENA_N, ARENA_N)     # screen top
CORNER_E = (ARENA_N, -ARENA_N)    # screen right
CORNER_S = (-ARENA_N, -ARENA_N)   # screen bottom
CORNER_W = (-ARENA_N, ARENA_N)    # screen left

SPAWN_W = (-ARENA_N + SPAWN_INSET, ARENA_N - SPAWN_INSET)
SPAWN_E = (ARENA_N - SPAWN_INSET, -ARENA_N + SPAWN_INSET)


def in_arena(x, y, pad=0.0):
    return abs(x) <= ARENA_N - pad and abs(y) <= ARENA_N - pad


# --------------------------------------------------------------------------------------
# lanes
# --------------------------------------------------------------------------------------


def _resample(points, n):
    """Even arc-length resample of a polyline."""
    segs = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
    lens = [math.dist(a, b) for a, b in segs]
    total = sum(lens) or 1.0
    out = []
    for i in range(n):
        want = total * i / (n - 1)
        acc = 0.0
        for j, ((a, b), L) in enumerate(zip(segs, lens)):
            if acc + L >= want or j == len(segs) - 1:
                t = 0.0 if L == 0 else min(1.0, (want - acc) / L)
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                break
            acc += L
    return out


def _smooth(points, passes=3):
    """Round the corner where a flank lane turns from one diamond edge onto the next."""
    pts = list(points)
    for _ in range(passes):
        nxt = [pts[0]]
        for i in range(1, len(pts) - 1):
            nxt.append((
                (pts[i - 1][0] + 2 * pts[i][0] + pts[i + 1][0]) * 0.25,
                (pts[i - 1][1] + 2 * pts[i][1] + pts[i + 1][1]) * 0.25,
            ))
        nxt.append(pts[-1])
        pts = nxt
    return pts


def build_lanes():
    """
    Three routes between the spawn corners. The flanks are deliberately axis-aligned:
    each runs out along one edge of the diamond, rounds a corner and comes back in
    along the next, so movement follows the isometric grid rather than cutting across
    it. The centre is the diagonal — shortest, and with the least to hide behind.
    """
    ci = ARENA_N - CORNER_INSET
    routes = {
        "centre": ([SPAWN_W, (0.0, 0.0), SPAWN_E], 1),
        "north": ([SPAWN_W, (ci * 0.15, ci), (ci, ci), (ci, -ci * 0.15), SPAWN_E], 5),
        "south": ([SPAWN_W, (-ci, ci * 0.15), (-ci, -ci), (-ci * 0.15, -ci), SPAWN_E], 5),
    }
    return [{"name": n, "path": _smooth(_resample(pts, 44), sm)}
            for n, (pts, sm) in routes.items()]


def seg_distance(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-9:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.dist(p, (ax + dx * t, ay + dy * t))


def dist_to_lanes(p, lanes):
    best = 1e9
    for lane in lanes:
        path = lane["path"]
        for i in range(len(path) - 1):
            best = min(best, seg_distance(p, path[i], path[i + 1]))
    return best


def lane_band(path, half):
    """
    Offset the centreline perpendicular in *world* space, then project. Offsetting in
    screen space would be wrong: the camera compresses vertically 2:1, so a fixed
    screen offset is a different world width depending on which way the lane runs.
    """
    left, right = [], []
    for i, p in enumerate(path):
        a = path[max(0, i - 1)]
        b = path[min(len(path) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L * half, dx / L * half
        left.append(to_screen(p[0] + nx, p[1] + ny))
        right.append(to_screen(p[0] - nx, p[1] - ny))
    return [[round(x, 1), round(y, 1)] for x, y in left + right[::-1]]


# --------------------------------------------------------------------------------------
# props
# --------------------------------------------------------------------------------------


def build_asteroid(seed, size):
    """
    A rock is one core ellipsoid with knobs welded on and craters sunk into the top.
    The knobs matter: a single smooth ellipsoid quantises into two or three broad bands
    and reads as a blob, whereas a knobbly surface throws its normals across the whole
    ramp and looks like rock.
    """
    rng = Rng(seed)
    p = [ell((0.0, 0.0, 0.0), (size * 0.60, size * 0.56, size * 0.44), "rock")]
    # large lobes that break the silhouette
    for _ in range(3 + int(rng.range(0.0, 3.0))):
        f = rng.range(0.30, 0.52)
        p.append(
            ell((rng.range(-0.40, 0.40) * size, rng.range(-0.40, 0.40) * size,
                 rng.range(-0.20, 0.24) * size),
                (size * f * rng.range(0.85, 1.15), size * f * rng.range(0.85, 1.15),
                 size * f * rng.range(0.65, 0.95)),
                "rock" if rng.next() > 0.28 else "rock_dark",
                axes_from_euler(yaw=rng.range(0.0, math.tau), pitch=rng.range(-0.5, 0.5)))
        )
    # small knobs riding the upper surface
    for _ in range(5 + int(rng.range(0.0, 5.0))):
        a, r = rng.range(0.0, math.tau), rng.range(0.20, 0.52) * size
        f = rng.range(0.10, 0.20) * size
        p.append(
            ell((math.cos(a) * r, math.sin(a) * r, rng.range(0.05, 0.34) * size),
                (f, f * rng.range(0.7, 1.3), f * rng.range(0.6, 1.0)),
                "rock" if rng.next() > 0.4 else "rock_dark",
                axes_from_euler(yaw=a, pitch=rng.range(-0.7, 0.7)))
        )
    # craters: shallow dark discs sunk into the top
    for _ in range(2 + int(rng.range(0.0, 3.0))):
        a, r = rng.range(0.0, math.tau), rng.range(0.0, 0.40) * size
        cr = size * rng.range(0.11, 0.20)
        p.append(
            ell((math.cos(a) * r, math.sin(a) * r, size * rng.range(0.30, 0.40)),
                (cr, cr * rng.range(0.8, 1.2), cr * 0.42), "rock_dark",
                axes_from_euler(yaw=a, pitch=rng.range(-0.3, 0.3)))
        )
    # a mineral seam on some rocks only; on every rock it turns into visual noise
    if rng.next() > 0.6:
        p.append(
            box((rng.range(-0.2, 0.2) * size, rng.range(-0.2, 0.2) * size, size * 0.40),
                (size * 0.24, size * 0.045, size * 0.03), "accent",
                axes_from_euler(yaw=rng.range(0.0, math.tau)))
        )
    return p


def build_debris(seed, size):
    """Wreckage: torn plates and a bent spar, in the fleet's own materials."""
    rng = Rng(seed)
    p = []
    mats = ("hull_dark", "armor", "deck", "hull", "armor", "deck", "hull_dark", "armor")
    for _ in range(3 + int(rng.range(0.0, 3.0))):
        p.append(
            box(
                (rng.range(-0.34, 0.34) * size, rng.range(-0.34, 0.34) * size,
                 rng.range(-0.18, 0.22) * size),
                (size * rng.range(0.22, 0.52), size * rng.range(0.07, 0.20),
                 size * rng.range(0.04, 0.11)),
                mats[int(rng.range(0.0, 8.0)) % 8],
                axes_from_euler(yaw=rng.range(0.0, math.tau),
                                pitch=rng.range(-0.7, 0.7), roll=rng.range(-0.7, 0.7)),
            )
        )
    a = rng.range(0.0, math.tau)
    p.append(
        cyl((math.cos(a) * size * 0.5, math.sin(a) * size * 0.5, rng.range(-0.1, 0.1) * size),
            (-math.cos(a) * size * 0.42, -math.sin(a) * size * 0.42, size * 0.16),
            size * 0.05, "armor")
    )
    # A minority of wrecks still carry hull paint; on every chunk it becomes confetti.
    if rng.next() > 0.72:
        p.append(
            box((rng.range(-0.25, 0.25) * size, rng.range(-0.25, 0.25) * size,
                 rng.range(0.0, 0.2) * size),
                (size * rng.range(0.18, 0.32), size * 0.10, size * 0.035), "plate",
                axes_from_euler(yaw=rng.range(0.0, math.tau), roll=rng.range(-0.6, 0.6)))
        )
    return p


# kind -> (cell px, world radius, variants, cover 0..1, builder, model size)
PROP_KINDS = {
    "asteroid_lg": (96, 3.4, 3, 0.85, build_asteroid, 3.1),
    "asteroid_md": (64, 2.2, 4, 0.60, build_asteroid, 2.0),
    "asteroid_sm": (40, 1.3, 4, 0.35, build_asteroid, 1.2),
    "debris_lg": (80, 2.8, 3, 0.55, build_debris, 2.6),
    "debris_sm": (44, 1.5, 4, 0.30, build_debris, 1.4),
}


def render_prop_sheets(outdir):
    meta = {}
    for kind, (cell, _r, nvar, _cov, builder, msize) in PROP_KINDS.items():
        rows = LIGHT_BUCKETS * FALLOFF_TIERS
        sheet = Image.new("RGBA", (cell * nvar, cell * rows), (0, 0, 0, 0))
        scale = (cell * 0.5 - 3) / (msize * 0.80)
        for var in range(nvar):
            prims = builder(hash((kind, var)) & 0xFFFF, msize)
            for k in range(LIGHT_BUCKETS):
                for tier in range(FALLOFF_TIERS):
                    img = render_frame(prims, scale, 0.0, cell, False,
                                       light_for_bucket(k), -tier)
                    sheet.paste(img, (var * cell, sheet_row(k, tier) * cell))
        name = f"prop_{kind}.png"
        sheet.save(os.path.join(outdir, name))
        meta[kind] = {"sheet": name, "cell": cell, "variants": nvar,
                      "light_buckets": LIGHT_BUCKETS, "falloff_tiers": FALLOFF_TIERS,
                      "row": "light_bucket * falloff_tiers + falloff_tier"}
        print(f"  {name}: {nvar} variants x {LIGHT_BUCKETS} directions x {FALLOFF_TIERS} tiers")
    return meta


def place_props(seed, lanes):
    """
    Scatter across the half of the diamond behind the west spawn, then rotate 180
    degrees onto the other half. Density comes from how far a candidate sits from the
    nearest lane; anything that would choke a lane is rejected outright, so all three
    routes stay open corner to corner.
    """
    rng = Rng(seed)
    heavy = ("asteroid_lg", "asteroid_md", "debris_lg")
    light = ("asteroid_sm", "debris_sm", "asteroid_md")
    # the half-plane split runs perpendicular to the centre lane, through the origin
    ax, ay = SPAWN_E[0] - SPAWN_W[0], SPAWN_E[1] - SPAWN_W[1]
    alen = math.hypot(ax, ay)
    ax, ay = ax / alen, ay / alen

    # A coarse spatial hash keeps the overlap test near O(1); the arena holds enough
    # rock that checking every placed prop each time gets slow.
    CELL = 8.0
    grid = {}

    def clear_of_neighbours(x, y, radius):
        cx, cy = int(x // CELL), int(y // CELL)
        for gx in range(cx - 1, cx + 2):
            for gy in range(cy - 1, cy + 2):
                for (ox, oy, orad) in grid.get((gx, gy), ()):
                    if math.dist((x, y), (ox, oy)) < (radius + orad) * 0.82:
                        return False
        return True

    half = []
    in_lane = []
    for _ in range(34000):
        x = rng.range(-ARENA_N, ARENA_N)
        y = rng.range(-ARENA_N, ARENA_N)
        if x * ax + y * ay > -2.0:          # keep to one side of centre
            continue
        if not in_arena(x, y, pad=1.5):
            continue

        # The field is thick everywhere; the lanes are what is carved out of it.
        # Density peaks right against a lane edge so the walls have a defined face.
        d = dist_to_lanes((x, y), lanes)
        if d < LANE_HALF:
            if rng.next() > 0.035:
                continue
            # Cover inside a lane is kept well spaced from other in-lane cover. Two
            # rocks that are each individually legal still plug the corridor if they
            # land beside each other.
            if any(math.dist((x, y), c) < 13.0 for c in in_lane):
                continue
            kind = light[int(rng.range(0.0, 3.0)) % 3]
        else:
            t = min(1.0, (d - LANE_HALF) / WALL_BAND)
            if rng.next() > 0.80 - 0.30 * t:
                continue
            kind = heavy[int(rng.range(0.0, 3.0)) % 3] if rng.next() > 0.25 \
                else light[int(rng.range(0.0, 3.0)) % 3]

        _c, radius, nvar, cover, _b, _m = PROP_KINDS[kind]

        # Never choke a lane. Testing the prop's edge rather than its centre matters:
        # a large rock centred just outside a lane still spills its whole sprite in.
        if d < LANE_HALF and (radius > 2.5 or d + radius > LANE_HALF - 1.5):
            continue
        if d >= LANE_HALF and d - radius < LANE_HALF - 1.0:
            continue
        if math.dist((x, y), SPAWN_W) < 22.0 or math.dist((x, y), SPAWN_E) < 22.0:
            continue
        if not clear_of_neighbours(x, y, radius):
            continue

        grid.setdefault((int(x // CELL), int(y // CELL)), []).append((x, y, radius))
        if d < LANE_HALF:
            in_lane.append((x, y))
        half.append({"kind": kind, "variant": int(rng.range(0.0, nvar)) % nvar,
                     "x": x, "y": y, "radius": radius, "cover": cover})

    props = []
    for o in half:
        for sign in (1.0, -1.0):
            x, y = o["x"] * sign, o["y"] * sign
            sx, sy = to_screen(x, y)
            props.append({
                "kind": o["kind"], "variant": o["variant"],
                "x": round(x, 2), "y": round(y, 2),
                "radius": o["radius"], "cover": o["cover"],
                "side": "west" if sign > 0 else "east",
                "light_bucket": bucket_towards_sun(x, y),
                "falloff_tier": falloff_tier(x, y),
                "screen": [round(sx, 1), round(sy, 1)],
            })
    return props


CRAFT_RADIUS = 2.0  # what has to fit down a lane: a fighter plus a little margin


def _corridor_cells(lane):
    """Integer cells within the lane's half width. Fixed by the path, so cache it."""
    path = lane["path"]
    cells = []
    n = int(ARENA_N)
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            if not in_arena(float(i), float(j)):
                continue
            for k in range(len(path) - 1):
                if seg_distance((float(i), float(j)), path[k], path[k + 1]) <= LANE_HALF:
                    cells.append((i, j))
                    break
    return cells


def _occupancy(props, craft_radius):
    cell = 8.0
    buckets = {}
    for p in props:
        buckets.setdefault((int(p["x"] // cell), int(p["y"] // cell)), []).append(p)

    def blocked(x, y):
        cx, cy = int(x // cell), int(y // cell)
        for gx in range(cx - 1, cx + 2):
            for gy in range(cy - 1, cy + 2):
                for p in buckets.get((gx, gy), ()):
                    if math.hypot(x - p["x"], y - p["y"]) < p["radius"] + craft_radius:
                        return True
        return False

    return blocked


def _flood(open_cells, start):
    seen = {start} if start in open_cells else set()
    queue = list(seen)
    while queue:
        i, j = queue.pop()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nb = (i + di, j + dj)
            if nb in open_cells and nb not in seen:
                seen.add(nb)
                queue.append(nb)
    return seen


def clear_lanes(props, lanes, craft_radius=CRAFT_RADIUS, max_passes=40):
    """
    Guarantee every lane is flyable end to end, and repair it if not.

    Rejecting bad candidates during scatter is not the same claim as a lane being open:
    several individually legal props can still close a corridor between them, which is
    exactly what happened here. So the corridors are flood filled from spawn to spawn
    and, where the fill stalls, the in-lane cover around the pinch is removed — in
    mirrored pairs, so the map stays symmetric — until the route opens.
    """
    start = (round(SPAWN_W[0]), round(SPAWN_W[1]))
    goal = (round(SPAWN_E[0]), round(SPAWN_E[1]))
    corridors = {lane["name"]: _corridor_cells(lane) for lane in lanes}
    removed = 0

    for lane in lanes:
        cells = corridors[lane["name"]]
        for _ in range(max_passes):
            blocked = _occupancy(props, craft_radius)
            open_cells = {c for c in cells if not blocked(float(c[0]), float(c[1]))}
            seen = _flood(open_cells, start)
            if goal in seen:
                break

            # first waypoint the fill could not get near: the pinch
            pinch = None
            for w in lane["path"]:
                if not any(math.hypot(w[0] - c[0], w[1] - c[1]) < 3.0 for c in seen):
                    pinch = w
                    break
            if pinch is None:
                break

            # Only in-lane cover is eligible: the flanking walls are what define the
            # lane, so removing those would dissolve the map rather than repair it.
            near = [p for p in props
                    if math.hypot(p["x"] - pinch[0], p["y"] - pinch[1]) < 9.0
                    and dist_to_lanes((p["x"], p["y"]), lanes) < LANE_HALF]
            if not near:
                break
            worst = min(near, key=lambda p: dist_to_lanes((p["x"], p["y"]), lanes))
            # take its 180 degree partner with it, so symmetry survives the repair
            doomed = {(round(worst["x"], 2), round(worst["y"], 2)),
                      (round(-worst["x"], 2), round(-worst["y"], 2))}
            before = len(props)
            props[:] = [p for p in props
                        if (round(p["x"], 2), round(p["y"], 2)) not in doomed]
            if len(props) == before:
                break
            removed += before - len(props)

    return removed


def verify_lanes(props, lanes, craft_radius=CRAFT_RADIUS):
    """Final check that every corridor connects spawn to spawn."""
    start = (round(SPAWN_W[0]), round(SPAWN_W[1]))
    goal = (round(SPAWN_E[0]), round(SPAWN_E[1]))
    blocked = _occupancy(props, craft_radius)
    results = []
    for lane in lanes:
        open_cells = {c for c in _corridor_cells(lane)
                      if not blocked(float(c[0]), float(c[1]))}
        seen = _flood(open_cells, start)
        results.append({"name": lane["name"], "passable": goal in seen,
                        "craft_radius": craft_radius, "open_cells": len(open_cells)})
    return results


# --------------------------------------------------------------------------------------
# painting
# --------------------------------------------------------------------------------------


def paint_background(palette):
    """
    Backlit starfield: a coarse quantised glow field radiating from the sun, painted at
    quarter resolution and upscaled nearest so it stays blocky like the sprites, then
    stars over the top and the sun disc itself.
    """
    warm = palette == "crimson"
    deep = (9, 7, 6) if warm else (7, 9, 13)
    steps = ([(9, 7, 6), (18, 13, 10), (30, 21, 14), (46, 31, 18), (68, 44, 22)] if warm
             else [(7, 9, 13), (12, 16, 24), (18, 25, 37), (26, 36, 52), (36, 50, 71)])

    qw, qh = MAP_W // 4, MAP_H // 4
    field = Image.new("RGB", (qw, qh), deep)
    fpx = field.load()
    sx, sy = to_screen(*SUN_XY)
    qsx, qsy = sx / 4.0, sy / 4.0
    reach = SUN_CORONA * 2.6 / 4.0
    for y in range(qh):
        for x in range(qw):
            t = max(0.0, 1.0 - math.hypot(x - qsx, y - qsy) / reach)
            fpx[x, y] = steps[min(len(steps) - 1, int(t * t * len(steps)))]
    img = field.resize((MAP_W, MAP_H), Image.NEAREST).convert("RGBA")

    px = img.load()
    rng = Rng(4242)
    tiers = ([(70, 58, 46), (120, 102, 80), (186, 166, 132), (240, 226, 196)] if warm
             else [(52, 62, 80), (92, 106, 130), (150, 168, 196), (226, 236, 250)])
    for _ in range(2400):
        x, y = int(rng.range(0, MAP_W)), int(rng.range(0, MAP_H))
        t = int(rng.range(0.0, 4.0)) % 4
        if math.hypot(x - sx, y - sy) < SUN_CORONA * (0.5 + 0.4 * t):
            continue
        px[x, y] = tiers[t] + (255,)
        if t == 3:
            for (dx, dy) in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if 0 <= x + dx < MAP_W and 0 <= y + dy < MAP_H:
                    px[x + dx, y + dy] = tiers[1] + (255,)

    corona = ([(96, 58, 24), (140, 84, 30), (198, 122, 40)] if warm
              else [(52, 74, 104), (78, 108, 148), (116, 152, 196)])
    disc = ([(255, 190, 92), (255, 226, 150), (255, 248, 214)] if warm
            else [(196, 224, 255), (226, 240, 255), (250, 252, 255)])
    x0, y0 = max(0, int(sx - SUN_CORONA)), max(0, int(sy - SUN_CORONA))
    x1, y1 = min(MAP_W, int(sx + SUN_CORONA)), min(MAP_H, int(sy + SUN_CORONA))
    for y in range(y0, y1):
        for x in range(x0, x1):
            d = math.hypot(x - sx, y - sy)
            if d > SUN_CORONA:
                continue
            if d > SUN_R:
                t = 1.0 - (d - SUN_R) / (SUN_CORONA - SUN_R)
                k = int(t * t * len(corona))
                if k <= 0:
                    continue
                b = px[x, y]
                c = corona[min(len(corona) - 1, k)]
                px[x, y] = (max(b[0], c[0]), max(b[1], c[1]), max(b[2], c[2]), 255)
            else:
                t = d / SUN_R
                px[x, y] = disc[min(len(disc) - 1, int(t * t * len(disc)))] + (255,)
    return img


def paint_grid(img, palette):
    """The tactical plane: grid lines along the world axes, so the diamond reads as ground."""
    d = ImageDraw.Draw(img, "RGBA")
    minor = (150, 132, 104, 18) if palette == "crimson" else (108, 132, 168, 18)
    major = (176, 156, 122, 38) if palette == "crimson" else (128, 156, 198, 38)
    edge = (206, 184, 142, 105) if palette == "crimson" else (150, 180, 220, 105)

    steps = int(ARENA_N / TILE)
    for i in range(-steps, steps + 1):
        c = i * TILE
        col = major if i % MAJOR == 0 else minor
        d.line([to_screen(c, -ARENA_N), to_screen(c, ARENA_N)], fill=col, width=1)
        d.line([to_screen(-ARENA_N, c), to_screen(ARENA_N, c)], fill=col, width=1)

    corners = [to_screen(*CORNER_N), to_screen(*CORNER_E),
               to_screen(*CORNER_S), to_screen(*CORNER_W)]
    d.line(corners + [corners[0]], fill=edge, width=2)
    return img


def paste_prop(canvas, sheets, prop):
    cell = PROP_KINDS[prop["kind"]][0]
    row = sheet_row(prop["light_bucket"], prop["falloff_tier"])
    v = prop["variant"]
    tile = sheets[prop["kind"]].crop((v * cell, row * cell, (v + 1) * cell, (row + 1) * cell))
    cx, cy = prop["screen"]
    canvas.alpha_composite(tile, (int(cx - cell / 2), int(cy - cell / 2)))


def paste_units(canvas, spritedir, lanes):
    placed = []
    try:
        carrier = Image.open(os.path.join(spritedir, "carrier_mk3_idle.png")).convert("RGBA")
        fighter = Image.open(
            os.path.join(spritedir, "fighter_interceptor_cruise.png")).convert("RGBA")
    except FileNotFoundError:
        print("  (no ship sheets yet — run generate_carrier.py for units on the map)")
        return placed

    for (spawn, row, name) in ((SPAWN_W, 2, "west"), (SPAWN_E, 6, "east")):
        sx, sy = to_screen(*spawn)
        cell = 96
        canvas.alpha_composite(carrier.crop((0, row * cell, cell, (row + 1) * cell)),
                               (int(sx - cell / 2), int(sy - cell / 2)))
        placed.append({"side": name, "craft": "carrier", "loadout": "mk3",
                       "facing_row": row, "facing": FACING_NAMES[row],
                       "x": round(spawn[0], 2), "y": round(spawn[1], 2),
                       "screen": [round(sx, 1), round(sy, 1)]})
        # an escort standing off the mouth of each lane
        for lane in lanes:
            path = lane["path"] if name == "west" else lane["path"][::-1]
            p = path[4]
            fx, fy = to_screen(p[0], p[1])
            fc = 48
            canvas.alpha_composite(fighter.crop((0, row * fc, fc, (row + 1) * fc)),
                                   (int(fx - fc / 2), int(fy - fc / 2)))
    return placed


# --------------------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="maps")
    ap.add_argument("--sprites", default="sprites")
    ap.add_argument("--palette", default="crimson", choices=sorted(PALETTES))
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--no-units", action="store_true", help="terrain only")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.sprites, exist_ok=True)
    use_palette(args.palette)

    print("props:")
    prop_meta = render_prop_sheets(args.sprites)

    lanes = build_lanes()
    props = place_props(args.seed, lanes)
    reopened = clear_lanes(props, lanes)
    passable = verify_lanes(props, lanes)
    if reopened:
        print(f"  cleared {reopened} props to reopen pinched lanes")
    # Painter's order: larger x + y sits higher on screen and is further from the
    # camera, so the far side of the field has to go down first.
    props.sort(key=lambda o: -(o["x"] + o["y"]))

    canvas = paint_background(args.palette)
    paint_grid(canvas, args.palette)
    sheets = {k: Image.open(os.path.join(args.sprites, m["sheet"])).convert("RGBA")
              for k, m in prop_meta.items()}
    for prop in props:
        paste_prop(canvas, sheets, prop)

    units = [] if args.no_units else paste_units(canvas, args.sprites, lanes)

    img_path = os.path.join(args.out, "battle_map.png")
    canvas.convert("RGB").save(img_path)

    sun_sx, sun_sy = to_screen(*SUN_XY)
    data = {
        "name": "Kestrel Reach",
        "palette": args.palette,
        "seed": args.seed,
        "image": os.path.basename(img_path),
        "width": MAP_W, "height": MAP_H,
        "scale_px_per_unit": MAP_SCALE,
        "projection": "orthographic dimetric, yaw 45 deg, pitch 30 deg (2:1)",
        "arena": {
            "shape": "world square |x|,|y| <= half_extent, projected as a screen diamond",
            "half_extent": ARENA_N,
            "tile": TILE,
            "grid_major_every": MAJOR,
            "corners": {
                n: {"x": c[0], "y": c[1],
                    "screen": [round(to_screen(*c)[0], 1), round(to_screen(*c)[1], 1)]}
                for n, c in (("north", CORNER_N), ("east", CORNER_E),
                             ("south", CORNER_S), ("west", CORNER_W))
            },
        },
        "symmetry": "180 degree rotation about (0,0); maps each spawn corner onto the other",
        "draw_order": "descending (x + y): larger is further from the camera",
        "sun": {
            "x": SUN_XY[0], "y": SUN_XY[1],
            "screen": [round(sun_sx, 1), round(sun_sy, 1)],
            "radius_px": SUN_R,
            "elevation_deg": round(math.degrees(SUN_ELEVATION), 1),
            "light_buckets": LIGHT_BUCKETS,
            "falloff_tiers": FALLOFF_TIERS,
            "note": "props are baked per light bucket and falloff tier, then placed with "
                    "the pair matching their true bearing and distance to the sun",
        },
        "spawns": [
            {"side": "west", "corner": "west", "x": SPAWN_W[0], "y": SPAWN_W[1],
             "facing_row": 2, "facing": "E",
             "screen": [round(to_screen(*SPAWN_W)[0], 1), round(to_screen(*SPAWN_W)[1], 1)]},
            {"side": "east", "corner": "east", "x": SPAWN_E[0], "y": SPAWN_E[1],
             "facing_row": 6, "facing": "W",
             "screen": [round(to_screen(*SPAWN_E)[0], 1), round(to_screen(*SPAWN_E)[1], 1)]},
        ],
        "lanes": [
            {
                "name": lane["name"],
                "half_width": LANE_HALF,
                "length": round(sum(math.dist(lane["path"][i], lane["path"][i + 1])
                                    for i in range(len(lane["path"]) - 1)), 1),
                "waypoints": [{"x": round(p[0], 2), "y": round(p[1], 2),
                               "screen": [round(to_screen(*p)[0], 1),
                                          round(to_screen(*p)[1], 1)]}
                              for p in lane["path"]],
                "band": lane_band(lane["path"], LANE_HALF),
                "passable": next(r["passable"] for r in passable if r["name"] == lane["name"]),
            }
            for lane in lanes
        ],
        "traversal_check": {
            "craft_radius": passable[0]["craft_radius"],
            "props_cleared_to_reopen": reopened,
            "method": "occupancy grid flood fill along each corridor, spawn to spawn",
            "results": passable,
        },
        "prop_kinds": {k: {**v, "world_radius": PROP_KINDS[k][1], "cover": PROP_KINDS[k][3]}
                       for k, v in prop_meta.items()},
        "props": props,
        "units": units,
    }
    with open(os.path.join(args.out, "battle_map.json"), "w") as fh:
        json.dump(data, fh, indent=2)

    kb = os.path.getsize(img_path) / 1024
    print(f"\n{img_path}  {MAP_W}x{MAP_H}  {kb:.0f} KB")
    print(f"{len(props)} props, {len(lanes)} lanes, {len(units)} units placed")
    for lane in data["lanes"]:
        ok = "open" if lane["passable"] else "BLOCKED"
        print(f"  lane {lane['name']:<7} {lane['length']:>6.1f} units  {ok}")
    if not all(l["passable"] for l in data["lanes"]):
        print("\nA lane is not traversable — reduce density or widen LANE_HALF.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
