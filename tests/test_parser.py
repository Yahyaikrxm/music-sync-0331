"""Tests for blend_sync.parser."""

from __future__ import annotations

import unittest

from blend_sync.parser import (
    RawTrackRow,
    clean_title_for_search,
    extract_track_id,
    extract_user_id,
    parse_track_row,
)


MY_IDS = ("xeldev",)
PARTNER_IDS = ("inubjkdj3pq8nlty5hztmoq5l", "Lina")


class TestParser(unittest.TestCase):
    def test_extract_ids(self) -> None:
        self.assertEqual(extract_track_id("/track/4uLU6hMCjMI75M1A2tKUQC?si=123"), "4uLU6hMCjMI75M1A2tKUQC")
        self.assertEqual(
            extract_user_id("https://open.spotify.com/user/inubjkdj3pq8nlty5hztmoq5l?si=abc"),
            "inubjkdj3pq8nlty5hztmoq5l",
        )

    def test_clean_title_preserves_remix_strips_remaster(self) -> None:
        self.assertEqual(clean_title_for_search("Dreams - 2004 Remaster"), "Dreams")
        self.assertEqual(clean_title_for_search("Dreams (Remastered 2018)"), "Dreams")
        self.assertEqual(clean_title_for_search("Latch (Acoustic)"), "Latch (Acoustic)")
        self.assertEqual(clean_title_for_search("Midnight City (Eric Prydz Remix)"), "Midnight City (Eric Prydz Remix)")

    def test_partner_only_by_display_name(self) -> None:
        row = RawTrackRow(
            track_url="/track/track111",
            title="Cruel Summer",
            artists=("Taylor Swift",),
            album="Lover",
            contributor_hrefs=(),
            contributor_labels=("Lina",),
        )
        parsed = parse_track_row(row, MY_IDS, PARTNER_IDS)
        assert parsed is not None
        self.assertTrue(parsed.matched_partner)
        self.assertFalse(parsed.matched_me)
        self.assertEqual(parsed.contributors_summary, "partner")
        self.assertTrue(parsed.should_include(ignore_shared_songs=True))
        self.assertTrue(parsed.should_include(ignore_shared_songs=False))

    def test_partner_only_by_user_id_href(self) -> None:
        row = RawTrackRow(
            track_url="/track/track222",
            title="Pink + White",
            artists=("Frank Ocean",),
            album="Blonde",
            contributor_hrefs=("/user/inubjkdj3pq8nlty5hztmoq5l",),
            contributor_labels=(),
        )
        parsed = parse_track_row(row, MY_IDS, PARTNER_IDS)
        assert parsed is not None
        self.assertTrue(parsed.matched_partner)
        self.assertFalse(parsed.matched_me)
        self.assertTrue(parsed.should_include(ignore_shared_songs=True))

    def test_my_song_skipped(self) -> None:
        row = RawTrackRow(
            track_url="/track/track333",
            title="Nights",
            artists=("Frank Ocean",),
            album="Blonde",
            contributor_hrefs=("/user/xeldev",),
            contributor_labels=("xeldev",),
        )
        parsed = parse_track_row(row, MY_IDS, PARTNER_IDS)
        assert parsed is not None
        self.assertTrue(parsed.matched_me)
        self.assertFalse(parsed.matched_partner)
        self.assertFalse(parsed.should_include(ignore_shared_songs=True))
        self.assertFalse(parsed.should_include(ignore_shared_songs=False))

    def test_shared_song_respects_ignore_shared_option(self) -> None:
        row = RawTrackRow(
            track_url="/track/track444",
            title="Kill Bill",
            artists=("SZA",),
            album="SOS",
            contributor_hrefs=("/user/inubjkdj3pq8nlty5hztmoq5l", "/user/xeldev"),
            contributor_labels=("Lina and xeldev",),
        )
        parsed = parse_track_row(row, MY_IDS, PARTNER_IDS)
        assert parsed is not None
        self.assertTrue(parsed.is_shared)
        self.assertEqual(parsed.contributors_summary, "both")
        self.assertFalse(parsed.should_include(ignore_shared_songs=True))
        self.assertTrue(parsed.should_include(ignore_shared_songs=False))

    def test_word_boundary_prevents_partial_name_match(self) -> None:
        row = RawTrackRow(
            track_url="/track/track555",
            title="Some Song",
            artists=("Some Artist",),
            album="Album",
            contributor_hrefs=(),
            contributor_labels=("Alina",),
        )
        parsed = parse_track_row(row, MY_IDS, PARTNER_IDS)
        assert parsed is not None
        self.assertFalse(parsed.matched_partner)
        self.assertEqual(parsed.contributors_summary, "unknown")
        self.assertFalse(parsed.should_include(ignore_shared_songs=False))


if __name__ == "__main__":
    unittest.main()
