-- trailer-bot schema
-- Dedupe: every video we have ever announced (or seeded) lives here.
CREATE TABLE IF NOT EXISTS seen_videos (
    video_id     TEXT PRIMARY KEY,
    channel_id   TEXT NOT NULL,
    studio       TEXT NOT NULL,
    title        TEXT NOT NULL,
    published_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_seen_channel ON seen_videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_seen_published ON seen_videos(published_at);

-- One announcement channel per guild.
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id     INTEGER PRIMARY KEY,
    channel_id   INTEGER NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Genre role menu: persistent button message -> genre role mapping.
CREATE TABLE IF NOT EXISTS genre_roles (
    guild_id   INTEGER NOT NULL,
    genre      TEXT NOT NULL,
    role_id    INTEGER NOT NULL,
    PRIMARY KEY (guild_id, genre)
);

-- Which message hosts the persistent role menu (so we can re-register on boot).
CREATE TABLE IF NOT EXISTS role_menus (
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, message_id)
);

-- Operational log of published trailers (for /latest and debugging).
CREATE TABLE IF NOT EXISTS published (
    video_id   TEXT PRIMARY KEY REFERENCES seen_videos(video_id),
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    posted_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
