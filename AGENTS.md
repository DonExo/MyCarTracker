# Project language preference

- Keep every authored form, label, button, message, and other interface text in English.
- Treat scraper-site languages as source data, not as the product's default language. Translate or map values such as vehicle colors to English before displaying or storing them for filters.
- Do not create or run unit tests; verify changes by reviewing the relevant code.

# Codebase map

- `config/` — Django project settings, root URLs, and WSGI entry point.
- `tracker/` — main app. Key modules: `models.py` (Vehicle/Listing/PriceObservation), `views.py`, `forms.py`, `matching.py` (duplicate detection), `colours.py` (language-to-English colour mapping), `services.py` (scrape orchestration).
- `tracker/scrapers/` — per-site adapters. `base.py` defines the contract; `mobile_bg.py` is the live adapter; add new sites per `README.md`.
- `tracker/management/commands/` — `scrape` (run collection), `run_scheduler` (loop), `seed_sources` (initial search setup).
- `templates/tracker/` — HTMX-based UI views (dashboard, detail, duplicates, runs, sources).
- `static/vendor/` — vendored Bootstrap/HTMX/Chart.js; no Node build step.
- `compose.yaml` + `Dockerfile` — production containers (web, scheduler, db, migrate).
- `.github/workflows/` — CI: Django tests against PostgreSQL plus Docker build check.

# Common practices

- Run commands through Docker Compose on a VPS, or `uv run` locally with SQLite (`DATABASE_ENGINE=sqlite`, `DJANGO_DEBUG=1`).
- Changes to scraper adapters need sanitized HTML fixtures in `tracker/tests/fixtures/` — but per project preference, do not create or run tests; just ensure fixtures exist if the user supplies them.
- Migrations are applied automatically by the `migrate` service before app startup.
