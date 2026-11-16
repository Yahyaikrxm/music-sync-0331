"""Configuration loader and validator for spotify-blend-sync."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parse_bool(value: str, default: bool) -> bool:
    if not value:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def normalize_spotify_url(url: str) -> str:
    """Strip tracking query parameters like ?si=... from a Spotify playlist URL."""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid SPOTIFY_BLEND_URL: {url!r}")
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def load_dotenv(env_path: Path) -> None:
    """Load key=value pairs from a .env file into os.environ without overriding existing vars."""
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


def _resolve_path(raw_path: str, base_dir: Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else (base_dir / path).resolve()


@dataclass(frozen=True)
class Config:
    """Runtime configuration for scraper, alerts, and API server."""

    project_root: Path
    spotify_blend_url: str
    my_identifiers: tuple[str, ...]
    partner_identifiers: tuple[str, ...]
    ignore_shared_songs: bool
    storage_state_path: Path
    db_path: Path
    headless: bool
    scroll_pause_ms: int
    max_scroll_steps: int
    api_host: str
    api_port: int
    api_token: str
    ntfy_topic_url: str
    alert_webhook_url: str
    alert_cooldown_hours: int

    def validate_api_token(self) -> None:
        """Ensure API_TOKEN is present and strong before starting the HTTP server."""
        if (
            not self.api_token
            or self.api_token.startswith("CHANGE_ME")
            or len(self.api_token) < 24
        ):
            raise ValueError(
                "API_TOKEN must be set to a random secret of at least 24 characters. "
                "Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )


def load_config(env_file: Path | None = None, base_dir: Path | None = None) -> Config:
    """Load configuration from .env (if present) and environment variables."""
    root = (base_dir or PROJECT_ROOT).resolve()
    load_dotenv(env_file or (root / ".env"))

    blend_url_raw = os.environ.get(
        "SPOTIFY_BLEND_URL",
        "https://open.spotify.com/playlist/37i9dQZF1EJzoGIrop9Bz9",
    )
    my_ids = _parse_csv(os.environ.get("MY_IDENTIFIERS", "xeldev"))
    partner_ids = _parse_csv(
        os.environ.get("PARTNER_IDENTIFIERS", "inubjkdj3pq8nlty5hztmoq5l,Lina")
    )
    if not my_ids:
        raise ValueError("MY_IDENTIFIERS must contain at least one username or display name.")
    if not partner_ids:
        raise ValueError("PARTNER_IDENTIFIERS must contain at least one user ID or display name.")

    return Config(
        project_root=root,
        spotify_blend_url=normalize_spotify_url(blend_url_raw),
        my_identifiers=my_ids,
        partner_identifiers=partner_ids,
        ignore_shared_songs=_parse_bool(os.environ.get("IGNORE_SHARED_SONGS", "true"), True),
        storage_state_path=_resolve_path(
            os.environ.get("STORAGE_STATE_PATH", "data/storage_state.json"), root
        ),
        db_path=_resolve_path(os.environ.get("DB_PATH", "data/blend_sync.db"), root),
        headless=_parse_bool(os.environ.get("HEADLESS", "true"), True),
        scroll_pause_ms=max(200, int(os.environ.get("SCROLL_PAUSE_MS", "700"))),
        max_scroll_steps=max(5, int(os.environ.get("MAX_SCROLL_STEPS", "60"))),
        api_host=os.environ.get("API_HOST", "127.0.0.1").strip() or "127.0.0.1",
        api_port=int(os.environ.get("API_PORT", "8787")),
        api_token=os.environ.get("API_TOKEN", "").strip(),
        ntfy_topic_url=os.environ.get("NTFY_TOPIC_URL", "").strip(),
        alert_webhook_url=os.environ.get("ALERT_WEBHOOK_URL", "").strip(),
        alert_cooldown_hours=max(1, int(os.environ.get("ALERT_COOLDOWN_HOURS", "24"))),
    )
