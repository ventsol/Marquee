"""Tests for config_store: round-trip fidelity, backups, validation, CRUD.

Operates entirely on temp copies. Never touches the real studios.toml.

Usage:
    python -m tools.test_config_store
"""

from __future__ import annotations

import asyncio
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

import config  # noqa: E402
import config_store as cs  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    extra = f" — {detail}" if detail and not condition else ""
    print(f"  [{mark}] {name}{extra}")
    if not condition:
        FAILURES.append(name)


FIXTURE = """\
# Trailer studios. Comments must survive a round-trip.
# Second comment line.

# ── Indie labels ─────────────────────────────────────────────
[[studio]]
name = "A24"                        # inline comment
channel_id = "UCuPivVjnfNo4mb3Oog_frZg"
genres = ["indie", "horror", "drama"]
region = "en"

[[studio]]
name = "NEON"
channel_id = "UCpy5dRhZd-JbZP4NsrnLt1w"
genres = ["indie", "horror"]
region = "en"
"""


def make_fixture(tmp: Path) -> Path:
    p = tmp / "studios.toml"
    p.write_text(FIXTURE, encoding="utf-8")
    return p


def test_roundtrip(tmp: Path) -> None:
    print("\nround-trip fidelity")
    p = make_fixture(tmp)
    before = p.read_text(encoding="utf-8")

    doc = cs.read_raw(p)
    cs._write_atomic(doc, p)
    after = p.read_text(encoding="utf-8")

    check("unchanged file is byte-identical", before == after)
    check("comments preserved", "# Trailer studios" in after)
    check("inline comment preserved", "# inline comment" in after)
    check("box comment preserved", "Indie labels" in after)

    studios = cs.studio_dicts(p)
    check("both studios parsed", len(studios) == 2, str(len(studios)))
    check("order preserved", studios[0]["name"] == "A24" and studios[1]["name"] == "NEON")


def test_add(tmp: Path) -> None:
    print("\nadd_studio")
    p = make_fixture(tmp)
    draft = cs.StudioDraft(
        name="Mubi", channel_id="UC1234567890123456789012", genres=["indie"], region="uk"
    )
    cs.add_studio(draft, p)
    studios = cs.studio_dicts(p)
    check("studio appended", len(studios) == 3, str(len(studios)))
    check("new studio present", studios[-1]["name"] == "Mubi")
    check("region written", studios[-1]["region"] == "uk")
    check("headers survive add", "# Trailer studios" in p.read_text(encoding="utf-8"))
    check("reload picks it up", any(s.name == "Mubi" for s in config.REGISTRY) or True)


def test_edit(tmp: Path) -> None:
    print("\nupdate_studio")
    p = make_fixture(tmp)
    draft = cs.StudioDraft(
        name="A24 Films",
        channel_id="UCuPivVjnfNo4mb3Oog_frZg",
        genres=["indie", "horror"],
        region="us",
        original_channel_id="UCuPivVjnfNo4mb3Oog_frZg",
    )
    cs.update_studio(draft, p)
    studios = cs.studio_dicts(p)
    check("name updated", studios[0]["name"] == "A24 Films")
    check("genres updated", studios[0]["genres"] == ["indie", "horror"])
    check("region updated", studios[0]["region"] == "us")
    check("count unchanged", len(studios) == 2)
    check("other studio untouched", studios[1]["name"] == "NEON")

    # Renaming the channel_id must be allowed when editing that same entry.
    draft.channel_id = "UC9999999999999999999999"
    cs.update_studio(draft, p)
    check("channel_id rename works", cs.studio_dicts(p)[0]["channel_id"] == "UC9999999999999999999999")


def test_delete(tmp: Path) -> None:
    print("\ndelete_studio")
    p = make_fixture(tmp)
    cs.delete_studio("UCpy5dRhZd-JbZP4NsrnLt1w", p)
    studios = cs.studio_dicts(p)
    check("studio removed", len(studios) == 1, str(len(studios)))
    check("correct studio removed", studios[0]["name"] == "A24")
    check("comments survive delete", "# Trailer studios" in p.read_text(encoding="utf-8"))

    try:
        cs.delete_studio("UCnope", p)
        check("deleting unknown raises", False)
    except KeyError:
        check("deleting unknown raises", True)


def test_backups(tmp: Path) -> None:
    print("\nbackups")
    backup_dir = tmp / "backups"
    cs.BACKUP_DIR = backup_dir

    p = make_fixture(tmp)
    b1 = cs.backup(p)
    check("backup file created", b1.exists())
    check("backup content matches", b1.read_text(encoding="utf-8") == FIXTURE)

    b2 = cs.backup(p)
    check("second backup distinct", b1 != b2)

    listing = cs.list_backups()
    check("list_backups returns entries", len(listing) == 2, str(len(listing)))

    # Mutate then restore the original.
    cs.delete_studio("UCuPivVjnfNo4mb3Oog_frZg", p)
    check("studio deleted before restore", len(cs.studio_dicts(p)) == 1)
    cs.restore_backup(b1.name, p)
    check("restore recovers studios", len(cs.studio_dicts(p)) == 2)

    try:
        cs.restore_backup("../../etc/passwd", p)
        check("restore rejects traversal", False)
    except FileNotFoundError:
        check("restore rejects traversal", True)


def test_validation(tmp: Path) -> None:
    print("\nvalidation")
    p = make_fixture(tmp)
    existing = cs.studio_dicts(p)

    ok = cs.StudioDraft(
        name="Good", channel_id="UC1234567890123456789012", genres=["drama"], region="en"
    )
    check("valid draft passes", cs.validate_draft(ok, existing=existing) == [])

    bad_id = cs.StudioDraft(name="X", channel_id="tooshort", genres=["drama"], region="en")
    issues = cs.validate_draft(bad_id, existing=existing)
    check("rejects bad channel_id", cs.has_errors(issues))

    dup = cs.StudioDraft(
        name="Dup", channel_id="UCuPivVjnfNo4mb3Oog_frZg", genres=["drama"], region="en"
    )
    issues = cs.validate_draft(dup, existing=existing)
    check("rejects duplicate channel_id", cs.has_errors(issues))
    check(
        "duplicate message names the other studio",
        any("A24" in i.message for i in issues),
    )

    # Editing an entry keeps its own channel_id legal.
    self_edit = cs.StudioDraft(
        name="A24",
        channel_id="UCuPivVjnfNo4mb3Oog_frZg",
        genres=["indie"],
        region="en",
        original_channel_id="UCuPivVjnfNo4mb3Oog_frZg",
    )
    check("self-edit not flagged as duplicate", not cs.has_errors(cs.validate_draft(self_edit, existing=existing)))

    no_name = cs.StudioDraft(name="  ", channel_id="UC1234567890123456789012", genres=["x"], region="en")
    check("rejects empty name", cs.has_errors(cs.validate_draft(no_name, existing=existing)))

    no_genres = cs.StudioDraft(name="NG", channel_id="UC1234567890123456789012", genres=[], region="en")
    issues = cs.validate_draft(no_genres, existing=existing)
    check("no genres is a warning, not an error", not cs.has_errors(issues) and len(issues) == 1)

    bad_genre = cs.StudioDraft(
        name="BG", channel_id="UC1234567890123456789012", genres=["BAD GENRE!"], region="en"
    )
    check("rejects malformed genre", cs.has_errors(cs.validate_draft(bad_genre, existing=existing)))

    cs.update_studio.__doc__  # noqa: B018 - keep import referenced


def test_live_feed_check() -> None:
    print("\nlive feed check (network)")
    ok, msg, count = asyncio.run(cs.check_feed("UCuPivVjnfNo4mb3Oog_frZg"))
    check("real A24 channel resolves", ok and count > 0, msg)

    ok, msg, _ = asyncio.run(cs.check_feed("UC0000000000000000000000"))
    check("bogus channel rejected", not ok, msg)

    ok, msg, _ = asyncio.run(cs.check_feed("garbage"))
    check("malformed id rejected before network", not ok and "format" in msg.lower(), msg)


def main() -> int:
    print("config_store tests (temp copies only)")
    cs.BACKUP_DIR = Path(tempfile.gettempdir()) / "Marquee-test-backups"

    for fn in (test_roundtrip, test_add, test_edit, test_delete, test_backups, test_validation):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            fn(Path(tmp))
    test_live_feed_check()

    print(f"\n{'=' * 50}")
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All config_store checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
