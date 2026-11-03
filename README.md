# homeutils

Personal homelab utility apps (networth tracker, medicine reminder, etc.), built as a single
Django project. Each utility lives as its own Django app under `apps/`, sharing one database,
one container, and one deploy.

Deployed separately by the [homelab](../homelab) IaC repo, which pulls the image this repo
publishes to `ghcr.io/<owner>/homeutils` — see that repo's `apps/homeutils/` for the deployment
side.

## Developing

```sh
uv sync
cp .env.example .env   # edit as needed
docker compose up -d db
uv run python manage.py migrate
uv run python manage.py runserver
```

Or run the whole stack (app + db) in Docker:

```sh
docker compose up --build
```

## Checks

```sh
uv run ruff check .
uv run ruff format .
uv run ty check
uv run pytest
```

All four run in CI on every PR (`.github/workflows/ci.yml`).

## Medication reminders (ntfy)

A `scheduler` service in `docker-compose.yml` runs `manage.py send_reminders` every 15 minutes,
checking every user's unlogged medications against their configured reminder time and pushing a
notification via [ntfy](https://ntfy.sh) if anything is still pending. At most one push per user
per day (`NotificationLog` dedupes it).

Setup, per user:
1. Install the [ntfy app](https://ntfy.sh/) on your phone and subscribe to a topic - treat the
   topic name like a shared secret if using the public `ntfy.sh` server (anyone who knows it can
   read your notifications), or self-host ntfy and point `server_url` at it instead.
2. On the app's Notifications page, set that same topic, a reminder time, and save. Use "Send
   test" to confirm delivery before relying on the schedule.

`DJANGO_TIME_ZONE` determines both the reminder-time comparison and the app's day boundary for
streaks/heatmaps - defaults to `America/Phoenix`; override it in `.env` if deploying elsewhere.

## Adventures (group climbing planner)

Picks where the group climbs each weekend, balancing everyone's to-do lists across the season.

- **Zones and crags**: a zone (Sedona, Red Rocks, ...) is a forecast point plus a set of crags,
  and is either *local* (day-trippable) or a *trip*. The group climbs exactly one crag per day.
  Add and edit both on the Zones page; a crag can link to OpenBeta areas for route counts.
- **Plans**: for each weekend the planner builds a *Local* plan (each day picks the best crag
  from any local zone) and one plan per trip zone (both days at crags in that zone), then
  ranks them. Weather comes first: each crag-day is rated go / marginal / no from its zone's
  16-day [Open-Meteo](https://open-meteo.com/) forecast, and sandstone crags are ruled out for
  48h after rain (`apps/adventures/conditions.py`). A second day at the same crag counts at
  half, so plans spread sessions out unless one crag is clearly best.
- **Adding to-dos from OpenBeta**: on "add to-do", pick a zone (or search any OpenBeta region,
  which becomes a new zone), then search climbs by name. Picking one fills in its name and
  grade and files it under the right crag, which is the region's sub-area the climb sits in,
  e.g. Red Rocks › Juniper Canyon. The crag is created if needed. Each zone's climb list is
  fetched from OpenBeta on first search and cached for 30 days.
- **Climbers** are profiles with an optional link to a login. Each has a ranked to-do list
  (#1 is worth 1, #2 is worth ½, ...) and hand-entered onsight/redpoint grades. A Mountain
  Project tick import only *suggests* grades.
- **Season ledger** (`apps/adventures/ledger.py`): each climbed day credits each attendee
  with half of what that crag is worth to them, and an equal half-share of the day's total.
  The difference adds up into an "owed" balance, and the planner weights each attendee by
  (1 + balance). Rained-out weekends credit nobody, so whoever was owed stays first in line.
- **Focus** (from the coach's input): *Volume* ranks by routes in attendees' onsight ranges
  (OpenBeta counts; use Zones → "Refresh route counts"). *Try hard* counts only projects.

## Adding a new utility app

```sh
uv run python manage.py startapp <name> apps/<name>
```

Then:
1. In `apps/<name>/apps.py`, set `name = "apps.<name>"` and `label = "<name>"`.
2. Add `"apps.<name>"` to `INSTALLED_APPS` in `config/settings.py`.
3. Add `path("<name>/", include("apps.<name>.urls"))` to `config/urls.py`.
4. Run `uv run python manage.py makemigrations <name>`.

## Releasing

Releases are automatic. Every push to `main` runs `.github/workflows/auto-tag.yml`, which:

1. Runs CI (`ci.yml`, called as a reusable workflow) - no tag is created if it fails.
2. Bumps and pushes a semver tag based on [Conventional Commits](https://www.conventionalcommits.org/)
   in the new commits: `fix:` -> patch, `feat:` -> minor, `feat!:`/`BREAKING CHANGE:` -> major,
   anything else -> patch.

That tag push triggers `.github/workflows/release.yml`, which re-runs CI (defense in depth, in
case a tag is ever pushed by hand) and then builds and pushes
`ghcr.io/<owner>/homeutils:<version>` and `:latest`. Make the GHCR package public so the homelab
repo can pull it without credentials.

**One-time setup**: `auto-tag.yml` pushes the tag using a `RELEASE_TOKEN` secret rather than the
default `GITHUB_TOKEN`, because GitHub deliberately prevents the default token's pushes from
triggering other workflows - a tag pushed with it would never fire `release.yml`. Create a
fine-grained PAT scoped to this repo with "Contents: Read and write" permission, then add it:

```sh
gh secret set RELEASE_TOKEN
```

To tag manually instead (e.g. a hotfix release outside normal `main` flow):

```sh
git tag v0.1.0
git push origin v0.1.0
```

Then bump the pinned image tag in the homelab repo's `apps/homeutils/docker-compose.yml`.
