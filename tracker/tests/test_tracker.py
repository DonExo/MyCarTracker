from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from tracker.colours import mobile_bg_colour
from tracker.forms import SourceForm
from tracker.colours import mobile_bg_colour
from tracker.matching import match_listing, merge_vehicles, similarity, split_listing
from tracker.models import (
    DuplicateCandidate,
    Listing,
    PriceObservation,
    ScrapeRun,
    Source,
    SourceListing,
)
from tracker.scrapers import adapter_for_url
from tracker.scrapers.base import ScrapedListing, ScrapeError, SearchPage
from tracker.scrapers.mobile_bg import MobileBgScraper, parse_price
from tracker.services import due, ingest, run_pending, scrape_lock, scrape_source

URL = "https://www.mobile.bg/obiavi/avtomobili-dzhipove/audi/q8"
FIXTURES = Path(__file__).parent / "fixtures"
VIN = "WAUZZZF17KD030042"


def advert(identity="1", price="50000", **kwargs):
    return ScrapedListing(
        external_id=identity,
        url=f"https://www.mobile.bg/obiava-{identity}-audi-q8",
        title="Audi Q8 55 TFSI",
        original_price=Decimal(price),
        currency="EUR",
        **kwargs,
    )


class ParserTests(SimpleTestCase):
    def setUp(self):
        self.scraper = MobileBgScraper()
        self.addCleanup(self.scraper.close)

    def test_real_shape_search_and_detail(self):
        page = self.scraper.parse_search((FIXTURES / "mobile_search.html").read_bytes(), URL)
        self.assertEqual(page.errors, [])
        self.assertEqual(page.advertised_total, 2)
        self.assertEqual(page.next_url, URL + "/p-2")
        self.assertEqual(page.listings[0].colour, "Black")
        car = self.scraper.enrich(page.listings[0], (FIXTURES / "mobile_detail.html").read_bytes())
        self.assertEqual(
            (car.year, car.mileage, car.fuel, car.power_hp), (2021, 87000, "Petrol", 340)
        )
        self.assertEqual(car.colour, "Black metallic")
        self.assertEqual(car.price_eur, Decimal("50000"))
        self.assertEqual(car.vin, VIN)
        self.assertEqual(car.phone, "359888000000")
        self.assertEqual(car.price_note, "Цената е с включено ДДС")

    def test_search_colour_is_found_when_spec_order_changes(self):
        html = (FIXTURES / "mobile_search.html").read_text().replace(
            "<span>Черен</span><span>Бензинов</span>",
            "<span>Бензинов</span><span>Черен</span>",
        )
        page = self.scraper.parse_search(html, URL)
        self.assertEqual(page.listings[0].colour, "Black")

    def test_currency_and_number_formats(self):
        for value in ["50 000 €", "50.000 EUR", "50,000 EUR", "50 000,00 €", "50,000.00 €"]:
            self.assertEqual(parse_price(value), (Decimal("50000"), "EUR"))
        self.assertEqual(parse_price("97 791,50 лв."), (Decimal("97791.50"), "BGN"))
        data = advert()
        data.currency, data.original_price = "BGN", Decimal("97791.50")
        self.assertEqual(data.price_eur, Decimal("50000"))
        for value in ["По договаряне", "0 €", "Call for price", "$50000"]:
            with self.assertRaises(ScrapeError):
                parse_price(value)

    def test_mobile_bg_colour_translations_and_spec_rejection(self):
        translations = {
            "Тъмно син": "Dark blue",
            "Бежов": "Beige",
            "Бял": "White",
            "Графит": "Graphite",
            "Зелен": "Green",
            "Златист": "Gold",
            "Металик": "Metallic",
            "Оранжев": "Orange",
            "Перла": "Pearl",
            "Светло сив": "Light gray",
            "Сив": "Gray",
            "Син": "Blue",
            "Сребърен": "Silver",
            "Т.зелен": "Dark green",
            "Тъмно сив": "Dark gray",
            "Тъмно син мет.": "Dark blue metallic",
            "Тъмно червен": "Dark red",
            "Хамелеон": "Chameleon",
            "Червен": "Red",
            "Черен": "Black",
        }
        for source, expected in translations.items():
            with self.subTest(source=source):
                self.assertEqual(mobile_bg_colour(source), expected)
        for fuel in ["Бензинов", "Дизелов", "Хибриден"]:
            self.assertEqual(mobile_bg_colour(fuel), "")

    def test_blocked_or_changed_layout_is_not_empty_inventory(self):
        with self.assertRaises(ScrapeError):
            self.scraper.parse_search("<h1>Please verify you are human</h1>", URL)
        self.assertEqual(self.scraper.parse_search("<div>0 от общо 0</div>", URL).listings, [])

    def test_malformed_card_is_reported(self):
        html = (
            (FIXTURES / "mobile_search.html")
            .read_text()
            .replace("50 000 € / 97 791,50 лв.", "Call for price")
        )
        page = self.scraper.parse_search(html, URL)
        self.assertEqual(len(page.errors), 1)
        self.assertEqual(page.listings, [])

    def test_search_url_allowlist(self):
        self.assertEqual(adapter_for_url(URL), "mobile_bg")
        for url in [
            "http://127.0.0.1/",
            "https://www.mobile.bg.evil.test/",
            "https://www.mobile.bg:8080/",
            "https://user@www.mobile.bg/",
            "https://www.mobile.bg/obiavi/bmw",
            "file:///etc/passwd",
        ]:
            with self.assertRaises(ValueError):
                adapter_for_url(url)

    def test_detail_identity_must_match(self):
        page = self.scraper.parse_search((FIXTURES / "mobile_search.html").read_bytes(), URL)
        html = (FIXTURES / "mobile_detail.html").read_text().replace("1234567890", "999")
        with self.assertRaises(ScrapeError):
            self.scraper.enrich(page.listings[0], html)

    def test_redirect_to_untrusted_host_is_not_fetched(self):
        robots = Mock(status_code=200, text="User-agent: *\nAllow: /")
        redirect = Mock(is_redirect=True, headers={"location": "https://127.0.0.1/secrets"})
        with patch.object(self.scraper, "_request", side_effect=[robots, redirect]) as request:
            with self.assertRaises(ValueError):
                self.scraper.fetch(URL)
        self.assertEqual(request.call_count, 2)

    def test_robots_disallow_stops_before_page_request(self):
        robots = Mock(status_code=200, text="User-agent: *\nDisallow: /")
        with patch.object(self.scraper, "_request", return_value=robots) as request:
            with self.assertRaises(ScrapeError):
                self.scraper.fetch(URL)
        self.assertEqual(request.call_count, 1)


class TrackerFixture(TestCase):
    def setUp(self):
        self.source = Source.objects.create(name="Q8", url=URL)
        self.run = ScrapeRun.objects.create(source=self.source, status="success")

    def add(self, identity="1", price="50000", **kwargs):
        ingest(self.run, advert(identity, price, **kwargs))
        return Listing.objects.get(external_id=identity)


class TrackerTests(TrackerFixture):
    def test_same_advert_is_idempotent_and_tracks_new_day(self):
        car = self.add()
        self.add()
        self.assertEqual(Listing.objects.count(), 1)
        self.assertEqual(PriceObservation.objects.count(), 1)
        later = ScrapeRun.objects.create(source=self.source)
        self.assertEqual(ingest(later, advert(price="48000")), (False, True))
        car.refresh_from_db()
        self.assertEqual(car.previous_price, Decimal("50000"))
        self.assertEqual(car.current_price, Decimal("48000"))
        self.assertEqual(car.observations.count(), 2)

    def test_unchanged_daily_price_still_has_observation(self):
        self.add()
        ingest(ScrapeRun.objects.create(source=self.source), advert())
        self.assertEqual(PriceObservation.objects.count(), 2)
        self.assertIsNone(Listing.objects.get().previous_price)

    def test_overlapping_searches_share_one_listing(self):
        self.add()
        other = Source.objects.create(name="Second search", url=URL + "?test=1")
        ingest(ScrapeRun.objects.create(source=other), advert())
        self.assertEqual(Listing.objects.count(), 1)
        self.assertEqual(SourceListing.objects.count(), 2)

    def test_identical_vin_groups_and_preserves_prices(self):
        a = self.add("1", vin=VIN)
        b = self.add("2", "47000", vin=VIN)
        self.assertEqual(a.vehicle_id, b.vehicle_id)
        self.assertEqual(Listing.objects.count(), 2)
        self.assertEqual(PriceObservation.objects.count(), 2)

    def test_similar_cars_are_candidates_not_automatic_merges(self):
        attrs = dict(
            year=2021,
            fuel="Petrol",
            power_hp=340,
            mileage=80000,
            colour="Black",
            phone="359888000000",
        )
        a, b = self.add("1", **attrs), self.add("2", **attrs)
        self.assertNotEqual(a.vehicle_id, b.vehicle_id)
        candidate = DuplicateCandidate.objects.get()
        self.assertGreaterEqual(candidate.score, 70)
        candidate.status = "rejected"
        candidate.save()
        match_listing(b)
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, "rejected")

    def test_conflicting_vins_never_merge(self):
        a = self.add("1", vin=VIN)
        b = self.add("2", vin="WAUZZZF17LD018362")
        self.assertEqual(similarity(a, b), (0, []))
        with self.assertRaises(ValueError):
            merge_vehicles(a.vehicle_id, b.vehicle_id, "test")

    def test_split_preserves_history_and_survives_rescrape(self):
        a, b = self.add("1", vin=VIN), self.add("2", vin=VIN)
        split_listing(b.pk)
        ingest(ScrapeRun.objects.create(source=self.source), advert("2", vin=VIN))
        b.refresh_from_db()
        self.assertNotEqual(a.vehicle_id, b.vehicle_id)
        self.assertEqual(b.observations.count(), 2)

    def test_detail_failure_preserves_vin_and_phone(self):
        car = self.add(vin=VIN, phone="123")
        ingest(ScrapeRun.objects.create(source=self.source), advert(price="48000"), enriched=False)
        car.refresh_from_db()
        self.assertEqual((car.vin, car.phone), (VIN, "123"))

    def test_empty_colour_does_not_erase_previous_value(self):
        car = self.add(colour="Black")
        ingest(ScrapeRun.objects.create(source=self.source), advert())
        car.refresh_from_db()
        self.assertEqual(car.colour, "Black")

    def fake_scrape(self, page):
        scraper = Mock()
        scraper.parse_search.return_value = page
        scraper.enrich.side_effect = lambda data, html: data
        with patch.dict("tracker.services.REGISTRY", {"mobile_bg": Mock(return_value=scraper)}):
            return scrape_source(self.source)

    def test_only_two_complete_misses_mark_missing(self):
        self.add()
        page = SearchPage([], None, advertised_total=0)
        self.fake_scrape(page)
        self.assertTrue(SourceListing.objects.get().active)
        self.fake_scrape(page)
        self.assertFalse(SourceListing.objects.get().active)
        self.assertEqual(PriceObservation.objects.count(), 1)

    def test_partial_run_cannot_mark_missing(self):
        self.add()
        run = self.fake_scrape(SearchPage([], None, errors=["bad card"], advertised_total=1))
        self.assertEqual(run.status, "partial")
        self.assertEqual(SourceListing.objects.get().missing_runs, 0)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_success_at)

    def test_truncated_pagination_cannot_mark_missing(self):
        self.add()
        run = self.fake_scrape(SearchPage([], None, advertised_total=30))
        self.assertEqual(run.status, "partial")
        self.assertTrue(SourceListing.objects.get().active)

    def test_page_limit_marks_partial(self):
        scraper = Mock()
        scraper.parse_search.return_value = SearchPage([advert("2")], URL + "/p-2")
        scraper.enrich.side_effect = lambda data, html: data
        with patch.dict("tracker.services.REGISTRY", {"mobile_bg": Mock(return_value=scraper)}):
            run = scrape_source(self.source, max_pages=1)
        self.assertEqual((run.status, run.seen), ("partial", 1))

    def test_pagination_deduplicates_repeated_promoted_adverts(self):
        scraper = Mock()
        scraper.parse_search.side_effect = [
            SearchPage([advert()], URL + "/p-2", advertised_total=2),
            SearchPage([advert(), advert("2")], None),
        ]
        scraper.enrich.side_effect = lambda data, html: data
        with patch.dict("tracker.services.REGISTRY", {"mobile_bg": Mock(return_value=scraper)}):
            run = scrape_source(self.source)
        self.assertEqual((run.status, run.pages, run.seen), ("success", 2, 2))
        self.assertEqual(PriceObservation.objects.count(), 2)

    @override_settings(TIME_ZONE="Europe/Skopje", SCRAPE_HOUR=6)
    def test_daily_schedule_and_manual_queue(self):
        early = datetime(2026, 9, 13, 3, 0, tzinfo=UTC)
        morning = datetime(2026, 9, 13, 4, 0, tzinfo=UTC)
        self.assertFalse(due(self.source, early))
        self.assertTrue(due(self.source, morning))
        self.source.last_attempt_at = morning
        self.assertFalse(due(self.source, morning))
        self.source.requested_at = morning
        self.assertTrue(due(self.source, morning))
        self.source.enabled = False
        self.assertFalse(due(self.source, morning))

    def test_crashed_run_recovery(self):
        self.run.status = "running"
        self.run.save()
        self.source.enabled = False
        self.source.save()
        run_pending()
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")

    def test_only_one_scraper_can_hold_lock(self):
        from django.db import connection

        if connection.vendor == "postgresql":
            other = connection.copy(alias="lock_check")
            try:
                with scrape_lock() as first, other.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", [72416382])
                    self.assertTrue(first)
                    self.assertFalse(cursor.fetchone()[0])
            finally:
                other.close()
            return
        with scrape_lock() as first, scrape_lock() as second:
            self.assertTrue(first)
            self.assertFalse(second)

    def test_source_form_rejects_arbitrary_host(self):
        form = SourceForm(data={"name": "Internal", "url": "https://127.0.0.1/private"})
        self.assertFalse(form.is_valid())


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class UITests(TrackerFixture):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user("donald", password="test-password-only")
        self.client.force_login(self.user)

    def test_all_main_pages_and_empty_states(self):
        for name in ["dashboard", "sources", "runs", "duplicates"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_anonymous_access_requires_login(self):
        self.client.logout()
        for name in ["dashboard", "sources", "runs", "duplicates"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_group_range_and_history_render(self):
        car = self.add("1", vin=VIN)
        self.add("2", "48000", vin=VIN)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "€48,000 – €50,000")
        response = self.client.get(reverse("vehicle", args=[car.vehicle_id]))
        self.assertContains(response, "price-data")
        self.assertContains(response, "All observations")

    def test_colour_is_visible_on_dashboard_and_vehicle_detail(self):
        car = self.add(colour="Black metallic")
        self.assertContains(self.client.get(reverse("dashboard")), "Black metallic")
        self.assertContains(
            self.client.get(reverse("vehicle", args=[car.vehicle_id])), "Black metallic"
        )

    def test_filters_and_htmx_fragment(self):
        self.add(year=2021, fuel="Petrol")
        response = self.client.get(
            reverse("dashboard"),
            {"year_from": "2021", "year_to": "2021"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, 'id="results"')
        self.assertNotContains(response, "<!doctype html>")
        response = self.client.get(reverse("dashboard"), {"year_from": "2022"})
        self.assertContains(response, "No cars match")

    def test_year_price_and_colour_filters_apply_together(self):
        shared = dict(year=2022, colour="Blue", vin=VIN)
        first = self.add("1", "40000", **shared)
        self.add("2", "60000", **shared)
        self.add("3", "52000", year=2022, colour="Black")

        response = self.client.get(
            reverse("dashboard"),
            {
                "year_from": "2021",
                "year_to": "2023",
                "price_from": "55000",
                "price_to": "65000",
                "colour": "Blue",
            },
        )

        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertEqual(response.context["page"].object_list[0].pk, first.vehicle_id)
        self.assertContains(response, 'name="year_from"')
        self.assertContains(response, 'name="year_to"')
        self.assertContains(response, 'name="price_from"')
        self.assertContains(response, 'name="price_to"')
        self.assertContains(response, 'name="colour"')

    def test_actions_require_post_and_csrf(self):
        url = reverse("source_action", args=[self.source.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        self.assertEqual(secure.post(url, {"action": "queue"}).status_code, 403)
        self.assertEqual(self.client.post(url, {"action": "queue"}).status_code, 302)
        self.source.refresh_from_db()
        self.assertIsNotNone(self.source.requested_at)

    def test_review_merge_and_split_end_to_end(self):
        attrs = dict(
            year=2021, fuel="Petrol", power_hp=340, mileage=80000, colour="Black", phone="123"
        )
        a, b = self.add("1", **attrs), self.add("2", **attrs)
        match = DuplicateCandidate.objects.get()
        self.assertEqual(self.client.get(reverse("duplicates")).status_code, 200)
        self.client.post(reverse("review_duplicate", args=[match.pk]), {"action": "merge"})
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.vehicle_id, b.vehicle_id)
        self.client.post(reverse("split", args=[b.pk]))
        b.refresh_from_db()
        self.assertNotEqual(a.vehicle_id, b.vehicle_id)
