"""Shared runtime state.

The standalone bot (`bot.py`) and the web app (`app.py`) both need to observe
the same live objects: the Discord client, the scheduler, and the outcome of
the last poll. Previously these lived in `bot.py` globals, which made the bot
impossible to embed. They now live here so either entry point can read them.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import discord

log = logging.getLogger("trailer.runtime")


@dataclass
class PollSnapshot:
    """Outcome of the most recent poll cycle, for display in the dashboard."""

    started_at: datetime | None = None
    finished_at: datetime | None = None
    fetched: int = 0
    new: int = 0
    announced: int = 0
    seeded: int = 0
    errors: list[str] = field(default_factory=list)
    trigger: str = "scheduled"  # "scheduled" | "manual" | "startup"

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    @property
    def running(self) -> bool:
        return self.started_at is not None and self.finished_at is None

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "running": self.running,
            "fetched": self.fetched,
            "new": self.new,
            "announced": self.announced,
            "seeded": self.seeded,
            "errors": self.errors,
            "trigger": self.trigger,
        }


class Runtime:
    """Mutable process-wide state. One instance per process."""

    def __init__(self) -> None:
        self.bot: discord.Client | None = None
        self.scheduler = None
        self.started_at: float = time.time()
        self.bot_task = None
        self.last_poll: PollSnapshot = PollSnapshot()
        self.poll_in_progress: bool = False
        # Set when on_ready has completed its one-time setup.
        self.ready: bool = False

    # ── Bot ──────────────────────────────────────────────────────────────────
    def attach_bot(self, bot: discord.Client) -> None:
        self.bot = bot

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def bot_user(self) -> str | None:
        if self.bot is not None and self.bot.user is not None:
            return str(self.bot.user)
        return None

    @property
    def bot_ready(self) -> bool:
        return bool(self.bot is not None and self.bot.is_ready())

    @property
    def latency_ms(self) -> int | None:
        if self.bot is None or not self.bot.is_ready():
            return None
        latency = self.bot.latency
        if latency != latency:  # NaN before first heartbeat
            return None
        return round(latency * 1000)

    @property
    def guild_count(self) -> int:
        return len(self.bot.guilds) if self.bot is not None else 0

    # ── Poll bookkeeping ─────────────────────────────────────────────────────
    def begin_poll(self, trigger: str = "scheduled") -> None:
        self.last_poll = PollSnapshot(
            started_at=datetime.now(timezone.utc), trigger=trigger
        )
        self.poll_in_progress = True

    def finish_poll(self, result) -> None:
        snap = self.last_poll
        snap.finished_at = datetime.now(timezone.utc)
        snap.fetched = getattr(result, "fetched", 0)
        snap.new = getattr(result, "new", 0)
        snap.announced = getattr(result, "announced", 0)
        snap.seeded = getattr(result, "seeded", 0)
        snap.errors = list(getattr(result, "errors", []) or [])
        self.poll_in_progress = False

    def fail_poll(self, exc: BaseException) -> None:
        snap = self.last_poll
        snap.finished_at = datetime.now(timezone.utc)
        snap.errors = [f"{type(exc).__name__}: {exc}"]
        self.poll_in_progress = False

    # ── Snapshot for the API ─────────────────────────────────────────────────
    def as_dict(self) -> dict:
        return {
            "bot_user": self.bot_user,
            "bot_ready": self.bot_ready,
            "ready": self.ready,
            "latency_ms": self.latency_ms,
            "guild_count": self.guild_count,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "poll_in_progress": self.poll_in_progress,
            "last_poll": self.last_poll.as_dict(),
            "scheduler_running": bool(
                self.scheduler is not None and getattr(self.scheduler, "running", False)
            ),
        }


# Process-wide singleton.
RUNTIME = Runtime()
