#!/usr/bin/env bash
# Deploy the latest main to the VPS. Run on the server, or from your own
# machine with:
#
#     ssh ionos 'bash ~/shipshape/deploy.sh'
#
# Only ever touches ShipShape: this checkout and shipshape.service. The other
# apps on the server (LedgerFinch, the Dip Harvester bot) are left alone.
set -euo pipefail

cd "$(dirname "$0")"
# uv lives here, and it isn't on PATH over a non-interactive ssh.
export PATH="$HOME/.local/bin:$PATH"

echo "==> Pulling main"
git pull --ff-only

echo "==> Syncing dependencies"
uv sync --frozen

# Production settings (secret key, allowed hosts, DEBUG off) live in .env.
set -a
# shellcheck disable=SC1091
source .env
set +a

if ! uv run python manage.py migrate --check >/dev/null 2>&1; then
    snapshot="$HOME/backups/snapshot_shipshape.sh"
    if [[ -x "$snapshot" ]]; then
        echo "==> Migrations pending: snapshotting the database first"
        "$snapshot"
    fi
    echo "==> Applying migrations"
    uv run python manage.py migrate --noinput
else
    echo "==> No migrations to apply"
fi

echo "==> Collecting static files"
uv run python manage.py collectstatic --noinput >/dev/null

echo "==> Restarting shipshape.service"
sudo systemctl restart shipshape
sleep 2
if systemctl is-active --quiet shipshape; then
    echo "==> Deployed $(git log --oneline -1)"
else
    echo "!!! shipshape.service failed to start. Recent log:" >&2
    sudo journalctl -u shipshape -n 30 --no-pager >&2
    exit 1
fi
