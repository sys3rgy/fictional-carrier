#!/usr/bin/env python3
"""
test_preview.py — drive the viewer with Playwright and check it actually renders.

Loads the standalone build (dist/carrier-sprites.html, no server needed), then
asserts that the stage canvas has non-background pixels, that switching loadout
and animation changes what is drawn, and that no console errors were raised.
Writes screenshots to tools/shots/ for eyeballing.
"""

import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "dist", "carrier-sprites.html")
MAP_PAGE = os.path.join(ROOT, "dist", "battle-map.html")
SHOTS = os.path.join(ROOT, "tools", "shots")

# Use the pre-installed Chromium when there is one, rather than downloading a browser.
CHROMIUM = "/opt/pw-browsers/chromium"

# Counts the stage canvas pixels that are not the empty-space background.
INKED = """() => {
  const c = document.getElementById('stage');
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i] > 90 || d[i + 1] > 90 || d[i + 2] > 110) n++;
  }
  return n;
}"""


MAP_INKED = """() => {
  const c = document.getElementById('map');
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i] > 40 || d[i + 1] > 40 || d[i + 2] > 40) n++;
  }
  return n;
}"""


def main():
    if not os.path.exists(PAGE):
        sys.exit(f"missing {PAGE} — run tools/build_artifact.py first")
    os.makedirs(SHOTS, exist_ok=True)

    errors = []
    with sync_playwright() as pw:
        launch = {"executable_path": CHROMIUM} if os.path.exists(CHROMIUM) else {}
        browser = pw.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1280, "height": 1100})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto("file://" + PAGE)
        page.wait_for_selector("#anim-buttons button")
        page.wait_for_timeout(900)

        # the ship is on screen at all
        inked = page.evaluate(INKED)
        assert inked > 500, f"stage looks empty ({inked} lit pixels)"

        # every control got built
        assert page.locator("#craft-buttons button").count() == 2, "expected 2 craft"
        assert page.locator("#loadouts button").count() == 3, "expected 3 carrier loadouts"
        assert page.locator("#anim-buttons button").count() == 6, "expected 6 animations"
        assert page.locator("#compass button").count() == 8, "expected 8 facings"
        assert page.locator("#strip figure").count() == 8, "expected 8 strip cells"
        assert page.locator("#dl-rows tr").count() == 6, "expected 6 download rows"

        # pause so frame comparisons are stable
        page.click("#play")
        page.wait_for_timeout(200)

        # facing changes the drawing
        before = page.evaluate(INKED)
        page.locator("#compass button").nth(2).click()
        page.wait_for_timeout(200)
        assert page.evaluate(INKED) != before, "changing facing did not change the stage"

        # animation switch works and the readout follows
        page.locator("#anim-buttons button").nth(3).click()
        page.wait_for_timeout(200)
        # inner_text() reflects the readout's uppercase transform
        readout = page.locator("#ro-anim").inner_text().strip().lower()
        assert readout == "launch", f"readout did not follow animation (got {readout!r})"

        page.screenshot(path=os.path.join(SHOTS, "viewer.png"), full_page=True)

        # upgraded loadout: more squadrons, more modules, still renders
        page.locator("#loadouts button").nth(2).click()
        page.wait_for_timeout(400)
        assert page.evaluate(INKED) > 500, "mk3 stage looks empty"
        # the five-slot frame plus a chassis: every configuration has the same part count
        mods = page.locator("#modules li").count()
        assert mods == 6, f"expected 6 parts on mk3 (chassis + 5 slots), got {mods}"
        # mk3 shares nothing with the starting ship
        added = page.locator("#modules li.added").count()
        assert added == 6, f"expected all 6 parts swapped on mk3, got {added}"

        page.screenshot(path=os.path.join(SHOTS, "viewer_mk3.png"), full_page=True)

        # the fighter is a second craft with its own cell size and animation set
        page.locator("#craft-buttons button").nth(1).click()
        page.wait_for_timeout(500)
        assert page.evaluate(INKED) > 200, "fighter stage looks empty"
        assert page.locator("#loadouts button").count() == 3, "expected 3 fighter loadouts"
        assert page.locator("#fact-cell").inner_text().strip() == "48\u00d748", "expected a 48px cell"
        names = page.locator("#anim-buttons").inner_text().lower()
        assert "destroyed" in names, "expected the fighter's destroyed animation"

        page.screenshot(path=os.path.join(SHOTS, "viewer_fighter.png"), full_page=True)

        # ---- the battle map page ------------------------------------------------
        if os.path.exists(MAP_PAGE):
            page.goto("file://" + MAP_PAGE)
            page.wait_for_selector("#lane-rows tr")
            page.wait_for_timeout(700)

            assert page.locator("#lane-rows tr").count() == 3, "expected 3 lanes"
            assert page.locator("#prop-rows tr").count() == 5, "expected 5 prop kinds"
            props = int(page.locator("#f-props").inner_text())
            assert props > 100, f"expected a populated field, got {props} props"
            # every prop mirrors onto the other side, so the count must be even
            assert props % 2 == 0, f"map is not symmetric: {props} props"

            drawn = page.evaluate(MAP_INKED)
            assert drawn > 5000, f"map canvas looks empty ({drawn} lit pixels)"

            # overlays actually change the canvas
            page.click("#t-cover")
            page.wait_for_timeout(250)
            assert page.evaluate(MAP_INKED) != drawn, "cover overlay drew nothing"

            page.screenshot(path=os.path.join(SHOTS, "map.png"), full_page=True)

        browser.close()

    if errors:
        sys.exit("console errors:\n  " + "\n  ".join(errors))
    print(f"preview ok — screenshots in {SHOTS}/")


if __name__ == "__main__":
    main()
