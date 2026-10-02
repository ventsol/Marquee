"""Write custom genre definitions to genre_extras.toml.

Deliberately NOT editing config.py. Python source is a bad thing for a web form
to rewrite; a sibling TOML file is data, is safe to regenerate wholesale, and
keeps the built-in GENRES table code-owned. Built-ins win on conflict.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import tomlkit

import config

log = logging.getLogger("trailer.genre_store")


def _doc() -> tomlkit.TOMLDocument:
    path = config.GENRE_EXTRAS_PATH
    if not path.exists():
        doc = tomlkit.document()
        doc.add(tomlkit.comment("Custom genre definitions written by the dashboard."))
        doc.add(tomlkit.comment("Built-in genres live in config.py and win on conflict."))
        doc.add(tomlkit.nl())
        doc["genre"] = tomlkit.table()
        return doc
    return tomlkit.parse(path.read_text(encoding="utf-8"))


def _write(doc: tomlkit.TOMLDocument, path: Path | None = None) -> Path:
    dest = path or config.GENRE_EXTRAS_PATH
    text = tomlkit.dumps(doc)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    config.reload_genre_extras()
    return dest


def set_genre(
    key: str,
    *,
    label: str,
    color: int,
    emoji: str,
    path: Path | None = None,
) -> Path:
    """Create or update a custom genre. Built-in keys are rejected."""
    key = key.strip().lower()
    if key in config.GENRES:
        raise ValueError(f"{key!r} is a built-in genre defined in config.py")

    doc = _doc()
    if "genre" not in doc:
        doc["genre"] = tomlkit.table()
    table = doc["genre"]

    entry = tomlkit.table()
    entry["label"] = label
    entry["color"] = int(color)
    entry["emoji"] = emoji
    table[key] = entry

    log.info("Saved custom genre %r", key)
    return _write(doc, path)


def delete_genre(key: str, path: Path | None = None) -> Path:
    key = key.strip().lower()
    doc = _doc()
    table = doc.get("genre") or {}
    if key not in table:
        raise KeyError(key)
    del table[key]
    log.info("Deleted custom genre %r", key)
    return _write(doc, path)
