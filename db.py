"""Async SQLite access layer. One shared connection, serialized by aiosqlite."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

import config

log = logging.getLogger("trailer.db")

_conn: aiosqlite.Connection | None = None


async def connect(path: Path | None = None) -> aiosqlite.Connection:
    """Open (once) the SQLite connection and apply schema.sql."""
    global _conn
    if _conn is not None:
        return _conn

    db_path = path or config.DATABASE_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")

    schema = config.SCHEMA_SQL.read_text(encoding="utf-8")
    await conn.executescript(schema)
    await conn.commit()

    _conn = conn
    log.info("SQLite ready at %s", db_path)
    return conn


async def close() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


def conn() -> aiosqlite.Connection:
    if _conn is None:
        raise RuntimeError("db.connect() must be awaited before use")
    return _conn


# ── seen_videos ──────────────────────────────────────────────────────────────
async def is_seen(video_id: str) -> bool:
    async with conn().execute(
        "SELECT 1 FROM seen_videos WHERE video_id = ?", (video_id,)
    ) as cur:
        return await cur.fetchone() is not None


async def mark_seen(
    video_id: str,
    channel_id: str,
    studio: str,
    title: str,
    published_at: str,
) -> None:
    await conn().execute(
        """
        INSERT OR IGNORE INTO seen_videos
            (video_id, channel_id, studio, title, published_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (video_id, channel_id, studio, title, published_at),
    )
    await conn().commit()


async def count_seen() -> int:
    async with conn().execute("SELECT COUNT(*) AS n FROM seen_videos") as cur:
        row = await cur.fetchone()
        return int(row["n"]) if row else 0


async def recent_seen(limit: int = 5, studio: str | None = None) -> list[aiosqlite.Row]:
    sql = "SELECT * FROM seen_videos"
    params: list[object] = []
    if studio:
        sql += " WHERE lower(studio) = lower(?)"
        params.append(studio)
    sql += " ORDER BY published_at DESC LIMIT ?"
    params.append(limit)
    async with conn().execute(sql, params) as cur:
        return list(await cur.fetchall())


# ── guild_config ─────────────────────────────────────────────────────────────
async def set_announce_channel(guild_id: int, channel_id: int) -> None:
    await conn().execute(
        """
        INSERT INTO guild_config (guild_id, channel_id, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(guild_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            updated_at = datetime('now')
        """,
        (guild_id, channel_id),
    )
    await conn().commit()


async def get_announce_channel(guild_id: int) -> int | None:
    async with conn().execute(
        "SELECT channel_id FROM guild_config WHERE guild_id = ?", (guild_id,)
    ) as cur:
        row = await cur.fetchone()
        return int(row["channel_id"]) if row else None


async def all_guild_configs() -> list[aiosqlite.Row]:
    async with conn().execute("SELECT * FROM guild_config") as cur:
        return list(await cur.fetchall())


# ── genre_roles ──────────────────────────────────────────────────────────────
async def set_genre_role(guild_id: int, genre: str, role_id: int) -> None:
    await conn().execute(
        """
        INSERT INTO genre_roles (guild_id, genre, role_id)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, genre) DO UPDATE SET role_id = excluded.role_id
        """,
        (guild_id, genre, role_id),
    )
    await conn().commit()


async def genre_roles(guild_id: int) -> dict[str, int]:
    async with conn().execute(
        "SELECT genre, role_id FROM genre_roles WHERE guild_id = ?", (guild_id,)
    ) as cur:
        return {row["genre"]: int(row["role_id"]) for row in await cur.fetchall()}


async def clear_genre_roles(guild_id: int) -> None:
    await conn().execute("DELETE FROM genre_roles WHERE guild_id = ?", (guild_id,))
    await conn().commit()


# ── role_menus ───────────────────────────────────────────────────────────────
async def add_role_menu(guild_id: int, channel_id: int, message_id: int) -> None:
    await conn().execute(
        """
        INSERT OR REPLACE INTO role_menus (guild_id, channel_id, message_id)
        VALUES (?, ?, ?)
        """,
        (guild_id, channel_id, message_id),
    )
    await conn().commit()


async def remove_role_menu(message_id: int) -> None:
    await conn().execute("DELETE FROM role_menus WHERE message_id = ?", (message_id,))
    await conn().commit()


async def all_role_menus() -> list[aiosqlite.Row]:
    async with conn().execute("SELECT * FROM role_menus") as cur:
        return list(await cur.fetchall())


# ── published ────────────────────────────────────────────────────────────────
async def record_published(video_id: str, guild_id: int, channel_id: int) -> None:
    await conn().execute(
        """
        INSERT OR REPLACE INTO published (video_id, guild_id, channel_id)
        VALUES (?, ?, ?)
        """,
        (video_id, guild_id, channel_id),
    )
    await conn().commit()
