"""SQLite persistence for synced tracks, scraper runs, and session health."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from blend_sync.parser import ParsedTrack


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return current UTC timestamp formatted as ISO-8601."""
    return utc_now().isoformat(timespec="seconds")


class SyncDatabase:
    """Thread-safe SQLite wrapper using WAL mode for concurrent server + cron access."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    track_key TEXT PRIMARY KEY,
                    spotify_track_id TEXT,
                    title TEXT NOT NULL,
                    clean_title TEXT NOT NULL,
                    primary_artist TEXT NOT NULL,
                    artists_json TEXT NOT NULL,
                    album TEXT NOT NULL DEFAULT '',
                    apple_music_query TEXT NOT NULL,
                    contributors_summary TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    synced_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    total_rows INTEGER NOT NULL DEFAULT 0,
                    eligible_rows INTEGER NOT NULL DEFAULT 0,
                    newly_queued INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def _get_state(self, conn: sqlite3.Connection, key: str, default: str = "") -> str:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def _set_state(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO app_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def upsert_eligible_tracks(
        self, tracks: list[ParsedTrack], now_iso: str | None = None
    ) -> int:
        """Insert newly discovered eligible tracks as pending; keep existing statuses intact."""
        now = now_iso or utc_now_iso()
        newly_queued = 0
        with self._connect() as conn:
            for track in tracks:
                artists_json = json.dumps(list(track.artists), ensure_ascii=False)
                existing = conn.execute(
                    "SELECT track_key FROM tracks WHERE track_key = ?",
                    (track.track_key,),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO tracks (
                            track_key, spotify_track_id, title, clean_title, primary_artist,
                            artists_json, album, apple_music_query, contributors_summary,
                            first_seen_at, last_seen_at, status, synced_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL)
                        """,
                        (
                            track.track_key,
                            track.spotify_track_id,
                            track.title,
                            track.clean_title,
                            track.primary_artist,
                            artists_json,
                            track.album,
                            track.apple_music_query,
                            track.contributors_summary,
                            now,
                            now,
                        ),
                    )
                    newly_queued += 1
                else:
                    conn.execute(
                        """
                        UPDATE tracks
                        SET last_seen_at = ?,
                            contributors_summary = ?,
                            apple_music_query = ?
                        WHERE track_key = ?
                        """,
                        (
                            now,
                            track.contributors_summary,
                            track.apple_music_query,
                            track.track_key,
                        ),
                    )
        return newly_queued

    def get_pending_tracks(self) -> list[dict]:
        """Return all tracks currently waiting to be added to Apple Music."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT track_key, spotify_track_id, title, clean_title, primary_artist,
                       artists_json, album, apple_music_query, contributors_summary,
                       first_seen_at, last_seen_at
                FROM tracks
                WHERE status = 'pending'
                ORDER BY first_seen_at ASC, rowid ASC
                """
            ).fetchall()

        results: list[dict] = []
        for row in rows:
            artists_list = json.loads(row["artists_json"])
            results.append(
                {
                    "id": row["track_key"],
                    "spotify_track_id": row["spotify_track_id"],
                    "title": row["title"],
                    "clean_title": row["clean_title"],
                    "artist": row["primary_artist"],
                    "artists": ", ".join(artists_list),
                    "album": row["album"],
                    "query": row["apple_music_query"],
                    "contributor": row["contributors_summary"],
                    "first_seen_at": row["first_seen_at"],
                }
            )
        return results

    def ack_tracks(
        self,
        track_keys: list[str] | None = None,
        ack_all: bool = False,
        now_iso: str | None = None,
    ) -> int:
        """Mark pending tracks as synced. Returns number of rows updated."""
        now = now_iso or utc_now_iso()
        with self._connect() as conn:
            if ack_all:
                cur = conn.execute(
                    """
                    UPDATE tracks
                    SET status = 'synced', synced_at = ?
                    WHERE status = 'pending'
                    """,
                    (now,),
                )
                return cur.rowcount

            keys = [k.strip() for k in (track_keys or []) if isinstance(k, str) and k.strip()]
            if not keys:
                return 0
            placeholders = ",".join("?" for _ in keys)
            cur = conn.execute(
                f"""
                UPDATE tracks
                SET status = 'synced', synced_at = ?
                WHERE status = 'pending' AND track_key IN ({placeholders})
                """,
                (now, *keys),
            )
            return cur.rowcount

    def record_run(
        self,
        started_at: str,
        finished_at: str,
        outcome: str,
        total_rows: int = 0,
        eligible_rows: int = 0,
        newly_queued: int = 0,
        error_message: str = "",
    ) -> None:
        """Log a scraper execution and update session health state."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    started_at, finished_at, outcome, total_rows,
                    eligible_rows, newly_queued, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started_at,
                    finished_at,
                    outcome,
                    total_rows,
                    eligible_rows,
                    newly_queued,
                    error_message,
                ),
            )
            self._set_state(conn, "last_run_at", finished_at)
            self._set_state(conn, "last_outcome", outcome)
            if outcome == "ok":
                self._set_state(conn, "session_valid", "true")
                self._set_state(conn, "last_success_at", finished_at)
                self._set_state(conn, "last_error", "")
                self._set_state(conn, "last_alert_sent_at", "")
            elif outcome == "session_expired":
                self._set_state(conn, "session_valid", "false")
                self._set_state(conn, "last_error", error_message)
            else:
                self._set_state(conn, "last_error", error_message)

    def should_send_alert(self, cooldown_hours: int, now: datetime | None = None) -> bool:
        """Return True if no alert was sent within the cooldown window."""
        current = now or utc_now()
        with self._connect() as conn:
            last_sent = self._get_state(conn, "last_alert_sent_at", "")
        if not last_sent:
            return True
        try:
            last_dt = datetime.fromisoformat(last_sent)
        except ValueError:
            return True
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return current - last_dt >= timedelta(hours=cooldown_hours)

    def mark_alert_sent(self, now_iso: str | None = None) -> None:
        """Record the timestamp when an alert was dispatched."""
        now = now_iso or utc_now_iso()
        with self._connect() as conn:
            self._set_state(conn, "last_alert_sent_at", now)

    def get_status(self) -> dict:
        """Return overall sync health, counts, and recent scraper runs."""
        with self._connect() as conn:
            session_valid_raw = self._get_state(conn, "session_valid", "unknown")
            last_success_at = self._get_state(conn, "last_success_at", "")
            last_run_at = self._get_state(conn, "last_run_at", "")
            last_outcome = self._get_state(conn, "last_outcome", "")
            last_error = self._get_state(conn, "last_error", "")

            counts = {
                row["status"]: int(row["cnt"])
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS cnt FROM tracks GROUP BY status"
                ).fetchall()
            }
            recent_runs = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, started_at, finished_at, outcome, total_rows,
                           eligible_rows, newly_queued, error_message
                    FROM runs
                    ORDER BY id DESC
                    LIMIT 10
                    """
                ).fetchall()
            ]

        session_expired = session_valid_raw == "false" or last_outcome == "session_expired"
        return {
            "session_valid": None if session_valid_raw == "unknown" else (session_valid_raw == "true"),
            "session_expired": session_expired,
            "last_run_at": last_run_at or None,
            "last_success_at": last_success_at or None,
            "last_outcome": last_outcome or None,
            "last_error": last_error or None,
            "pending_count": counts.get("pending", 0),
            "synced_count": counts.get("synced", 0),
            "recent_runs": recent_runs,
        }
