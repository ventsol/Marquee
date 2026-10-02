"""End-to-end tests for the dashboard.

Runs the real FastAPI app against a temp studios.toml and a temp database.
The Discord bot is disabled (WEBAPP_RUN_BOT=false) so nothing connects.

Usage:
    python -m tools.test_webapp
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

os.environ["WEBAPP_PASSWORD"] = "test-secret"
os.environ["WEBAPP_RUN_BOT"] = "false"
os.environ["DATABASE_PATH"] = str(Path(tempfile.gettempdir()) / "Marquee-test.db")

from fastapi.testclient import TestClient  # noqa: E402

import config  # noqa: E402
import config_store as cs  # noqa: E402

FAILURES: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="trailer-webapp-test-"))


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    extra = f" — {detail}" if detail and not condition else ""
    print(f"  [{mark}] {name}{extra}")
    if not condition:
        FAILURES.append(name)


FIXTURE = """\
# Header comment that must survive.

[[studio]]
name = "A24"
channel_id = "UCuPivVjnfNo4mb3Oog_frZg"
genres = ["indie", "horror", "drama"]
region = "en"

[[studio]]
name = "Crunchyroll"
channel_id = "UC6pGDc4bFGD1_36IKv3FnYg"
genres = ["anime", "animation"]
region = "en"
"""


def setup_module_state() -> None:
    """Point the app at a temp studios.toml, db, and backup dir."""
    from starlette.testclient import TestClient as _TC  # noqa: F401

    global STUDIOS, BACKUPS
    STUDIOS = TMP / "studios.toml"
    STUDIOS.write_text(FIXTURE, encoding="utf-8")

    config.STUDIOS_TOML = STUDIOS
    cs.BACKUP_DIR = TMP / "backups"
    config.reload_registry()


def main() -> int:
    setup_module_state()

    import app as webapp  # imported after env is set

    print("webapp end-to-end tests\n")

    with TestClient(webapp.app) as client:
        # ── Auth ─────────────────────────────────────────────────────────
        print("auth")
        r = client.get("/", follow_redirects=False)
        check("unauthenticated / redirects to /login", r.status_code == 302 and "/login" in r.headers.get("location", ""), str(r.status_code))

        r = client.get("/api/status", follow_redirects=False)
        check("unauthenticated /api returns 401 JSON", r.status_code == 401, str(r.status_code))

        r = client.post(
            "/login", data={"password": "wrong", "next": "/"}, follow_redirects=False
        )
        check(
            "wrong password rejected",
            r.status_code == 303 and "error" in r.headers.get("location", ""),
            f"{r.status_code} {r.headers.get('location')}",
        )

        r = client.post(
            "/login",
            data={"password": "test-secret", "next": "/studios"},
            follow_redirects=False,
        )
        check(
            "correct password redirects to next",
            r.status_code == 303 and r.headers.get("location") == "/studios",
            f"{r.status_code} {r.headers.get('location')}",
        )

        r = client.get("/api/status")
        check("authenticated api works", r.status_code == 200)
        check("status has expected keys", {"bot_ready", "studios", "seen_count"} <= set(r.json()))

        # ── Pages render ─────────────────────────────────────────────────
        print("\npages render")
        for path, needle in [
            ("/", "Studio"),
            ("/studios", "A24"),
            ("/studios/new", "Add studio"),
            ("/genres", "Genres"),
            ("/activity", "Activity"),
            ("/backups", "Backups"),
        ]:
            r = client.get(path)
            check(
                f"GET {path}",
                r.status_code == 200 and needle.lower() in r.text.lower(),
                f"status={r.status_code}",
            )

        r = client.get("/studios/UCuPivVjnfNo4mb3Oog_frZg/edit")
        check("edit form prefills name", "A24" in r.text)
        check("edit form checks existing genres", 'value="horror"' in r.text and "checked" in r.text)

        r = client.get("/studios/UCNONEXISTENT000000000000/edit", follow_redirects=False)
        check("editing unknown studio redirects", r.status_code == 303)

        # ── Filters ──────────────────────────────────────────────────────
        print("\nfiltering")
        r = client.get("/studios?genre=anime")
        check("genre filter narrows results", "Crunchyroll" in r.text and "A24" not in r.text)
        r = client.get("/studios?q=a24")
        check("search filter works", "A24" in r.text and "Crunchyroll" not in r.text)
        r = client.get("/studios?region=zz")
        check("no-match filter shows empty state", "No studios match" in r.text)

        # ── Create ───────────────────────────────────────────────────────
        print("\ncreate studio")
        r = client.post(
            "/studios/save",
            data={
                "mode": "new",
                "name": "Mubi",
                "channel_id": "UC1234567890123456789012",
                "region": "uk",
                "genres": ["indie"],
                "custom_genre": "noir, arthouse",
                "original_channel_id": "",
            },
            follow_redirects=False,
        )
        check("create redirects", r.status_code == 303, str(r.status_code))
        rows = cs.studio_dicts(STUDIOS)
        check("studio count grew", len(rows) == 3, str(len(rows)))
        new = next(x for x in rows if x["name"] == "Mubi")
        check("custom genres parsed", new["genres"] == ["arthouse", "indie", "noir"], str(new["genres"]))
        check("region saved", new["region"] == "uk")
        check("registry reloaded in-process", any(s.name == "Mubi" for s in config.REGISTRY))
        check("backup written on create", len(cs.list_backups()) >= 1)
        check("header comment survived", FIXTURE.splitlines()[0] in STUDIOS.read_text(encoding="utf-8"))

        # ── Validation rejects bad input ─────────────────────────────────
        print("\ncreate validation")
        r = client.post(
            "/studios/save",
            data={
                "mode": "new",
                "name": "Bad",
                "channel_id": "tooshort",
                "region": "en",
                "genres": ["drama"],
                "custom_genre": "",
                "original_channel_id": "",
            },
            follow_redirects=False,
        )
        check("bad channel_id re-renders form", r.status_code == 200 and "24 characters" in r.text)
        check("bad channel_id not written", len(cs.studio_dicts(STUDIOS)) == 3)

        r = client.post(
            "/studios/save",
            data={
                "mode": "new",
                "name": "Dup",
                "channel_id": "UCuPivVjnfNo4mb3Oog_frZg",
                "region": "en",
                "genres": ["drama"],
                "custom_genre": "",
                "original_channel_id": "",
            },
            follow_redirects=False,
        )
        check("duplicate channel_id rejected", r.status_code == 200 and "Already used" in r.text)
        check("duplicate not written", len(cs.studio_dicts(STUDIOS)) == 3)

        # ── Edit ─────────────────────────────────────────────────────────
        print("\nedit studio")
        r = client.post(
            "/studios/save",
            data={
                "mode": "edit",
                "name": "A24 Films",
                "channel_id": "UCuPivVjnfNo4mb3Oog_frZg",
                "region": "us",
                "genres": ["indie", "horror"],
                "custom_genre": "",
                "original_channel_id": "UCuPivVjnfNo4mb3Oog_frZg",
            },
            follow_redirects=False,
        )
        check("edit redirects", r.status_code == 303, str(r.status_code))
        rows = cs.studio_dicts(STUDIOS)
        edited = next(x for x in rows if x["channel_id"] == "UCuPivVjnfNo4mb3Oog_frZg")
        check("name updated", edited["name"] == "A24 Films")
        # Genres are normalised to a sorted set by the save route.
        check("genres replaced", sorted(edited["genres"]) == ["horror", "indie"], str(edited["genres"]))
        check("region updated", edited["region"] == "us")
        check("count unchanged by edit", len(rows) == 3, str(len(rows)))

        # ── Delete ───────────────────────────────────────────────────────
        print("\ndelete studio")
        n_before = len(cs.list_backups())
        r = client.post("/studios/UC1234567890123456789012/delete", follow_redirects=False)
        check("delete redirects", r.status_code == 303, str(r.status_code))
        rows = cs.studio_dicts(STUDIOS)
        check("studio removed", len(rows) == 2, str(len(rows)))
        check("correct studio removed", not any(x["name"] == "Mubi" for x in rows))
        check("backup written on delete", len(cs.list_backups()) > n_before)

        r = client.post("/studios/UCgone000000000000000000/delete", follow_redirects=False)
        check("deleting unknown redirects with error", r.status_code == 303 and "error" in r.headers.get("location", ""))

        # ── Restore ──────────────────────────────────────────────────────
        print("\nrestore backup")
        # Pick the backup that still contains the studio we deleted. Same-second
        # backups tie on mtime, so selecting by position is not reliable.
        target = None
        for b in cs.list_backups(limit=50):
            text = (cs.BACKUP_DIR / b["name"]).read_text(encoding="utf-8")
            if "Mubi" in text:
                target = b["name"]
                break
        check("found a backup containing the deleted studio", target is not None)

        r = client.post(
            "/backups/restore", data={"name": target}, follow_redirects=False
        )
        check("restore redirects", r.status_code == 303, str(r.status_code))
        check(
            "restore recovered deleted studio",
            any(x["name"] == "Mubi" for x in cs.studio_dicts(STUDIOS)),
        )

        r = client.post("/backups/restore", data={"name": "nope.toml"}, follow_redirects=False)
        check("restoring missing backup errors gracefully", r.status_code == 303)

        # ── Genres ───────────────────────────────────────────────────────
        print("\ngenres")
        r = client.get("/genres")
        check("genres page lists declared genres", "anime" in r.text and "horror" in r.text)

        # "kaiju" is a BUILT-IN in config.py, so it must be rejected.
        r = client.post(
            "/genres/save",
            data={"genre_key": "kaiju", "label": "Kaiju", "color": "#16A085", "emoji": "x"},
            follow_redirects=False,
        )
        check(
            "built-in genre key is rejected",
            "error" in r.headers.get("location", ""),
            r.headers.get("location", ""),
        )

        # A genuinely new key should be persisted to genre_extras.toml.
        extras_path = TMP / "genre_extras.toml"
        config.GENRE_EXTRAS_PATH = extras_path
        r = client.post(
            "/genres/save",
            data={"genre_key": "noir", "label": "Noir", "color": "#16A085", "emoji": "🕵"},
            follow_redirects=False,
        )
        check("custom genre saved", r.status_code == 303, str(r.status_code))
        check("genre_extras.toml written", extras_path.exists())
        check("genre label applied", config.genre_label("noir") == "Noir")
        check("genre colour applied", config.genre_color("noir") == 0x16A085)

        r = client.post(
            "/genres/save",
            data={"genre_key": "rgb", "label": "RGB", "color": "nothex", "emoji": ""},
            follow_redirects=False,
        )
        check("bad colour rejected", "error" in r.headers.get("location", ""))

        r = client.post("/genres/noir/delete", follow_redirects=False)
        check("custom genre deleted", r.status_code == 303)
        check("genre label falls back after delete", config.genre_label("noir") == "Noir")
        check(
            "extras file no longer lists it",
            "noir" not in extras_path.read_text(encoding="utf-8"),
        )

        # ── API ──────────────────────────────────────────────────────────
        print("\napi")
        r = client.post("/api/studios/test", json={"channel_id": "garbage"})
        check("test endpoint rejects malformed id", r.status_code == 200 and r.json()["ok"] is False)

        r = client.post("/api/refresh")
        check("refresh 409s without a connected bot", r.status_code == 409, str(r.status_code))

        r = client.get("/healthz")
        check("healthz ok", r.status_code == 200)

        r = client.get("/api/status/card")
        check("status card partial renders", r.status_code == 200 and "stat-value" in r.text)

        # ── Logout ───────────────────────────────────────────────────────
        print("\nlogout")
        client.post("/logout", follow_redirects=False)
        r = client.get("/studios", follow_redirects=False)
        check("after logout, pages redirect to login", r.status_code == 302)

    print(f"\n{'=' * 50}")
    shutil.rmtree(TMP, ignore_errors=True)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All webapp checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
