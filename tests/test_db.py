"""Tests for blend_sync.db."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from blend_sync.db import SyncDatabase
from blend_sync.parser import RawTrackRow, parse_track_row


def _make_track(track_id: str, title: str, artist: str):
    raw = RawTrackRow(
        track_url=f"/track/{track_id}",
        title=title,
        artists=(artist,),
        album="Album",
        contributor_hrefs=("/user/inubjkdj3pq8nlty5hztmoq5l",),
        contributor_labels=("Lina",),
    )
    parsed = parse_track_row(raw, ("xeldev",), ("inubjkdj3pq8nlty5hztmoq5l", "Lina"))
    assert parsed is not None
    return parsed


class TestDatabase(unittest.TestCase):
    def test_upsert_ack_and_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = SyncDatabase(Path(tmp) / "test.db")
            t1 = _make_track("aaa", "Song One", "Artist A")
            t2 = _make_track("bbb", "Song Two", "Artist B")

            newly = db.upsert_eligible_tracks([t1, t2], now_iso="2026-11-16T09:00:00+00:00")
            self.assertEqual(newly, 2)
            pending = db.get_pending_tracks()
            self.assertEqual([p["id"] for p in pending], ["spotify:aaa", "spotify:bbb"])

            acked = db.ack_tracks(track_keys=["spotify:aaa"], now_iso="2026-11-16T09:05:00+00:00")
            self.assertEqual(acked, 1)
            self.assertEqual([p["id"] for p in db.get_pending_tracks()], ["spotify:bbb"])

            # Re-scraping t1 and t2 + new t3 must NOT re-queue t1!
            t3 = _make_track("ccc", "Song Three", "Artist C")
            newly2 = db.upsert_eligible_tracks([t1, t2, t3], now_iso="2026-11-23T09:00:00+00:00")
            self.assertEqual(newly2, 1)
            self.assertEqual([p["id"] for p in db.get_pending_tracks()], ["spotify:bbb", "spotify:ccc"])

            acked_all = db.ack_tracks(ack_all=True)
            self.assertEqual(acked_all, 2)
            self.assertEqual(db.get_pending_tracks(), [])

    def test_run_tracking_and_alert_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = SyncDatabase(Path(tmp) / "test.db")
            t0 = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)

            db.record_run(
                started_at=t0.isoformat(),
                finished_at=t0.isoformat(),
                outcome="session_expired",
                error_message="Login required",
            )
            status = db.get_status()
            self.assertTrue(status["session_expired"])
            self.assertFalse(status["session_valid"])

            self.assertTrue(db.should_send_alert(24, now=t0))
            db.mark_alert_sent(t0.isoformat())
            self.assertFalse(db.should_send_alert(24, now=t0 + timedelta(hours=6)))
            self.assertTrue(db.should_send_alert(24, now=t0 + timedelta(hours=25)))

            # Subsequent successful run clears expiry and cooldown
            t1 = t0 + timedelta(hours=26)
            db.record_run(
                started_at=t1.isoformat(),
                finished_at=t1.isoformat(),
                outcome="ok",
                total_rows=50,
                eligible_rows=20,
                newly_queued=3,
            )
            status2 = db.get_status()
            self.assertFalse(status2["session_expired"])
            self.assertTrue(status2["session_valid"])


if __name__ == "__main__":
    unittest.main()
