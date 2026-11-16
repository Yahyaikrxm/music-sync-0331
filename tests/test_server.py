"""Integration tests for blend_sync.server."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from blend_sync.config import Config
from blend_sync.db import SyncDatabase
from blend_sync.parser import RawTrackRow, parse_track_row
from blend_sync.server import create_server


TOKEN = "test_bearer_token_with_plenty_of_entropy_987654321"


class TestServer(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.config = Config(
            project_root=root,
            spotify_blend_url="https://open.spotify.com/playlist/test",
            my_identifiers=("xeldev",),
            partner_identifiers=("inubjkdj3pq8nlty5hztmoq5l", "Lina"),
            ignore_shared_songs=True,
            storage_state_path=root / "storage_state.json",
            db_path=root / "blend_sync.db",
            headless=True,
            scroll_pause_ms=700,
            max_scroll_steps=10,
            api_host="127.0.0.1",
            api_port=0,
            api_token=TOKEN,
            ntfy_topic_url="",
            alert_webhook_url="",
            alert_cooldown_hours=24,
        )
        self.db = SyncDatabase(self.config.db_path)
        self.server = create_server(self.config)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self._tmp.cleanup()

    def _request(
        self,
        path: str,
        method: str = "GET",
        token: str | None = None,
        body: dict | None = None,
    ) -> tuple[int, dict, dict]:
        headers = {}
        data = None
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            method=method,
            headers=headers,
            data=data,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            try:
                return err.code, dict(err.headers), json.loads(err.read().decode("utf-8"))
            finally:
                err.close()

    def test_health_and_auth_guards(self) -> None:
        status, headers, payload = self._request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")

        unauth_status, _, _ = self._request("/songs")
        self.assertEqual(unauth_status, 401)

        bad_status, _, _ = self._request("/songs", token="wrong-token")
        self.assertEqual(bad_status, 401)

    def test_songs_fetch_and_ack_lifecycle(self) -> None:
        raw = RawTrackRow(
            track_url="/track/xyz789",
            title="Espresso",
            artists=("Sabrina Carpenter",),
            album="Short n' Sweet",
            contributor_hrefs=("/user/inubjkdj3pq8nlty5hztmoq5l",),
            contributor_labels=("Lina",),
        )
        parsed = parse_track_row(raw, self.config.my_identifiers, self.config.partner_identifiers)
        assert parsed is not None
        self.db.upsert_eligible_tracks([parsed], now_iso="2026-11-16T09:00:00+00:00")
        self.db.record_run(
            started_at="2026-11-16T09:00:00+00:00",
            finished_at="2026-11-16T09:00:10+00:00",
            outcome="ok",
            total_rows=10,
            eligible_rows=1,
            newly_queued=1,
        )

        status, _, payload = self._request("/songs", token=TOKEN)
        self.assertEqual(status, 200)
        self.assertFalse(payload["session_expired"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["batch_ids"], ["spotify:xyz789"])
        self.assertEqual(payload["songs"][0]["query"], "Espresso Sabrina Carpenter")

        ack_status, _, ack_payload = self._request(
            "/songs/ack",
            method="POST",
            token=TOKEN,
            body={"ids": payload["batch_ids"]},
        )
        self.assertEqual(ack_status, 200)
        self.assertEqual(ack_payload["acknowledged"], 1)
        self.assertEqual(ack_payload["remaining_pending"], 0)

        status2, _, payload2 = self._request("/songs", token=TOKEN)
        self.assertEqual(status2, 200)
        self.assertEqual(payload2["count"], 0)

    def test_session_expired_surfaced_to_shortcut_and_rate_limit(self) -> None:
        self.db.record_run(
            started_at="2026-11-16T10:00:00+00:00",
            finished_at="2026-11-16T10:00:02+00:00",
            outcome="session_expired",
            error_message="Cookies expired",
        )
        status, _, payload = self._request("/songs", token=TOKEN)
        self.assertEqual(status, 200)
        self.assertTrue(payload["session_expired"])
        self.assertIn("mac_login.py", payload["alert"])

        # Trigger rate limit on repeated invalid attempts
        for _ in range(15):
            code, _, _ = self._request("/status", token="bad")
            self.assertEqual(code, 401)
        blocked_code, _, _ = self._request("/status", token="bad")
        self.assertEqual(blocked_code, 429)


if __name__ == "__main__":
    unittest.main()
