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
