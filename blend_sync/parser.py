"""Pure-Python track parsing, contributor attribution, and filtering logic."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse


_TRACK_ID_RE = re.compile(r"/track/([A-Za-z0-9]+)")
_USER_ID_RE = re.compile(r"/user/([^/?#]+)")
_REMASTER_SUFFIX_RE = re.compile(
    r"\s*(?:-\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?|\((?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?\))\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RawTrackRow:
    """Raw track row data extracted from the Spotify web player DOM."""

    track_url: str
    title: str
    artists: tuple[str, ...]
    album: str
    contributor_hrefs: tuple[str, ...]
    contributor_labels: tuple[str, ...]


@dataclass(frozen=True)
class ParsedTrack:
    """Normalized track with attribution classification."""

    track_key: str
    spotify_track_id: str | None
    title: str
    clean_title: str
    primary_artist: str
    artists: tuple[str, ...]
    album: str
    apple_music_query: str
    matched_me: bool
    matched_partner: bool
    contributors_summary: str

    @property
    def is_shared(self) -> bool:
        """True if attributed to both the user and their partner."""
        return self.matched_me and self.matched_partner

    def should_include(self, ignore_shared_songs: bool) -> bool:
        """Return True when the track belongs in the Apple Music sync queue."""
        if not self.matched_partner:
            return False
        if self.matched_me and ignore_shared_songs:
            return False
        return True


def extract_track_id(track_url: str) -> str | None:
    """Extract the Spotify track ID from a /track/<id> URL or path."""
    if not track_url:
        return None
    match = _TRACK_ID_RE.search(urlparse(track_url).path)
    return match.group(1) if match else None


def extract_user_id(user_href: str) -> str | None:
    """Extract the Spotify user ID from a /user/<id> URL or path."""
    if not user_href:
        return None
    match = _USER_ID_RE.search(urlparse(user_href).path)
    return match.group(1) if match else None


def clean_title_for_search(title: str) -> str:
    """Strip noisy remaster tags that can hurt cross-platform search matching."""
    cleaned = _REMASTER_SUFFIX_RE.sub("", title.strip()).strip()
    return cleaned or title.strip()


def make_track_key(spotify_track_id: str | None, title: str, artists: tuple[str, ...]) -> str:
    """Return a stable unique key for deduplication."""
    if spotify_track_id:
        return f"spotify:{spotify_track_id}"
    normalized = f"{','.join(a.strip().lower() for a in artists)}::{title.strip().lower()}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return f"hash:{digest}"


def _identifier_matches(identifier: str, user_ids: set[str], labels: tuple[str, ...]) -> bool:
    ident_lower = identifier.strip().lower()
    if not ident_lower:
        return False
    if ident_lower in user_ids:
        return True
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(ident_lower)}(?![A-Za-z0-9_])")
    for label in labels:
        if pattern.search(label.lower()):
            return True
    return False


def classify_contributors(
    contributor_hrefs: tuple[str, ...],
    contributor_labels: tuple[str, ...],
    my_identifiers: tuple[str, ...],
    partner_identifiers: tuple[str, ...],
) -> tuple[bool, bool]:
    """Return (matched_me, matched_partner) from a track row's contributor signals."""
    user_ids = {
        uid.lower()
        for href in contributor_hrefs
        if (uid := extract_user_id(href)) is not None
    }
    matched_me = any(_identifier_matches(ident, user_ids, contributor_labels) for ident in my_identifiers)
    matched_partner = any(
        _identifier_matches(ident, user_ids, contributor_labels) for ident in partner_identifiers
    )
    return matched_me, matched_partner


def parse_track_row(
    row: RawTrackRow,
    my_identifiers: tuple[str, ...],
    partner_identifiers: tuple[str, ...],
) -> ParsedTrack | None:
    """Convert a RawTrackRow into a ParsedTrack, or None if missing essential fields."""
    title = row.title.strip()
    artists = tuple(a.strip() for a in row.artists if a.strip())
    if not title or not artists:
        return None

    spotify_track_id = extract_track_id(row.track_url)
    clean_title = clean_title_for_search(title)
    primary_artist = artists[0]
    query = f"{clean_title} {primary_artist}".strip()

    matched_me, matched_partner = classify_contributors(
        row.contributor_hrefs,
        row.contributor_labels,
        my_identifiers,
        partner_identifiers,
    )

    if matched_me and matched_partner:
        summary = "both"
    elif matched_partner:
        summary = "partner"
    elif matched_me:
        summary = "me"
    else:
        summary = "unknown"

    return ParsedTrack(
        track_key=make_track_key(spotify_track_id, title, artists),
        spotify_track_id=spotify_track_id,
        title=title,
        clean_title=clean_title,
        primary_artist=primary_artist,
        artists=artists,
        album=row.album.strip(),
        apple_music_query=query,
        matched_me=matched_me,
        matched_partner=matched_partner,
        contributors_summary=summary,
    )
