"""Single-instance guard.

`bot.py` and `app.py` both run a poller. Two pollers means two announcement
passes racing on the same feeds — dedupe usually saves you, but it's a race,
not a guarantee. This refuses to start a second poller-capable process.

A stale lock (crashed process) is detected via PID liveness and reclaimed.
"""

from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path

import config

log = logging.getLogger("trailer.lock")

LOCK_PATH = config.ROOT / ".marquee.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import subprocess

            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return str(pid) in out.stdout
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _read_lock() -> tuple[int, str] | None:
    if not LOCK_PATH.exists():
        return None
    try:
        text = LOCK_PATH.read_text(encoding="utf-8").strip()
        if not text:
            return None
        pid_str, _, kind = text.partition(":")
        return int(pid_str), (kind or "unknown")
    except (OSError, ValueError):
        return None


def acquire(kind: str) -> bool:
    """Take the lock. Returns False if another live process holds it.

    The create must be ATOMIC. A read-then-write check races: two processes
    launched in the same instant both see "no lock", both write, and the
    second silently overwrites the first. O_CREAT|O_EXCL makes the filesystem
    arbitrate, so exactly one caller can win.
    """
    for attempt in (1, 2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = _read_lock()
            if existing is None:
                # Unreadable/empty lock: treat as stale and retry once.
                if attempt == 1:
                    LOCK_PATH.unlink(missing_ok=True)
                    continue
                return False

            pid, holder_kind = existing
            if pid == os.getpid():
                return True  # we already hold it

            if _pid_alive(pid):
                log.error(
                    "Another Marquee process is already running "
                    "(pid %d, mode=%s). Refusing to start a second poller.",
                    pid,
                    holder_kind,
                )
                return False

            log.warning("Reclaiming stale lock from pid %d (%s)", pid, holder_kind)
            LOCK_PATH.unlink(missing_ok=True)
            continue
        except OSError as exc:
            log.error("Could not create lock file: %s", exc)
            return False
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}:{kind}")
            atexit.register(release)
            return True

    return False


def release() -> None:
    try:
        existing = _read_lock()
        if existing is not None and existing[0] == os.getpid():
            LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def holder() -> str | None:
    """Human-readable description of the current holder, if any."""
    existing = _read_lock()
    if existing is None:
        return None
    pid, kind = existing
    if pid == os.getpid():
        return None
    if _pid_alive(pid):
        return f"pid {pid} ({kind})"
    return None
