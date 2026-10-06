"""Write a page crop (words, rules, images of a region) as a layout test fixture.

    PYTHONPATH=<operonx branch> uv run python scripts/crop_page.py FILE.pdf PAGE X0 Y0 X1 Y1 OUT.json

Coordinates are points, origin top-left. See operonx_kb.testing.layout_crops.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("page", type=int)
    ap.add_argument("box", type=float, nargs=4)
    ap.add_argument("out", type=Path)
    args = ap.parse_args()

    from operonx_kb.pdf.backend import DoclingParseBackend
    from operonx_kb.testing.layout_crops import crop_page, write_crop

    page = DoclingParseBackend().pages(args.pdf.read_bytes())[args.page - 1]
    crop = crop_page(page, args.box, f"{args.pdf.name} page {args.page}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_crop(crop, args.out)
    print(
        f"{args.out}: {len(crop['words'])} words, {len(crop['rules'])} rules, {len(crop['images'])} images"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
