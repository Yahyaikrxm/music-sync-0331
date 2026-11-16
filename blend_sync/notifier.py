"""Optional push notification alerts when Spotify session expires or scraping fails."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from blend_sync.config import Config

logger = logging.getLogger(__name__)


def send_alert(config: Config, title: str, message: str) -> bool:
    """Dispatch an alert to configured notification endpoints (ntfy.sh and/or webhook)."""
    sent_any = False

    if config.ntfy_topic_url:
        try:
            req = urllib.request.Request(
                config.ntfy_topic_url,
                data=message.encode("utf-8"),
                method="POST",
                headers={
                    "Title": title,
                    "Priority": "high",
                    "Tags": "warning,musical_note",
                    "Content-Type": "text/plain; charset=utf-8",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    sent_any = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send ntfy alert: %s", exc)

    if config.alert_webhook_url:
        payload = json.dumps(
            {
                "title": title,
                "content": f"**{title}**\n{message}",
                "text": f"{title}: {message}",
            }
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                config.alert_webhook_url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "spotify-blend-sync/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    sent_any = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send webhook alert: %s", exc)

    return sent_any
