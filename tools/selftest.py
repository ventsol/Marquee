"""Offline self-test: DB schema, dedupe, filter logic, embed + view building.

No Discord connection and no network required.

Usage:
    python -m tools.selftest
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

import config  # noqa: E402
import db  # noqa: E402
import filters  # noqa: E402
import views  # noqa: E402
from youtube import Trailer  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


# ── Filter logic ─────────────────────────────────────────────────────────────
def test_filters() -> None:
    print("\nfilters.py")
    should_announce = [
        ("Dune: Part Three | Official Trailer", "Warner Bros. Pictures"),
        ("Clarissa - Official Trailer", "NEON"),
        ("Messi and the Giants | Teaser Trailer", "Walt Disney Studios"),
        ("Final Trailer - Sinners", "A24"),
        ("Red Band Trailer | Nosferatu", "Focus Features"),
        ("HIS NEW MOVIE - Official Trailer 2", "Paramount Pictures"),
    ]
    should_skip = [
        ("Batman: Arkham Case Files - Scarecrow | Villain Origin Explained", "DC"),
        ("Multiversal Bake-Off | Marvel Super Heroes: What The--?!", "Marvel Entertainment"),
        ("SURVIVE RANGA PLAYTIME CHALLENGE #shorts", "Crunchyroll"),
        ("BLACK CLOVER CATCH UP WATCH PARTY | LIVE", "Crunchyroll"),
        ("Crossing The Line (2002) | Full Movie", "Lionsgate Movies"),
        ("David Asks Maggie to Sing in the Studio", "Focus Features"),
        ("Behind the Scenes: Props", "A24"),
        ("Now Playing in theaters", "Universal Pictures"),
        ("Episode - 155 | Who killed Asmita?", "Zee Studios"),
    ]

    for title, studio in should_announce:
        check(
            f"announce: {title[:52]}",
            filters.is_trailer(title, studio, mode=filters.MODE_TRAILERS),
        )
    for title, studio in should_skip:
        check(
            f"skip:     {title[:52]}",
            not filters.is_trailer(title, studio, mode=filters.MODE_TRAILERS),
        )

    check("mode=all announces everything", filters.is_trailer("random vlog", "A24", mode=filters.MODE_ALL))
    check(
        "mode=promo allows clips",
        filters.is_trailer("Official Clip | A Bug's Life", "Pixar", mode=filters.MODE_ANY_PROMO),
    )
    check(
        "mode=trailers rejects clips",
        not filters.is_trailer("Official Clip | A Bug's Life", "Pixar", mode=filters.MODE_TRAILERS),
    )
    check("kind trailer", filters.classify_kind("X | Official Trailer") == "trailer")
    check("kind red-band", filters.classify_kind("Red Band Trailer") == "red-band")
    check("kind teaser", filters.classify_kind("Teaser Trailer") == "teaser")


# ── Registry ─────────────────────────────────────────────────────────────────
def test_registry() -> None:
    print("\nconfig.py / studios.toml")
    reg = config.load_registry()
    check("registry not empty", len(reg) > 0, f"{len(reg)} studios")
    check("all channel ids valid format", all(
        s.channel_id.startswith("UC") and len(s.channel_id) == 24 for s in reg
    ))
    check("all studios have genres", all(s.genres for s in reg))
    check("all_genres non-empty", len(reg.all_genres) > 0, str(reg.all_genres))
    check("rss url shape", reg.studios[0].rss_url.startswith("https://www.youtube.com/feeds/videos.xml?channel_id="))
    check("by_genre works", len(reg.by_genre("horror")) > 0)
    check("genre_label known", config.genre_label("sci-fi") == "Sci-Fi")
    check("genre_label unknown titlecases", config.genre_label("kaiju") == "Kaiju")


# ── Embeds + views ───────────────────────────────────────────────────────────
def test_views() -> None:
    print("\nviews.py")
    t = Trailer(
        video_id="dQw4w9WgXcQ",
        channel_id="UCuPivVjnfNo4mb3Oog_frZg",
        studio="A24",
        title="The Brutalist | Official Trailer",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        published_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        thumbnail="https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        genres=("indie", "horror"),
        kind="trailer",
    )
    embed = views.build_trailer_embed(t)
    d = embed.to_dict()
    check("embed has title", bool(d.get("title")))
    check("embed has url", d.get("url") == t.url)
    check("embed has image", d["image"]["url"] == t.thumbnail)
    check("embed colour from primary genre", d.get("color") == config.genre_color("indie"))
    check("embed fields present", len(d.get("fields", [])) >= 2)
    check("embed title <= 256", len(d["title"]) <= 256)

    # Long-title truncation
    long_t = Trailer(**{**t.__dict__, "title": "X" * 500})
    check("long title truncated", len(views.build_trailer_embed(long_t).to_dict()["title"]) <= 256)

    # View construction
    genres = config.REGISTRY.all_genres
    view = views.GenreMenuView(genres)
    check("view is persistent (timeout None)", view.timeout is None)
    check("view respects 25-component limit", len(view.children) <= 25)
    check("buttons have stable custom_id", all(
        getattr(c, "custom_id", "").startswith("trailer:genre:") for c in view.children
    ))

    class _FakeGuild:
        name = "Test Guild"

    menu = views.build_menu_embed(_FakeGuild(), genres[:6]).to_dict()  # type: ignore[arg-type]
    check("menu embed builds", bool(menu.get("title")) and bool(menu.get("description")))
    check("menu embed lists genres", len(menu.get("fields", [])) == 1)


# ── Database ─────────────────────────────────────────────────────────────────
async def test_db() -> None:
    print("\ndb.py")
    # ignore_cleanup_errors: Windows keeps the WAL/-shm handles briefly after
    # close, which would otherwise raise a spurious PermissionError.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        await db.connect(Path(tmp) / "test.db")

        check("seen count starts at 0", await db.count_seen() == 0)

        await db.mark_seen("vid1", "UC1", "A24", "Trailer One", "2026-10-01T00:00:00+00:00")
        check("mark_seen inserts", await db.count_seen() == 1)
        check("is_seen true", await db.is_seen("vid1"))
        check("is_seen false for unknown", not await db.is_seen("nope"))

        await db.mark_seen("vid1", "UC1", "A24", "Trailer One", "2026-10-01T00:00:00+00:00")
        check("dedupe: re-insert ignored", await db.count_seen() == 1)

        await db.set_announce_channel(111, 222)
        check("announce channel set", await db.get_announce_channel(111) == 222)
        await db.set_announce_channel(111, 333)
        check("announce channel upsert", await db.get_announce_channel(111) == 333)
        check("announce channel unset guild", await db.get_announce_channel(999) is None)

        await db.set_genre_role(111, "horror", 444)
        await db.set_genre_role(111, "anime", 555)
        mapping = await db.genre_roles(111)
        check("genre roles stored", mapping == {"horror": 444, "anime": 555}, str(mapping))
        await db.set_genre_role(111, "horror", 666)
        check("genre role upsert", (await db.genre_roles(111))["horror"] == 666)

        await db.add_role_menu(111, 222, 777)
        menus = await db.all_role_menus()
        check("role menu recorded", len(menus) == 1 and menus[0]["message_id"] == 777)
        await db.remove_role_menu(777)
        check("role menu removed", len(await db.all_role_menus()) == 0)

        await db.record_published("vid1", 111, 222)
        check("published recorded", len(await db.all_guild_configs()) == 1)

        await db.clear_genre_roles(111)
        check("genre roles cleared", await db.genre_roles(111) == {})

        recent = await db.recent_seen(limit=5)
        check("recent_seen returns rows", len(recent) == 1)
        check("recent_seen studio filter", len(await db.recent_seen(studio="A24")) == 1)

        await db.close()


# ── Registry validation guards ───────────────────────────────────────────────
def test_registry_guards() -> None:
    print("\nstudio registry validation")
    import tomllib

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "bad.toml"
        p.write_text("[studio]\nname='Bad'\nchannel_id='tooshort'\ngenres=['x']\n", encoding="utf-8")
        try:
            config.load_registry(p)
            check("rejects malformed channel_id", False)
        except ValueError as exc:
            check("rejects malformed channel_id", "suspicious channel_id" in str(exc))

        dup = Path(tmp) / "dup.toml"
        cid = "UCuPivVjnfNo4mb3Oog_frZg"
        dup.write_text(
            f"[[studio]]\nname='A'\nchannel_id='{cid}'\ngenres=['x']\n"
            f"[[studio]]\nname='B'\nchannel_id='{cid}'\ngenres=['y']\n",
            encoding="utf-8",
        )
        try:
            config.load_registry(dup)
            check("rejects duplicate channel_id", False)
        except ValueError as exc:
            check("rejects duplicate channel_id", "duplicate" in str(exc))


def main() -> int:
    print("Marquee self-test (offline)")
    test_filters()
    test_registry()
    test_views()
    test_registry_guards()
    asyncio.run(test_db())

    print(f"\n{'=' * 50}")
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
