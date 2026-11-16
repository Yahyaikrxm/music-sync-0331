#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

mkdir -p data logs
chmod 700 data

if [[ ! -f .env ]]; then
  cp .env.example .env
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  python3 -c "
from pathlib import Path
p = Path('.env')
text = p.read_text(encoding='utf-8').replace('CHANGE_ME_GENERATE_A_LONG_RANDOM_TOKEN_HERE', '$TOKEN')
p.write_text(text, encoding='utf-8')
"
  chmod 600 .env
  echo "Created .env with a newly generated API_TOKEN."
else
  echo ".env already exists (leaving untouched)."
fi

if [[ ! -x .venv/bin/pip ]]; then
  if ! python3 -m venv .venv 2>/dev/null; then
    rm -rf .venv
    echo "python3-venv/ensurepip not available; creating .venv without pip and bootstrapping pip..."
    python3 -m venv --without-pip .venv
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python -
  fi
fi

.venv/bin/pip install --no-cache-dir --upgrade pip
.venv/bin/pip install --no-cache-dir -r requirements.txt

# Load PLAYWRIGHT_BROWSERS_PATH from .env if set, or default to /mnt/external_hdd when writable
BROWSERS_PATH_FROM_ENV="$(grep -E '^PLAYWRIGHT_BROWSERS_PATH=' .env | tail -n1 | cut -d= -f2- || true)"
if [[ -n "$BROWSERS_PATH_FROM_ENV" ]]; then
  export PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_PATH_FROM_ENV"
elif [[ -d /mnt/external_hdd && -w /mnt/external_hdd ]]; then
  export PLAYWRIGHT_BROWSERS_PATH="/mnt/external_hdd/ms-playwright"
  if ! grep -q '^PLAYWRIGHT_BROWSERS_PATH=' .env; then
    printf '\nPLAYWRIGHT_BROWSERS_PATH=%s\n' "$PLAYWRIGHT_BROWSERS_PATH" >> .env
  fi
fi

if [[ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]]; then
  mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
  echo "Installing Playwright Chromium into $PLAYWRIGHT_BROWSERS_PATH ..."
fi
.venv/bin/python -m playwright install chromium

echo ""
echo "Setup complete!"
echo "If Chromium needs OS shared libraries on a minimal server, run:"
echo "  sudo $PROJECT_DIR/.venv/bin/python -m playwright install-deps chromium"
