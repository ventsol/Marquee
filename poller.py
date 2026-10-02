"""Poll loop: fetch feeds -> dedupe -> publish to Discord.

Public API:
    poll_once(bot, *, announce=True) -> PollResult
    start_scheduler(bot) -> AsyncIOScheduler
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import db
import runtime as rt
import youtube

log = logging.getLogger("trailer.poller")


@dataclass
class PollResult:
    fetched: int = 0
    new: int = 0
    announced: int = 0
    seeded: int = 0
    errors: list[str] = field(default_factory=list)


async def _seen_id_set() -> set[str]:
    """All video IDs we already know about."""
    async with db.conn().execute("SELECT video_id FROM seen_videos") as cur:
        return {row["video_id"] for row in await cur.fetchall()}


async def _publish(bot: discord.Client, trailer: youtube.Trailer) -> int:
    """Announce one trailer to every configured guild. Returns announce count."""
    from views import build_trailer_embed  # local import: avoids cycle

    guilds = await db.all_guild_configs()
    if not guilds:
        log.debug("No guilds configured; skipping announce for %s", trailer.video_id)
        return 0

    announced = 0
    resolved = None
    for row in guilds:
        guild = bot.get_guild(int(row["guild_id"]))
        if guild is None:
            continue
        channel = guild.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.abc.Messageable):
            log.warning(
                "Guild %s: configured channel %s is not messageable",
                guild.id, row["channel_id"],
            )
            continue

        if resolved is None:
            resolved = await _resolve_mentions(guild, trailer)

        mentions, allowed = resolved if guild.id == int(row["guild_id"]) else (None, None)
        embed = build_trailer_embed(trailer)
        try:
            await channel.send(
                content=mentions if mentions else None,
                embed=embed,
                allowed_mentions=allowed
                or discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            log.warning(
                "Guild %s: missing Send Messages / Embed Links in #%s",
                guild.id, getattr(channel, "name", channel.id),
            )
            continue
        except discord.HTTPException as exc:
            log.error("Guild %s: send failed: %s", guild.id, exc)
            continue

        await db.record_published(trailer.video_id, guild.id, channel.id)
        announced += 1

    return announced


async def _resolve_mentions(
    guild: discord.Guild, trailer: youtube.Trailer
) -> tuple[str | None, discord.AllowedMentions]:
    """Ping only the self-assigned genre roles that match this trailer."""
    mapping = await db.genre_roles(guild.id)
    if not mapping:
        return None, discord.AllowedMentions.none()

    matched: list[discord.Role] = []
    for genre in trailer.genres:
        role_id = mapping.get(genre)
        if not role_id:
            continue
        role = guild.get_role(role_id)
        if role is not None:
            matched.append(role)

    if not matched:
        return None, discord.AllowedMentions.none()

    content = " ".join(r.mention for r in matched)
    allowed = discord.AllowedMentions(roles=matched, everyone=False, users=False)
    return content, allowed


async def poll_once(
    bot: discord.Client,
    *,
    announce: bool = True,
    trigger: str = "scheduled",
) -> PollResult:
    """Run a single poll cycle. Concurrent calls are rejected."""
    if rt.RUNTIME.poll_in_progress:
        log.warning("Poll already in progress; skipping %s trigger", trigger)
        return PollResult()

    rt.RUNTIME.begin_poll(trigger)
    result = PollResult()

    try:
        seen = await _seen_id_set()
        is_cold_start = not seen

        trailers = await youtube.fetch_all(
            config.REGISTRY.studios,
            seen_ids=seen,
            filter_mode=config.FILTER_MODE,
        )
        result.fetched = len(trailers)

        # Newest first so a cold start seeds in reverse-chronological order.
        for trailer in trailers:
            # Cold start: record everything but stay silent, so a fresh deploy or
            # a wiped volume never dumps 30 back-catalog trailers into the channel.
            if is_cold_start and announce and config.SEED_QUIETLY:
                await db.mark_seen(
                    trailer.video_id,
                    trailer.channel_id,
                    trailer.studio,
                    trailer.title,
                    trailer.published_iso,
                )
                result.seeded += 1
                continue

            await db.mark_seen(
                trailer.video_id,
                trailer.channel_id,
                trailer.studio,
                trailer.title,
                trailer.published_iso,
            )
            result.new += 1

            if not announce:
                continue

            try:
                result.announced += await _publish(bot, trailer)
            except Exception as exc:  # noqa: BLE001 - one bad post can't kill the loop
                log.exception("Publish failed for %s", trailer.video_id)
                result.errors.append(f"{trailer.video_id}: {exc}")

    except Exception as exc:  # noqa: BLE001
        rt.RUNTIME.fail_poll(exc)
        log.exception("Poll cycle failed")
        raise
    else:
        rt.RUNTIME.finish_poll(result)

    log.info(
        "Poll complete (%s): %d fetched, %d new, %d announced, %d seeded",
        trigger, result.fetched, result.new, result.announced, result.seeded,
    )
    return result


def start_scheduler(bot: discord.Client) -> AsyncIOScheduler | None:
    """Start the interval poll job. Idempotent — safe to call twice."""
    if rt.RUNTIME.scheduler is not None and rt.RUNTIME.scheduler.running:
        log.warning("Scheduler already running; not starting a second one")
        return rt.RUNTIME.scheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        poll_once,
        "interval",
        minutes=config.POLL_INTERVAL_MINUTES,
        args=[bot],
        kwargs={"announce": True, "trigger": "scheduled"},
        id="poll",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.start()
    rt.RUNTIME.scheduler = scheduler
    log.info("Scheduler started (every %d min)", config.POLL_INTERVAL_MINUTES)
    return scheduler


def stop_scheduler() -> None:
    scheduler = rt.RUNTIME.scheduler
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
    rt.RUNTIME.scheduler = None
