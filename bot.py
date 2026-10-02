"""
Marquee — standalone Discord bot entry point.

Announces new movie trailers from official studio YouTube channels,
pinging self-assigned genre roles.

The bot itself lives in `botcore.py` so the web app (`app.py`) can run it in
the same event loop. This file is only the standalone launcher.

    python bot.py          # bot + poller only  (no dashboard)
    python app.py          # bot + poller + web dashboard
"""

from __future__ import annotations

import asyncio
import logging

import botcore
import config
import lockfile
import runtime as rt

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("trailer")


def main() -> None:
    if not config.DISCORD_TOKEN:
        log.critical("DISCORD_TOKEN not set. Copy .env.example to .env and fill it in.")
        return
    if not config.REGISTRY:
        log.critical("studios.toml has no [[studio]] entries. Nothing to watch.")
        return

    if not lockfile.acquire("bot"):
        log.critical(
            "Refusing to start: another Marquee process is running. "
            "Stop it first, or use app.py for the dashboard."
        )
        return

    log.info(
        "Starting Marquee: %d studios, %d genres, poll every %d min",
        len(config.REGISTRY),
        len(config.REGISTRY.all_genres),
        config.POLL_INTERVAL_MINUTES,
    )

    bot = botcore.create_bot()

    async def runner() -> None:
        try:
            await botcore.start_bot(bot, config.DISCORD_TOKEN)
        finally:
            if rt.RUNTIME.scheduler is not None:
                rt.RUNTIME.scheduler.shutdown(wait=False)
            await botcore.stop_bot(bot)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        log.info("Shutting down (interrupt)")


if __name__ == "__main__":
    main()
