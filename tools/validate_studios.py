"""Health check: does every channel_id in studios.toml resolve to a real feed?

Usage:
    python -m tools.validate_studios

Exit code is non-zero if any channel fails, so CI can gate on it.
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
import youtube  # noqa: E402


async def main() -> int:
    registry = config.load_registry()
    print(f"Validating {len(registry)} studios from studios.toml\n")

    results = await youtube.validate_registry(registry)

    ok = [name for name, good in results.items() if good]
    bad = [name for name, good in results.items() if not good]

    for name, good in results.items():
        studio = next(s for s in registry if s.name == name)
        mark = "OK  " if good else "FAIL"
        print(f"  [{mark}] {name:<38} {studio.channel_id}")

    print(f"\n{len(ok)} ok, {len(bad)} failed")
    if bad:
        print("\nFix these channel IDs (likely wrong or the channel has no uploads):")
        for name in bad:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
