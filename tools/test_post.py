"""Force a real trailer post into Discord, for testing.

Marquee only posts genuinely-new uploads and dedupes against everything it has
ever seen, so a fresh install can wait days for its first post. This tool
forgets a video's "seen" record so it can be announced immediately, or posts a
record the bot has never ingested.

All write operations are opt-in and reversible.

Usage:
    # List recent trailers already in the DB, with an index
    python -m tools.test_post --list

    # Re-announce DB entry #3 (forgets it, then posts it)
    python -m tools.test_post --index 3

    # Post a trailer from the live feeds that was never ingested
    python -m tools.test_post --studio NEON

    # Re-announce using the bot's real pipeline (announce to Discord)
    python -m tools.test_post --index 3 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

import config  # noqa: E402
import db  # noqa: E402
import youtube  # noqa: E402


async def list_recent(limit: int) -> int:
    await db.connect()
    rows = await db.recent_seen(limit=limit)
    print(f"{len(rows)} most recent ingested trailers:\n")
    for i, r in enumerate(rows):
        print(f"  [{i}] {r['published_at'][:10]}  {r['studio']}")
        print(f"      {r['title'][:70]}")
        print(f"      id={r['video_id']}")
        print()
    print("Re-announce one with:  python -m tools.test_post --index N")
    await db.close()
    return 0


async def force_post(index: int | None, studio: str | None, dry_run: bool) -> int:
    import botcore

    await db.connect()

    # Build the trailer we want to post.
    if index is not None:
        rows = await db.recent_seen(limit=50)
        if index < 0 or index >= len(rows):
            print(f"[X] index {index} out of range (0-{len(rows) - 1})")
            return 1
        row = rows[index]
        # Reconstruct a Trailer from the DB row.
        from datetime import datetime, timezone

        from youtube import Trailer

        trailer = Trailer(
            video_id=row["video_id"],
            channel_id=row["channel_id"],
            studio=row["studio"],
            title=row["title"],
            url=f"https://youtu.be/{row['video_id']}",
            published_at=datetime.fromisoformat(row["published_at"]),
            thumbnail=f"https://i.ytimg.com/vi/{row['video_id']}/hqdefault.jpg",
            genres=tuple(
                s.genres for s in config.REGISTRY if s.name == row["studio"]
            )[0]
            if any(s.name == row["studio"] for s in config.REGISTRY)
            else (),
            kind="trailer",
        )
    elif studio:
        print(f"Fetching live feeds to find a {studio} trailer...")
        found = await youtube.fetch_all(
            config.REGISTRY.studios, seen_ids=set(), filter_mode=config.FILTER_MODE
        )
        matches = [t for t in found if t.studio.lower() == studio.lower()]
        if not matches:
            print(f"[X] No current trailer found for studio {studio!r}")
            print("    Try: python -m tools.dry_run --limit 40")
            return 1
        trailer = matches[0]
    else:
        print("[X] Provide --index N or --studio NAME")
        return 1

    print("\n--- Will post ---")
    print(f"  Studio : {trailer.studio}")
    print(f"  Title  : {trailer.title}")
    print(f"  URL    : {trailer.url}")
    print(f"  Genres : {', '.join(trailer.genres) or '(none)'}")
    print(f"  Kind   : {trailer.kind}")

    if trailer.genres:
        import discord

        print("\n  Would ping roles for:", ", ".join(trailer.genres))
    else:
        print("\n  [!] No genres -> nobody will be pinged.")
        print("      Add genres to this studio in studios.toml or the dashboard.")

    if dry_run:
        print("\n[dry-run] Nothing posted, nothing written.")
        await db.close()
        return 0

    # Republishing an existing video must keep its seen_videos row: the
    # `published` table has a FK onto it, so deleting it makes the post-send
    # bookkeeping fail. Record it with the same values instead (INSERT OR
    # IGNORE, so it's a no-op when the row is already there).
    await db.mark_seen(
        trailer.video_id,
        trailer.channel_id,
        trailer.studio,
        trailer.title,
        trailer.published_iso,
    )
    print("\nEnsured 'seen' record exists (required by the published FK).")

    bot = botcore.create_bot()

    import runtime as rt

    rt.RUNTIME.attach_bot(bot)

    async def run():
        # Connect, then force a single-video publish through the real path.
        connected = asyncio.Event()

        @bot.event
        async def on_ready():
            connected.set()

        task = asyncio.create_task(bot.start(config.DISCORD_TOKEN))
        await asyncio.wait_for(connected.wait(), timeout=30)

        try:
            from poller import _publish

            count = await _publish(bot, trailer)
            if count:
                print(f"\n[OK] Posted to {count} guild(s). Check your channel.")
            else:
                print("\n[X] Nothing posted. Is /setup done in your server?")
                print("    Check: python -m tools.db_status")
        finally:
            await bot.close()
            task.cancel()

    try:
        await run()
    finally:
        await db.close()
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description="Force a test trailer post")
    ap.add_argument("--list", action="store_true", help="list recent trailers")
    ap.add_argument("--index", type=int, help="re-announce DB entry N")
    ap.add_argument("--studio", help="pull a live trailer from this studio")
    ap.add_argument("--dry-run", action="store_true", help="preview only")
    args = ap.parse_args()

    if args.list:
        return await list_recent(20)
    return await force_post(args.index, args.studio, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
