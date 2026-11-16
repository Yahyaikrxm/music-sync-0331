"""Tests for blend_sync.config."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from blend_sync.config import load_config, normalize_spotify_url


class TestConfig(unittest.TestCase):
    def test_normalize_spotify_url_strips_si_param(self) -> None:
        raw = "https://open.spotify.com/playlist/37i9dQZF1EJzoGIrop9Bz9?si=rEwbCsYZTnWH5pZTIyDEIw"
        self.assertEqual(
            normalize_spotify_url(raw),
            "https://open.spotify.com/playlist/37i9dQZF1EJzoGIrop9Bz9",
        )

    def test_load_config_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "SPOTIFY_BLEND_URL=https://open.spotify.com/playlist/abc123?si=xyz",
                        "MY_IDENTIFIERS=xeldev",
                        "PARTNER_IDENTIFIERS=inubjkdj3pq8nlty5hztmoq5l,Lina",
                        "IGNORE_SHARED_SONGS=false",
                        "API_TOKEN=super_secret_random_token_at_least_32_chars",
                    ]
                ),
                encoding="utf-8",
            )
            saved = {
                k: os.environ.pop(k, None)
                for k in (
                    "SPOTIFY_BLEND_URL",
                    "MY_IDENTIFIERS",
                    "PARTNER_IDENTIFIERS",
                    "IGNORE_SHARED_SONGS",
                    "API_TOKEN",
                )
            }
            try:
                cfg = load_config(env_file=env_file, base_dir=root)
                self.assertEqual(cfg.spotify_blend_url, "https://open.spotify.com/playlist/abc123")
                self.assertEqual(cfg.my_identifiers, ("xeldev",))
                self.assertEqual(cfg.partner_identifiers, ("inubjkdj3pq8nlty5hztmoq5l", "Lina"))
                self.assertFalse(cfg.ignore_shared_songs)
                cfg.validate_api_token()
            finally:
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v

    def test_validate_api_token_rejects_placeholder_and_short(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text("API_TOKEN=short_token\n", encoding="utf-8")
            old = os.environ.pop("API_TOKEN", None)
            try:
                cfg = load_config(env_file=env_file, base_dir=root)
                with self.assertRaises(ValueError):
                    cfg.validate_api_token()
            finally:
                if old is not None:
                    os.environ["API_TOKEN"] = old


if __name__ == "__main__":
    unittest.main()
