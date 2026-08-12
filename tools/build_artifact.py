#!/usr/bin/env python3
"""
build_artifact.py — turn preview/index.html into a single self-contained page.

The viewer normally fetches sprites/carrier_atlas.json and the PNGs at runtime.
This inlines the atlas and every sheet as data URIs so the page works with no
network access at all, which is what an Artifact needs. Everything else about
the page is untouched: one viewer, two delivery modes.
"""

import base64
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "preview", "index.html")
SPRITES = os.path.join(ROOT, "sprites")
OUT = os.path.join(ROOT, "dist", "carrier-sprites.html")


def data_uri(path):
    with open(path, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")


def main():
    with open(os.path.join(SPRITES, "sprite_atlas.json")) as fh:
        atlas = json.load(fh)

    assets = {}
    for loadout in (l for c in atlas["craft"] for l in c["loadouts"]):
        for anim in loadout["animations"]:
            for key in ("sheet", "sheet_2x"):
                name = anim[key]
                assets[name] = data_uri(os.path.join(SPRITES, name))

    with open(SRC) as fh:
        html = fh.read()

    html, n1 = re.subn(
        r"const EMBEDDED_ATLAS = null;\s*/\* build:atlas \*/",
        "const EMBEDDED_ATLAS = " + json.dumps(atlas, separators=(",", ":")) + ";",
        html,
    )
    html, n2 = re.subn(
        r"const EMBEDDED_ASSETS = null;\s*/\* build:assets \*/",
        "const EMBEDDED_ASSETS = " + json.dumps(assets, separators=(",", ":")) + ";",
        html,
    )
    if n1 != 1 or n2 != 1:
        sys.exit(f"build markers not found in {SRC} (atlas={n1}, assets={n2})")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        fh.write(html)

    mb = len(html.encode()) / 1e6
    print(f"wrote {OUT} ({mb:.2f} MB, {len(assets)} sheets embedded)")


if __name__ == "__main__":
    main()
