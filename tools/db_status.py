"""Show what Marquee has ingested and where it posts.

Usage:
    python -m tools.db_status
    python -m tools.db_status --limit 15
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


async def main(limit: int) -> int:
    await db.connect()

    total = await db.count_seen()
    configs = await db.all_guild_configs()

    print(f"Database: {config.DATABASE_PATH}")
    print(f"Videos seen: {total}")
    print(f"Studios watched: {len(config.REGISTRY)}")
    print()

    print(f"Guilds configured to post: {len(configs)}")
    for row in configs:
        print(f"  guild {row['guild_id']}  ->  channel {row['channel_id']}")

    if not configs:
        print()
        print("  [!] No guild configured yet. Run /setup channel:#trailers in Discord.")

    # Per-guild genre roles
    for row in configs:
        roles = await db.genre_roles(int(row["guild_id"]))
        print(f"\n  Genre roles for {row['guild_id']}: {len(roles)}")
        for genre, role_id in sorted(roles.items()):
            print(f"    {genre:<14} -> role {role_id}")

    rows = await db.recent_seen(limit=limit)
    print(f"\nMost recent {len(rows)} ingested:")
    for r in rows:
        title = r["title"][:56]
        print(f"  {r['published_at'][:10]}  {r['studio']:<26} {title}")

    await db.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    raise SystemExit(asyncio.run(main(ap.parse_args().limit)))
