# Lancer fleet sprites

Isometric sprite sheets for a modular sci-fi carrier and the fighters that launch from
it. Two craft, three module loadouts each, **8 facings x 6 animations** per loadout,
plus 2x.

| Craft     | Cell  | Loadouts                        | Animations |
|-----------|-------|---------------------------------|------------|
| `carrier` | 96x96 | mk1, mk2, mk3                   | idle, cruise, bay_open, launch, damage, powerdown |
| `fighter` | 48x48 | interceptor, bomber, elite      | idle, cruise, boost, fire, damage, destroyed |

The carrier's hull is a long slender spine carrying a stepped stack of armour plate, a
chisel prow and a forward sensor spar, with the hangar built into the forward mass — no
flat top deck. The fighter is a broad arrowhead flying wing with a swept delta, a
dorsal intake and a single centreline nozzle. Bone plating, crimson paint blocks,
amber-lit recesses on both.

The squadrons in the carrier's `launch` animation **are the fighter model**, scaled
down and dropped into the carrier's space, so the air wing cannot drift away from the
sprites it launches as.

The sprites are not drawn frame by frame. Each craft is described once as a small 3D
model made of primitives — boxes, ellipsoids, cylinders, flat quads — grouped into
swappable **modules**. Each animation is a function that poses that model for a frame;
the posed model is then rotated in 45 degree steps to produce all 8 facings from the
single pose, projected through a fixed isometric camera, depth resolved, and drawn
straight to pixels with hard edges, a limited palette and ball-style shading. A 1px
silhouette outline pass and an engine bloom pass finish each frame, and the frames are
tiled into sheets.

Because one model feeds every frame, all 8 facings and all 6 animations agree on
proportion, lighting and palette by construction. Change a hull and everything
re-renders — both craft, every loadout, in about 10 seconds.

## Run it

```sh
pip install pillow
python3 generate_carrier.py                   # every craft and loadout -> sprites/
python3 generate_carrier.py --craft fighter   # just the fighters
python3 generate_carrier.py --loadout mk1     # just one loadout
python3 generate_carrier.py --palette steel   # the cool scheme instead of the livery
python3 generate_carrier.py --contact         # also write contact sheets for eyeballing
python3 generate_carrier.py --shadow          # bake a drop shadow (see note below)
```

Build the battle map (needs the ship sheets first, for the units on it):

```sh
python3 generate_map.py                   # -> maps/battle_map.png + .json
python3 generate_map.py --seed 7          # a different field, same rules
python3 generate_map.py --no-units        # terrain only
```

Preview them:

```sh
python3 -m http.server            # then open /preview/ and /preview/map.html
python3 tools/build_artifact.py   # standalone pages -> dist/
python3 tools/test_preview.py     # Playwright check of both viewers
```

## What you get

`sprites/<craft>_<loadout>_<animation>.png` — one sheet per animation, plus `@2x`.
**Rows are the 8 facings, columns are the animation frames.**

| Carrier     | Frames | FPS | Playback |   | Fighter     | Frames | FPS | Playback |
|-------------|--------|-----|----------|---|-------------|--------|-----|----------|
| `idle`      | 8      | 10  | loop     |   | `idle`      | 8      | 10  | loop     |
| `cruise`    | 8      | 14  | loop     |   | `cruise`    | 8      | 12  | loop     |
| `bay_open`  | 8      | 12  | once     |   | `boost`     | 8      | 16  | loop     |
| `launch`    | 12     | 14  | once     |   | `fire`      | 6      | 16  | loop     |
| `damage`    | 8      | 12  | loop     |   | `damage`    | 8      | 12  | loop     |
| `powerdown` | 8      | 10  | once     |   | `destroyed` | 10     | 14  | once     |

`sprites/<craft>_<loadout>.json` describes one craft, `sprites/sprite_atlas.json`
describes everything — frame size, facing table, fps and loop flags per animation.

Every sheet for a given loadout shares one scale and one long-axis offset, computed
once from the hull, so the ship sits in the same pixels across every animation and
facing. (The offset matters because the hull is bow-heavy — without it, half the cell
is spent on empty space behind the stern.) You can cross-fade
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

`mk1` is the starting ship: a two-squadron bay built into the forward hull.

| Key           | Fighter                       | Role         | Modules |
|---------------|-------------------------------|--------------|---------|
| `interceptor` | Kite-class interceptor        | escort       | airframe, engine_std, cannons_light |
| `bomber`      | Kite-class strike bomber      | anti-capital | + torpedo_pods |
| `elite`       | Kite-class heavy interceptor  | superiority  | engine_boosted, cannons_heavy, wingtip_missiles |

### Paint schemes

Geometry names its materials semantically — `hull`, `plate`, `deck`, `trench`,
`runlight` — so a paint scheme is a swappable table in the same way a hangar is a
swappable module. Two ship:

| Key       | Look |
|-----------|------|
| `crimson` | Default. Bone hull with crimson plating, amber-lit recesses and trenches, green marker lights, fleet chevron struck across the bay roof. |
| `steel`   | Cool steel-blue hull with cyan bay lighting. |

`plate` is the bold painted armour block, `trench` the recessed strip lighting,
`runlight` the marker lights along the hull. Painted regions are real geometry —
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
    bx, bhx, bhy, bhz = hull_of(cfg)   # mount relative to whichever bay mass is fitted
    p = []
    for sy in (1.0, -1.0):
        p.append(cyl((bx, sy * (bhy + 0.18), 0.10),
                     (bx - 0.6, sy * (bhy + 0.18), 0.10), 0.09, "armor"))
    return p

CARRIER_MODULES["shield_ring"] = m_shield_ring
CARRIER_LOADOUTS["mk4"] = {
    "name": "Lancer-class, shielded",
    "modules": ["chassis", "bridge_std", "hangar_large", "engines_uprated", "shield_ring"],
    "squadrons": 4,
    "bays": BAYS_4,
}
```

Re-run the generator and every facing and animation of `mk4` exists. Modules read
`hull_of(cfg)` to find the fitted bay mass, so armour and turrets follow whichever
hangar is installed rather than hard-coding positions.

`st` carries the per-frame pose: `throttle`, `door`, `launch`, `fire`, `blast`,
`damage`, `power`, `nav`, `strobe`, `bob`, plus `t` and `frame`. Reading it is what
makes a module animate.

## Adding a craft

`CRAFT` is the registry — one row per buildable hull, naming its cell size, loadouts,
animation list and build function. Everything downstream (fitting, rendering, sheet
assembly, the atlas) reads from it, so a third hull is a row plus its module
functions. The fighter was added that way.

## The battle map

`generate_map.py` builds **Kestrel Reach**: a symmetric two-carrier arena, 1920x1000,
at the same px-per-unit as the ships so a carrier sprite drops straight onto it.

- **Two spawns** at opposite ends, facing each other.
- **Three lanes** running end to end. Dense asteroid and wreckage bands wall them off;
  the lane corridors are kept clear by rejecting any prop that would narrow one below
  a flyable width, so all three routes are always open.
- **Cover** scattered inside the lanes — every prop carries a world radius and a cover
  value in the JSON.
- **A sun that actually lights the field.** Each prop variant is baked once per light
  direction (8 azimuths) and per falloff tier (3), and placed with the pair matching
  its real bearing and distance to the sun. Dimming shifts the ramp index rather than
  scaling colour, so a distant rock stays inside the palette instead of inventing new
  tones. This is the same trick that bakes a ship once per facing.
- **Backlit starfield**, with a quantised glow field radiating from the sun, painted at
  quarter resolution and upscaled nearest so it stays as blocky as the sprites.
- **180 degree rotational symmetry** about the centre — the left half is generated and
  rotated onto the right, so neither side gets the better ground.

`maps/battle_map.json` is the map, not a description of the picture: bounds, both
spawns, lane waypoints, and every prop with position, collision radius, cover value and
which light bake it uses.

```js
// which props does a shot from A to B pass behind?
map.props.filter(p => distancePointToSegment(p, a, b) < p.radius)
         .reduce((acc, p) => Math.max(acc, p.cover), 0);
```

Open `preview/map.html` to pan and zoom it with the lane, cover, spawn and sun overlays
drawn from that JSON.

## Notes on the render

- **Palette.** Every surface resolves to a step in one of the ramps of the active
  scheme, dark to light. Emissive materials — engines, windows, bay lights, trenches,
  marker lights, sparks — bypass the lighting entirely and pick their step from an
  intensity the animation sets, which is how the same geometry reads as powered,
  idling or dead. Those intensities are deliberately capped short of the top step so
  a fully open bay reads as deep amber light rather than a blown-out hole in the hull.
- **Shading.** Half-lambert against one key light and one fill, remapped onto the
  ramp before quantising. Half-lambert rather than plain lambert because it keeps
  curved parts reading as balls instead of collapsing to two tones — but on its own it
  only spans about 0.50..0.96 against this light rig, so the top ramp step swallows
  every surface within 40 degrees of straight up. `SHADE_LO` / `SHADE_GAIN` stretch
  the occupied band across the whole ramp. Without that a flat-topped hull like the
  fighter's wing quantises to a single tone and reads as a pale blob.
- **Faceting.** The fighter's wing rolls in three broad groups rather than a smooth
  per-strip ramp. Six evenly-stepped rolls produce six slivers that each round to the
  same ramp step; three broad facets produce three distinct tones across the span.
- **Bloom.** Halos use two fixed colours from the active scheme, and only the emitters
  named in its `bloom` set get one. Everything else stays a crisp single pixel, which
  keeps the palette tight.
- **Drop shadow.** Off by default: this is a ship in space, with no ground to cast
  onto. `--shadow` bakes one if you end up wanting these over a hangar deck or a
  planet surface.
