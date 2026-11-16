# Spotify Blend → Apple Music Sync

Automatically pulls **only your partner's tracks** (Lina) from your shared Spotify Blend playlist on your Linux home server (`yxhyai.com`), serves them over a token-protected HTTPS endpoint, and lets an **Apple Shortcut** on your iPhone/Mac add them to your Apple Music playlist every week.

---

## Features
- **No Spotify Premium or Apple Developer (\$99/yr) account required.**
- **Smart contributor filtering:** Skips your own tracks (`xeldev`), keeps Lina's tracks (`inubjkdj3pq8nlty5hztmoq5l` / `Lina`), and has an `IGNORE_SHARED_SONGS=true` option to also skip songs attributed to **both** of you.
- **Zero-duplicate SQLite queue:** Already-synced songs are never queued twice.
- **Session expiry detection & alerts:** If Spotify logs your cookies out, the scraper flags `session_expired: true` in the API (so your iPhone Shortcut notifies you) and optionally sends an instant push notification via `ntfy.sh` or Discord webhook.
- **1-command Mac re-login helper:** `scripts/mac_login.py` saves your Mac Chromium profile so refreshing expired cookies takes ~15 seconds and uploads straight to your server.
- **Secure by default:** API server binds to `127.0.0.1:8787`, requires a strong `Authorization: Bearer` token (constant-time verified + brute-force rate limited), and sits behind Caddy HTTPS on `yxhyai.com`.

---

## 1. Linux Server Setup (`yxhyai.com`)

```bash
cd /home/yxhyai/Documents/spotify-blend-sync
chmod +x scripts/setup_linux.sh scripts/trigger_sync.py scripts/mac_login.py
./scripts/setup_linux.sh
```

> **Note on Ubuntu 26.04:** `setup_linux.sh` automatically bootstraps `.venv` even if `python3.14-venv` isn't installed, and stores Playwright's Chromium in `/mnt/external_hdd/ms-playwright` if `/mnt/external_hdd` is writable.
> If Chromium needs shared OS libraries on a minimal headless server (such as `libatk-1.0.so.0`, `libgbm.so.1`, `libasound.so.2`), run:
> ```bash
> sudo .venv/bin/python -m playwright install-deps chromium
> ```

Review `.env` (created automatically with a random `API_TOKEN`):
- `SPOTIFY_BLEND_URL` is pre-filled with your Blend URL (`https://open.spotify.com/playlist/37i9dQZF1EJzoGIrop9Bz9`).
- `MY_IDENTIFIERS=xeldev`
- `PARTNER_IDENTIFIERS=inubjkdj3pq8nlty5hztmoq5l,Lina`
- `IGNORE_SHARED_SONGS=true` *(set to `false` if you ever want songs attributed to both of you)*
- *(Optional)* Set `NTFY_TOPIC_URL=https://ntfy.sh/<your-secret-topic>` and install the free **ntfy** app on your iPhone to get immediate push alerts whenever Spotify cookies expire.

Save your `API_TOKEN` from `.env`—you'll paste it into your Apple Shortcut in Step 5.

---

## 2. Log In From Your Mac (One-Time & Re-Login Helper)

On your **Mac**:

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
```

Copy `scripts/mac_login.py` to your Mac (or clone/sync this folder), then run:

```bash
python3 mac_login.py \
  --ssh-target yxhyai@yxhyai.com \
  --remote-dir /home/yxhyai/Documents/spotify-blend-sync \
  --run-sync-after \
  --remember
```

1. A Chromium window opens to your Blend playlist.
2. Log into your `xeldev` Spotify account and confirm the Blend tracks load.
3. Return to your Mac terminal and press **Enter**.
4. The script saves `storage_state.json`, uploads it via `scp` (`chmod 600`), and runs a verification sync on your server.

### When cookies expire later
You will know immediately (via `ntfy.sh` push notification or the iPhone Shortcut notification). Because you passed `--remember`, all you have to run on your Mac is:

```bash
python3 mac_login.py
```

Your saved `~/.spotify-blend-mac-profile` opens, you re-authenticate if prompted, press **Enter**, and it uploads + verifies automatically.

---

## 3. Expose Securely on `yxhyai.com` via Caddy

`caddy` is already installed on your server. Add one of the blocks from `deploy/Caddyfile.example` to `/etc/caddy/Caddyfile` (for example, `blend.yxhyai.com` pointing to `127.0.0.1:8787`), then reload Caddy:

```bash
sudo systemctl reload caddy
```

---

## 4. Enable Weekly Auto-Sync & API Service (`systemd`)

Install the systemd units from `deploy/`:

```bash
sudo cp deploy/blend-sync-api.service /etc/systemd/system/
sudo cp deploy/blend-sync-scrape.service /etc/systemd/system/
sudo cp deploy/blend-sync-scrape.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now blend-sync-api.service
sudo systemctl enable --now blend-sync-scrape.timer
```

Check status anytime:
```bash
sudo systemctl status blend-sync-api.service
systemctl list-timers blend-sync-scrape.timer
```

---

## 5. Apple Shortcut Setup (iPhone / Mac)

Create a Shortcut named **Sync Lina's Blend** and schedule it weekly (e.g., Sunday at 09:30, 30 minutes after the server scrape) via **Shortcuts → Automation → Time of Day → Weekly**:

1. **Get Contents of URL**
   - URL: `https://blend.yxhyai.com/songs` *(or `https://yxhyai.com/blend-api/songs`)*
   - Method: `GET`
   - Headers: `Authorization` = `Bearer <YOUR_API_TOKEN>`
2. **Get Dictionary from Input** *(Contents of URL)*
3. **Get Dictionary Value** for `session_expired` in Dictionary
4. **If** `Dictionary Value` is `true` *(or `1`)*:
   - **Show Notification**: *"Spotify Blend Sync paused — run mac_login.py on Mac to refresh cookies."*
   - **Stop This Shortcut**
5. **Get Dictionary Value** for `count` in Dictionary
6. **If** `count` is `0`:
   - **Stop This Shortcut**
7. **Get Dictionary Value** for `songs` in Dictionary
8. **Repeat with Each** item in `songs`:
   - **Get Dictionary Value** for `query` in `Repeat Item`
   - **Search iTunes Store**: Search `Dictionary Value`, Attribute `All`, Entity `Songs`, Limit `1`
   - **If** `iTunes Product` has any value:
     - **Add to Playlist**: Add `iTunes Product` to `<Your Apple Music Playlist>`
9. **End Repeat**
10. **Get Dictionary Value** for `batch_ids` in Dictionary *(from Step 2)*
11. **Get Contents of URL**
    - URL: `https://blend.yxhyai.com/songs/ack`
    - Method: `POST`
    - Headers: `Authorization` = `Bearer <YOUR_API_TOKEN>`
    - Request Body: `JSON` → `ids` = `batch_ids` *(Array)*

---

## Troubleshooting & Diagnostics

- **Test scrape without writing to DB:**
  ```bash
  .venv/bin/python scripts/trigger_sync.py --dry-run --verbose
  ```
- **Inspect Spotify DOM rows if contributors aren't matching:**
  ```bash
  .venv/bin/python scripts/trigger_sync.py --dry-run --debug-dom --verbose
  ```
  This saves the first 3 track rows to `data/debug_dom.html` (no cookies included).
- **Check server health & status:**
  ```bash
  curl -s http://127.0.0.1:8787/health
  curl -s -H "Authorization: Bearer <YOUR_API_TOKEN>" http://127.0.0.1:8787/status
  ```
