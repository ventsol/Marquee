"""Audit genre coverage: which genres can never fire, and why.

Useful after trimming the studio list. If no remaining studio declares a genre,
its Discord role exists but will never be pinged.

Usage:
    python -m tools.genre_audit
"""

from __future__ import annotations

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


async def main() -> int:
    await db.connect()
    registry = config.load_registry()

    live = set(registry.all_genres)
    defined = set(config.all_genre_definitions())

    print(f"Studios configured: {len(registry)}")
    print(f"Genres backed by at least one studio: {len(live)}")
    print()

    print("Coverage per genre:")
    for genre in sorted(defined | live):
        studios = [s.name for s in registry if genre in s.genres]
        mark = "OK " if studios else "DEAD"
        detail = ", ".join(studios) if studios else "(no studio declares this)"
        print(f"  [{mark}] {genre:<14} {detail}")

    dead = sorted(defined - live)
    print()
    if dead:
        print(f"Genres that can NEVER fire ({len(dead)}): {', '.join(dead)}")
    else:
        print("Every defined genre has at least one studio.")

    # Compare against roles that actually exist in each guild.
    configs = await db.all_guild_configs()
    for row in configs:
        guild_id = int(row["guild_id"])
        roles = await db.genre_roles(guild_id)
        orphaned = sorted(set(roles) - live)
        print()
        print(f"Guild {guild_id}: {len(roles)} genre role(s) in Discord")
        if orphaned:
            print(f"  Orphaned roles (exist but nothing will ping them): {orphaned}")
            print("  Remove them in Discord, or re-add a studio that uses them.")
        else:
            print("  No orphaned roles.")

    await db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
