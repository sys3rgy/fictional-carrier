#!/usr/bin/env python3
"""
build_artifact.py — turn the preview pages into single self-contained files.

The viewers normally fetch their JSON and PNGs at runtime. This inlines them as
data URIs so each page works with no network access at all, which is what an
Artifact needs. Everything else about the pages is untouched: one viewer, two
delivery modes.

Builds dist/carrier-sprites.html (the sprite viewer) and dist/battle-map.html
(the map viewer).
"""

import base64
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "preview", "index.html")
MAP_SRC = os.path.join(ROOT, "preview", "map.html")
SPRITES = os.path.join(ROOT, "sprites")
MAPS = os.path.join(ROOT, "maps")
OUT = os.path.join(ROOT, "dist", "carrier-sprites.html")
MAP_OUT = os.path.join(ROOT, "dist", "battle-map.html")


def data_uri(path):
    with open(path, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")


def inline(html, marker, name, value):
    html, n = re.subn(
        r"const " + name + r" = null;\s*/\* build:" + marker + r" \*/",
        "const " + name + " = " + value + ";",
        html,
    )
    if n != 1:
        sys.exit(f"build marker {marker!r} not found (matched {n})")
    return html


def build_map():
    map_json = os.path.join(MAPS, "battle_map.json")
    if not os.path.exists(map_json):
        print("skipping the map page — run generate_map.py first")
        return
    with open(map_json) as fh:
        data = json.load(fh)

    with open(MAP_SRC) as fh:
        html = fh.read()
    html = inline(html, "map", "EMBEDDED_MAP", json.dumps(data, separators=(",", ":")))
    html = inline(html, "image", "EMBEDDED_IMAGE",
                  json.dumps(data_uri(os.path.join(MAPS, data["image"]))))

    os.makedirs(os.path.dirname(MAP_OUT), exist_ok=True)
    with open(MAP_OUT, "w") as fh:
        fh.write(html)
    print(f"wrote {MAP_OUT} ({len(html.encode()) / 1e6:.2f} MB, "
          f"{len(data['props'])} props embedded)")


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

    build_map()


if __name__ == "__main__":
    main()
