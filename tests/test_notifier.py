"""Tests for blend_sync.notifier."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from blend_sync.config import Config
from blend_sync.notifier import send_alert


def _make_config(ntfy: str = "", webhook: str = "") -> Config:
    return Config(
        project_root=Path("."),
        spotify_blend_url="https://open.spotify.com/playlist/test",
        my_identifiers=("xeldev",),
        partner_identifiers=("Lina",),
        ignore_shared_songs=True,
        storage_state_path=Path("data/storage_state.json"),
        db_path=Path("data/blend_sync.db"),
        headless=True,
        scroll_pause_ms=700,
        max_scroll_steps=10,
        api_host="127.0.0.1",
        api_port=8787,
        api_token="a_very_long_secure_test_token_123456",
        ntfy_topic_url=ntfy,
        alert_webhook_url=webhook,
        alert_cooldown_hours=24,
    )


class TestNotifier(unittest.TestCase):
    def test_send_alert_noop_when_unconfigured(self) -> None:
        self.assertFalse(send_alert(_make_config(), "Title", "Body"))

    @patch("blend_sync.notifier.urllib.request.urlopen")
    def test_send_alert_dispatches_ntfy_and_webhook(self, mock_urlopen: MagicMock) -> None:
        resp = MagicMock()
        resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = resp

        cfg = _make_config(
            ntfy="https://ntfy.sh/example-topic",
            webhook="https://discord.com/api/webhooks/123/abc",
        )
        ok = send_alert(cfg, "Expired", "Please re-login")
        self.assertTrue(ok)
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("blend_sync.notifier.urllib.request.urlopen", side_effect=OSError("network down"))
    def test_send_alert_handles_network_error_gracefully(self, _mock: MagicMock) -> None:
        cfg = _make_config(ntfy="https://ntfy.sh/example-topic")
        self.assertFalse(send_alert(cfg, "Expired", "Please re-login"))


if __name__ == "__main__":
    unittest.main()
