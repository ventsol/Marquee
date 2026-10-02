"""Decide whether a feed item is actually a trailer worth announcing.

Studio feeds are noisy: shorts, "now playing" bumpers, full-movie uploads,
watch parties, clips, and behind-the-scenes. We only want trailers/teasers
(plus optionally first-look clips).

Strategy:
  1. Hard-exclude obvious non-trailers (Shorts, full movies, live streams,
     watch parties, recaps, behind-the-scenes, game/music content, etc.)
  2. Require a trailer signal (trailer / teaser / first look / official...),
     unless the studio is trusted enough to post only trailers.
"""

from __future__ import annotations

import re

# ── Hard excludes ────────────────────────────────────────────────────────────
# Each pattern matching the title means "never announce this".
_EXCLUDE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bshorts?\b", re.I),
    re.compile(r"#shorts?\b", re.I),
    re.compile(r"\bfull\s+(?:movie|film|episode)\b", re.I),
    re.compile(r"\bwatch\s+(?:full|now|live)\b", re.I),
    re.compile(r"\bwatch\s+party\b", re.I),
    re.compile(r"\bcatch\s+up\b", re.I),
    re.compile(r"\blive\s*stream\b", re.I),
    re.compile(r"\bpremiere\s+livestream\b", re.I),
    re.compile(r"\bbenind\s+the\s+scenes\b|\bbehind-the-scenes\b", re.I),
    re.compile(r"\bmaking\s+of\b", re.I),
    re.compile(r"\bdeleted\s+scene", re.I),
    re.compile(r"\bbloopers?\b|\bgag\s+reel\b", re.I),
    re.compile(r"\binterview\b", re.I),
    re.compile(r"\bpress\s+conference\b|\bq\s*&\s*a\b", re.I),
    re.compile(r"\bnow\s+playing\b|\bin\s+theaters\s+now\b|\bbuy\s+it\b", re.I),
    re.compile(r"\btickets?\s+(?:on\s+sale|available|now)\b", re.I),
    re.compile(r"\bon\s+digital\b|\bown\s+it\s+(?:now|today)\b", re.I),
    re.compile(r"\bnow\s+streaming\b|\bstreaming\s+(?:now|on)\b", re.I),
    re.compile(r"\bepisode\s*\d+", re.I),
    re.compile(r"\bseason\s*\d+\s*(?:part|episode)", re.I),
    re.compile(r"\brecap\b|\breview\b|\breaction\b", re.I),
    re.compile(r"\bsoundtrack\b|\bost\b|\bmusic\s+video\b|\blyric\b", re.I),
    re.compile(r"\btheme\s+song\b|\baudio\s+launch\b|\bvideo\s+song\b", re.I),
    re.compile(r"\bgameplay\b|\btrailer\s+reaction\b", re.I),
    re.compile(r"^\s*(?:live|replay)\b", re.I),
    re.compile(r"\bpodcast\b", re.I),
    re.compile(r"\bcar\s*['’]?s?\s+script\b", re.I),
    re.compile(r"\bhistorian\b|\barchival?\b", re.I),
    re.compile(r"\bwatch\s+the\s+full\b", re.I),
    # Social/editorial content studios post alongside real trailers.
    re.compile(r"\bepisode\s*\-?\s*\d+", re.I),
    re.compile(r"\bbake[\s-]?off\b", re.I),
    re.compile(r"\bcooking\b|\brecipe\b", re.I),
    re.compile(r"\bcatch\s*up\b|\bexplained\b", re.I),
    re.compile(r"\bvillain\s+origin\b|\borigin\s+story\b", re.I),
    re.compile(r"\bfan\s+(?:art|film|made)\b", re.I),
    re.compile(r"\bwhat\s+the\b", re.I),
    re.compile(r"#\w+", re.I),  # hashtag-only promo captions
]

# ── Trailer signals ──────────────────────────────────────────────────────────
_TRAILER_SIGNALS: list[re.Pattern[str]] = [
    re.compile(r"\btrailer\b", re.I),
    re.compile(r"\bteaser\b", re.I),
    re.compile(r"\bfirst\s+look\b", re.I),
    re.compile(r"\bsneak\s+peek\b", re.I),
    re.compile(r"\bannouncement\s+(?:video|trailer)\b", re.I),
    re.compile(r"\bofficial\s+clip\b", re.I),
    re.compile(r"\bconcept\s+trailer\b", re.I),
    re.compile(r"\b(?:motion|key)\s+poster\b", re.I),
    re.compile(r"\bintro\s+trailer\b", re.I),
]

# Small/festival labels that genuinely post little else besides trailers.
# Deliberately EXCLUDES big channels (Marvel, DC, A24, Universal, ...) because
# they interleave shorts, social clips, and editorial content constantly.
_TRAILER_ONLY_STUDIOS = {
    "NEON",
    "GKIDS Films",
    "Aniplex USA",
    "Focus Features",
    "Sivakarthikeyan Productions",
}

# ── Kind classification ──────────────────────────────────────────────────────
_KIND_ORDER: list[tuple[str, re.Pattern[str]]] = [
    ("red-band", re.compile(r"red[\s-]?band", re.I)),
    ("final", re.compile(r"\bfinal\s+trailer\b", re.I)),
    ("teaser", re.compile(r"\bteaser\b", re.I)),
    ("trailer", re.compile(r"\btrailer\b", re.I)),
    ("first-look", re.compile(r"\bfirst\s+look\b", re.I)),
    ("clip", re.compile(r"\bclip\b", re.I)),
]


def classify_kind(title: str) -> str:
    for kind, pattern in _KIND_ORDER:
        if pattern.search(title):
            return kind
    return "other"


def is_excluded(title: str) -> bool:
    return any(p.search(title) for p in _EXCLUDE_PATTERNS)


def has_trailer_signal(title: str) -> bool:
    return any(p.search(title) for p in _TRAILER_SIGNALS)


# Modes, weakest -> strongest filtering.
MODE_ALL = "all"           # announce every upload (no filtering)
MODE_TRAILERS = "trailers" # trailers + teasers (default, recommended)
MODE_ANY_PROMO = "promo"   # trailers, teasers, first-looks, and clips

VALID_MODES = {MODE_ALL, MODE_TRAILERS, MODE_ANY_PROMO}

# Kinds that count as a trailer/teaser.
_TRAILER_KINDS = {"trailer", "teaser", "final", "red-band", "first-look"}


def is_trailer(
    title: str,
    studio: str,
    *,
    mode: str = MODE_TRAILERS,
) -> bool:
    """True if this feed item should be announced under the given mode."""
    if mode == MODE_ALL:
        return True
    if not title:
        return False
    if is_excluded(title):
        return False

    kind = classify_kind(title)

    if mode == MODE_ANY_PROMO:
        # Anything that looks like promo material, or from a trusted label.
        return has_trailer_signal(title) or studio in _TRAILER_ONLY_STUDIOS

    # MODE_TRAILERS: strict — must actually be a trailer/teaser.
    if kind in _TRAILER_KINDS:
        return True

    # A trusted trailer-only label posting an untitled promo still counts.
    return studio in _TRAILER_ONLY_STUDIOS and has_trailer_signal(title)
