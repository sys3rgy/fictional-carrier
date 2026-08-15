#!/usr/bin/env python3
"""
test_combat.py — drive the combat prototype under scripted commanders.

A combat model is only interesting if playing it better wins more. So this does not
check that the arena draws: it plays all three scenarios to termination under five
policies of increasing sophistication and asserts the gradient between them.

The policies, weakest first:

  charge    fly at the nearest thing
  matchup   read the counter-chain, put the right craft on the right target
  screen    matchup, plus escorts intercept threats to the bomber before it commits
  umbrella  fall back inside the carrier's flak and stay there
  anvil     hold the flak facing the threat, reach out so the duel anchors under your
            own guns, and pile onto anything already pinned

What must hold:
  - patrol is the tutorial: every policy wins it
  - convoy is the screening lesson: charge and matchup lose it, screening wins it
  - outnumbered is the umbrella lesson: only the anvil wins it, and it costs craft
  - turtling is never enough on its own — the umbrella policy loses outnumbered
  - no policy beats a scenario the next one down cannot (monotonic difficulty)

The three that would silently rot in a refactor, and why they are asserted:
  - orders are binding (GDD v4 3.1). If a craft ever locks something other than what it
    was ordered onto, skill stops paying and the whole table flattens to 'charge'.
  - the duel anchor is biased toward whoever is holding station, which is the only
    reason the flak reliably covers a defended fight.
  - an even duel does not resolve. Ammunition is unlimited, so nothing external stops two
    evenly matched craft from grinding each other down; if EVEN dps ever rises enough to
    kill inside a mission, every engagement becomes a 1-for-1 trade and the counter-chain
    stops being the thing that decides fights.
  - a capital's flak is what bounds a bombing run. It is the only pressure left on the
    bomber now that it cannot run out of ordnance, and it is what the convoy lesson rests
    on: measured, at 0 the mission falls to a naive charge.
"""

import functools
import http.server
import os
import socketserver
import sys
import threading

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROMIUM = "/opt/pw-browsers/chromium"

# The arena is fetched, and file:// blocks fetch, so the page is served over http. That
# also means the run exercises the renderer against real sprites rather than only
# stepping the model with nothing drawn.


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
    handler = functools.partial(Quiet, directory=ROOT)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/combat/index.html"

COMMON = """
  const C = window.__combat, s = C.s;
  const B = {fighter:'bomber', interceptor:'fighter', bomber:'capital'};
  const foes = s.units.filter(o=>o.alive&&o.side==='foe'&&o.type!=='capital');
  const cap  = s.units.find(o=>o.alive&&o.type==='capital');
  const car  = s.units.find(o=>o.alive&&o.type==='carrier');
  const mine = s.units.filter(u=>u.alive&&u.side==='friend'&&u.type!=='carrier');
  const dist = (a,b)=>Math.hypot(a.x-b.x,a.y-b.y);
"""

POLICIES = {
    "charge": COMMON + """
      mine.forEach(u=>{
        if (u.lock) return;
        const f = foes.slice().sort((a,b)=>dist(a,u)-dist(b,u))[0] || cap;
        if (f) u.order = {kind:'engage', target:f.id};
      });
    """,
    "matchup": COMMON + """
      mine.forEach(u=>{
        if (u.lock) return;
        let pick = null;
        if (u.type==='bomber') pick = cap;
        else pick = foes.find(o=>B[u.type]===o.type) || foes.find(o=>B[o.type]!==u.type) || foes[0];
        if (pick) u.order = {kind:'engage', target:pick.id};
      });
    """,
    "screen": COMMON + """
      const bomber = mine.find(u=>u.type==='bomber');
      const loose = foes.filter(o=>!o.lock);
      // whoever has the bomber pinned is the only target that matters
      const captor = bomber && bomber.lock
        ? s.units.find(o=>o.id===bomber.lock && o.alive && o.side==='foe') : null;
      mine.forEach(u=>{
        if (u.type==='bomber') {
          if (u.lock) return;
          const near = foes.filter(o=>bomber && dist(o,bomber)<7 && !o.lock);
          if (near.length) u.order = {kind:'move', x:u.x+9, y:u.y-9};
          else if (cap) u.order = {kind:'engage', target:cap.id};
          return;
        }
        if (captor) { u.order = {kind:'engage', target:captor.id}; return; }
        if (u.lock) return;
        const pick = loose.find(o=>B[u.type]===o.type) || loose.find(o=>B[o.type]!==u.type)
                  || loose[0] || foes[0];
        if (pick) u.order = {kind:'engage', target:pick.id};
      });
    """,
    "umbrella": COMMON + """
      let slot = 0;
      mine.forEach(u=>{
        if (u.lock) return;
        if (u.type==='bomber' && foes.length===0 && cap) { u.order={kind:'engage',target:cap.id}; return; }
        u.order = {kind:'defend', target:car.id, slot: slot++};
      });
    """,
    "anvil": COMMON + """
      const R = 11.5;
      let slot = 0;
      mine.forEach(u=>{
        if (u.lock) return;
        if (u.type==='bomber') {
          if (foes.length===0 && cap) u.order={kind:'engage', target:cap.id};
          else u.order={kind:'defend', target:car.id, slot:4};
          return;
        }
        if (u.order.kind==='engage') {              // keep a reach-out, do not re-decide
          const cur = s.units.find(o=>o.id===u.order.target);
          if (cur && cur.alive && !cur.lock) return;
        }
        const cand = foes.filter(o=>!o.lock).sort((a,b)=>dist(a,u)-dist(b,u));
        const pick = cand.find(o=>B[u.type]===o.type) || cand.find(o=>B[o.type]!==u.type) || cand[0];
        if (pick) {
          const mx=(u.x+pick.x)/2, my=(u.y+pick.y)/2;   // only reach out if the duel lands under our flak
          if (Math.hypot(mx-car.x, my-car.y) < R) { u.order={kind:'engage', target:pick.id}; return; }
        }
        const pinned = foes.filter(o=>o.lock).sort((a,b)=>dist(a,u)-dist(b,u))[0];
        if (!pick && pinned) { u.order={kind:'engage', target:pinned.id}; return; }
        u.order = {kind:'defend', target:car.id, slot: slot++};
      });
    """,
}

# Audits every lock the model forms against the invariant that actually matters: no duel
# exists that neither party chose. Getting jumped is legal — the enemy that caught you
# named you — but nobody ever tangles with something merely because they flew past it.
RUN = """
(args) => {
  const [pol, limit] = args;
  const C = window.__combat, f = new Function(pol);
  const seen = new Map();       // unit id -> the lock we last audited
  let t = 0, violations = [], defendAnchors = [], engageAnchors = [];
  const R = 11.5;
  while (t < limit) {
    f();
    C.step(1, 1/30);
    t += 1/30;
    const s = C.s;
    const car = s.units.find(u => u.alive && u.type === 'carrier');
    s.units.forEach(u => {
      if (!u.lock || seen.get(u.id) === u.lock) return;
      seen.set(u.id, u.lock);
      const o = s.units.find(x => x.id === u.lock);
      if (!o) { violations.push(`${u.type} locked a unit that does not exist`); return; }
      // one side must have named the other, by craft (engage) or by volume (defend)
      const named = (a, b) => a.order.kind === 'engage' && a.order.target === b.id;
      const held  = (a, b) => a.order.kind === 'defend' && car &&
                              Math.hypot(b.x - car.x, b.y - car.y) < R + 1.0;
      if (!named(u, o) && !named(o, u) && !held(u, o) && !held(o, u))
        violations.push(
          `${u.side} ${u.type} on '${u.order.kind}' locked ${o.side} ${o.type} on ` +
          `'${o.order.kind}' — neither chose the other`);
      if (car && u.anchor) {
        const r = Math.hypot(u.anchor.x - car.x, u.anchor.y - car.y);
        (u.order.kind === 'defend' ? defendAnchors : engageAnchors).push(r);
      }
    });
    if (s.over !== null) break;
  }
  const s = C.s;
  return {
    t, over: s.over, violations,
    defendAnchors, engageAnchors,
    lost: s.units.filter(u => !u.alive && u.side === 'friend' && u.type !== 'carrier').length,
    foesLeft: s.units.filter(u => u.alive && u.side === 'foe' && u.type !== 'capital').length,
  };
}
"""

# Two rules that no longer have a magazine enforcing them, so they are asserted directly.
#
# EVEN_PROBE: park two identical fighters in a duel and check that a mission-length stretch
# of grinding does not kill either. Ammunition used to do this — a duel ran dry at ~16s and
# the kill landed at ~18s — so with unlimited ammo the dps table is the only thing left
# holding "pinning is the default outcome" up.
EVEN_PROBE = """
() => {
  const C = window.__combat;
  C.start('patrol');
  const s = C.s;
  for (let i = 0; i < 900; i++) {
    s.units.filter(u=>u.alive&&u.side==='friend'&&u.type==='fighter'&&!u.lock).forEach(u=>{
      const f = s.units.filter(o=>o.alive&&o.side==='foe'&&o.type==='fighter').sort(
        (a,b)=>Math.hypot(a.x-u.x,a.y-u.y)-Math.hypot(b.x-u.x,b.y-u.y))[0];
      if (f) u.order = {kind:'engage', target:f.id};
    });
    C.step(1, 1/30);
    const duel = C.s.units.find(u=>u.alive&&u.lock&&u.side==='friend'&&u.type==='fighter');
    if (!duel) continue;
    const foe = C.s.units.find(x=>x.id===duel.lock);
    if (foe.type !== 'fighter') continue;
    // isolate: no assists, no flak, nobody else in reach
    C.s.units.filter(u=>u!==duel&&u!==foe).forEach(u=>{ u.x += 900; u.y += 900; });
    const a0 = duel.cond, b0 = foe.cond;
    for (let k = 0; k < 30 * 40; k++) C.step(1, 1/30);   // 40s, longer than any mission
    // 'alive' is not the question — a routed craft leaves the field and reads as not
    // alive. The question is whether the grinding produced a kill.
    return { killed: duel.cond <= 0 || foe.cond <= 0,
             bothBroke: !!(duel.broken && foe.broken),
             dealtToFoe: +(b0 - foe.cond).toFixed(1),
             floorFrac: +(foe.cond / foe.maxCond).toFixed(2) };
  }
  return { bothAlive: null };
}
"""

# CAP_FLAK_PROBE: fly an untouched bomber onto an undefended capital and read off what the
# run costs it. This is the only pressure left on a bomber that cannot run out of ordnance,
# and the convoy lesson rests on it: at zero, that mission falls to a naive charge.
CAP_FLAK_PROBE = """
() => {
  const C = window.__combat;
  C.start('convoy');
  const s = C.s;
  const cap = s.units.find(u=>u.type==='capital');
  const bomber = s.units.find(u=>u.side==='friend'&&u.type==='bomber');
  s.units.filter(u=>u.side==='foe'&&u.type!=='capital').forEach(u=>{ u.x += 900; u.y += 900; });
  const start = bomber.cond;
  bomber.order = {kind:'engage', target:cap.id};
  for (let k = 0; k < 30 * 60 && cap.alive; k++) {
    bomber.order = {kind:'engage', target:cap.id};
    C.step(1, 1/30);
  }
  return { capDead: !cap.alive, bomberAlive: bomber.alive,
           costHull: +(start - bomber.cond).toFixed(1), startHull: start };
}
"""

SCENARIOS = ["patrol", "convoy", "outnumbered"]
ORDER = ["charge", "matchup", "screen", "umbrella", "anvil"]


def main():
    errors = []
    httpd, page_url = serve()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" and "favicon" not in m.location.get("url", "") else None)
        page.goto(page_url)
        page.wait_for_load_state("networkidle")
        page.wait_for_function("window.__combat !== undefined")

        table, violations = {}, []
        defend_r, engage_r = [], []
        for sc in SCENARIOS:
            for pol in ORDER:
                page.evaluate(f"window.__combat.start({sc!r})")
                r = page.evaluate(RUN, [POLICIES[pol], 90.0])
                table[(sc, pol)] = r
                violations += [f"{sc}/{pol}: {v}" for v in r["violations"][:3]]
                defend_r += r["defendAnchors"]
                engage_r += r["engageAnchors"]

        even = page.evaluate(EVEN_PROBE)
        capflak = page.evaluate(CAP_FLAK_PROBE)

        browser.close()
    httpd.shutdown()

    for sc in SCENARIOS:
        row = "  ".join(
            f"{pol}={'WIN ' if table[(sc, pol)]['over'] else 'loss'}" for pol in ORDER)
        print(f"{sc:<13} {row}")

    def won(sc, pol):
        return bool(table[(sc, pol)]["over"])

    # --- the gradient
    for pol in ORDER:
        assert won("patrol", pol), (
            f"patrol is the tutorial and must be winnable even by '{pol}' — it is the "
            "scenario that teaches the counter-chain, so it cannot also gate on tactics")

    assert not won("convoy", "charge") and not won("convoy", "matchup"), (
        "convoy fell to a policy that does not screen the bomber; the escort lesson is "
        "no longer being taught")
    assert won("convoy", "screen"), (
        "screening the bomber no longer wins convoy — the escort lesson has no payoff")

    assert won("outnumbered", "anvil"), (
        "outnumbered is unwinnable even by the policy its own brief prescribes")
    for pol in ["charge", "matchup", "screen", "umbrella"]:
        assert not won("outnumbered", pol), (
            f"outnumbered fell to '{pol}'; it is meant to be the scenario that only the "
            "umbrella solves")
    assert table[("outnumbered", "anvil")]["lost"] >= 1, (
        "outnumbered was won without losing anybody — five against three should cost")

    # Turtling is safety, not a win condition: if sitting in the flak ever beats
    # outnumbered on its own, the umbrella has become a button rather than a position.
    assert not won("outnumbered", "umbrella"), (
        "pure turtling beat outnumbered — the flak is doing the player's work")

    # --- monotonic difficulty: nothing later is easier than something earlier
    for pol in ORDER:
        wins = [won(sc, pol) for sc in SCENARIOS]
        assert wins == sorted(wins, reverse=True), (
            f"policy '{pol}' beats a harder scenario than one it loses: {wins}")

    # --- orders are binding (GDD v4 3.1)
    assert not violations, (
        "a craft locked something other than what it was ordered onto — orders are "
        "advisory again, and skill stops paying:\n  " + "\n  ".join(violations[:6]))

    # --- the anchor favours whoever held station, which is what puts the flak on it
    assert defend_r and engage_r, "no duels were sampled — the harness is not driving"
    d, e = sum(defend_r) / len(defend_r), sum(engage_r) / len(engage_r)
    assert d < e, (
        f"defended duels anchor at {d:.1f} from the carrier and reached-out ones at "
        f"{e:.1f}: holding station no longer buys you ground, so the flak covers "
        "fights at random")
    assert d < 11.5, (
        f"defended duels anchor at {d:.1f}, outside the {11.5} flak radius — the "
        "umbrella never fires")

    # --- an even duel must not resolve inside a mission
    assert even.get("killed") is not None, "the even-duel probe never got a duel to test"
    assert not even["killed"], (
        "an even duel produced a kill in 40s of grinding. With no magazine to run dry, an "
        "even duel that resolves turns every engagement into a 1-for-1 trade and the "
        "counter-chain stops deciding fights (GDD v4 3.4)")
    assert even["dealtToFoe"] > 0, (
        "an even duel dealt no damage at all — a pin should still be a grind, not a "
        "stalemate two craft can sit in for free")
    assert even["bothBroke"], (
        "an even duel ground on past the floor without either craft breaking off; a pin "
        "that neither side can end or leave is a deadlock, and rout objectives become "
        "unreachable")

    # --- the capital's flak is the only thing bounding a bombing run now
    assert capflak["capDead"], (
        "an unmolested bomber could not kill a capital at all; the objective is "
        "unreachable rather than defended")
    assert capflak["costHull"] > 0, (
        "a bombing run cost the bomber nothing. Flak is the only pressure left on a craft "
        "that cannot run out of ordnance, and with it at zero convoy falls to a naive "
        "charge — measured, not assumed")
    frac = capflak["costHull"] / capflak["startHull"]
    assert 0.20 <= frac <= 0.75, (
        f"a clean bombing run costs {frac:.0%} of the bomber's hull; outside 20-75% the "
        "run is either free or unsurvivable, and the convoy lesson stops being teachable")

    if errors:
        sys.exit("console errors:\n  " + "\n  ".join(errors))

    print(f"\ncombat ok — 15 scripted runs, skill gradient holds")
    print(f"anchors — defended duels {d:.1f} from the carrier, reached-out {e:.1f} "
          f"(flak radius 11.5)")
    print("orders are binding; ammunition is unlimited")
    print(f"even duel — 40s of grinding killed nobody ({even['dealtToFoe']:.0f} dealt, "
          f"floored at {even['floorFrac']:.0%}); both broke off instead")
    print(f"bombing run — a clean pass on a capital costs {frac:.0%} of the bomber's hull")


if __name__ == "__main__":
    main()
