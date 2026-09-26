# ShipShape ⚓

[![CI](https://github.com/LlewopNomis/DjangoShipShape/actions/workflows/ci.yml/badge.svg)](https://github.com/LlewopNomis/DjangoShipShape/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-hosted Django app for keeping track of everything on a boat — what you
have, where it lives, what it's worth, and what you've done to keep it
running. Built because boat gear ends up stashed in a dozen lockers and
nobody can ever say what's actually aboard, or what it would cost to replace.

## Features

- **Location tree** — nested locations (e.g. Galley → Sole → Under panel 3),
  built on [django-treebeard](https://django-treebeard.readthedocs.io/), with
  photos attachable to any location.
- **Item categories** — a second, independent tree for classifying items
  (e.g. Tools → Hand tools), so an item is tagged by *what it is* and
  *where it lives* at the same time.
- **Inventory items** — quantity, condition, an optional `$` value (for
  insurance/valuation purposes), free-text notes, and photos — including a
  "this is a receipt" flag so purchase proof stays attached to the item.
- **PDF attachments** — the same upload spot on items, locations, and
  repairs also takes PDFs (manuals, receipts, warranty docs), stored as-is
  rather than converted to an image, so they stay full quality and
  multi-page. Shown as a document icon instead of a thumbnail.
- **Search across all three dimensions** — filter the inventory list by
  free-text search, category (including everything nested under it), or
  location (ditto).
- **Repair & service log** — a dated ship's-log entry per repair or service
  job, with its own category (routine maintenance, condition-based repair,
  emergency repair, upgrade/improvement — edit the list any time), optional
  location, hours spent, notes, and photos.
- **Inventory consumption** — a repair log entry can record several
  inventory items as used against it; stock is decremented automatically,
  validated against what's actually in stock, and restored if you remove
  the entry (or delete the whole repair).
- **Thumbnail previews** — each location's item listing shows the item's
  primary photo (or first uploaded) as a thumbnail, no extra setup needed.
- **Django admin** with drag-and-drop tree reordering for locations and
  categories, for quick bulk edits.
- **SQLite, no external services** — runs on your own machine, or on a
  small server over Tailscale (see [Running on a server](#running-on-a-server)).

## Roadmap

`LocationHotspot` is already modeled (see `inventory/models.py`) to support
a future image-map style drill-down: click a region of a location photo
(e.g. the galley floor) to jump into the sub-location it represents. Not
built yet — the data model is just ready for it.

## Tech stack

Django 6.1 · django-treebeard · Pillow · Bootstrap 5 (via CDN) · SQLite ·
gunicorn + WhiteNoise for serving

## Getting started

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this-repo-url>
cd DjangoShipShape
uv sync
uv run manage.py migrate
uv run manage.py createsuperuser   # the login for the app and /admin/
uv run manage.py runserver
```

Then open http://127.0.0.1:8000/.

Every page needs a login — use the account from `createsuperuser` (the
`createsuperuser` step above is therefore required, not optional). Sessions
last 90 days, so you won't be asked often.

### Running on a server

The app is built to run behind [Tailscale](https://tailscale.com/) rather
than on the public internet: gunicorn binds to the machine's Tailscale IP,
so only devices on your tailnet can reach it (WireGuard encrypts the
traffic, so plain HTTP is fine). There's no nginx needed — WhiteNoise
serves static files and Django serves uploaded photos, which is plenty for
a single user.

Settings that differ from a local checkout come from environment variables:

| Variable | Server value |
|---|---|
| `DJANGO_DEBUG` | `0` |
| `DJANGO_SECRET_KEY` | a long random string — `python -c 'import secrets; print(secrets.token_urlsafe(50))'`. Required when `DJANGO_DEBUG=0`; the app refuses to start with the built-in dev key. |
| `DJANGO_ALLOWED_HOSTS` | comma-separated hostnames/IPs you'll browse to, e.g. `ionos-vps,100.118.115.74` |

Then:

```bash
uv sync
uv run manage.py migrate
uv run manage.py collectstatic --noinput
uv run gunicorn djangoshipshape.wsgi -b <tailscale-ip>:8001 -w 2
```

Uploaded photos are resized to fit 1600 px on their longest side and
rotated upright (phones often store portrait shots sideways with a
rotation flag). Re-encoding also drops embedded EXIF data such as GPS
position. PDFs and GIFs are stored as-is.

## Using it

1. Add a top-level **Location** to start the tree, then keep adding
   sub-locations to drill down as far as makes sense. There's no wrong way to
   root it — two approaches both work well:
   - **The boat's name as the single root** (e.g. "Serendipity" → Galley →
     Sole → Under panel 3), if you want one tree that mirrors the whole
     vessel.
   - **Key areas as separate root nodes** (e.g. "Galley", "Engine room",
     "Cockpit locker" each as their own top-level location), if you'd rather
     jump straight to an area without an extra click through the boat name
     first.

   Either is fine — sub-locations, items, and search all work the same way
   regardless of which you pick.
2. Add **Item categories** the same way (they nest too, independently of
   locations).
3. Add **items**, tagging each to a location and category, with quantity,
   condition, and a `$` value if you know it.
4. Attach **photos** to items and locations — mark one "Primary" so it's the
   one used for thumbnails, or "Receipt" if it's proof of purchase rather
   than a photo of the item.
5. When you do a repair or service, log it from **Repair log** — set a
   category, date, optional location and hours spent, then use "Record use"
   to note which inventory items (and how many) it consumed. Stock updates
   immediately.

## Data & backups

Everything lives in `db.sqlite3` plus the `media/` folder (your photos).
Back up both together — neither is committed to this repository.

## License

MIT — see [LICENSE](LICENSE).
