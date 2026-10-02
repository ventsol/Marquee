"""Configuration loaded from environment (.env) plus the studios registry.

Mirrors the xembed-bot convention: a plain module of constants, loaded once.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ── Discord ──────────────────────────────────────────────────────────────────
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "").strip()
DEV_GUILD_IDS: list[int] = [
    int(g)
    for g in os.getenv("DEV_GUILD_IDS", "").replace(" ", "").split(",")
    if g.strip().isdigit()
]

# ── Runtime ──────────────────────────────────────────────────────────────────
POLL_INTERVAL_MINUTES: int = _env_int("POLL_INTERVAL_MINUTES", 15)
SEED_QUIETLY: bool = _env_bool("SEED_QUIETLY", True)

# How aggressively to filter studio uploads (see filters.py):
#   trailers -> trailers + teasers only  (default, recommended)
#   promo    -> also first-looks and official clips
#   all      -> announce every upload
FILTER_MODE: str = os.getenv("FILTER_MODE", "trailers").strip().lower()
DATABASE_PATH: Path = ROOT / os.getenv("DATABASE_PATH", "data/trailer.db")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper().strip()
HTTP_TIMEOUT_SECONDS: int = _env_int("HTTP_TIMEOUT_SECONDS", 15)
HTTP_CONCURRENCY: int = _env_int("HTTP_CONCURRENCY", 8)

STUDIOS_TOML: Path = ROOT / "studios.toml"
SCHEMA_SQL: Path = ROOT / "schema.sql"

# ── Web app ──────────────────────────────────────────────────────────────────
# Required for the dashboard. Without it app.py refuses to start, because the
# dashboard can rewrite studios.toml and delete studios.
WEBAPP_PASSWORD: str = os.getenv("WEBAPP_PASSWORD", "").strip()

# Railway (and most PaaS) inject PORT and expect the app to bind 0.0.0.0.
# A runtime-provided PORT always implies "I'm in a container, bind publicly",
# so it overrides the .env default rather than relying on WEBAPP_HOST being
# absent. Local runs keep the loopback default.
_railway_port = os.getenv("PORT", "").strip()
_in_container = bool(_railway_port)

if _in_container:
    WEBAPP_HOST: str = os.getenv("RAILWAY_BIND_HOST", "0.0.0.0").strip()
    WEBAPP_PORT: int = int(_railway_port)
else:
    WEBAPP_HOST = os.getenv("WEBAPP_HOST", "127.0.0.1").strip()
    WEBAPP_PORT = _env_int("WEBAPP_PORT", 8000)

# Run the Discord bot inside the web app process. Set false to serve the
# dashboard only (useful while the standalone bot is running elsewhere).
WEBAPP_RUN_BOT: bool = _env_bool("WEBAPP_RUN_BOT", True)


# ── Genre taxonomy ───────────────────────────────────────────────────────────
# Canonical genre keys -> display label + role colour.
# Anything a studio declares in studios.toml that is NOT listed here still works;
# it just falls back to a default colour and a title-cased label.
GENRES: dict[str, dict[str, object]] = {
    "horror":      {"label": "Horror",      "color": 0x8B0000, "emoji": "\U0001F480"},
    "thriller":    {"label": "Thriller",    "color": 0x4B0082, "emoji": "\U0001F52A"},
    "action":      {"label": "Action",      "color": 0xE74C3C, "emoji": "\U0001F4A5"},
    "sci-fi":      {"label": "Sci-Fi",      "color": 0x1ABC9C, "emoji": "\U0001F680"},
    "drama":       {"label": "Drama",       "color": 0x95A5A6, "emoji": "\U0001F3AD"},
    "comedy":      {"label": "Comedy",      "color": 0xF1C40F, "emoji": "\U0001F602"},
    "romance":     {"label": "Romance",     "color": 0xE91E63, "emoji": "\U0001F495"},
    "indie":       {"label": "Indie",       "color": 0x7F8C8D, "emoji": "\U0001F3AC"},
    "animation":   {"label": "Animation",   "color": 0x9B59B6, "emoji": "\U0001F3A8"},
    "anime":       {"label": "Anime",       "color": 0xE056A0, "emoji": "\U0001F338"},
    "documentary": {"label": "Documentary", "color": 0x27AE60, "emoji": "\U0001F30D"},
    "family":      {"label": "Family",      "color": 0x3498DB, "emoji": "\U0001F46A"},
    "kaiju":       {"label": "Kaiju",       "color": 0x16A085, "emoji": "\U0001F409"},
    "bollywood":   {"label": "Bollywood",   "color": 0xFF9933, "emoji": "\U0001F3B5"},
    "tamil":       {"label": "Tamil",       "color": 0xFF4500, "emoji": "\U0001F3AF"},
    "telugu":      {"label": "Telugu",      "color": 0xFFD700, "emoji": "\U0001F3C6"},
    "malayalam":   {"label": "Malayalam",   "color": 0x00B894, "emoji": "\U0001F30A"},
}

DEFAULT_GENRE_COLOR = 0x5865F2  # Discord blurple


# ── Studio registry ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Studio:
    name: str
    channel_id: str
    genres: tuple[str, ...] = ()
    region: str = "en"

    @property
    def rss_url(self) -> str:
        return (
            "https://www.youtube.com/feeds/videos.xml"
            f"?channel_id={self.channel_id}"
        )


@dataclass
class Registry:
    studios: list[Studio] = field(default_factory=list)

    def __iter__(self):
        return iter(self.studios)

    def __len__(self) -> int:
        return len(self.studios)

    @property
    def channel_ids(self) -> list[str]:
        return [s.channel_id for s in self.studios]

    def by_channel(self, channel_id: str) -> Studio | None:
        for s in self.studios:
            if s.channel_id == channel_id:
                return s
        return None

    def by_genre(self, genre: str) -> list[Studio]:
        key = genre.strip().lower()
        return [s for s in self.studios if key in s.genres]

    @property
    def all_genres(self) -> list[str]:
        """Genres declared in studios.toml, canonical order first."""
        declared: set[str] = set()
        for s in self.studios:
            declared.update(s.genres)
        ordered = [g for g in GENRES if g in declared]
        ordered += sorted(
            g for g in declared if g not in GENRES and g not in GENRE_EXTRAS
        )
        return ordered


def genre_label(genre: str) -> str:
    meta = _genre_meta(genre)
    if meta:
        return str(meta["label"])
    return genre.replace("-", " ").title()


def genre_color(genre: str) -> int:
    meta = _genre_meta(genre)
    if meta:
        return int(meta["color"])  # type: ignore[arg-type]
    return DEFAULT_GENRE_COLOR


def genre_emoji(genre: str) -> str:
    meta = _genre_meta(genre)
    if meta:
        return str(meta["emoji"])
    return "\U0001F3AC"


def load_registry(path: Path | None = None) -> Registry:
    """Parse studios.toml into a Registry. Raises on malformed entries."""
    src = path or STUDIOS_TOML
    with src.open("rb") as fh:
        data = tomllib.load(fh)

    # A single [[studio]] block parses as a dict, not a list — normalize so a
    # one-studio file doesn't crash with a confusing TypeError.
    raw_studios = data.get("studio", [])
    if isinstance(raw_studios, dict):
        raw_studios = [raw_studios]
    if not isinstance(raw_studios, list):
        raise ValueError(f"{src.name}: 'studio' must be an array of tables")

    studios: list[Studio] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_studios):
        if not isinstance(raw, dict):
            raise ValueError(f"{src.name}: [[studio]] #{i} is not a table")
        try:
            name = str(raw["name"]).strip()
            channel_id = str(raw["channel_id"]).strip()
        except KeyError as exc:
            raise ValueError(f"{src.name}: [[studio]] #{i} missing {exc}") from exc

        if not channel_id.startswith("UC") or len(channel_id) != 24:
            raise ValueError(
                f"{src.name}: studio {name!r} has a suspicious channel_id "
                f"{channel_id!r} (expected 24 chars starting with 'UC')"
            )
        if channel_id in seen:
            raise ValueError(f"{src.name}: duplicate channel_id for {name!r}")
        seen.add(channel_id)

        studios.append(
            Studio(
                name=name,
                channel_id=channel_id,
                genres=tuple(
                    str(g).strip().lower() for g in raw.get("genres", [])
                ),
                region=str(raw.get("region", "en")).strip().lower(),
            )
        )

    return Registry(studios=studios)


def reload_registry(path: Path | None = None) -> Registry:
    """Re-read studios.toml into the module-level REGISTRY.

    Callers must read `config.REGISTRY` through the module attribute rather
    than binding it to a local at import time, or they'll hold a stale object.
    """
    global REGISTRY
    REGISTRY = load_registry(path)
    return REGISTRY


# Genres declared in studios.toml that have no entry in GENRES above. These are
# writable to `genre_extras.toml` by the web app, keeping this Python file
# generated-by-hand only.
GENRE_EXTRAS_PATH: Path = ROOT / "genre_extras.toml"


def _load_genre_extras(path: Path | None = None) -> dict[str, dict[str, object]]:
    """Merge hand-written genre definitions from genre_extras.toml, if present."""
    src = path or GENRE_EXTRAS_PATH
    if not src.exists():
        return {}
    try:
        with src.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logging.getLogger("trailer.config").warning(
            "Ignoring unreadable %s: %s", src.name, exc
        )
        return {}

    out: dict[str, dict[str, object]] = {}
    for key, value in (data.get("genre") or {}).items():
        if not isinstance(value, dict):
            continue
        out[str(key)] = {
            "label": str(value.get("label", str(key).replace("-", " ").title())),
            "color": int(value.get("color", DEFAULT_GENRE_COLOR)),
            "emoji": str(value.get("emoji", "\U0001F3AC")),
        }
    return out


GENRE_EXTRAS: dict[str, dict[str, object]] = _load_genre_extras()


def reload_genre_extras(path: Path | None = None) -> dict[str, dict[str, object]]:
    global GENRE_EXTRAS
    GENRE_EXTRAS = _load_genre_extras(path)
    return GENRE_EXTRAS


def _genre_meta(genre: str) -> dict[str, object] | None:
    return GENRES.get(genre) or GENRE_EXTRAS.get(genre)


def all_genre_definitions() -> dict[str, dict[str, object]]:
    """Every genre the app knows about: built-ins plus extras."""
    merged = dict(GENRE_EXTRAS)
    merged.update(GENRES)  # built-ins win on conflict
    return merged


# Loaded at import time; reload via reload_registry() after edits.
REGISTRY: Registry = load_registry()
