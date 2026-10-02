"""Prove the lockfile is race-free: N processes launched simultaneously,
exactly ONE must win.

Usage:
    python -m tools.test_lockfile
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

ROOT = Path(__file__).resolve().parent.parent

CHILD = """
import sys, time
sys.path.insert(0, r"{root}")
import lockfile

ok = lockfile.acquire("racer")
print("WON" if ok else "LOST", flush=True)
if ok:
    time.sleep(1.5)   # hold it so late starters see a live holder
"""


def main() -> int:
    import lockfile

    print("Race test: launching 8 processes at once\n")

    lockfile.LOCK_PATH.unlink(missing_ok=True)
    script = ROOT / "_race_child.py"
    script.write_text(CHILD.format(root=str(ROOT)), encoding="utf-8")

    try:
        procs = [
            subprocess.Popen(
                [sys.executable, str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(ROOT),
            )
            for _ in range(8)
        ]

        won = lost = errored = 0
        for p in procs:
            out, err = p.communicate(timeout=60)
            if "WON" in out:
                won += 1
            elif "LOST" in out:
                lost += 1
            else:
                errored += 1
                if err.strip():
                    print("  stderr:", err.strip()[:200])
    finally:
        script.unlink(missing_ok=True)
        lockfile.LOCK_PATH.unlink(missing_ok=True)

    print(f"  winners: {won}")
    print(f"  losers : {lost}")
    if errored:
        print(f"  errored: {errored}")

    print()
    if won == 1 and lost == 7:
        print("[PASS] Exactly one process acquired the lock.")
        return 0
    print(f"[FAIL] Expected 1 winner, got {won}. The lock still races.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
