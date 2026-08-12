#!/usr/bin/env python3
"""
generate_map.py — procedural isometric battle map for the Lancer fleet.

Builds a symmetric two-carrier arena: a backlit starfield, a sun that is an actual
light source rather than a painted highlight, dense asteroid and wreckage fields whose
gaps form three navigable lanes end to end, and scattered cover inside those lanes.

Props are rendered with the same primitive rasteriser as the ships (generate_carrier),
so the map and the units on it share a camera, a palette and a light model. Each prop
variant is baked once per light direction — eight azimuth buckets, exactly the way a
ship is baked once per facing — and placed with the bucket nearest the true direction
to the sun. That is what makes the sun read as lighting the field.

The layout has 180 degree rotational symmetry about the map centre, so neither side
gets the better ground.

Outputs:
    sprites/prop_<kind>.png     variant columns x light-bucket rows
    maps/battle_map.png         the composited map
    maps/battle_map.json        game data: bounds, spawns, lanes, props, sun

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

from PIL import Image

from generate_carrier import (
    CAM_PITCH, FACING_NAMES, MATERIALS, Rng, axes_from_euler, box, cyl, ell,
    render_frame, use_palette, vnorm, PALETTES,
)

# --------------------------------------------------------------------------------------
# map frame
#
# The camera compresses world space to screen 1:1 along u (screen horizontal) and 2:1
# along v (screen vertical), so the map is authored directly in (u, v) and converted to
# world only where the lighting needs a real direction.
# --------------------------------------------------------------------------------------

MAP_W, MAP_H = 1920, 1000
MAP_SCALE = 12.9  # px per world unit, matching the ships' own fit scale

HALF_U = (MAP_W * 0.5) / MAP_SCALE            # 74.4
HALF_V = (MAP_H * 0.5) / (0.5 * MAP_SCALE)    # 155.0 of v maps to 1000px

PLAY_U, PLAY_V = 66.0, 60.0  # the playable area, inset from the canvas
LANE_V = (-46.0, 0.0, 46.0)  # lane centrelines
LANE_HALF = 11.0             # navigable half width
WALL_V = (-23.0, 23.0, -69.0, 69.0)  # the dense bands that create the lanes
WALL_HALF = 9.0

SUN_U, SUN_V = -68.0, 84.0
SUN_R = 108          # disc radius in px
SUN_CORONA = 300     # glow reach in px

LIGHT_BUCKETS = 8
SUN_ELEVATION = math.radians(38.0)
# Falloff tiers. Direction alone barely reads on a lumpy rock; what sells a sun is
# near rocks being bright and far ones sinking towards the background.
FALLOFF_TIERS = 3
FALLOFF_EDGES = (66.0, 118.0)


def uv_to_screen(u, v):
    return (MAP_W * 0.5 + u * MAP_SCALE, MAP_H * 0.5 - v * 0.5 * MAP_SCALE)


def uv_to_world(u, v):
    """u runs along world (1,-1), v along world (1,1); both normalised."""
    k = 1.0 / math.sqrt(2.0)
    return ((u + v) * k, (v - u) * k)


def light_for_bucket(k):
    az = k * math.tau / LIGHT_BUCKETS
    return vnorm((math.cos(az) * math.cos(SUN_ELEVATION),
                  math.sin(az) * math.cos(SUN_ELEVATION),
                  math.sin(SUN_ELEVATION)))


def bucket_towards_sun(u, v):
    """Which baked light direction best matches the true bearing to the sun."""
    px, py = uv_to_world(u, v)
    sx, sy = uv_to_world(SUN_U, SUN_V)
    az = math.atan2(sy - py, sx - px)
    return int(round(az / (math.tau / LIGHT_BUCKETS))) % LIGHT_BUCKETS


def falloff_tier(u, v):
    """(u, v) is an orthonormal frame, so plain distance here is world distance."""
    d = math.hypot(u - SUN_U, v - SUN_V)
    return 0 if d < FALLOFF_EDGES[0] else (1 if d < FALLOFF_EDGES[1] else 2)


def sheet_row(bucket, tier):
    return bucket * FALLOFF_TIERS + tier


# --------------------------------------------------------------------------------------
# props
# --------------------------------------------------------------------------------------


def build_asteroid(seed, size):
    """
    A rock is one core ellipsoid with knobs welded on and craters sunk into the top.
    The knobs matter: a single smooth ellipsoid quantises into two or three broad
    bands and reads as a blob, whereas a knobbly surface throws its normals across
    the whole ramp and looks like rock.
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
    # A minority of wrecks still carry hull paint. Putting it on every chunk turns the
    # whole field into red confetti at map scale.
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
    """One sheet per kind: variant columns, light-bucket rows."""
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


# --------------------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------------------


def lane_offset(v_centre, u):
    """Lanes bow gently rather than running dead straight."""
    return v_centre + math.sin(u * 0.045) * 4.5 + math.sin(u * 0.017 + 1.3) * 3.0


def dist_to_lanes(u, v):
    return min(abs(v - lane_offset(c, u)) for c in LANE_V)


def place_props(seed):
    """
    Scatter the field across the left half, then rotate it 180 degrees onto the right.
    Density comes from which band a candidate lands in; anything that would choke a
    lane is rejected outright, so the three routes always stay open end to end.
    """
    rng = Rng(seed)
    heavy = ("asteroid_lg", "asteroid_md", "debris_lg")
    light = ("asteroid_sm", "debris_sm", "asteroid_md")
    half = []

    for _ in range(2600):
        u = rng.range(-PLAY_U, -0.5)
        v = rng.range(-PLAY_V - 12.0, PLAY_V + 12.0)

        lane_d = dist_to_lanes(u, v)
        wall_d = min(abs(v - w) for w in WALL_V)

        if wall_d < WALL_HALF:
            # the thick bands that make the lanes; densest at their centre
            if rng.next() > 0.30 + 0.55 * (wall_d / WALL_HALF):
                kind = heavy[int(rng.range(0.0, 3.0)) % 3]
            else:
                continue
        elif lane_d < LANE_HALF:
            # cover inside a lane, sparse enough to leave a driving line
            if rng.next() > 0.055:
                continue
            kind = light[int(rng.range(0.0, 3.0)) % 3]
        else:
            if rng.next() > 0.10:
                continue
            kind = light[int(rng.range(0.0, 3.0)) % 3]

        _c, radius, nvar, cover, _b, _m = PROP_KINDS[kind]

        # never block a lane: a prop must leave a gap wide enough to fly through
        if lane_d < LANE_HALF and lane_d + radius > LANE_HALF - 1.5:
            continue
        if lane_d < LANE_HALF and radius > 2.5:
            continue
        # keep clear of the spawns
        if math.hypot(u + PLAY_U * 0.94, v) < 22.0:
            continue
        # no overlaps
        if any(math.hypot(u - o["u"], v - o["v"]) < (radius + o["radius"]) * 0.82 for o in half):
            continue

        half.append({
            "kind": kind, "variant": int(rng.range(0.0, nvar)) % nvar,
            "u": round(u, 2), "v": round(v, 2), "radius": radius, "cover": cover,
        })

    props = []
    for i, o in enumerate(half):
        for sign in (1.0, -1.0):
            q = dict(o)
            q["u"], q["v"] = round(o["u"] * sign, 2), round(o["v"] * sign, 2)
            q["side"] = "west" if sign > 0 else "east"
            q["light_bucket"] = bucket_towards_sun(q["u"], q["v"])
            q["falloff_tier"] = falloff_tier(q["u"], q["v"])
            sx, sy = uv_to_screen(q["u"], q["v"])
            q["screen"] = [round(sx, 1), round(sy, 1)]
            props.append(q)
    return props


def lane_data():
    lanes = []
    for name, c in zip(("north", "centre", "south"), LANE_V):
        pts = []
        for i in range(25):
            u = -PLAY_U + (2.0 * PLAY_U) * i / 24.0
            v = lane_offset(c, u)
            sx, sy = uv_to_screen(u, v)
            pts.append({"u": round(u, 2), "v": round(v, 2),
                        "screen": [round(sx, 1), round(sy, 1)]})
        lanes.append({"name": name, "half_width": LANE_HALF, "waypoints": pts})
    return lanes


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
    sx, sy = uv_to_screen(SUN_U, SUN_V)
    qsx, qsy = sx / 4.0, sy / 4.0
    reach = SUN_CORONA * 2.6 / 4.0
    for y in range(qh):
        for x in range(qw):
            d = math.hypot(x - qsx, y - qsy) / reach
            t = max(0.0, 1.0 - d)
            fpx[x, y] = steps[min(len(steps) - 1, int(t * t * len(steps)))]
    img = field.resize((MAP_W, MAP_H), Image.NEAREST).convert("RGBA")

    px = img.load()
    rng = Rng(4242)
    tiers = ([(70, 58, 46), (120, 102, 80), (186, 166, 132), (240, 226, 196)] if warm
             else [(52, 62, 80), (92, 106, 130), (150, 168, 196), (226, 236, 250)])
    for _ in range(2300):
        x, y = int(rng.range(0, MAP_W)), int(rng.range(0, MAP_H))
        t = int(rng.range(0.0, 4.0)) % 4
        # stars thin out into the sun's glare
        if math.hypot(x - sx, y - sy) < SUN_CORONA * (0.5 + 0.4 * t):
            continue
        c = tiers[t] + (255,)
        px[x, y] = c
        if t == 3:
            for (dx, dy) in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if 0 <= x + dx < MAP_W and 0 <= y + dy < MAP_H:
                    px[x + dx, y + dy] = tiers[1] + (255,)

    # the sun: quantised corona rings, then the disc
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
                base = px[x, y]
                c = corona[min(len(corona) - 1, k)]
                px[x, y] = (max(base[0], c[0]), max(base[1], c[1]), max(base[2], c[2]), 255)
            else:
                t = d / SUN_R
                px[x, y] = disc[min(len(disc) - 1, int(t * t * len(disc)))] + (255,)
    return img


def paste_prop(canvas, sheets, prop):
    kind = prop["kind"]
    cell = PROP_KINDS[kind][0]
    src = sheets[kind]
    cx, cy = prop["screen"]
    row = sheet_row(prop["light_bucket"], prop["falloff_tier"])
    tile = src.crop((prop["variant"] * cell, row * cell,
                     (prop["variant"] + 1) * cell, (row + 1) * cell))
    canvas.alpha_composite(tile, (int(cx - cell / 2), int(cy - cell / 2)))


def paste_units(canvas, spritedir):
    """Drop the actual carrier and fighter sprites in, facing each other."""
    placed = []
    try:
        carrier = Image.open(os.path.join(spritedir, "carrier_mk3_idle.png")).convert("RGBA")
        fighter = Image.open(os.path.join(spritedir, "fighter_interceptor_cruise.png")).convert("RGBA")
    except FileNotFoundError:
        print("  (no ship sheets yet — run generate_carrier.py for units on the map)")
        return placed

    for (u, v, row, name) in ((-PLAY_U * 0.94, 0.0, 2, "west"), (PLAY_U * 0.94, 0.0, 6, "east")):
        sx, sy = uv_to_screen(u, v)
        cell = 96
        tile = carrier.crop((0, row * cell, cell, (row + 1) * cell))
        canvas.alpha_composite(tile, (int(sx - cell / 2), int(sy - cell / 2)))
        placed.append({"side": name, "craft": "carrier", "loadout": "mk3",
                       "facing_row": row, "facing": FACING_NAMES[row],
                       "u": round(u, 2), "v": round(v, 2),
                       "screen": [round(sx, 1), round(sy, 1)]})
        # a small escort wing spread across the lane mouths
        for i, lv in enumerate(LANE_V):
            fu = u + (9.0 if u < 0 else -9.0)
            fv = lane_offset(lv, fu) * 0.34
            fx, fy = uv_to_screen(fu + i * 0.0, fv)
            fc = 48
            ft = fighter.crop((0, row * fc, fc, (row + 1) * fc))
            canvas.alpha_composite(ft, (int(fx - fc / 2), int(fy - fc / 2)))
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

    props = place_props(args.seed)
    # far props first so nearer ones overlap them correctly
    props.sort(key=lambda o: (o["v"], o["u"]))

    canvas = paint_background(args.palette)
    sheets = {k: Image.open(os.path.join(args.sprites, m["sheet"])).convert("RGBA")
              for k, m in prop_meta.items()}
    for prop in props:
        paste_prop(canvas, sheets, prop)

    units = [] if args.no_units else paste_units(canvas, args.sprites)

    img_path = os.path.join(args.out, "battle_map.png")
    canvas.convert("RGB").save(img_path)

    sun_sx, sun_sy = uv_to_screen(SUN_U, SUN_V)
    data = {
        "name": "Kestrel Reach",
        "palette": args.palette,
        "seed": args.seed,
        "image": os.path.basename(img_path),
        "width": MAP_W, "height": MAP_H,
        "scale_px_per_unit": MAP_SCALE,
        "projection": "orthographic dimetric, yaw 45 deg, pitch 30 deg (2:1)",
        "axes": {
            "u": "screen horizontal, world (1,-1) normalised, 1 unit = 1 px * scale",
            "v": "screen vertical, world (1,1) normalised, compressed 2:1 on screen",
        },
        "bounds": {"u": [-PLAY_U, PLAY_U], "v": [-PLAY_V, PLAY_V]},
        "symmetry": "180 degree rotation about (0,0)",
        "sun": {
            "u": SUN_U, "v": SUN_V,
            "screen": [round(sun_sx, 1), round(sun_sy, 1)],
            "radius_px": SUN_R,
            "elevation_deg": round(math.degrees(SUN_ELEVATION), 1),
            "light_buckets": LIGHT_BUCKETS,
            "note": "props are baked per light bucket and placed with the bucket "
                    "nearest their true bearing to the sun",
        },
        "spawns": [
            {"side": "west", "u": -PLAY_U * 0.94, "v": 0.0, "facing_row": 2, "facing": "E",
             "screen": [round(uv_to_screen(-PLAY_U * 0.94, 0.0)[0], 1),
                        round(uv_to_screen(-PLAY_U * 0.94, 0.0)[1], 1)]},
            {"side": "east", "u": PLAY_U * 0.94, "v": 0.0, "facing_row": 6, "facing": "W",
             "screen": [round(uv_to_screen(PLAY_U * 0.94, 0.0)[0], 1),
                        round(uv_to_screen(PLAY_U * 0.94, 0.0)[1], 1)]},
        ],
        "lanes": lane_data(),
        "prop_kinds": {k: {**v, "world_radius": PROP_KINDS[k][1], "cover": PROP_KINDS[k][3]}
                       for k, v in prop_meta.items()},
        "props": props,
        "units": units,
    }
    with open(os.path.join(args.out, "battle_map.json"), "w") as fh:
        json.dump(data, fh, indent=2)

    kb = os.path.getsize(img_path) / 1024
    print(f"\n{img_path}  {MAP_W}x{MAP_H}  {kb:.0f} KB")
    print(f"{len(props)} props, {len(data['lanes'])} lanes, {len(units)} units placed")


if __name__ == "__main__":
    sys.exit(main())
