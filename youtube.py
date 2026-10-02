"""YouTube channel feed ingestion via the free per-channel RSS feed.

No API key, no quota. https://www.youtube.com/feeds/videos.xml?channel_id=UC...
Gives us: video id, title, published timestamp, channel title, media thumbnail.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import httpx

import config
import filters

log = logging.getLogger("trailer.youtube")

# Matching on the media:title field is more reliable than entry.title for
# YouTube's RSS, but entry.title is usually cleaner for display.
_PAREN_JUNK = re.compile(r"\s*[\(\[](?:official\s+)?(?:hd|4k|uhd)[\)\]]\s*", re.I)


@dataclass(frozen=True)
class Trailer:
    video_id: str
    channel_id: str
    studio: str
    title: str
    url: str
    published_at: datetime
    thumbnail: str | None
    genres: tuple[str, ...]
    kind: str

    @property
    def published_iso(self) -> str:
        return self.published_at.astimezone(timezone.utc).isoformat()


def clean_title(raw: str) -> str:
    title = html.unescape(raw or "").strip()
    title = _PAREN_JUNK.sub(" ", title)
    title = re.sub(r"\s{2,}", " ", title)
    return title.strip(" -–—|")


def _parse_published(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            # feedparser gives a UTC struct_time.
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _parse_entry(entry, studio: config.Studio) -> Trailer | None:
    video_id = entry.get("yt_videoid") or entry.get("id", "").split(":")[-1]
    if not video_id:
        return None

    link = entry.get("link") or f"https://www.youtube.com/watch?v={video_id}"
    title = clean_title(entry.get("title", ""))

    thumbnail = None
    thumbs = entry.get("media_thumbnail") or []
    if thumbs:
        thumbnail = thumbs[0].get("url")

    return Trailer(
        video_id=video_id,
        channel_id=studio.channel_id,
        studio=studio.name,
        title=title or f"Untitled video ({video_id})",
        url=link,
        published_at=_parse_published(entry),
        thumbnail=thumbnail,
        genres=studio.genres,
        kind=filters.classify_kind(entry.get("title", "")),
    )


async def fetch_studio(
    client: httpx.AsyncClient,
    studio: config.Studio,
    *,
    seen_ids: set[str],
    filter_mode: str = filters.MODE_TRAILERS,
) -> list[Trailer]:
    """Fetch one studio feed, returning new items that pass the trailer filter."""
    try:
        resp = await client.get(studio.rss_url)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.warning(
            "%s (%s): HTTP %s from RSS feed",
            studio.name, studio.channel_id, exc.response.status_code,
        )
        return []
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        log.warning("%s (%s): feed fetch failed: %s", studio.name, studio.channel_id, exc)
        return []

    feed = feedparser.parse(resp.content)
    if getattr(feed, "bozo", 0) and not feed.entries:
        log.warning("%s: malformed or empty feed", studio.name)
        return []

    found: list[Trailer] = []
    skipped = 0
    for entry in feed.entries:
        trailer = _parse_entry(entry, studio)
        if trailer is None or trailer.video_id in seen_ids:
            continue
        if not filters.is_trailer(entry.get("title", ""), studio.name, mode=filter_mode):
            skipped += 1
            continue
        found.append(trailer)

    log.debug(
        "%s: %d new, %d filtered out of %d entries",
        studio.name, len(found), skipped, len(feed.entries),
    )
    return found


async def fetch_all(
    studios: list[config.Studio],
    *,
    seen_ids: set[str],
    timeout: int | None = None,
    concurrency: int | None = None,
    filter_mode: str = filters.MODE_TRAILERS,
) -> list[Trailer]:
    """Fetch every studio feed with bounded concurrency. Flattens results."""
    if not studios:
        return []

    limit = concurrency or config.HTTP_CONCURRENCY
    sem = asyncio.Semaphore(limit)
    timeout_cfg = httpx.Timeout(timeout or config.HTTP_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": (
            "Marquee/1.0 (+https://github.com/; Discord trailer notifier)"
        ),
        "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    }

    async with httpx.AsyncClient(
        timeout=timeout_cfg, headers=headers, follow_redirects=True
    ) as client:

        async def worker(studio: config.Studio) -> list[Trailer]:
            async with sem:
                return await fetch_studio(
                    client, studio, seen_ids=seen_ids, filter_mode=filter_mode
                )

        results = await asyncio.gather(
            *(worker(s) for s in studios), return_exceptions=True
        )

    trailers: list[Trailer] = []
    for studio, result in zip(studios, results):
        if isinstance(result, BaseException):
            log.error("%s: worker crashed: %s", studio.name, result)
            continue
        trailers.extend(result)

    trailers.sort(key=lambda t: t.published_at, reverse=True)
    return trailers


async def validate_registry(registry: config.Registry) -> dict[str, bool]:
    """One-shot health check: does each channel_id actually resolve to a feed?"""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(config.HTTP_TIMEOUT_SECONDS),
        headers={"User-Agent": "Marquee/1.0 (validation)"},
        follow_redirects=True,
    ) as client:

        async def check(studio: config.Studio) -> tuple[str, bool]:
            try:
                resp = await client.get(studio.rss_url)
                if resp.status_code != 200:
                    return studio.name, False
                feed = feedparser.parse(resp.content)
                return studio.name, bool(feed.entries)
            except Exception:  # noqa: BLE001 - validation shouldn't raise
                return studio.name, False

        results = await asyncio.gather(*(check(s) for s in registry))
    return dict(results)
