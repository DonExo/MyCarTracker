# MyCarTracker

A private, Dockerized **Django 6.0** app for following Audi Q8 asking prices over time.
Starts with [Mobile.bg's Audi Q8 search](https://www.mobile.bg/obiavi/avtomobili-dzhipove/audi/q8).

## Start on your VPS

Requires Docker Engine and Docker Compose v2.

```bash
git clone https://github.com/DonExo/MyCarTracker.git
cd MyCarTracker
cp .env.example .env
```

Edit `.env`: replace **both secret placeholders** (`DJANGO_SECRET_KEY` and
`POSTGRES_PASSWORD`). Generate each value with `openssl rand -hex 32`.
Keep `localhost,127.0.0.1` in `DJANGO_ALLOWED_HOSTS` for health checks; add your domain.

```bash
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Open **http://localhost:8000**, sign in, open **Searches**, and click **Scrape now**.
The scheduler picks it up within 30 seconds. The first complete scrape can take
15–30 minutes depending on the number of adverts and network speed. Prices appear
incrementally; refresh Cars or Scrape history for progress.

When running on a remote VPS, use an SSH tunnel for private access:

```bash
ssh -L 8000:127.0.0.1:8000 your-user@your-server
```

Then visit http://localhost:8000 on your computer. For a domain, place Nginx or Caddy
with HTTPS in front of `127.0.0.1:8000`. Set `DJANGO_ALLOWED_HOSTS`,
`DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.example`, and `DJANGO_HTTPS=1`.
Your proxy must overwrite `X-Forwarded-Proto`. Recreate app containers after changing `.env`.

This is a **single private workspace**: every account you create can see and manage
all tracked cars. There is no public signup. PostgreSQL and the app bind privately
by default; no default account or password is created.

## What it does

- Collects each search page, follows pagination, and reads advert details.
- Runs daily at **06:00 Europe/Skopje**, configurable in `.env`. Missed runs are
  picked up after restart; a failed daily attempt retries on the next local day
  or when you click Scrape now. The scheduler must remain running.
- Stores separate **Vehicle → Listing → PriceObservation** records. An advert
  found in two saved searches remains one listing with two search memberships.
- Keeps an observation on every successful visit, even if the price is unchanged.
  Manual extra runs preserve intraday changes too. The graph displays the last
  observation per local day; the table retains every observation.
- Shows **€From–€To across currently active adverts for the same car**. This is
  distinct from the car's historical price range. Missing adverts are excluded
  from the current range, but their last price and history remain accessible.
- Shows recent price drops, filters by year/fuel, and links to original adverts.
- Includes duplicate review, separate-advert controls, run history, and read-only
  inspection of collected records in Django admin.

The source search includes Q8 e-tron and other variants listed by Mobile.bg. They
are retained; use the fuel filter to separate them. Advertised prices are asking
prices, not verified transaction prices. VAT notes are preserved as posted.
EUR is the comparison currency; historical BGN amounts convert at **1 EUR = 1.95583 BGN**.
Unknown currencies and absent/negotiable prices produce visible parsing issues.

## Duplicate matching

1. **Same site + advert ID** updates the existing listing.
2. **Identical non-empty VIN** automatically groups separate adverts. Conflicting
   VINs prevent a merge; grouping never deletes an advert or its observations.
3. When both VINs are present, matching VINs are an obvious match and conflicting
   VINs rule out a match. If either VIN is missing, mileage within 3,000 km, year,
   colour and current price are checked together; at least three of these four
   matches create a review candidate. Mileage carries twice the score weight of
   each other check. Seller phone, fuel, engine power, photos and description do
   not affect this decision. Similar cars are not automatically merged solely on
   their specifications.
4. Reviewers can merge groups or reject a pair. A rejected pair stays rejected.
   **Separate advert** moves one listing and all its history into a new group
   and locks its identity against automatic regrouping.

Photos are shown for human comparison, but image contents and common dealer
descriptions do not affect matching. Missing VINs can leave duplicates
undetected, so the review queue is intentionally conservative. No paid AI
service is required.

## Scraping safeguards and failure behavior

- Exact HTTPS hostname allowlists, URL validation, checked redirects, robots.txt,
  sequential requests, a minimum two-second default interval and bounded retries.
- No CAPTCHA solving, proxy rotation, or attempts to bypass a website block.
- HTTP 429, HTTP 403 and robots restrictions stop collection. Challenge pages or
  unrecognized HTML fail visibly instead of appearing to be empty inventory.
- An advert becomes missing only after **two complete successful searches** omit
  it. A partial run, parse error, lost pagination, page cap or advertised-total
  mismatch never increments missing counters. An overlapping search can still
  keep the advert active.
- A PostgreSQL session advisory lock prevents simultaneous scheduler/manual runs.
  Interrupted runs are marked failed when the scheduler recovers the lock.
- Website HTML can change. Review failures in Scrape history and update the
  affected adapter. Stop scraping if the source's policy no longer allows it.

## Add a website adapter

Adapters are explicit Python files, not generated or executed from user input.
The first implementation is `tracker/scrapers/mobile_bg.py`, based on Mobile.bg
HTML inspected on 2026-09-13. To add another website:

1. Copy `tracker/scrapers/example.py.template` to a new `.py` module.
2. Implement its exact `allowed_hosts`, `validate_search_url()`, `parse_search()`
   and `enrich()` methods. Return `SearchPage` and `ScrapedListing` objects.
3. Register it in `tracker/scrapers/__init__.py` under a stable adapter key.
4. Add sanitized HTML fixtures and parser tests. Rebuild the containers.
5. Save a direct search URL in Searches; the registry selects its adapter.

The shared collection and matching pipeline is site independent. The first UI
and model defaults target Audi Q8 only. To support more models, explicitly extend
the normalized data/model scope and matching rules before registering searches
for those models. Do not silently mix other models into the Q8 view.

## Operations

```bash
# Logs and container state
docker compose logs -f scheduler
docker compose ps

# Immediate collection (will refuse if another scrape holds the lock)
docker compose exec scheduler python manage.py scrape
docker compose exec scheduler python manage.py scrape --source 1

# Bounded diagnostic; deliberately returns nonzero if pagination remains
docker compose exec scheduler python manage.py scrape --source 1 --max-pages 1

# Upgrade after pulling changes
git pull --ff-only
docker compose up -d --build

# PostgreSQL backup; keep a copy off the VPS
docker compose exec -T db pg_dump -U cartracker -d cartracker -Fc > cartracker.dump

# Restore into a fresh database with app/scheduler stopped
docker compose stop web scheduler
docker compose exec -T db pg_restore -U cartracker -d cartracker --clean --if-exists < cartracker.dump
docker compose up -d web scheduler
```

PostgreSQL data persists in a named Docker volume. **`docker compose down -v`
deletes that volume**. The `migrate` service applies migrations and seeds the
initial search before web/scheduler startup. `/health/` checks DB connectivity;
inspect Scrape history for collection health.

## Development and tests

Python 3.12+ and [uv](https://docs.astral.sh/uv/) are required for local development.
The Docker build uses the committed dependency lock. Frontend assets (Bootstrap
5.3.8, HTMX 2.0.8, Chart.js 4.5.1) are vendored, so no CDN is required at runtime.
There is no Node build step. Django's templates, CSRF protection, authentication,
Gunicorn and WhiteNoise handle the web surface.

```bash
uv sync --frozen
export DJANGO_SECRET_KEY=local-development-only
export DATABASE_ENGINE=sqlite
export DJANGO_DEBUG=1
uv run python manage.py migrate
uv run python manage.py seed_sources
uv run python manage.py createsuperuser
uv run python manage.py runserver
# In a second terminal with the same environment:
uv run python manage.py run_scheduler

uv run python manage.py test tracker
uv run python manage.py makemigrations --check --dry-run
uv run ruff check .
```

SQLite is for local development and fast tests. Production uses PostgreSQL 18.
CI tests against PostgreSQL and separately builds/starts the Docker web stack.
Fixtures contain synthetic advert content. Tests cover parsing, pagination,
history, currency conversion, matching, splitting, source overlap, missing-advert
rules, scheduling, authentication and CSRF. CI does not crawl Mobile.bg.

## Deployment status

This repository contains the application and daily scheduler configuration.
Pushing it to GitHub does **not** start a server or collect prices. Run the Docker
instructions above on your VPS to begin persistent daily tracking.
