"""Playwright scraper for reading Spotify Blend playlists and attributing tracks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from blend_sync.config import Config
from blend_sync.db import SyncDatabase, utc_now_iso
from blend_sync.notifier import send_alert
from blend_sync.parser import ParsedTrack, RawTrackRow, parse_track_row

logger = logging.getLogger(__name__)


class SessionExpiredError(RuntimeError):
    """Raised when Spotify requires re-authentication."""


@dataclass(frozen=True)
class ScrapeSummary:
    """Summary of a completed scraper run."""

    outcome: str
    total_rows: int
    eligible_rows: int
    newly_queued: int
    skipped_mine: int
    skipped_shared: int
    skipped_unknown: int
    error_message: str = ""


_EXTRACT_VISIBLE_ROWS_JS = """
() => {
  const rows = Array.from(document.querySelectorAll('[data-testid="tracklist-row"]'));
  return rows.map((row) => {
    const ariaRowIndex = row.closest('[aria-rowindex]')?.getAttribute('aria-rowindex') || '';
    const trackLink = row.querySelector('a[href*="/track/"]');
    const trackUrl = trackLink ? (trackLink.getAttribute('href') || '') : '';
    const title = trackLink ? (trackLink.textContent || '').trim() : '';

    const artistSeen = new Set();
    const artists = [];
    for (const a of row.querySelectorAll('a[href*="/artist/"]')) {
      const name = (a.textContent || '').trim();
      if (name && !artistSeen.has(name)) {
        artistSeen.add(name);
        artists.push(name);
      }
    }

    const albumLink = row.querySelector('a[href*="/album/"]');
    const album = albumLink ? (albumLink.textContent || '').trim() : '';

    const contributorHrefs = [];
    const contributorLabels = [];
    const pushLabel = (val) => {
      if (!val) return;
      const clean = String(val).trim();
      if (clean && !contributorLabels.includes(clean)) {
        contributorLabels.push(clean);
      }
    };

    for (const u of row.querySelectorAll('a[href*="/user/"]')) {
      const href = u.getAttribute('href') || '';
      if (href && !contributorHrefs.includes(href)) {
        contributorHrefs.push(href);
      }
      pushLabel(u.textContent);
      pushLabel(u.getAttribute('aria-label'));
      pushLabel(u.getAttribute('title'));
    }

    const cells = Array.from(row.querySelectorAll('[role="gridcell"]'));
    for (const cell of cells) {
      const colIdx = cell.getAttribute('aria-colindex') || '';
      if (colIdx === '1') continue;
      if (cell.querySelector('a[href*="/track/"]')) continue;
      if (cell.querySelector('a[href*="/album/"]')) continue;

      const rawText = (cell.textContent || '').trim();
      if (/^\\d+:\\d{2}$/.test(rawText)) continue;

      for (const img of cell.querySelectorAll('img[alt]')) {
        pushLabel(img.getAttribute('alt'));
      }
      for (const el of cell.querySelectorAll('[aria-label], [title]')) {
        const label = el.getAttribute('aria-label') || '';
        const t = el.getAttribute('title') || '';
        // Ignore generic action button labels
        if (label && !/^(save to|add to|more options|play )/i.test(label)) {
          pushLabel(label);
        }
        if (t && !/^(save to|add to|more options|play )/i.test(t)) {
          pushLabel(t);
        }
      }
      if (rawText) {
        pushLabel(rawText);
      }
    }

    return {
      key: ariaRowIndex ? `${ariaRowIndex}|${trackUrl}` : `${trackUrl}|${title}|${artists.join(',')}`,
      trackUrl,
      title,
      artists,
      album,
      contributorHrefs,
      contributorLabels,
    };
  });
}
"""


def _check_if_logged_out(page) -> bool:
    """Detect whether Spotify has redirected or rendered a logged-out state."""
    current_url = page.url.lower()
    if "accounts.spotify.com" in current_url or "/login" in current_url:
        return True

    has_login_btn = page.locator('[data-testid="login-button"]').count() > 0
    has_user_widget = page.locator('[data-testid="user-widget-link"]').count() > 0
    if has_login_btn and not has_user_widget:
        return True
    return False


def fetch_blend_rows(
    config: Config,
    debug_dom_path: Path | None = None,
) -> list[RawTrackRow]:
    """Open the Blend playlist with Playwright and scroll to collect all track rows."""
    if not config.storage_state_path.is_file():
        raise SessionExpiredError(
            f"Missing Spotify session file at {config.storage_state_path}. "
            "Run scripts/mac_login.py on your Mac to log in and upload it."
        )

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run scripts/setup_linux.sh first."
        ) from exc

    collected: dict[str, RawTrackRow] = {}

    with sync_playwright() as p:
        launch_args = ["--disable-blink-features=AutomationControlled"]
        browser = p.chromium.launch(headless=config.headless, args=launch_args)
        context = browser.new_context(
            storage_state=str(config.storage_state_path),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        try:
            logger.info("Opening Spotify Blend URL: %s", config.spotify_blend_url)
            page.goto(config.spotify_blend_url, wait_until="domcontentloaded", timeout=45000)

            # Allow client-side hydration / potential redirect to settle.
            page.wait_for_timeout(3000)

            if _check_if_logged_out(page):
                raise SessionExpiredError(
                    "Spotify session has expired (login prompt detected). "
                    "Run scripts/mac_login.py on your Mac to refresh cookies."
                )

            try:
                page.wait_for_selector('[data-testid="tracklist-row"]', timeout=25000)
            except PlaywrightTimeoutError as exc:
                if _check_if_logged_out(page):
                    raise SessionExpiredError(
                        "Spotify session expired while loading the Blend playlist."
                    ) from exc
                raise RuntimeError(
                    "Timed out waiting for playlist tracks to render on open.spotify.com."
                ) from exc

            if debug_dom_path is not None:
                sample_html = page.evaluate(
                    """() => Array.from(document.querySelectorAll('[data-testid="tracklist-row"]'))
                    .slice(0, 3)
                    .map(el => el.outerHTML)
                    .join('\\n<hr/>\\n')"""
                )
                debug_dom_path.parent.mkdir(parents=True, exist_ok=True)
                debug_dom_path.write_text(sample_html, encoding="utf-8")
                logger.info("Saved sample DOM rows to %s", debug_dom_path)

            idle_steps = 0
            for _step in range(config.max_scroll_steps):
                visible = page.evaluate(_EXTRACT_VISIBLE_ROWS_JS)
                added_this_step = 0
                for item in visible:
                    key = item["key"]
                    if key not in collected and item["title"] and item["artists"]:
                        collected[key] = RawTrackRow(
                            track_url=item["trackUrl"],
                            title=item["title"],
                            artists=tuple(item["artists"]),
                            album=item["album"],
                            contributor_hrefs=tuple(item["contributorHrefs"]),
                            contributor_labels=tuple(item["contributorLabels"]),
                        )
                        added_this_step += 1

                if added_this_step == 0:
                    idle_steps += 1
                else:
                    idle_steps = 0

                if idle_steps >= 3:
                    break

                # Scroll the last visible tracklist row into view to advance Spotify's virtual list.
                scrolled = page.evaluate(
                    """() => {
                      const rows = document.querySelectorAll('[data-testid="tracklist-row"]');
                      if (!rows.length) return false;
                      rows[rows.length - 1].scrollIntoView({ behavior: 'instant', block: 'center' });
                      return true;
                    }"""
                )
                if not scrolled:
                    break
                page.wait_for_timeout(config.scroll_pause_ms)

            # Persist refreshed cookies/localStorage back if session is valid.
            if collected:
                context.storage_state(path=str(config.storage_state_path))
        finally:
            context.close()
            browser.close()

    return list(collected.values())


def run_scrape(
    config: Config,
    dry_run: bool = False,
    debug_dom: bool = False,
) -> ScrapeSummary:
    """Execute a full Blend scrape, filter tracks, update SQLite, and alert on expiry."""
    started_at = utc_now_iso()
    db = SyncDatabase(config.db_path)
    debug_path = (config.project_root / "data" / "debug_dom.html") if debug_dom else None

    try:
        raw_rows = fetch_blend_rows(config, debug_dom_path=debug_path)
    except SessionExpiredError as exc:
        finished_at = utc_now_iso()
        err_msg = str(exc)
        logger.error("Session expired: %s", err_msg)
        if not dry_run:
            db.record_run(
                started_at=started_at,
                finished_at=finished_at,
                outcome="session_expired",
                error_message=err_msg,
            )
            if db.should_send_alert(config.alert_cooldown_hours):
                sent = send_alert(
                    config,
                    title="Spotify Blend Sync: Login Expired",
                    message=(
                        "Your Spotify cookies expired and the Blend sync has paused.\n"
                        "Run `python3 scripts/mac_login.py` on your Mac to log back in."
                    ),
                )
                if sent:
                    db.mark_alert_sent(finished_at)
        return ScrapeSummary(
            outcome="session_expired",
            total_rows=0,
            eligible_rows=0,
            newly_queued=0,
            skipped_mine=0,
            skipped_shared=0,
            skipped_unknown=0,
            error_message=err_msg,
        )
    except Exception as exc:  # noqa: BLE001
        finished_at = utc_now_iso()
        err_msg = str(exc)
        logger.exception("Scrape failed: %s", err_msg)
        if not dry_run:
            db.record_run(
                started_at=started_at,
                finished_at=finished_at,
                outcome="error",
                error_message=err_msg,
            )
            if db.should_send_alert(config.alert_cooldown_hours):
                sent = send_alert(
                    config,
                    title="Spotify Blend Sync: Error",
                    message=f"Weekly Blend scrape failed: {err_msg}",
                )
                if sent:
                    db.mark_alert_sent(finished_at)
        return ScrapeSummary(
            outcome="error",
            total_rows=0,
            eligible_rows=0,
            newly_queued=0,
            skipped_mine=0,
            skipped_shared=0,
            skipped_unknown=0,
            error_message=err_msg,
        )

    eligible: list[ParsedTrack] = []
    skipped_mine = 0
    skipped_shared = 0
    skipped_unknown = 0

    for raw in raw_rows:
        parsed = parse_track_row(raw, config.my_identifiers, config.partner_identifiers)
        if parsed is None:
            continue
        if parsed.should_include(config.ignore_shared_songs):
            eligible.append(parsed)
        elif parsed.is_shared:
            skipped_shared += 1
        elif parsed.matched_me:
            skipped_mine += 1
        else:
            skipped_unknown += 1
            logger.debug(
                "Unattributed row %r (%s): hrefs=%s labels=%s",
                parsed.title,
                parsed.primary_artist,
                raw.contributor_hrefs,
                raw.contributor_labels,
            )

    finished_at = utc_now_iso()
    newly_queued = 0
    if not dry_run:
        newly_queued = db.upsert_eligible_tracks(eligible, now_iso=finished_at)
        db.record_run(
            started_at=started_at,
            finished_at=finished_at,
            outcome="ok",
            total_rows=len(raw_rows),
            eligible_rows=len(eligible),
            newly_queued=newly_queued,
        )

    return ScrapeSummary(
        outcome="ok",
        total_rows=len(raw_rows),
        eligible_rows=len(eligible),
        newly_queued=newly_queued,
        skipped_mine=skipped_mine,
        skipped_shared=skipped_shared,
        skipped_unknown=skipped_unknown,
    )
