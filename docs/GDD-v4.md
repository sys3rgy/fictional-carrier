# Game Design Document v4

*The single current source of truth, superseding v3. v3 remains valid in most of its
detail; this revision changes the campaign frame, corrects the role ecology, closes four
open questions, reorders the build, and adds the asset pipeline section that §4.4 of v3
implicitly required but did not specify.*

*Working title: v3 was titled REARGUARD. The name is unsettled — see §11.*

---

## 1. The pitch

Your fleet is gone. The enemy holds the sector. You are the last carrier still flying,
and **you cannot win the war.** You can only be a nuisance — hit their supply, break
their stations, blind their sensors — and make the conquest cost more than it should.

You are a **commander, not a pilot.** You issue intent; your craft carry it out. You
pause whenever a moment deserves a decision.

Being outnumbered is not a difficulty setting. It is the permanent, correct state of
the game.

**Format:** real-time with pause, roguelite, one run through four subsectors.
**Reference points:** Into the Breach (you will lose, and losing is the subject, not a
failure of the design), Phantom Brigade (campaign board — but simpler), Dome Keeper (two
loops in tension, upgrades that die with the run), Dragon Age: Origins (active pause,
commander stance), Thronefall (one legible fantasy, the thing you protect always on
screen).

### 1.1 What kind of roguelite this is

**You are expected to die, and to die often.** The job is impossible; that is the honest
reading of the premise, not a difficulty tuning decision. A run is a raid you do not come
back from, and the question is how much you broke on the way down.

Winning — clearing all four subsectors — is an achievement, not the expected outcome.
This is the Into the Breach contract: the game is not apologising for killing you, and it
does not soften the loss to keep you comfortable.

**What this buys:** a hard fail state needs no justification, no failing forward, and no
consolation systems. **What it costs:** run length becomes the load-bearing number. Into
the Breach runs are short enough that a loss at the last node costs you an evening's
patience, not an evening. See §11.

---

## 2. The three layers

| Layer | What the player does | Status |
|---|---|---|
| **The battle** | Command a wing in real time with pause | **Built and fun** |
| **The carrier** | Spend salvage on five hull slots between missions | Designed |
| **The campaign** | Clear a subsector's missions, strip its anchor, jump on | Designed |

The layers rhyme deliberately. The in-battle greed dial (stay one more pass, or go home
and rearm) is the same shape as the campaign greed dial (take the tempting salvage, or
stop the operation that will hurt you). A player learns one instinct and it works at both
scales.

---

## 3. The battle

*Unchanged from v3 except §3.3, which was wrong, and §3.6, which is now settled.*

### 3.1 Stance

You never fly a stick, never aim a gun. Orders are intent a craft fulfils on its own
until it completes, becomes impossible, or you override it. Your attention is freed for
the decisive moments: the redirect, the ability, the desperation launch.

**Time flows continuously. Pause is a right you exercise at will**, not a phase the game
imposes. Auto-pause stops the game on the beats that deserve a decision (a wave arriving,
an ambush springing, a pilot ejecting, a craft running dry with no standing order), with
a toggle for players who would rather run uninterrupted.

### 3.2 Order verbs

Deliberately few. Every addition must argue past this list.

- **Engage [target]** — chase, close, fight.
- **Move to [point]** — reposition without seeking a fight.
- **Defend [carrier / point / craft]** — hold station and intercept threats to it.
- **Return and dock** — break off, fly home, rearm. Disengaging cleanly is its own skill.
- **Abilities**, fired manually. The EMP stun is the archetype: a spent decision, not a
  passive proc.

### 3.3 The counter-chain — *corrected*

v3 called this a triangle. It is not one, and the mislabel matters: a doc that
misdescribes its own ecology fails Law 2 exactly as a lying readout does.

**The actual shape is a chain plus an objective:**

```
Interceptor  ──beats──▶  Fighter  ──beats──▶  Bomber  ──only thing that threatens──▶  Capital
                                                 ▲                                        │
                                                 └────────── flak punishes ───────────────┘
```

- **Fighter** — generalist, natural predator of bombers, gets pinned by interceptors.
- **Interceptor** — specialist fighter-killer; turns a grinding duel into an execution.
- **Bomber** — the only thing that meaningfully threatens a capital. Fragile in a duel.
- **Capital** — not a counter to anything except bombers, and only via flak.

For a true cycle, bombers would have to beat interceptors. Nothing in the fiction supports
that, and forcing it would be a lie told for symmetry's sake.

**So the interceptor is genuinely dominant in craft-versus-craft, and that is fine.** What
stops interceptor spam is not a counter — it is that a wing of pure interceptors wins every
duel and accomplishes nothing, because it cannot threaten a capital. **Balance comes from
objectives, not from a cycle.**

> **The failure mode this predicts:** any mission winnable without killing a capital is a
> mission where interceptors dominate. Mission design must therefore keep a capital, a
> station, or an equivalent hard target in the win condition — or knowingly accept that
> this one is an interceptor mission.

**The chain must stay readable.** Targeting rings are coloured by matchup, so the ecology
teaches itself without a tutorial.

**The UI must not lie.** A real bug once let fighters strafe a capital down in eight
seconds while the readout said POOR. Craft guns now use a flat anti-capital coefficient
with no matchup bonus. If the readout and the formula ever disagree again, the formula is
wrong.

### 3.4 Dogfights

Two opposing fighters that meet enter a **locked, mutual engagement**, pinned to a fixed
anchor recorded at lock time. Presentationally the animation sells a swirling duel through
all axes; underneath it resolves as a contained one-on-one. **The animation implies volume;
the logic stays contained.**

Resolution is **attrition over time, deterministic, no positional advantage.** Angles and
energy are a pilot's concern and the player is a commander.

**Even matchups grind; mismatches are fast.** This makes a dogfight primarily a way to take
a piece off the board *temporarily*. Pinning is the default outcome; killing is what
happens when you solve the matchup. It is what the enemy does to you — tie up your escorts
so the bombers get a clear run — and it is what the interceptor is *for*.

Sending help stacks damage. The EMP swings duels. Disengaging costs you a few free seconds
on the way out. "Locked" means committed to each other, **not** untargetable by anyone else
— without that, the whole intervene-and-rescue layer dies.

**Orders are binding, and *where* a duel is fought is decided by who came to whom.** Two
rules the prototype forced, both load-bearing:

- **A craft only locks what it was ordered onto.** Opportunistic locking — grabbing
  whatever wandered into range — made assignment advisory, and skilled play measured no
  better than charging. Being *jumped* is still legal: the enemy that caught you named
  you. Nobody ever tangles with something merely because they flew past it.
- **The anchor favours whoever held station.** A duel is anchored 35% of the way from the
  defender toward the craft that came to them, not at the midpoint. This is the only
  reason the flak umbrella reliably covers a defended fight (§3.7), and it makes "reach
  out and take him" a real cost rather than a free action.

**An even duel wears a craft to a quarter of its condition and no further** — a hard floor,
not a slow rate. Below that the loser **breaks off and runs for home**: combat-ineffective,
out of the fight, pilot alive. Killing takes an advantage, an assist, or the carrier's
flak, none of which are floored.

This was a per-sortie magazine until the combat prototype removed ammunition, at which
point it became clear the magazine had been the *only* thing making the sentence above
true, and that no dps value can imitate it — any rate slow enough never to resolve inside
a mission is too slow to feel like a fight (§10.2). Breaking off is the better rule
anyway: it gives a fight a losing side without a corpse, and it makes `rout` an objective
you can meet without killing everyone.

> **Because most duels end in a pin rather than a kill, most of what is on screen is the
> default outcome.** The pin must not read as dead time. With ammunition unlimited there
> is no magazine to watch drain, so what the UI has to sell during a lock is the *approach
> to the floor* — the moment a craft is about to break — and the arrival of help.

### 3.5 Logistics: the second loop — *not in the prototype*

**The combat prototype has unlimited ammunition and no rearm cycle**, on purpose: the
question it exists to answer is whether the fight is fun before logistics props it up.
What follows is the design as it stands for the full game; §10.2 records what removing it
cost and what had to be built to replace it.

- **Ordnance only refills at the carrier.** Winning an even duel costs roughly 90% of a
  magazine; killing a fighter with the right tool costs about 25%. The chain therefore
  matters twice — once for survival, once for supply.
- **The deck cycles one craft at a time.** Sending one home means another waits.
- **Running dry is loud**: the craft goes visibly dark with a pulsing DRY flag, and a
  per-craft *return when dry* standing order can be switched off to keep it out for
  pinning, blocking or scooping a pod.

> **Before reinstating this, read §10.2.** Ammunition was doing three jobs beyond supply,
> and two of them now have their own mechanics — the even-duel floor and the capital's
> per-salvo flak. Putting the magazine back on top of those without removing them would
> bound the bomber's exposure twice and make even duels unable to resolve at all.

Combat pulls the wing forward; logistics pulls it home. **Risk is a product of geometry
and clocks. There is no separate risk system.**

### 3.6 Pods, recovery, and pilots — *settled*

Downed pilots eject and drift on a timer. Any friendly craft that reaches one recovers
them, and a recovered pilot **takes a spare airframe and flies again**. Going back for
people is force regeneration, not sentiment.

**Pilots persist for the length of a run.** This closes v3's open question, and with it the
end-of-battle name roster returns.

A pilot is a name, a count of missions survived, and a count of times ejected. That is the
whole feature. It costs almost nothing and it changes what a rescue *is*: not an abstract
airframe returning to the pool, but the one who has already punched out twice. **The
rescue loop already existed and did no emotional work; this is the cheapest possible way
to make it pay.**

Pilots die with the run, like everything else (§4.2).

### 3.7 The carrier in battle

Slow, deliberate, repositionable. She carries a **flak umbrella** — a visible zone where
capitals engage hostile craft — which is the mechanism that lets an outnumbered wing
achieve local superiority. Forward means short rearm trips and cover over the fight; it
also puts the thing you cannot lose inside their reach.

The umbrella is the clearest expression of Law 7 in the game: swapping a turret changes a
radius you can see on screen.

**The umbrella is a position, not a button.** Getting this wrong was the single largest
correction the combat prototype produced, and it took three passes:

- **Hostiles will not fly into it for free.** They press to the lip of the envelope and
  wait. So sitting inside is *safe* and wins nothing — the objective is never in there
  with you, and turtling produces a standoff rather than a victory.
- **They do not wait forever.** After about twelve seconds on the boundary a hostile
  decides your carrier is worth the burn and comes in anyway. The umbrella buys a window
  to consolidate, recover a pod and pick your fight. It is not somewhere you get to live.
- **What it actually gives you is the anchor.** Because a defended duel is fought on the
  defender's ground (§3.4), reaching out from inside to a hostile loitering on the edge
  drags the fight in under your own guns. *That* is the play the outnumbered scenario is
  built around, and it is the only line that wins it.

The failure mode this replaced is worth recording: with hostiles set to charge, duels
anchored at a radius of 11.6 against an umbrella of 11.5 — every fight landed a hair
outside the guns and **the flak never fired a shot in any scenario**, while still being
drawn on screen as though it were doing something. Law 2.

### 3.8 Fog and surprise

Hidden information is essential, not forbidden. The defining scene is an enemy fighter
emerging from a salvage hulk where it was hiding, and a desperation launch to buy thirty
seconds. **That moment is impossible in an open-hand game**, which is why the earlier WEGO
design was abandoned.

*Most* threats stay visible and legible; hidden ones are rare and telegraphed where
possible. **Surprise is a spice, not the meal.** The situation must always be answerable by
a skilled commander, even when it is hard.

### 3.9 Surrender

When an enemy craft's means of escape is gone — its capital destroyed — it may surrender
rather than fight to the death. This gives losses on both sides weight, makes killing a
capital feel decisive rather than grindy, and mirrors the player's own manifest logic
pointed at the enemy.

**On Law 5 (symmetric rules):** the player's wing has no equivalent, because losing your
carrier ends the run and the case never arises. Stated explicitly so the asymmetry is a
decision rather than an oversight.

---

## 4. The carrier

### 4.1 The correction this section records

The stated fantasy has always been *"what if I'm managing a carrier in space."* What got
built is a game about commanding fighters, with the carrier as scenery. The clue was in the
build: making her movable produced the single best decision in the game. **She is the
protagonist.**

### 4.2 Upgrades live inside the run

Dome Keeper's model, not Thronefall's. You spend on the ship constantly and by the end you
have built a *specific* carrier — and all of it evaporates when the run ends.

- **Within a run:** the configuration is built, replaced, and lost.
- **Between runs:** only the *catalogue* grows. Later runs have more shelf to choose from,
  never a stronger starting position.

### 4.3 The five-slot frame

Five fixed slots. **You never add, you always replace.**

| Slot | Governs | Sim values it drives |
|---|---|---|
| **Bridge** | How much you see and how well informed you are | detection range, fog; later, intel and approach options |
| **Hangar** | The deck cycle and the wing | deck operations, rearm duration, deploy cap, spare airframes, stores |
| **Engines** | How freely you reposition | carrier speed and turn rate |
| **Turret mount A** | The flak umbrella | point-defence count, umbrella radius, anti-craft damage |
| **Turret mount B** | *(as above, independently chosen)* | |

**Nothing here is a new system.** Every slot is a face on a number the simulation already
runs. This is the cheapest depth available.

**The two mounts are independent slots, not one part.** That is what makes "two of one or
one of each" a tactical identity rather than a label:

- **Anti-missile battery** — stops bomber salvos, the only real threat to the hull.
- **Anti-fighter flak** — kills craft inside the umbrella.
- **Laser turret** — the starting part, weak at both.

**Armour is not a slot, and should not become one.** It would be a sixth slot and a new
system (damage reduction) in a frame whose whole discipline is that every slot is a face on
an existing number. The prototype art for an armour belt survives as a chassis variant, not
as a part.

### 4.4 The ship as a record of your decisions

Every part is visible on the hull. Two players finish a run with visibly different vessels
— one stubby and bristling with flak, one long and clean with an oversized hangar deck —
and neither needs a stat screen to know what they built.

**The silhouette must stay honest.** A ship covered in guns should *look* like it cannot
launch much. **Discipline: every part is visible somewhere on the hull, and every part
changes exactly one thing the player can feel.** If you cannot see it and cannot feel it,
it is not a part.

See §8 — this promise has an asset cost that has to be paid a specific way or not at all.

### 4.5 The opening

Three carriers to choose from, each with its own starting slate and fighter complement.
The starting loadout says out loud that this is **not a warship**:

> **Carrier A** — Civilian Bridge · Small Hangar · Grade 3 Engines (slow) · 2× Laser
> Turret. Two general-purpose fighters.

Not a fleet carrier being equipped. A wreck being patched. Everything bolted on afterwards
is scavenged.

The first battle **is** the tutorial, by being a small battle. No tutorial mode.

---

## 5. The campaign

### 5.1 The shape of a run — *revised*

A run is four subsectors, entered in sequence. Inside one, you jump from mission to
mission until you have stripped the **anchor** enough to assault it. Killing the anchor
ends the subsector and lets you jump on. Clear all four and you have won.

**Exactly one Control bar is live at a time** — the current subsector's. It is not a
global doom counter and previous subsectors do not keep ticking behind you. Leaving a
subsector retires its bar for good.

> **If the live bar fills, the run ends.** No failing forward. Per §1.1, this is the
> expected outcome of most runs and needs no softening.

**Carries between subsectors:** the carrier, her parts, the pilots, accumulated damage.
**Resets:** the mission board and the Control bar.

### 5.2 The anchor

Each subsector has an **anchor** — a command station or flagship holding the region. It is
both the source of the pressure and the way out.

It is too strong to attack on arrival. The missions available are you **stripping** it:
killing its escort wing, cutting its supply, blinding its sensors. Each strip weakens it;
when it is weak enough the **assault mission** appears. Killing it ends the subsector.

This gives every mission a purpose without justifying each one individually. You are never
running errands; you are dismantling the thing holding the region.

### 5.3 No second clock

There is no global timer and no "survive N turns" win. Relief reaches you when the
sector's spine is broken, so **clearing all four subsectors is the win.** The thing the
player does moment to moment is the thing that wins.

The Control bar is pressure, not a countdown you race — it rises only because their
operations complete (§5.4).

### 5.4 The board is the enemy's schedule

Every card is an **operation in progress** with a countdown — not "this offer expires" but
**how long until it completes and pays them.** A convoy three turns from its destination. A
relay two turns from going live.

When a timer reaches zero the operation **resolves in their favour**: the convoy arrives and
Control jumps; the relay goes live and Control now climbs *faster from here*; the shipyard
launches and the next missions are harder.

A mission expiring feels like the game taking a toy away. **An operation completing feels
like you failed to stop something.** Same mechanics, completely different read.

**The threat hierarchy designs itself.** Some operations cause a one-time hit; others
permanently raise the rate. Players learn that a relay going live is worse than a convoy
getting through, even though the convoy is the bigger immediate number. The difficulty
curve emerges from the player's own neglect.

**Control is a readout, not an arbitrary rise.**

### 5.5 Three kinds of card

- **Threats** — enemy operations with countdowns. Ignore them and something gets worse.
- **Opportunities** — also timed, but ignoring one costs nothing except the thing itself.
  A derelict worth salvaging. A stranded pilot. A contact who will not wait.
- **Objectives** — the anchor strips. **No timer at all.** Permanently available, and the
  only things that get you out of the subsector.

Urgent, tempting, important. The classic failure is the urgent crowding out the important:
you spend the whole subsector fighting fires, never touch the anchor, and die having been
busy the entire time. That is a *good* feeling to design for.

The board's job is not "which fire do you fight." It is **to make you greedy**, with
threats as the punishment for greed.

**Supporting rules:** most threats should be small. The board stays small — five or six
cards. Generation guarantees at most one real emergency at a time, so the loud card is
actually loud.

> **Law: the urgent must never fully crowd out the important.** The player must always be
> able to make anchor progress if willing to eat some Control.

### 5.6 The turn economy

Missions cost **turns**, and everything on the board advances while you are busy. A convoy
ambush might be 1; a station assault 3. A three-turn mission means returning to find two
operations completed — a cost that is **felt rather than stated**.

**A failed mission still costs the turns.** Failure stings through the clock; no separate
punishment system is needed.

The question is not *"which of these do I want?"* but **"what can I afford to let happen
while I do this?"**

**Skipping is legal.** Burn a turn and let the board move.

### 5.7 Salvage and shop must not compete

- **Wreckage** gives *parts*: unpredictable, free, determined by who you just fought.
- **The shop** gives *repairs and the specific thing you wanted*, at a cost.

Salvage has a sink, and pushing for the enemy capital in a battle pays for your next
hangar bay. The in-battle greed dial connects straight to the upgrade screen.

---

## 6. The approach

Chosen **after** picking a mission, **before** the battle loads.

**Why it works now when it failed before:** you have already paid for it. Bridge and
engines are slots bought *instead of* turrets or hangar capacity. The approach menu is the
payoff for how you built.

**Every option must pass the sentence-with-a-but test.**

- **Warp far, creep in dark.** Arrive undetected, pick your moment. *But* long flights,
  brutal rearm cycles, and pods drifting out of reach.
- **Warp close, hit hard.** Umbrella over the battle immediately, short cycles. *But* they
  see you coming and the thing you cannot lose is in reach from the first second.

Terrain participates: nebulae, asteroid fields and debris belts are what make the slow
approach viable at all. **The battle map generator already produces this terrain with
lanes, cover values and traversal guarantees** — see §8.3.

**Stealth ships in two stages.** v1: stealth is a *head start*, not a state. v2: a real
detection state with sensor cones. Not before v1 proves itself.

**Launch timing.** Craft can start in the tubes: **hold and swarm**, **scout ahead**, or
**bait** — one out to be worth shooting at while the rest come in elsewhere.

---

## 7. Mission types

Grouped by **tactical shape**, because shape costs money to build and fiction is free to
reskin.

**Supported by the engine as it stands**
- Survive until jump *(built)* · Break the patrol *(built)* · Convoy raid · Escort
  · Graveyard salvage

**Small additions**
- Station assault (the natural shape for anchor assaults) · Search and rescue · Disable,
  don't destroy · Decapitation · Shipyard strike · Beacon run

**Bigger reach**
- Infiltration · Minefield · Shadow

Per §3.3, every mission shape needs a stated answer to *"can this be won without killing a
hard target?"* — if yes, it is an interceptor mission by construction, and that should be
a choice rather than a discovery.

---

## 8. The asset pipeline — *new*

§4.4 promises that every part is visible on the hull and that two players finish with
visibly different ships. That promise has a cost, and it has to be paid in a specific way.

### 8.1 Why whole-ship sprites cannot work

Five slots with roughly four catalogue entries each is on the order of a **thousand
configurations**. A full sprite set for one carrier is 8 facings × 6 animations ≈ 416
frames. Pre-rendering every configuration is around **400,000 frames** — hours of compute
and gigabytes on disk, regenerated every time a single part changes.

### 8.2 Modules are rendered as layers

The renderer already describes each craft as a set of independent module geometries, so
each module is baked to its **own sheet**, on a shared camera, scale and registration, and
the game composites five layers at runtime.

**That is ~20 module sheets instead of ~1,000 ship sheets** — a couple of minutes to build,
and it makes §4.4 free instead of combinatorially impossible.

Two things this requires:

- **One canonical scale and origin per craft**, fitted to the largest possible
  configuration, rather than a per-loadout fit. Otherwise layers do not line up.
- **A per-facing draw order**, because layering loses the per-pixel depth sorting a
  whole-ship render gets for free. Each module's centroid depth is computed at bake time
  and the resulting back-to-front order is published per facing.

### 8.3 The battle map is already data

The map generator produces the terrain §6 depends on: a diamond arena on the isometric
grid, spawns at opposing corners, lanes running along the isometric axes, and asteroid and
wreckage fields that wall them in.

It emits the map as **data, not a picture** — arena bounds, spawns, lane waypoints, and
every prop with world position, collision radius and **cover value**, which is what the
battle layer needs for line-of-sight and cover queries.

**Lanes are verified, not assumed.** Each corridor is flood-filled from spawn to spawn
against an occupancy grid at craft radius; where the fill stalls, the in-lane cover at the
pinch is removed in mirrored pairs until the route opens. The generator fails loudly if a
lane is still blocked. This was not paranoia — the first build had a lane sealed by five
individually legal props.

---

## 9. Design laws

1. **Commander, not pilot.** No direct flight or manual aiming.
2. **The UI must not lie.** If the readout says POOR, the formula must agree. This applies
   to the design documents too (§3.3).
3. **Only bombers threaten capitals.**
4. **Two loops in tension.** Risk comes from geometry and clocks, never a separate system.
5. **Symmetric rules.** Both sides obey the same logistics, matchups and ordnance limits —
   and where they cannot, the asymmetry is stated (§3.9).
6. **Survival is the win — *at battle scale*.** Killing everything is never a battle
   objective. At campaign scale the win is clearing all four subsectors. The two are not in
   conflict; the scope qualifier is there because v3's phrasing read global.
7. **Every part is visible and felt.** Otherwise it is not a part.
8. **The sentence-with-a-but test.** Any choice without a real second half is a buff with
   extra clicks.
9. **The urgent must not crowd out the important.**
10. **Surprise is a spice, not the meal.**
11. **Balance comes from objectives, not from a cycle.** The counter-chain has a dominant
    craft; what disciplines it is that the dominant craft cannot win the mission.

---

## 10. Status and what is next

**Built and playable:** the battle. Real-time with pause, readable matchups, ordnance and
deck cycling, pods and rescue with spare airframes, a movable carrier with a flak umbrella,
waves against a survival clock, auto-pause.

**Built as assets:** a procedural sprite pipeline for the carrier and its fighter — modules,
loadouts, 8 facings, 6 animations each — and a procedural battle map with verified lanes,
cover data and a sun that is a real light source.

**Built as a campaign shell:** the subsector board with its three card types, the Control
bar and turn economy, the anchor strip-and-assault loop, the five-slot refit driven by the
layered sprites, purchasable airframes, the approach choice, and pilots persisting for a
run. The battle sits behind `resolveBattle()`, the seam the real engine plugs into.

**Built as a combat prototype:** three scenarios of increasing difficulty against binding
orders, the counter-chain, pods, the EMP and a working flak umbrella — deliberately
**without the rearm cycle**, to find out whether the fight is fun before the logistics
loop is there to prop it up.

**Designed, not built:** every mission type beyond the shapes the shell generates, and the
real-time battle's integration with the campaign layer.

### 10.0 What building the shell taught

Three things only showed up once it was playable, and all three are design findings
rather than bugs:

- **The logistics loop is not optional.** Without a rearm cycle, an even duel costing 90%
  of a magazine means every craft gets exactly one engagement and a mission is a single
  exchange. §3.5 reads like flavour; it is load-bearing.
- **Matchups have to be assigned, not random.** With arbitrary pairing, bringing the right
  tool did not guarantee it met the right target, which quietly nullified the counter-chain.
  The player is a commander: assignment is the thing they are doing.
- **Law 11 has an economic half.** Only bombers strip an anchor, so if airframes cannot be
  bought, a wing can never make objective progress. "Balance comes from objectives" needs
  the objective-capable craft to be purchasable, or the law just blocks the player.

**Honest risk, restated:** there is one excellent battle and no game around it. This design
has pivoted twice and each pivot was correct, but the thing that finishes games is
finishing something.

### 10.1 Build order — *reordered*

v3 ordered this: missions → upgrade screen → board. **That is now flipped**, because the
named risk is "no game around the battle," and more missions is more battle.

1. **Close the loop, ugly.** The thinnest possible subsector board and upgrade screen —
   placeholder art, three cards, no polish — so a run plays end to end with the two mission
   types that already exist. *A closed loop with two mission types beats an open loop with
   five.*
2. **Layered module rendering and the two turret slots**, because the upgrade screen cannot
   show a ship it cannot draw (§8.2).
3. **Convoy raid and escort missions.** They invert the engine — "kill something running
   away" and "protect something that is not you" — and now they drop into a game that
   exists.

**The argument for v3's order, recorded because it is real:** missions 1 and 3 test whether
the battle layer *generalises*, and finding out it does not is better early than late. That
de-risks a different thing than the one §10 names as the risk. If the fear is never
finishing, close the loop first.

### 10.2 What building combat taught — *new*

The prototype exists to answer one question — *is the fight fun on its own?* — so it was
built with §3.5 deliberately removed, first as "no rearm cycle" and then, on a second
pass, as **unlimited ammunition**: nothing is spent but the craft themselves. Five
scripted commanders of increasing sophistication play all three scenarios to termination,
and the measurement that matters is whether playing better wins more:

| | charge | matchup | screen | umbrella | anvil |
|---|---|---|---|---|---|
| **patrol** | win | win | win | win | win |
| **convoy** | loss | loss | win | loss | win |
| **outnumbered** | loss | loss | loss | loss | **win** |

Each scenario teaches exactly one thing, and the policy that learns it is the first one to
beat it. Findings, in the order they were forced:

- **The gradient does not exist for free.** Before orders were binding (§3.4), all five
  columns were identical — every policy performed like `charge`, because assignment did
  not survive contact. *A counter-chain with unassignable matchups is not a mechanic.*
- **The umbrella is a position, not a button** (§3.7). Getting this wrong cost three
  passes and produced the single most embarrassing bug in the project: an envelope drawn
  on screen, named in a mission brief as the way to win, that had never fired a shot.
- **Losing the objective should not end the mission.** The bomber dies first in every
  scenario, because pinning it is correct enemy doctrine. Ending the instant it died threw
  away the pods, so a dead strike now converts the mission into an extraction: you cannot
  win, but pilots persist (§3.6) and there is still something to play for.

#### What ammunition was secretly doing

Removing the magazine did not simplify the model — it exposed three jobs the magazine had
been doing that nothing else was:

- **It was the reason even duels did not kill.** A duel ran dry at ~16s and the kill did
  not land until ~18s, so "pinning is the default outcome" was enforced by *supply*. With
  ammunition unlimited, every even engagement became a 1-for-1 trade, and no dps value
  fixes it: any rate slow enough never to resolve inside a 90s mission is too slow to feel
  like a fight. The replacement is a stated rule rather than a tuned approximation — **an
  even duel wears a craft to a quarter of its condition and no further**, and finishing it
  needs an advantage, an assist or the carrier's flak, none of which are floored.
- **It was the bomber's clock over the target.** A bomber had a fixed number of salvoes,
  so its exposure was bounded whether or not anything was shooting at it. Without that, a
  lone bomber loiters indefinitely and *the convoy mission falls to a naive charge*. The
  fix was already in the design and simply unimplemented: the **flak-punishes-bombers**
  return arrow of §3.3. Measured, this one number carries the whole screening lesson — at
  zero, charge wins convoy; above about 15 per salvo, even a well-screened bomber cannot
  finish. It is charged per salvo rather than per second, because a per-second cost
  punishes exactly the thing screening produces: a bomber that breaks off and comes back.
- **It was hiding a tie-break bug.** Two identical fighters reach zero on the same tick,
  and whichever the update loop reached first won — always the friendly, because friendly
  units are added to the array first. Ammunition ran out before ties ever happened, so
  this never surfaced. Duel damage is now gathered and applied in separate passes so both
  halves resolve together.

**A craft ground to the floor breaks off and runs.** This fell out of the floor rule and is
better than what it replaced: it makes `rout` an objective you can actually meet without
killing everything, it gives a fight a losing side without a corpse, and the pilot lives —
which is the point of §3.6. Losses across the scripted runs dropped noticeably once fights
could end this way.

**On the question that prompted this — is it fun?** The `outnumbered` line is: fall back,
let them stack on the lip of the flak, reach out and pull one in, fight three-on-one under
your own guns, and eat the losses. It takes about forty-five seconds and costs a craft.
That reads as a fight worth having.

**What this says about §3.5.** Two passes at removing logistics both ended by rebuilding
something logistics had been providing for free — a bound on the bomber's exposure, and a
reason even duels do not resolve. That is not an argument that the rearm cycle is
mandatory; the replacements are cheaper and more legible than the loop they stand in for.
It *is* an argument that the loop was never only about supply, and that anything replacing
it has to be checked against what else it was quietly holding up.

---

## 11. Open questions

- **The project name.** v3 was titled REARGUARD; that name is currently unclaimed.
- **Run length.** §1.1 makes death the expected outcome, which makes run length the
  load-bearing number. v3 said 70–90 minutes. Into the Breach is ~30–45. A loss at the
  fourth subsector of a 90-minute run is a different emotion from a loss at the fourth
  island of a 40-minute one. Candidates: shorten subsectors, or reduce to three.
- ~~Turn budget per subsector and how it escalates across the four.~~ *Answered in the
  shell: escalation lives in a per-subsector `pressure` multiplier on what a completed
  operation costs, not in bigger numbers on the cards. Deaths moved from the first
  subsector to the fourth once it was in.*
- How many strips an anchor needs before the assault unlocks, and whether that is visible.
- Whether some operations are **forced** — an ambush that simply happens.
- Whether the anchor actively hunts you as Control climbs.
- Whether repairs are automatic, cost salvage, or cost a turn.
- Which approach options are gated by which slots, and whether a bare starting ship has any
  approach choice at all.
- Whether launch timing is per-craft or a single opening posture for the wing.
- What a lost run leaves behind beyond the catalogue.

---

## Appendix: changes from v3

| # | Change | Section |
|---|---|---|
| 1 | Death is the expected outcome; Into the Breach contract stated explicitly | §1.1 |
| 2 | Role triangle corrected to a counter-chain with a dominant craft | §3.3 |
| 3 | New law: balance comes from objectives, not from a cycle | §9.11 |
| 4 | Pilots persist for a run; name roster returns | §3.6 |
| 5 | Exactly one Control bar live at a time, stated explicitly | §5.1 |
| 6 | Turret mounts are two independent slots | §4.3 |
| 7 | Armour ruled out as a slot, with the reason | §4.3 |
| 8 | Surrender asymmetry stated against Law 5 | §3.9 |
| 9 | Law 6 given a scope qualifier | §9.6 |
| 10 | Build order flipped to close the loop first | §10.1 |
| 11 | Asset pipeline section added: layered modules, verified map data | §8 |
| 12 | Pin readability: UI foregrounds ordnance during a lock | §3.4 |
| 13 | Run length raised as the load-bearing open question | §11 |
| 14 | Orders are binding: a craft only locks what it was ordered onto | §3.4 |
| 15 | Duel anchors favour whoever held station, which is what puts the flak on them | §3.4 |
| 16 | The umbrella is a position, not a button: hostiles hold the lip, then press | §3.7 |
| 17 | Losing the objective converts the mission to an extraction rather than ending it | §10.2 |
| 18 | Combat measured as a skill gradient across five scripted commanders | §10.2 |
| 19 | Prototype runs on unlimited ammunition; §3.5 marked as not-in-prototype | §3.5, §10.2 |
| 20 | Even duels floor at a quarter condition rather than being bounded by a magazine | §3.4 |
| 21 | A craft ground to the floor breaks off and runs; rout is winnable without kills | §3.4 |
| 22 | Capital flak on bombers implemented as the §3.3 return arrow, charged per salvo | §3.3, §10.2 |
