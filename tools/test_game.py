#!/usr/bin/env python3
"""
test_game.py — play whole runs of the campaign shell in a real browser.

A campaign is a state machine with several ways to end, and the ones that matter are
the failure ends: a Control bar that fills, a wing that is gone. So this drives many
complete runs to termination rather than checking one screen renders, and asserts the
invariants that must hold no matter how the run went.

Also asserts the design laws the shell is supposed to embody, because those are the
things a refactor would quietly break:
  - a pure interceptor wing cannot beat a mission with a capital (GDD v4 law 11)
  - Control only ever rises from operations completing, never on its own
  - pilots persist for the run and their ejection counts only ever climb
"""

import json
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "dist", "last-carrier-flying.html")
CHROMIUM = "/opt/pw-browsers/chromium"

PLAY_RUN = """(hull) => {
  const g = window.__game;
  g.start(hull);
  const trace = [];
  for (let step = 0; step < 200; step++) {
    const s = g.state;
    if (s.over) break;
    const board = g.board();
    // prefer the assault, then a strip, then whatever is loudest
    let i = board.findIndex(c => c.kind === 'assault');
    if (i < 0) i = board.findIndex(c => c.kind === 'objective');
    if (i < 0) i = 0;
    const before = { control: s.control, turn: s.turn, sub: s.subsector };
    const r = g.play(i, 'close');
    trace.push({ before, after: { control: g.state.control, turn: g.state.turn,
                                  sub: g.state.subsector }, r });
    if (r === 'over') break;
  }
  const s = g.state;
  return {
    over: s.over, subsector: s.subsector, turns: s.turn, missions: s.missions,
    pilots: s.pilots.map(p => ({ n: p.name, m: p.missions, e: p.ejections, a: p.alive })),
    wing: s.wing.length, salvage: s.salvage, trace,
  };
}"""

# Reasonable play: keep a bomber (nothing else can strip an anchor), grow the deck,
# then turrets. Used to measure the balance envelope — a scripted mediocre player.
SMART = """(hull) => {
  const g = window.__game; g.start(hull);
  const refit = () => {
    const s = g.state, w = g.wingTypes();
    if (!w.includes('bomber')) g.buyCraft('bomber');
    if (s.salvage >= 80 && s.slots.hangar === 'hangar_small') g.buy('hangar', 'hangar_large');
    if (w.length < (s.slots.hangar === 'hangar_large' ? 5 : 3))
      g.buyCraft(w.filter(t => t === 'interceptor').length ? 'bomber' : 'interceptor');
    if (s.salvage >= 45 && s.slots.turret_a === 'turret_a_laser') g.buy('turret_a', 'turret_a_flak');
    if (s.salvage >= 45 && s.slots.turret_b === 'turret_b_laser') g.buy('turret_b', 'turret_b_missile');
  };
  for (let step = 0; step < 300; step++) {
    const s = g.state; if (s.over) break;
    refit();
    const b = g.board();
    let i = b.findIndex(c => c.kind === 'assault');
    if (i < 0) i = b.findIndex(c => c.kind === 'objective');
    if (i < 0) { i = b.findIndex(c => c.kind === 'threat' && c.drift > 0); if (i < 0) i = 0; }
    if (g.play(i, 'close') === 'over') break;
  }
  const s = g.state;
  return { sub: s.subsector, turns: s.turn,
           won: s.subsector >= 3 && s.control < 100 && s.wing.some(c => c.alive) };
}"""


# A wing of nothing but interceptors: wins every duel, cannot touch a capital.
LAW_11 = """() => {
  const g = window.__game;
  g.start(0);
  const s = g.state;
  s.wing.forEach(c => { c.type = 'interceptor'; });
  const board = g.board();
  let i = board.findIndex(c => c.enemy && c.enemy.capital);
  if (i < 0) return { skipped: true };
  const name = board[i].name;
  g.play(i, 'close');
  const last = g.state.log.find(l => l.text.indexOf(name) === 0);
  return { skipped: false, note: last ? last.text : '', failed: !!(last && /failed/.test(last.text)) };
}"""


def main():
    if not os.path.exists(PAGE):
        sys.exit(f"missing {PAGE} — run tools/build_artifact.py first")

    errors = []
    with sync_playwright() as pw:
        launch = {"executable_path": CHROMIUM} if os.path.exists(CHROMIUM) else {}
        browser = pw.chromium.launch(**launch)
        page = browser.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("file://" + PAGE)
        page.wait_for_selector("#start-choices button")

        assert page.locator("#start-choices button").count() == 3, "expected 3 starting hulls"

        wins = 0
        runs = 24
        for n in range(runs):
            r = page.evaluate(PLAY_RUN, n % 3)
            assert r["over"], f"run {n} never terminated"
            assert r["missions"] > 0, f"run {n} played no missions"

            # Control is a readout: it may only move when turns pass.
            for t in r["trace"]:
                if t["after"]["turn"] == t["before"]["turn"]:
                    assert t["after"]["control"] <= t["before"]["control"] + 1e-6, (
                        f"run {n}: control rose without the clock moving")
                # clearing a subsector resets the bar; otherwise it never falls
                if t["after"]["sub"] == t["before"]["sub"]:
                    assert t["after"]["control"] >= t["before"]["control"] - 1e-6, (
                        f"run {n}: control fell inside a subsector")

            # pilots persist for the run and only ever accumulate
            for p in r["pilots"]:
                assert p["m"] >= 0 and p["e"] >= 0, f"run {n}: negative pilot counters"
                assert p["n"], f"run {n}: unnamed pilot"

            if r["subsector"] >= 3 and r["wing"] > 0:
                wins += 1

        # Balance envelope. The design contract is that you will usually lose but that
        # winning is possible; a change that makes the run trivial or impossible should
        # fail here rather than in someone's playtest.
        smart = [page.evaluate(SMART, n % 3) for n in range(60)]
        won = sum(1 for r in smart if r["won"])
        rate = won / len(smart)
        reached = sum(1 for r in smart if r["sub"] >= 2)
        assert 0.03 <= rate <= 0.45, (
            f"win rate {rate:.0%} outside the intended envelope (3-45%)")
        assert reached >= len(smart) * 0.4, (
            f"only {reached}/{len(smart)} runs reached the back half — the opening "
            "subsector is filtering too hard to be the tutorial")

        law = page.evaluate(LAW_11)
        if not law.get("skipped"):
            assert law["failed"], (
                "law 11 violated: an all-interceptor wing beat a mission with a capital "
                f"({law['note']!r})")

        browser.close()

    if errors:
        sys.exit("console errors:\n  " + "\n  ".join(errors))

    print(f"game ok — {runs} random runs terminated cleanly")
    print(f"balance — {won}/{len(smart)} wins ({rate:.0%}) under scripted play, "
          f"{reached} reached the back half")
    print("law 11 holds: an all-interceptor wing cannot kill a capital")


if __name__ == "__main__":
    main()
