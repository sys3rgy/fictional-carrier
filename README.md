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

### The five-slot frame

The carrier is a fixed chassis plus five slots (`docs/GDD-v4.md` §4.3). You never add,
you always replace.

| Slot | Options |
|------|---------|
| `bridge`   | `bridge_civilian`, `bridge_military` |
| `hangar`   | `hangar_small` (2 squadrons), `hangar_large` (4) |
| `engines`  | `engines_basic`, `engines_uprated` |
| `turret_a` | `turret_a_laser`, `turret_a_flak`, `turret_a_missile` |
| `turret_b` | `turret_b_laser`, `turret_b_flak`, `turret_b_missile` |

The two turret mounts are **independent slots**, which is what makes "two of one or one
of each" a tactical identity rather than a label. Each type is a different silhouette —
slim twin barrels, a squat four-barrel cluster, a boxy cell launcher — so a player can
read what a ship carries without a stat screen.

Armour is a **chassis variant** (`chassis_armoured`), not a sixth slot: it would be a new
system in a frame whose discipline is that every slot is a face on an existing number.

Three sample loadouts ship pre-rendered:

| Key   | Ship                             | Squadrons | Notable |
|-------|----------------------------------|-----------|---------|
| `mk1` | Lancer-class escort carrier      | 2         | the starting wreck: civilian bridge, small hangar, slow engines, 2× laser |
| `mk2` | Lancer-class, expanded bay refit | 4         | one of each turret |
| `mk3` | Lancer-class battlecarrier       | 4         | double flak, armoured chassis |

### Layered modules

`--layers` bakes each module to its own sheet so the game can composite a ship at
runtime instead of shipping a sprite set per configuration:

```sh
python3 generate_carrier.py --layers    # -> sprites/layer_<craft>_<module>_<anim>.png
```

Five slots with a handful of parts each is **144 configurations today** and grows
multiplicatively as the catalogue does. Pre-rendering every one is ~60,000 frames now
and far worse later; baking 84 module layers is ~25 seconds.

`sprites/layers_carrier.json` carries everything a compositor needs:

- `canonical_scale` / `canonical_offset_x` — one fit for **all** configurations, taken
  against the largest ship that can exist, so layers line up.
- `draw_order` — per facing, back to front. A whole-ship render gets occlusion free from
  the per-pixel depth buffer; layers do not, so each module's centroid depth along the
  camera axis is measured at bake time. Modules absent from a configuration are skipped.

```js
const L = layers;                       // layers_carrier.json
for (const mod of L.draw_order[facingRow].filter(m => ship.parts.includes(m))) {
  const s = L.layers.find(x => x.module === mod && x.animation === anim);
  if (s) ctx.drawImage(sheet(s.sheet), frame * S, facingRow * S, S, S, px, py, S, S);
}
```

**Known difference:** a composited ship is ~2–4% of pixels different from the equivalent
monolithic render, entirely at module boundaries, because each layer carries its own 1px
outline. It reads as extra panel definition rather than as an error. If you want them
identical, drop the outline from layer bakes and run one outline pass on the composite.

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

`generate_map.py` builds **Kestrel Reach**, at the same px-per-unit as the ships so a
carrier sprite drops straight onto it.

The arena is a **square in world space**, which this camera projects as a diamond — the
playfield is isometric, not just the things standing on it. A tactical grid is drawn on
the ecliptic plane along the world axes so the diamond reads as ground.

- **Spawns at opposing diamond corners**, facing each other.
- **Three lanes running along the isometric axes.** `centre` cuts the diagonal —
  shortest and most exposed. `north` and `south` each run out along one diamond edge,
  round a corner and come back in along the next, so movement follows the iso grid
  rather than cutting across it.
- **Asteroid and wreckage fields** wall the corridors in; looser cover sits inside
  them. Every prop carries a world radius and cover value in the JSON.
- **A sun that actually lights the field.** Each prop variant is baked once per light
  direction (8 azimuths) and per falloff tier (3), and placed with the pair matching
  its real bearing and distance to the sun. Dimming shifts the ramp index rather than
  scaling colour, so a distant rock stays inside the palette. Same trick that bakes a
  ship once per facing.
- **Backlit starfield**, a quantised glow field radiating from the sun, painted at
  quarter resolution and upscaled nearest so it stays as blocky as the sprites.
- **180 degree rotational symmetry** about the centre, which maps each spawn corner
  onto the other, so neither side gets the better ground.

### Lanes are verified, not assumed

Rejecting bad candidates during scatter is *not* the same claim as a lane being open:
several individually legal props can still close a corridor between them, which is
exactly what happened on the first build. So after placement each corridor is flood
filled on an occupancy grid from spawn to spawn, with a craft radius of 2.0 units. If
the fill stalls, the in-lane cover at the pinch is removed — in mirrored pairs, so the
map stays symmetric — until the route opens. The generator exits non-zero if any lane
is still blocked, and `traversal_check` in the JSON records the result.

Sizing follows from the same check. Three corridors of half-width `H` crossing a
diamond of half-extent `N` occupy roughly `4.3 * H / N` of the playfield; at the
original 52-unit arena with 12-unit lanes that was 84%, leaving almost no ground for
the fields meant to be walling them in. The arena is now 76 units with 10-unit lanes,
which leaves 43%.

`maps/battle_map.json` is the map, not a description of the picture: arena and grid,
both spawns, lane waypoints plus a precomputed screen band per lane, the traversal
result, and every prop with world position, collision radius, cover value and which
light bake it uses.

```js
// how much cover does a shot from A to B pass behind?
map.props.filter(p => distancePointToSegment(p, a, b) < p.radius)
         .reduce((acc, p) => Math.max(acc, p.cover), 0);
```

Open `preview/map.html` to pan and zoom it with the lane, cover, spawn, sun and arena
overlays drawn from that JSON.

## The game

`game/index.html` is the campaign shell from GDD v4 — everything around the battle.
Four subsectors, one Control bar live at a time, an anchor to strip and assault, the
turn economy, the five-slot refit driven by the layered sprites, and pilots that
persist for a run.

```sh
python3 -m http.server           # then open /game/
python3 tools/build_artifact.py  # standalone -> dist/last-carrier-flying.html
python3 tools/test_game.py       # plays ~84 complete runs in a browser
```

**The battle is not built here.** It is behind one function:

```js
resolveBattle(mission, approach) -> { win, log, killed, capDead, downed, rescued, salvage }
```

The stand-in is deliberately transparent — every line of its report is a thing that
happened, in order — and it honours the counter-chain: matchups are assigned rather than
random, an even duel costs ~90% of a magazine, the deck cycles one craft at a time, and
only an unpinned bomber can touch a capital. Swapping the real engine in is replacing
that function.

### What the harness measures

`tools/test_game.py` plays whole runs rather than checking a screen renders, because a
campaign is a state machine whose interesting ends are the failures. It asserts:

- every run terminates, from 24 random-ish policies
- Control never rises without the clock moving, and never falls inside a subsector
- pilots persist and their counters only accumulate
- **law 11**: an all-interceptor wing cannot beat a mission with a capital
- **the balance envelope**: 3–45% wins under a scripted mediocre player, and at least
  40% of runs reaching the back half

That envelope is a regression guard, not a target. Building the shell moved it a lot:

| Change | Effect |
|---|---|
| First playable | 0/24 wins — every run died in subsector 1 |
| Only relays raise the permanent rate | rate stopped running away |
| Airframes purchasable | a bomber became reachable, so anchors could be stripped at all |
| Assigned matchups | bringing the right tool started meeting the right target |
| **Rearm cycle added** | the biggest one — without it a mission was a single exchange |
| Per-subsector pressure curve | deaths moved from the first subsector to the last |

Current: ~10–15% wins under scripted play, most runs ending in the fourth subsector.
A human reading the board should do better than the script.

## Combat

`combat/index.html` is the real fight — the thing `resolveBattle()` stands in for —
built **without the rearm cycle** to answer one question on its own terms: is the
combat fun before logistics is there to prop it up?

```sh
python3 generate_map.py --combat  # -> maps/combat_arena.{png,json}
python3 -m http.server            # then open /combat/  (the arena is fetched, so http)
python3 tools/build_artifact.py   # standalone -> dist/wing-command.html
python3 tools/test_combat.py      # 15 scripted runs across 3 scenarios
```

Ordnance is still a per-sortie budget, but nothing flies home. A dry craft can still
pin, block and pull a pod out — pinning is free, killing is what costs a magazine.

### What the harness measures

A combat model is only interesting if playing it better wins more, so `test_combat.py`
plays every scenario under five commanders of increasing sophistication and asserts the
gradient between them:

| | charge | matchup | screen | umbrella | anvil |
|---|---|---|---|---|---|
| **patrol** — the tutorial | win | win | win | win | win |
| **convoy** — the screening lesson | loss | loss | win | win | win |
| **outnumbered** — the umbrella lesson | loss | loss | loss | loss | **win** |

`charge` flies at the nearest thing. `matchup` reads the counter-chain. `screen` guards
the bomber before it commits. `umbrella` turtles in the flak. `anvil` holds the flak
facing the threat, reaches out so the duel anchors under its own guns, and piles onto
anything already pinned.

Each scenario teaches one thing, and the first policy that learns it is the first to
beat it. The suite also asserts the three rules that would rot silently in a refactor:
orders are binding, defended duels anchor closer to the carrier than reached-out ones,
and a craft with an empty magazine still holds its lock while dealing nothing.

Three findings, in the order they were forced:

| Symptom | Cause | Fix |
|---|---|---|
| Every policy performed identically | craft locked whatever they flew past, so assignment did not survive contact | orders are binding — you only lock what you were ordered onto, or whoever chose you |
| `outnumbered` unwinnable under every policy, including the umbrella its brief prescribes | duels anchored at 11.6 against a flak radius of 11.5 — **the umbrella never fired a shot** | hostiles hold the lip of the envelope; a defended duel anchors on the defender's ground |
| The umbrella then won everything by itself | nothing punished turtling | hostiles wait ~12s on the boundary, then come in anyway. The flak buys a window, not a home |

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
