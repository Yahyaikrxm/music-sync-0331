#!/usr/bin/env python3
"""CLI entrypoint to run the Spotify Blend scraper (used manually or by systemd/cron)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from blend_sync.config import load_config  # noqa: E402
from blend_sync.scraper import run_scrape  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Spotify Blend for partner tracks.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and classify songs without modifying SQLite or sending alerts.",
    )
    parser.add_argument(
        "--debug-dom",
        action="store_true",
        help="Save the first 3 track rows' HTML to data/debug_dom.html for inspection.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config()
    summary = run_scrape(config, dry_run=args.dry_run, debug_dom=args.debug_dom)

    print(
        f"Outcome: {summary.outcome} | "
        f"Total rows: {summary.total_rows} | "
        f"Partner eligible: {summary.eligible_rows} | "
        f"Newly queued: {summary.newly_queued} | "
        f"Skipped (mine={summary.skipped_mine}, shared={summary.skipped_shared}, unknown={summary.skipped_unknown})"
    )
    if summary.error_message:
        print(f"Details: {summary.error_message}", file=sys.stderr)

    if summary.outcome == "ok":
        return 0
    if summary.outcome == "session_expired":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
