"""Read/write access to studios.toml for the web app.

`studios.toml` stays the source of truth so it remains hand-editable and
git-diffable. This module adds:

  * comment- and ordering-preserving edits (tomlkit, not stdlib tomllib)
  * atomic writes (temp file + os.replace) so a crash can't truncate the file
  * a timestamped backup before every mutation
  * validation, including an optional live "does this feed resolve?" check

stdlib `tomllib` can only read; tomlkit is required for round-tripping.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import tomlkit
import tomlkit.items

import config

log = logging.getLogger("trailer.config_store")

BACKUP_DIR = config.ROOT / "backups"
CHANNEL_ID_RE = re.compile(r"^UC[\w-]{22}$")
GENRE_RE = re.compile(r"^[a-z0-9][a-z0-9 +&-]{0,30}$")


@dataclass
class ValidationIssue:
    field: str
    message: str
    severity: str = "error"  # "error" | "warning"


@dataclass
class StudioDraft:
    """A studio as submitted by the web form, before validation."""

    name: str = ""
    channel_id: str = ""
    genres: list[str] = None  # type: ignore[assignment]
    region: str = "en"
    original_channel_id: str | None = None  # set when editing an existing studio

    def __post_init__(self) -> None:
        if self.genres is None:
            self.genres = []


# ── Reading ──────────────────────────────────────────────────────────────────
def read_registry(path: Path | None = None) -> config.Registry:
    """Parse via the existing validated loader."""
    return config.load_registry(path)


def read_raw(path: Path | None = None) -> tomlkit.TOMLDocument:
    src = path or config.STUDIOS_TOML
    return tomlkit.parse(src.read_text(encoding="utf-8"))


def studio_dicts(path: Path | None = None) -> list[dict]:
    """Raw studio entries as plain dicts (for templates and the API)."""
    doc = read_raw(path)
    raw = doc.get("studio", [])
    if isinstance(raw, tomlkit.items.Table):
        raw = [raw]
    out: list[dict] = []
    for item in raw:
        out.append(
            {
                "name": str(item.get("name", "")),
                "channel_id": str(item.get("channel_id", "")),
                "genres": [str(g) for g in item.get("genres", [])],
                "region": str(item.get("region", "en")),
            }
        )
    return out


# ── Validation ───────────────────────────────────────────────────────────────
def validate_draft(
    draft: StudioDraft,
    *,
    existing: list[dict] | None = None,
    path: Path | None = None,
) -> list[ValidationIssue]:
    """Static validation. No network. Returns [] when the draft is clean."""
    issues: list[ValidationIssue] = []
    existing = existing if existing is not None else studio_dicts(path)

    name = (draft.name or "").strip()
    channel_id = (draft.channel_id or "").strip()

    if not name:
        issues.append(ValidationIssue("name", "Studio name is required."))
    elif len(name) > 100:
        issues.append(ValidationIssue("name", "Studio name must be under 100 chars."))

    if not channel_id:
        issues.append(ValidationIssue("channel_id", "Channel ID is required."))
    elif not CHANNEL_ID_RE.match(channel_id):
        issues.append(
            ValidationIssue(
                "channel_id",
                "Channel ID must be 24 characters starting with 'UC' "
                "(e.g. UCuPivVjnfNo4mb3Oog_frZg).",
            )
        )
    else:
        # A channel may only appear once, unless we're editing that same entry.
        for other in existing:
            other_id = other.get("channel_id")
            if other_id == channel_id and other_id != draft.original_channel_id:
                issues.append(
                    ValidationIssue(
                        "channel_id",
                        f"Already used by {other.get('name', 'another studio')!r}.",
                    )
                )
                break

    if not draft.genres:
        issues.append(
            ValidationIssue(
                "genres",
                "Pick at least one genre, or trailers won't ping anyone.",
                severity="warning",
            )
        )
    for genre in draft.genres:
        if not GENRE_RE.match(genre):
            issues.append(
                ValidationIssue(
                    "genres",
                    f"Genre {genre!r} should be lowercase letters, digits, "
                    "spaces, or '-', '_', '&', '+'.",
                )
            )

    if not (draft.region or "").strip():
        issues.append(ValidationIssue("region", "Region is required."))

    return issues


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(i.severity == "error" for i in issues)


async def check_feed(channel_id: str) -> tuple[bool, str, int]:
    """Live check: does this channel ID resolve to a non-empty RSS feed?

    Returns (ok, message, entry_count). Requires network.
    """
    import httpx
    import feedparser

    channel_id = (channel_id or "").strip()
    if not CHANNEL_ID_RE.match(channel_id):
        return False, "Channel ID format is invalid.", 0

    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(config.HTTP_TIMEOUT_SECONDS),
            headers={"User-Agent": "Marquee/1.0 (webapp validation)"},
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        return False, f"Network error: {exc}", 0

    if resp.status_code != 200:
        return False, f"YouTube returned HTTP {resp.status_code}.", 0

    feed = feedparser.parse(resp.content)
    count = len(feed.entries)
    if count == 0:
        return (
            False,
            "Feed is empty. The channel ID may be wrong, or the channel "
            "exposes no uploads.",
            0,
        )
    return True, f"Feed OK \u2014 {count} recent video(s).", count


# ── Backups ──────────────────────────────────────────────────────────────────
def backup(path: Path | None = None) -> Path:
    """Copy studios.toml into backups/ with a UTC timestamp. Returns the path."""
    src = path or config.STUDIOS_TOML
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{src.stem}-{stamp}.toml"
    # Avoid clobbering when two saves land in the same second.
    suffix = 1
    while dest.exists():
        dest = BACKUP_DIR / f"{src.stem}-{stamp}-{suffix}.toml"
        suffix += 1
    shutil.copy2(src, dest)
    log.info("Backed up %s -> %s", src.name, dest.name)
    return dest


def list_backups(limit: int = 20) -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    files = sorted(BACKUP_DIR.glob("*.toml"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "modified": datetime.fromtimestamp(
                p.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }
        for p in files[:limit]
    ]


def restore_backup(name: str, path: Path | None = None) -> Path:
    """Restore a backup over studios.toml, backing up the current file first."""
    dest = path or config.STUDIOS_TOML
    src = BACKUP_DIR / Path(name).name  # basename only: no traversal
    if not src.exists() or src.suffix != ".toml":
        raise FileNotFoundError(f"No such backup: {name}")
    backup(dest)
    shutil.copy2(src, dest)
    config.reload_registry()
    log.info("Restored %s from %s", dest.name, src.name)
    return dest


# ── Writing ──────────────────────────────────────────────────────────────────
def _write_atomic(doc: tomlkit.TOMLDocument, path: Path) -> None:
    text = tomlkit.dumps(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _find_studio(doc: tomlkit.TOMLDocument, channel_id: str):
    raw = doc.get("studio", [])
    if isinstance(raw, tomlkit.items.Table):
        raw = [raw]
    for item in raw:
        if str(item.get("channel_id", "")).strip() == channel_id:
            return item
    return None


def _new_studio_table(draft: StudioDraft) -> tomlkit.items.Table:
    table = tomlkit.table()
    table["name"] = draft.name.strip()
    table["channel_id"] = draft.channel_id.strip()
    table["genres"] = [g.strip().lower() for g in draft.genres]
    table["region"] = (draft.region or "en").strip().lower()
    return table


def add_studio(draft: StudioDraft, path: Path | None = None) -> Path:
    """Append a studio. Backs up first. Tolerates a missing [[studio]] array."""
    dest = path or config.STUDIOS_TOML
    if not dest.exists():
        dest.write_text("", encoding="utf-8")

    doc = read_raw(dest)
    if "studio" not in doc:
        doc["studio"] = tomlkit.aot()

    raw = doc["studio"]
    # A single [[studio]] block parses as a Table, not an AoT; normalise.
    if isinstance(raw, tomlkit.items.Table):
        aot = tomlkit.aot()
        aot.append(raw)
        doc["studio"] = aot
        raw = doc["studio"]

    raw.append(_new_studio_table(draft))  # type: ignore[union-attr]

    backup(dest)
    _write_atomic(doc, dest)
    config.reload_registry()
    log.info("Added studio %r (%s)", draft.name, draft.channel_id)
    return dest


def update_studio(draft: StudioDraft, path: Path | None = None) -> Path:
    """Update a studio, identified by original_channel_id.

    Replaces the whole table in place so key order and surrounding comments
    survive. Changing the channel_id is supported.
    """
    dest = path or config.STUDIOS_TOML
    ident = draft.original_channel_id or draft.channel_id
    doc = read_raw(dest)

    doc_as_plain = studio_plain_list(doc)
    issues = validate_draft(draft, existing=doc_as_plain)
    if has_errors(issues):
        raise ValueError("; ".join(i.message for i in issues if i.severity == "error"))

    raw = doc.get("studio", [])
    if isinstance(raw, tomlkit.items.Table):
        raw = [raw]

    target = None
    for item in raw:
        if str(item.get("channel_id", "")).strip() == ident:
            target = item
            break
    if target is None:
        raise KeyError(f"No studio with channel_id {ident!r}")

    target["name"] = draft.name.strip()
    target["channel_id"] = draft.channel_id.strip()
    target["genres"] = [g.strip().lower() for g in draft.genres]
    target["region"] = (draft.region or "en").strip().lower()

    backup(dest)
    _write_atomic(doc, dest)
    config.reload_registry()
    log.info("Updated studio %r (%s)", draft.name, draft.channel_id)
    return dest


def delete_studio(channel_id: str, path: Path | None = None) -> Path:
    """Remove a studio by channel_id. Backs up first."""
    dest = path or config.STUDIOS_TOML
    doc = read_raw(dest)
    raw = doc.get("studio", [])
    if isinstance(raw, tomlkit.items.Table):
        raw = [raw]

    kept = [i for i in raw if str(i.get("channel_id", "")).strip() != channel_id]
    if len(kept) == len(raw):
        raise KeyError(f"No studio with channel_id {channel_id!r}")

    aot = tomlkit.aot()
    for item in kept:
        aot.append(item)
    doc["studio"] = aot

    backup(dest)
    _write_atomic(doc, dest)
    config.reload_registry()
    log.info("Deleted studio %s", channel_id)
    return dest


def studio_plain_list(doc: tomlkit.TOMLDocument | None = None) -> list[dict]:
    if doc is None:
        return studio_dicts()
    raw = doc.get("studio", [])
    if isinstance(raw, tomlkit.items.Table):
        raw = [raw]
    return [
        {
            "name": str(i.get("name", "")),
            "channel_id": str(i.get("channel_id", "")),
            "genres": [str(g) for g in i.get("genres", [])],
            "region": str(i.get("region", "en")),
        }
        for i in raw
    ]
