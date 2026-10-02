"""Poll every feed and print what WOULD be announced. No Discord, no DB writes.

Usage:
    python -m tools.dry_run
    python -m tools.dry_run --genre horror
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which chokes on emoji/CJK in titles.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

import config  # noqa: E402
import youtube  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--genre", help="only studios tagged with this genre")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument(
        "--mode",
        choices=["trailers", "promo", "all"],
        default=config.FILTER_MODE,
        help="filter strength (default: .env FILTER_MODE)",
    )
    args = parser.parse_args()

    studios = config.REGISTRY.by_genre(args.genre) if args.genre else list(config.REGISTRY)
    if not studios:
        print(f"No studios tagged {args.genre!r}")
        return 1

    print(
        f"Dry run over {len(studios)} studio feed(s) — FILTER_MODE={args.mode}\n"
    )
    trailers = await youtube.fetch_all(
        studios, seen_ids=set(), filter_mode=args.mode
    )

    for t in trailers[: args.limit]:
        genres = " ".join(config.genre_label(g) for g in t.genres)
        print(f"{t.published_at:%Y-%m-%d}  [{t.kind:<8}] {t.studio}")
        print(f"    {t.title}")
        print(f"    https://youtu.be/{t.video_id}   ({genres})")
        print()

    print(f"{len(trailers)} item(s) fetched. Nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
