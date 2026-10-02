"""Render an icon SVG to PNG sizes using headless Chrome.

Discord wants a square PNG (512x512 recommended, min 128x128). This produces
the sizes you need plus a favicon, without needing native Cairo libraries.

Usage:
    python -m tools.render_icon                     # default: icon.svg
    python -m tools.render_icon --svg icon-monster.svg
    python -m tools.render_icon --svg icon-monster.svg --name monster
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

SIZES = [1024, 512, 256, 128, 64]


def find_browser() -> str | None:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    return shutil.which("chrome") or shutil.which("chromium") or shutil.which("msedge")


def render(browser: str, svg_markup: str, size: int, dest: Path) -> bool:
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:transparent;}"
        f"#s{{width:{size}px;height:{size}px;}}"
        f"#s svg{{display:block;width:{size}px;height:{size}px;}}"
        "</style></head><body><div id='s'>"
        f"{svg_markup}"
        "</div></body></html>"
    )

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "icon.html"
        page.write_text(html, encoding="utf-8")

        cmd = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={size},{size}",
            f"--screenshot={dest}",
            "--default-background-color=00000000",
            "--no-sandbox",
            page.as_uri(),
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            print(f"  [X] {size}px: timed out")
            return False

        if not dest.exists():
            print(f"  [X] {size}px: no file produced")
            return False
        return True


def legibility_report(png: Path) -> None:
    """Quantify how the mark holds up at the sizes Discord actually uses."""
    im = Image.open(png).convert("RGB")

    def stats(size: int) -> tuple[int, int, int]:
        t = im.resize((size, size), Image.LANCZOS)
        w, h = t.size
        px = t.load()

        # Anything clearly not the background plate.
        bg = px[4, 4]
        ink = 0
        rows_with_ink = 0
        for y in range(h):
            row = sum(
                1
                for x in range(w)
                if abs(px[x, y][0] - bg[0]) + abs(px[x, y][1] - bg[1]) + abs(px[x, y][2] - bg[2])
                > 120
            )
            ink += row
            if row:
                rows_with_ink += 1
        # Distinct horizontal bands = separated features (eyes, mouth).
        bands = 0
        prev = 0
        for y in range(h):
            row = sum(
                1
                for x in range(w)
                if abs(px[x, y][0] - bg[0]) + abs(px[x, y][1] - bg[1]) + abs(px[x, y][2] - bg[2])
                > 120
            )
            if row > 0 and prev == 0:
                bands += 1
            prev = row
        return ink, rows_with_ink, bands

    print(f"\nLegibility of {png.name}:")
    print("  size | detail px | rows w/ detail | feature bands")
    for size in (24, 32, 48, 64, 128):
        ink, rows, bands = stats(size)
        print(f"  {size:>4} | {ink:>9} | {rows:>14} | {bands}")


def crop_safety(png: Path) -> None:
    """Check nothing important lands in the corners Discord's circle crop removes.

    Discord circle-masks avatars. Any content outside the inscribed circle is
    lost, so flag it before you upload.
    """
    im = Image.open(png).convert("RGBA")
    size = im.size[0]
    cx = cy = size / 2
    r = size / 2
    px = im.load()

    outside_ink = 0
    total_ink = 0
    for y in range(0, size, 4):
        for x in range(0, size, 4):
            if px[x, y][3] > 60:
                total_ink += 1
                if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
                    outside_ink += 1

    pct = outside_ink * 100 / max(total_ink, 1)
    status = "OK" if outside_ink == 0 else "WARN"
    print(f"\n  [{status}] crop safety: {outside_ink} of {total_ink} ink px "
          f"({pct:.1f}%) fall outside the circle")
    if outside_ink:
        print("       Those pixels are lost when Discord circle-crops.")

    bbox = im.getbbox()
    print(f"  fills canvas: {(bbox[2]-bbox[0])/size*100:.1f}% wide, "
          f"{(bbox[3]-bbox[1])/size*100:.1f}% tall")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg", default="icon.svg", help="SVG filename in static/")
    ap.add_argument("--name", help="output prefix (default: derived from --svg)")
    ap.add_argument("--all", action="store_true", help="render every icon-*.svg")
    args = ap.parse_args()

    browser = find_browser()
    if browser is None:
        print("[X] No Chrome/Edge/Chromium found to render with.")
        return 1

    if args.all:
        targets = [p for p in sorted(OUT.glob("icon-*.svg")) if p.name != "icon.svg"]
        targets = [OUT / "icon.svg"] + targets
    else:
        targets = [OUT / args.svg]

    print(f"Renderer: {browser}\n")

    for svg_path in targets:
        if not svg_path.exists():
            print(f"[X] Missing {svg_path}")
            continue

        prefix = args.name or svg_path.stem
        svg_markup = svg_path.read_text(encoding="utf-8")
        print(f"--- {svg_path.name} -> {prefix}-N.png ---")

        ok = 0
        for size in SIZES:
            dest = OUT / f"{prefix}-{size}.png"
            if render(browser, svg_markup, size, dest):
                print(f"  [OK] {dest.name}  ({dest.stat().st_size / 1024:.1f} KB)")
                ok += 1

        biggest = OUT / f"{prefix}-512.png"
        if biggest.exists():
            legibility_report(biggest)
            crop_safety(biggest)
        print()

    print("To use an icon in the app, copy it over icon.png / icon.svg.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
