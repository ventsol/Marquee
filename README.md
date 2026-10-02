# 🎬 Marquee

Discord bot that announces **new movie trailers** from official studio YouTube
channels. Trailers post to one channel per server; members opt in to **genre
roles** via a button menu so they're only pinged for the genres they care about.

Ships with a **web dashboard** for customizing studios and genres without
hand-editing config files.

> Project folder is still `Whale-Project\trailer-bot`; only the product name changed.

**Discord display name:** `Marquee`

- **No API key, no quota.** Uses YouTube's free per-channel RSS feeds.
- **Customizable by studio and genre** — via the dashboard or `studios.toml`.
- **Noise-filtered** — ignores Shorts, full movies, watch parties, "now playing"
  bumpers, BTS, music videos, and recap/editorial content.

---

## Quick start

```powershell
cd Whale-Project\trailer-bot
Copy-Item .env.example .env
# edit .env and set DISCORD_TOKEN and WEBAPP_PASSWORD
.\start_webapp.bat          # dashboard + bot + poller (recommended)
# or
.\start_bot.bat             # bot + poller only, no dashboard
```

Both launchers create the venv and install deps on first run.

> **Run one or the other, not both.** Each starts a poller; two pollers means
> two announcement passes. A lockfile makes the second process refuse to start,
> but only if it's on the same machine and directory.

### Web dashboard

`http://127.0.0.1:8000` — sign in with `WEBAPP_PASSWORD`.

| Page | What it does |
|---|---|
| **Dashboard** | Live bot/poller status, stats, "Poll now", "Check all feeds" |
| **Studios** | Search/filter by genre and region; add, edit, delete; per-feed health check |
| **Genres** | Customize label, colour, and emoji for custom genres |
| **Activity** | Recently ingested trailers with thumbnails |
| **Backups** | Browse and restore previous `studios.toml` versions |

Safety rails:

- **`WEBAPP_PASSWORD` is required.** The dashboard can rewrite `studios.toml`
  and delete studios, so `app.py` refuses to start without it.
- **Every mutation writes a timestamped backup** to `backups/` first, then
  writes atomically (temp file + `os.replace`), so a crash can't truncate the file.
- **Comments and formatting in `studios.toml` survive edits** — it round-trips
  byte-for-byte when nothing changed (verified in tests).
- **Binds to `127.0.0.1`** by default and rejects unauthenticated API calls with
  a 401 rather than redirecting.

Set `WEBAPP_RUN_BOT=false` to serve the dashboard without running the bot in
that process (useful when `bot.py` is already running elsewhere).

### Discord setup

1. Create an app at <https://discord.com/developers/applications> → **Bot** → copy the token.
2. Under **Privileged Gateway Intents**, enable **Message Content Intent**
   and **Server Members Intent**.
3. Invite it with scopes `bot` + `applications.commands` and permissions:
   **Manage Roles**, **Send Messages**, **Embed Links**.
4. Put a genre role *below* the bot's highest role, or the bot can't assign it.

### In-server setup

```
/setup channel:#trailers     # where trailers get posted (admin)
/genres                      # creates genre roles + posts the button menu
```

Members then click buttons to toggle genres. `/unsubscribe` clears them all.

---

## Commands

| Command | Who | What |
|---|---|---|
| `/setup channel:` | Manage Server | Set the announcement channel |
| `/genres` | Manage Roles | Create genre roles + post the toggle menu |
| `/latest [count] [genre]` | everyone | Show recent trailers the bot has seen |
| `/studios [genre]` | everyone | List watched studios and their genres |
| `/refresh` | Manage Server | Poll every feed immediately |
| `/unsubscribe` | everyone | Remove all your genre roles |
| `/status` | everyone | Health, stats, scheduler state |

---

## Adding a studio

Add a block to `studios.toml`:

```toml
[[studio]]
name = "Mubi"
channel_id = "UCxxxxxxxxxxxxxxxxxxxxxx"
genres = ["indie", "drama"]
region = "en"
```

**Finding the channel ID:** open the channel page → *View Source* → search
`externalId`. It's the `UC…` value (24 chars).

Then verify every ID actually resolves to a live feed:

```powershell
.\.venv\Scripts\python.exe -m tools.validate_studios
```

A channel can exist but expose an **empty** feed (some topic/auto-generated
channels do). The validator catches that — it requires at least one entry.

### Adding a genre

Add it to `GENRES` in `config.py` to control its label, colour, and emoji.
Genres used in `studios.toml` without an entry here still work — they just get a
title-cased label and the default Discord colour.

---

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `DISCORD_TOKEN` | — | **Required.** Bot token. |
| `DEV_GUILD_IDS` | *(blank)* | Comma-separated guild IDs for instant command sync |
| `POLL_INTERVAL_MINUTES` | `15` | How often to poll all feeds |
| `SEED_QUIETLY` | `true` | On a fresh DB, record the backlog without announcing it |
| `FILTER_MODE` | `trailers` | `trailers`, `promo`, or `all` — see below |
| `DATABASE_PATH` | `data/trailer.db` | SQLite location |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `HTTP_TIMEOUT_SECONDS` | `15` | Per-feed timeout |
| `HTTP_CONCURRENCY` | `8` | Max parallel feed fetches |
| `WEBAPP_PASSWORD` | — | **Required by `app.py`.** Dashboard password. |
| `WEBAPP_HOST` | `127.0.0.1` | Dashboard bind address |
| `WEBAPP_PORT` | `8000` | Dashboard port |
| `WEBAPP_RUN_BOT` | `true` | Run the bot inside the dashboard process |

### Filter modes

Measured against the 31 seeded studios on a single day:

| Mode | Items | What passes |
|---|---|---|
| `trailers` *(default)* | ~42 | trailers + teasers only |
| `promo` | ~85 | also first-looks and official clips |
| `all` | ~409 | every upload, unfiltered |

`promo` is a reasonable choice if you also want clips. Start with `trailers`.

---

## Tools

```powershell
# Offline: filters, registry, embeds, views, and the DB
.\.venv\Scripts\python.exe -m tools.selftest

# Offline: studios.toml round-trip fidelity, backups, validation, CRUD
.\.venv\Scripts\python.exe -m tools.test_config_store

# Offline: dashboard routes, auth, CRUD, backups, genres (bot disabled)
.\.venv\Scripts\python.exe -m tools.test_webapp

# Live: does every channel_id resolve to a non-empty feed?
.\.venv\Scripts\python.exe -m tools.validate_studios

# Live: what would be announced right now (writes nothing)
.\.venv\Scripts\python.exe -m tools.dry_run --mode trailers --limit 20
```

`dry_run` is the fastest way to sanity-check a new studio — if its items never
show up, the filter is rejecting them or the feed is empty.

---

## How it works

```
studios.toml ──► youtube.fetch_all()  (bounded-concurrency httpx, feedparser)
                     │
                     ├─ filters.is_trailer()   drop shorts/bumpers/clips
                     ├─ dedupe vs seen_videos  (SQLite)
                     └─ poller._publish()
                            ├─ one embed to each guild's announce channel
                            └─ @mention only matching self-assigned genre roles
```

- **Genre attribution is studio-declared**, not guessed from the title. A trailer
  inherits the genres on its studio's registry entry, which keeps it predictable
  and easy to tune.
- **Cold start is silent.** If the database is empty, the existing backlog is
  recorded but not announced, so a first run (or a wiped hosting volume) never
  spams the channel.
- **Routing is per-guild.** Every guild picks its own channel; genre roles are
  per-guild too, so one bot serves many servers with different tastes.

### Files

| File | Role |
|---|---|
| `bot.py` | Standalone bot launcher |
| `botcore.py` | Client construction, events, slash commands (shared) |
| `app.py` | FastAPI dashboard + embedded bot/poller |
| `runtime.py` | Process-wide state (bot, scheduler, last poll) |
| `config.py` | Env + `studios.toml` loading, genre taxonomy |
| `config_store.py` | Comment-preserving `studios.toml` read/write + backups |
| `genre_store.py` | Custom genres -> `genre_extras.toml` |
| `lockfile.py` | Single-instance guard |
| `youtube.py` | RSS fetch and parsing |
| `filters.py` | Trailer/noise decision logic |
| `poller.py` | Poll cycle, dedupe, publish |
| `views.py` | Embeds and the persistent genre menu |
| `db.py` / `schema.sql` | SQLite storage |
| `webapp/auth.py` | Password auth + middleware |
| `templates/`, `static/` | Dashboard UI |
| `tools/` | selftest, config/webapp tests, validator, dry run |

---

## Hosting

The scheduler needs a **long-running process** — it will not work on a
cold-start serverless platform.

The dashboard is an **admin tool**: it can rewrite config and delete studios.
If you expose it beyond localhost, put it behind HTTPS and a real reverse proxy,
and use a long random `WEBAPP_PASSWORD`. Prefer running it locally and running
only `bot.py` on the server, with `WEBAPP_RUN_BOT=false` when you do open the
dashboard remotely.

- **Railway / Fly / a VPS / a home box** are all fine.
- Docker/Railway: set `DISCORD_TOKEN` (and `DEV_GUILD_IDS` while testing) as env vars.
- Mount a volume for `data/` if the host has ephemeral disk. If the DB is lost,
  `SEED_QUIETLY=true` means the bot re-seeds silently instead of re-announcing
  everything — so losing the volume is safe, just briefly quiet.

---

## License

MIT
