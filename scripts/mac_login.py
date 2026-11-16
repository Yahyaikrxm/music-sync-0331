#!/usr/bin/env python3
"""Mac helper to log into Spotify in a real browser and upload cookies to the Linux server.

Run this once initially, and re-run anytime Spotify logs your server session out.
Requires only: pip install playwright && python3 -m playwright install chromium
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_BLEND_URL = "https://open.spotify.com/playlist/37i9dQZF1EJzoGIrop9Bz9"
MAC_CONFIG_PATH = Path.home() / ".spotify-blend-mac.json"
DEFAULT_PROFILE_DIR = Path.home() / ".spotify-blend-mac-profile"
DEFAULT_OUTPUT_PATH = Path.cwd() / "storage_state.json"


def _load_saved_prefs() -> dict:
    if MAC_CONFIG_PATH.is_file():
        try:
            return json.loads(MAC_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_prefs(prefs: dict) -> None:
    MAC_CONFIG_PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    os.chmod(MAC_CONFIG_PATH, 0o600)


def main() -> int:
    saved = _load_saved_prefs()

    parser = argparse.ArgumentParser(
        description="Log into Spotify on your Mac and export storage_state.json for the Linux server."
    )
    parser.add_argument(
        "--url",
        default=saved.get("url", DEFAULT_BLEND_URL),
        help="Spotify Blend URL to open after login.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(saved.get("output", str(DEFAULT_OUTPUT_PATH))),
        help="Local path to save storage_state.json.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(saved.get("profile_dir", str(DEFAULT_PROFILE_DIR))),
        help="Persistent Chromium user data directory on Mac for faster re-logins.",
    )
    parser.add_argument(
        "--ssh-target",
        default=saved.get("ssh_target", ""),
        help="Optional SSH target (e.g. yxhyai@yxhyai.com) to upload cookies automatically.",
    )
    parser.add_argument(
        "--remote-dir",
        default=saved.get(
            "remote_dir",
            "/home/yxhyai/Documents/spotify-blend-sync",
        ),
        help="Project directory on the Linux server.",
    )
    parser.add_argument(
        "--run-sync-after",
        action="store_true",
        default=saved.get("run_sync_after", False),
        help="After uploading cookies, run trigger_sync.py remotely over SSH to verify immediately.",
    )
    parser.add_argument(
        "--remember",
        action="store_true",
        help="Save --ssh-target, --remote-dir, and --run-sync-after to ~/.spotify-blend-mac.json.",
    )
    args = parser.parse_args()

    if args.remember:
        _save_prefs(
            {
                "url": args.url,
                "output": str(args.output),
                "profile_dir": str(args.profile_dir),
                "ssh_target": args.ssh_target,
                "remote_dir": args.remote_dir,
                "run_sync_after": args.run_sync_after,
            }
        )
        print(f"Saved preferences to {MAC_CONFIG_PATH}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is not installed on this Mac.\n"
            "Install it with:\n"
            "  python3 -m pip install playwright\n"
            "  python3 -m playwright install chromium",
            file=sys.stderr,
        )
        return 1

    args.profile_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    print("Opening Chromium on your Mac...")
    print("1. Log into your Spotify account (xeldev) in the browser window.")
    print("2. Make sure the Blend playlist tracks are visible.")
    print("3. Return here and press ENTER once logged in (or wait for auto-detection).")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(args.profile_dir),
            headless=False,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.url, wait_until="domcontentloaded")

            input("\nPress ENTER here after you confirm you are logged in and can see the Blend tracks... ")

            logged_in = page.locator('[data-testid="user-widget-link"]').count() > 0
            if not logged_in:
                print(
                    "Warning: Spotify profile widget was not detected yet; saving session anyway. "
                    "Make sure you were logged in before closing."
                )

            context.storage_state(path=str(args.output))
            os.chmod(args.output, 0o600)
            print(f"Saved Spotify session to: {args.output}")
        finally:
            context.close()

    if args.ssh_target:
        remote_state = f"{args.remote_dir.rstrip('/')}/data/storage_state.json"
        print(f"Uploading session to {args.ssh_target}:{remote_state} ...")
        subprocess.run(
            ["ssh", args.ssh_target, f"mkdir -p {shlex.quote(args.remote_dir.rstrip('/') + '/data')} && chmod 700 {shlex.quote(args.remote_dir.rstrip('/') + '/data')}"],
            check=True,
        )
        subprocess.run(
            ["scp", str(args.output), f"{args.ssh_target}:{remote_state}"],
            check=True,
        )
        subprocess.run(
            ["ssh", args.ssh_target, f"chmod 600 {shlex.quote(remote_state)}"],
            check=True,
        )
        print("Upload complete!")

        if args.run_sync_after:
            remote_cmd = (
                f"cd {shlex.quote(args.remote_dir)} && "
                ".venv/bin/python scripts/trigger_sync.py"
            )
            print("Running remote verification sync...")
            subprocess.run(["ssh", args.ssh_target, remote_cmd], check=True)
    else:
        print(
            "\nNext step — copy this file to your Linux server:\n"
            f"  scp {shlex.quote(str(args.output))} "
            f"yxhyai@yxhyai.com:{args.remote_dir.rstrip('/')}/data/storage_state.json"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
