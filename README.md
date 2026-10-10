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

Push a tag matching `v*.*.*` to trigger `.github/workflows/release.yml`, which builds the image
and pushes `ghcr.io/<owner>/homeutils:<version>` and `:latest`. Make the GHCR package public so
the homelab repo can pull it without credentials:

```sh
git tag v0.1.0
git push origin v0.1.0
```

Then bump the pinned image tag in the homelab repo's `apps/homeutils/docker-compose.yml`.
