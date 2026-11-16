"""Unit tests for blend_sync.scraper orchestration and filtering."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blend_sync.config import Config
from blend_sync.db import SyncDatabase
from blend_sync.parser import RawTrackRow
from blend_sync.scraper import SessionExpiredError, run_scrape


def _config(root: Path, ignore_shared: bool = True, ntfy: str = "") -> Config:
    return Config(
        project_root=root,
        spotify_blend_url="https://open.spotify.com/playlist/37i9dQZF1EJzoGIrop9Bz9",
        my_identifiers=("xeldev",),
        partner_identifiers=("inubjkdj3pq8nlty5hztmoq5l", "Lina"),
        ignore_shared_songs=ignore_shared,
        storage_state_path=root / "storage_state.json",
        db_path=root / "blend_sync.db",
        headless=True,
        scroll_pause_ms=700,
        max_scroll_steps=10,
        api_host="127.0.0.1",
        api_port=8787,
        api_token="test_bearer_token_with_plenty_of_entropy_987654321",
        ntfy_topic_url=ntfy,
        alert_webhook_url="",
        alert_cooldown_hours=24,
    )


SAMPLE_ROWS = [
    RawTrackRow("/track/t1", "Partner Song", ("Artist 1",), "Alb1", ("/user/inubjkdj3pq8nlty5hztmoq5l",), ("Lina",)),
    RawTrackRow("/track/t2", "My Old Song", ("Artist 2",), "Alb2", ("/user/xeldev",), ("xeldev",)),
    RawTrackRow(
        "/track/t3",
        "Shared Song",
        ("Artist 3",),
        "Alb3",
        ("/user/inubjkdj3pq8nlty5hztmoq5l", "/user/xeldev"),
        ("Lina and xeldev",),
    ),
]


class TestScraperOrchestration(unittest.TestCase):
    @patch("blend_sync.scraper.fetch_blend_rows", return_value=SAMPLE_ROWS)
    def test_run_scrape_ignores_shared_by_default(self, _mock_fetch) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp), ignore_shared=True)
            summary = run_scrape(cfg)
            self.assertEqual(summary.outcome, "ok")
            self.assertEqual(summary.total_rows, 3)
            self.assertEqual(summary.eligible_rows, 1)
            self.assertEqual(summary.newly_queued, 1)
            self.assertEqual(summary.skipped_mine, 1)
            self.assertEqual(summary.skipped_shared, 1)

            db = SyncDatabase(cfg.db_path)
            pending = db.get_pending_tracks()
            self.assertEqual([p["title"] for p in pending], ["Partner Song"])

    @patch("blend_sync.scraper.fetch_blend_rows", return_value=SAMPLE_ROWS)
    def test_run_scrape_can_include_shared_songs(self, _mock_fetch) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp), ignore_shared=False)
            summary = run_scrape(cfg)
            self.assertEqual(summary.outcome, "ok")
            self.assertEqual(summary.eligible_rows, 2)
            self.assertEqual(summary.skipped_mine, 1)
            self.assertEqual(summary.skipped_shared, 0)

            db = SyncDatabase(cfg.db_path)
            pending = db.get_pending_tracks()
            self.assertEqual([p["title"] for p in pending], ["Partner Song", "Shared Song"])

    @patch("blend_sync.scraper.send_alert", return_value=True)
    @patch("blend_sync.scraper.fetch_blend_rows", side_effect=SessionExpiredError("Login required"))
    def test_run_scrape_handles_session_expired_with_cooldown(self, _mock_fetch, mock_alert) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp), ntfy="https://ntfy.sh/test-topic")
            s1 = run_scrape(cfg)
            self.assertEqual(s1.outcome, "session_expired")
            self.assertEqual(mock_alert.call_count, 1)

            # Second run within cooldown does not spam alerts
            s2 = run_scrape(cfg)
            self.assertEqual(s2.outcome, "session_expired")
            self.assertEqual(mock_alert.call_count, 1)


if __name__ == "__main__":
    unittest.main()
