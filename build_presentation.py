#!/usr/bin/env python3
"""Build the self-contained presentation by inlining screenshots as base64.

The deck must work offline, on any machine, with no server and no relative-path
breakage — so the PNGs are embedded rather than linked.

Run:  python build_presentation.py
Out:  presentation.html
"""

from __future__ import annotations

import base64
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "presentation_template.html")
OUTPUT = os.path.join(HERE, "presentation.html")

IMAGES = {
    "__DASH_OPEN__": "dashboard_verified.png",
    "__DASH_ACT__": "dashboard_actuated.png",
    "__PHONE__": "phone_verified.png",
}


def data_uri(path: str) -> str:
    with open(path, "rb") as fh:
        blob = fh.read()
    return "data:image/png;base64," + base64.b64encode(blob).decode("ascii")


def main() -> int:
    html = io.open(TEMPLATE, encoding="utf-8").read()

    missing = [f for f in IMAGES.values() if not os.path.exists(os.path.join(HERE, f))]
    if missing:
        print("ERROR: missing screenshots: " + ", ".join(missing), file=sys.stderr)
        return 1

    total_kb = 0
    for token, filename in IMAGES.items():
        path = os.path.join(HERE, filename)
        uri = data_uri(path)
        if token not in html:
            print(f"WARNING: token {token} not found in template", file=sys.stderr)
        html = html.replace(token, uri)
        kb = os.path.getsize(path) // 1024
        total_kb += kb
        print(f"  embedded {filename:26s} {kb:5d} KB")

    io.open(OUTPUT, "w", encoding="utf-8").write(html)
    out_kb = os.path.getsize(OUTPUT) // 1024

    # Any leftover placeholder means a broken image in the deck.
    leftover = [t for t in IMAGES if t in html]
    if leftover:
        print("ERROR: unresolved placeholders: " + ", ".join(leftover), file=sys.stderr)
        return 1

    print(f"\n  wrote presentation.html  {out_kb} KB "
          f"({total_kb} KB of images, base64 adds ~33%)")
    print("  open it in any browser — no server, no network needed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
