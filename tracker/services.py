import logging
from contextlib import contextmanager
from dataclasses import asdict

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from .matching import match_listing, split_listing
from .models import Listing, PriceObservation, ScrapeRun, Source, SourceListing, Vehicle
from .scrapers import REGISTRY, vendor_for_adapter
from .scrapers.base import ScrapeError

logger = logging.getLogger(__name__)
LOCK_ID = 72416382


@contextmanager
def scrape_lock():
    """Session lock held across network I/O without a long-running DB transaction."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
            acquired = cursor.fetchone()[0]
        try:
            yield acquired
        finally:
            if acquired:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])
    else:
        # Development SQLite: cross-process lock on the same machine.
        import fcntl
        import tempfile
        from pathlib import Path

        with (Path(tempfile.gettempdir()) / "mycartracker-scrape.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
            else:
                try:
                    yield True
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)


@transaction.atomic
def ingest(run, data, *, enriched=True):
    now = timezone.now()
    listing = Listing.objects.filter(
        adapter=run.source.adapter, external_id=data.external_id
    ).first()
    created = listing is None
    changed = False
    vendor = vendor_for_adapter(run.source.adapter)
    if created:
        listing = Listing(
            vehicle=Vehicle.objects.create(),
            adapter=run.source.adapter,
            vendor=vendor,
            external_id=data.external_id,
            first_seen=now,
        )
    elif vendor:
        listing.vendor = vendor
    # If a previously advertised VIN changes, don't contaminate a multi-advert group.
    if not created and data.vin and listing.vin and data.vin != listing.vin:
        if Listing.objects.filter(vehicle=listing.vehicle).count() > 1:
            split_listing(listing.pk)
            listing.refresh_from_db()
    if not created and listing.current_price != data.price_eur:
        changed = True
        listing.previous_price = listing.current_price
        listing.price_changed_at = now
    values = asdict(data)
    for field in (
        "url",
        "title",
        "year",
        "mileage",
        "fuel",
        "power_hp",
        "colour",
        "seller",
        "location",
        "description",
        "images",
        "currency",
        "original_price",
        "price_note",
    ):
        value = values[field]
        if field == "colour":
            value = value or listing.colour
        setattr(listing, field, value)
    # An unavailable detail page must not erase previously collected identity.
    if enriched or data.vin:
        listing.vin = data.vin or listing.vin
    if enriched:
        listing.phone = data.phone or listing.phone
    listing.current_price = data.price_eur
    listing.last_seen = now
    listing.save()
    SourceListing.objects.update_or_create(
        source=run.source,
        listing=listing,
        defaults={"last_seen_run": run, "missing_runs": 0, "active": True},
    )
    values["original_price"] = str(data.original_price)
    PriceObservation.objects.get_or_create(
        listing=listing,
        run=run,
        defaults={
            "observed_at": now,
            "price_eur": data.price_eur,
            "original_price": data.original_price,
            "currency": data.currency,
            "snapshot": values,
        },
    )
    match_listing(listing)
    return created, changed


def scrape_source(source, *, max_pages=None):
    """Caller holds scrape_lock. Partial results are useful but never prove removal."""
    cls = REGISTRY.get(source.adapter)
    if cls is None:
        raise ValueError("Unknown scraper adapter")
    cls.validate_search_url(source.url)
    started = timezone.now()
    source.last_attempt_at = started
    source.requested_at = None
    source.save(update_fields=["last_attempt_at", "requested_at"])
    run = ScrapeRun.objects.create(source=source, started_at=started)
    scraper = None
    visited, seen_ids, errors = set(), set(), []
    next_url = source.url
    expected = None
    limit = max_pages or settings.SCRAPE_MAX_PAGES
    complete = False
    try:
        scraper = cls()
        while next_url:
            if next_url in visited:
                raise ScrapeError("Pagination loop detected")
            if run.pages >= limit:
                raise ScrapeError("Page limit reached; result is incomplete")
            visited.add(next_url)
            page = scraper.parse_search(scraper.fetch(next_url), next_url)
            if expected is None:
                expected = page.advertised_total
            run.pages += 1
            errors.extend(page.errors)
            for data in page.listings:
                if data.external_id in seen_ids:
                    continue
                seen_ids.add(data.external_id)
                enriched = True
                try:
                    data = scraper.enrich(data, scraper.fetch(data.url))
                except (ScrapeError, ValueError) as exc:
                    errors.append(f"{data.external_id}: {exc}")
                    enriched = False
                    # Stop hammering the site after a block or rate limit.
                    if "429" in str(exc) or "403" in str(exc) or "robots.txt" in str(exc):
                        raise
                created, changed = ingest(run, data, enriched=enriched)
                run.seen += 1
                run.created += int(created)
                run.changed += int(changed)
            run.save(update_fields=["pages", "seen", "created", "changed"])
            next_url = page.next_url
        if expected is not None and run.seen < expected:
            errors.append(
                f"Search advertised {expected} listings but only {run.seen} unique adverts were read; removal checks skipped."
            )
        complete = not errors
        run.status = ScrapeRun.Status.SUCCESS if complete else ScrapeRun.Status.PARTIAL
    except Exception as exc:
        logger.exception("Scrape failed for source %s", source.pk)
        errors.append(str(exc)[:500])
        run.status = ScrapeRun.Status.PARTIAL if run.seen else ScrapeRun.Status.FAILED
    finally:
        if scraper:
            scraper.close()
        run.errors = errors[:100]
        run.finished_at = timezone.now()
        run.save()
    if complete:
        with transaction.atomic():
            missing = SourceListing.objects.filter(source=source).exclude(last_seen_run=run)
            missing.update(missing_runs=F("missing_runs") + 1)
            missing.filter(missing_runs__gte=2).update(active=False)
            Source.objects.filter(pk=source.pk).update(last_success_at=run.finished_at)
    return run


def due(source, now=None):
    now = timezone.localtime(now or timezone.now())
    if not source.enabled:
        return False
    if source.requested_at:
        return True
    if now.hour < settings.SCRAPE_HOUR:
        return False
    return (
        source.last_attempt_at is None
        or timezone.localtime(source.last_attempt_at).date() < now.date()
    )


def run_pending():
    with scrape_lock() as acquired:
        if not acquired:
            return
        # Owning the global lock proves no other healthy scraper is still running.
        ScrapeRun.objects.filter(status=ScrapeRun.Status.RUNNING).update(
            status=ScrapeRun.Status.FAILED,
            finished_at=timezone.now(),
            errors=["Worker stopped before this run finished. No removal checks were applied."],
        )
        for source in Source.objects.filter(enabled=True).order_by("pk"):
            if due(source):
                scrape_source(source)
