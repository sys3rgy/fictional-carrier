# Lancer-class carrier sprites

Isometric sprite sheets for a modular sci-fi carrier: **8 facings x 6 animations x 3
module loadouts**, 96x96 per cell at 1x, plus 2x.

The sprites are not drawn frame by frame. The carrier is described once as a small 3D
model made of primitives — boxes, ellipsoids, cylinders, flat quads — grouped into
swappable **modules**. Each animation is a function that poses that model for a frame;
the posed model is then rotated in 45 degree steps to produce all 8 facings from the
single pose, projected through a fixed isometric camera, depth resolved, and drawn
straight to pixels with hard edges, a limited palette and ball-style shading. A 1px
silhouette outline pass and an engine bloom pass finish each frame, and the frames are
tiled into sheets.

Because one model feeds every frame, all 8 facings and all 6 animations agree on
proportion, lighting and palette by construction. Change the hull and everything
re-renders — the whole set takes about 8 seconds.

## Run it

```sh
pip install pillow
python3 generate_carrier.py                   # all loadouts -> sprites/
python3 generate_carrier.py --loadout mk1     # just one
python3 generate_carrier.py --palette steel   # the cool scheme instead of the livery
python3 generate_carrier.py --contact         # also write contact sheets for eyeballing
python3 generate_carrier.py --shadow          # bake a drop shadow (see note below)
```

Preview them:

```sh
python3 -m http.server            # then open /preview/
python3 tools/build_artifact.py   # standalone page -> dist/carrier-sprites.html
python3 tools/test_preview.py     # Playwright check of the viewer
```

## What you get

`sprites/carrier_<loadout>_<animation>.png` — one sheet per animation, plus `@2x`.
**Rows are the 8 facings, columns are the animation frames.**

| Animation   | Frames | FPS | Playback |
|-------------|--------|-----|----------|
| `idle`      | 8      | 10  | loop     |
| `cruise`    | 8      | 14  | loop     |
| `bay_open`  | 8      | 12  | once     |
| `launch`    | 12     | 14  | once     |
| `damage`    | 8      | 12  | loop     |
| `powerdown` | 8      | 10  | once     |

`sprites/carrier_<loadout>.json` describes one ship, `sprites/carrier_atlas.json`
describes all of them — frame size, facing table, fps and loop flags per animation.

Every sheet for a given loadout shares one scale, computed once from the hull, so the
ship sits in the same pixels across every animation and facing. You can cross-fade
between animations without the sprite jumping.

### Facings

Row 0 points screen-south (towards the bottom of the screen) and each row steps 45
degrees anticlockwise from there:

| Row | 0 | 1  | 2 | 3  | 4 | 5  | 6 | 7  |
|-----|---|----|---|----|---|----|---|----|
|     | S | SE | E | NE | N | NW | W | SW |

The camera is orthographic dimetric — yaw 45 degrees, pitch 30 degrees — which is the
classic 2:1 pixel ratio, so these line up with standard isometric tiles.

### Loadouts

| Key   | Ship                          | Squadrons | Modules |
|-------|-------------------------------|-----------|---------|
| `mk1` | Lancer-class escort carrier   | 2         | chassis, bridge_std, hangar_small, engines_basic |
| `mk2` | Lancer-class, expanded bay refit | 4      | + hangar_large, engines_uprated, sensor_array |
| `mk3` | Lancer-class battlecarrier    | 4         | + weapons_pods, armor_belt |

`mk1` is the starting ship: a small hangar bay with two squadron launch tubes.

### Paint schemes

Geometry names its materials semantically — `hull`, `plate`, `deck`, `trench`,
`runlight` — so a paint scheme is a swappable table in the same way a hangar is a
swappable module. Two ship:

| Key       | Look |
|-----------|------|
| `crimson` | Default. Bone hull with crimson plating, amber-lit recesses and trenches, green deck marker lights, fleet chevron on the deck. |
| `steel`   | Cool steel-blue hull with cyan bay lighting. |

`plate` is the bold painted armour block, `trench` the recessed strip lighting,
`runlight` the marker lights along the deck rail. Painted regions are real geometry —
thin panels laid on the hull surface — not a texture, which is why they hold their
shape correctly across all 8 facings.

A scheme also carries its own outline colour, bloom colours and a `bloom` set naming
which emitters get a halo. That set is per-scheme on purpose: a warm halo around a
cyan light reads as a bug, so `steel` blooms only the engines and sparks while
`crimson` blooms its amber bays and trenches too.

Adding one is a dict entry in `PALETTES` with the same material keys; nothing in the
geometry changes.

## Using them

```js
const S = 96, FACINGS = ["S","SE","E","NE","N","NW","W","SW"];

// frame -> source rect in the sheet
function src(frame, facingRow) {
  return { x: frame * S, y: facingRow * S, w: S, h: S };
}

// draw centred on the unit's screen position; keep smoothing off
ctx.imageSmoothingEnabled = false;
const r = src(frame, facingRow);
ctx.drawImage(sheet, r.x, r.y, r.w, r.h, px - S / 2, py - S / 2, S, S);
```

Pick the facing row from the unit's heading: `row = Math.round(bearingFromSouth / 45) % 8`,
where `bearingFromSouth` increases anticlockwise.

## Adding or changing a module

A module is a function taking `(state, cfg)` and returning a list of primitives in ship
space — `+x` is the bow, `+y` is port, `+z` is up. Write it, register it, use it:

```python
def m_shield_ring(st, cfg):
    hx, hy, dx = deck_of(cfg)          # mount relative to whichever deck is fitted
    p = []
    for sy in (1.0, -1.0):
        p.append(cyl((dx, sy * (hy + 0.18), 0.10),
                     (dx - 0.6, sy * (hy + 0.18), 0.10), 0.09, "armor"))
    return p

MODULES["shield_ring"] = m_shield_ring
LOADOUTS["mk4"] = {
    "name": "Lancer-class, shielded",
    "modules": ["chassis", "bridge_std", "hangar_large", "engines_uprated", "shield_ring"],
    "squadrons": 4,
    "bays": BAYS_4,
}
```

Re-run the generator and every facing and animation of `mk4` exists. Modules read
`deck_of(cfg)` to find the fitted flight deck, so armour and turrets follow whichever
hangar is installed rather than hard-coding positions.

`st` carries the per-frame pose: `throttle`, `door`, `launch`, `damage`, `power`,
`nav`, `strobe`, `bob`, plus `t` and `frame`. Reading it is what makes a module animate.

## Notes on the render

- **Palette.** Every surface resolves to a step in one of the ramps of the active
  scheme, dark to light. Emissive materials — engines, windows, bay lights, trenches,
  marker lights, sparks — bypass the lighting entirely and pick their step from an
  intensity the animation sets, which is how the same geometry reads as powered,
  idling or dead. Those intensities are deliberately capped short of the top step so
  a fully open bay reads as deep amber light rather than a blown-out hole in the hull.
- **Shading.** Half-lambert against one key light and one fill, quantised into the
  ramp. Half-lambert rather than plain lambert because at 96px it keeps curved parts
  reading as balls instead of collapsing to two tones.
- **Bloom.** Halos use two fixed colours from the active scheme, and only the emitters
  named in its `bloom` set get one. Everything else stays a crisp single pixel, which
  keeps the palette tight.
- **Drop shadow.** Off by default: this is a ship in space, with no ground to cast
  onto. `--shadow` bakes one if you end up wanting these over a hangar deck or a
  planet surface.
