"""Secure, zero-dependency HTTP API server for Apple Shortcuts integration."""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from blend_sync.config import Config, load_config
from blend_sync.db import SyncDatabase

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 64 * 1024
_AUTH_FAIL_LIMIT = 15
_AUTH_FAIL_WINDOW_SECS = 60.0
_MAX_TRACKED_IPS = 1024


class AuthRateLimiter:
    """In-memory sliding-window rate limiter for failed authentication attempts."""

    def __init__(
        self,
        limit: int = _AUTH_FAIL_LIMIT,
        window_seconds: float = _AUTH_FAIL_WINDOW_SECS,
        max_ips: int = _MAX_TRACKED_IPS,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_ips = max_ips
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, dq: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()

    def is_blocked(self, client_ip: str) -> bool:
        """Return True if the IP has exceeded allowed auth failures in the window."""
        now = time.monotonic()
        with self._lock:
            dq = self._failures.get(client_ip)
            if dq is None:
                return False
            self._prune(dq, now)
            if not dq:
                self._failures.pop(client_ip, None)
                return False
            return len(dq) >= self.limit

    def record_failure(self, client_ip: str) -> None:
        """Record a failed auth attempt for client_ip."""
        now = time.monotonic()
        with self._lock:
            if client_ip not in self._failures and len(self._failures) >= self.max_ips:
                oldest_ip = next(iter(self._failures))
                self._failures.pop(oldest_ip, None)
            dq = self._failures[client_ip]
            self._prune(dq, now)
            dq.append(now)

    def clear(self, client_ip: str) -> None:
        """Clear failures after a valid request."""
        with self._lock:
            self._failures.pop(client_ip, None)


class BlendHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer holding shared config, DB wrapper, and rate limiter."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        config: Config,
        db: SyncDatabase,
    ) -> None:
        self.config = config
        self.db = db
        self.rate_limiter = AuthRateLimiter()
        super().__init__(server_address, BlendRequestHandler)


class BlendRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler exposing /health, /songs, /songs/ack, and /status."""

    server: BlendHTTPServer

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        logger.info("%s - %s", self._client_ip(), format % args)

    def _client_ip(self) -> str:
        peer_ip = self.client_address[0]
        if peer_ip in {"127.0.0.1", "::1"}:
            forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            if forwarded:
                return forwarded
        return peer_ip

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authenticate(self) -> bool:
        client_ip = self._client_ip()
        limiter = self.server.rate_limiter
        if limiter.is_blocked(client_ip):
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"ok": False, "error": "Too many failed authentication attempts."},
            )
            return False

        auth_header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not auth_header.startswith(prefix):
            limiter.record_failure(client_ip)
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": "Missing Bearer token."},
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
            return False

        provided = auth_header[len(prefix) :].strip()
        expected = self.server.config.api_token
        if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            limiter.record_failure(client_ip)
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": "Invalid Bearer token."},
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
            return False

        limiter.clear(client_ip)
        return True

    def _read_json_body(self) -> dict | None:
        raw_len = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_len)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid Content-Length."})
            return None

        if length < 0 or length > _MAX_BODY_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "Request body too large."},
            )
            return None

        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON body."})
            return None

        if not isinstance(data, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "JSON body must be an object."})
            return None
        return data

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if not self._authenticate():
            return

        if path == "/songs":
            status = self.server.db.get_status()
            songs = self.server.db.get_pending_tracks()
            session_expired = bool(status["session_expired"])
            alert = (
                "Spotify session expired! Run scripts/mac_login.py on your Mac to log back in."
                if session_expired
                else ""
            )
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "session_expired": session_expired,
                    "alert": alert,
                    "last_success_at": status["last_success_at"],
                    "last_run_at": status["last_run_at"],
                    "count": len(songs),
                    "batch_ids": [song["id"] for song in songs],
                    "songs": songs,
                },
            )
            return

        if path == "/status":
            status = self.server.db.get_status()
            self._send_json(HTTPStatus.OK, {"ok": True, **status})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if not self._authenticate():
            return

        if path == "/songs/ack":
            body = self._read_json_body()
            if body is None:
                return

            ack_all = bool(body.get("ack_all", False))
            ids = body.get("ids")
            if not ack_all and not isinstance(ids, list):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "Provide 'ids' list or 'ack_all': true."},
                )
                return

            updated = self.server.db.ack_tracks(track_keys=ids, ack_all=ack_all)
            remaining = len(self.server.db.get_pending_tracks())
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "acknowledged": updated,
                    "remaining_pending": remaining,
                },
            )
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})


def create_server(config: Config) -> BlendHTTPServer:
    """Validate configuration and construct the HTTP server."""
    config.validate_api_token()
    db = SyncDatabase(config.db_path)
    return BlendHTTPServer((config.api_host, config.api_port), config, db)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    server = create_server(config)
    logger.info(
        "Starting spotify-blend-sync API server on http://%s:%d",
        config.api_host,
        config.api_port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down API server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
