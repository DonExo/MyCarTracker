import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from django.conf import settings


class ScrapeError(Exception):
    pass


@dataclass
class ScrapedListing:
    external_id: str
    url: str
    title: str
    original_price: Decimal
    currency: str
    year: int | None = None
    mileage: int | None = None
    fuel: str = ""
    power_hp: int | None = None
    colour: str = ""
    seller: str = ""
    phone: str = ""
    location: str = ""
    description: str = ""
    vin: str = ""
    images: list[str] = field(default_factory=list)
    price_note: str = ""

    @property
    def price_eur(self):
        if self.currency == "EUR":
            return self.original_price
        if self.currency == "BGN":
            return (self.original_price / Decimal("1.95583")).quantize(Decimal("0.01"))
        raise ScrapeError(f"Unsupported currency: {self.currency}")


@dataclass
class SearchPage:
    listings: list[ScrapedListing]
    next_url: str | None
    errors: list[str] = field(default_factory=list)
    advertised_total: int | None = None


class BaseScraper(ABC):
    allowed_hosts: set[str] = set()
    # Public marketplace name shown in the UI and stored on each listing.
    vendor: str = ""

    def __init__(self):
        self.client = httpx.Client(
            headers={"User-Agent": settings.SCRAPER_USER_AGENT},
            timeout=30,
            follow_redirects=False,
        )
        self.robots = {}
        self.last_request = 0.0

    def close(self):
        self.client.close()

    @classmethod
    def validate_url(cls, url):
        p = urlsplit(url)
        if (
            p.scheme != "https"
            or p.hostname not in cls.allowed_hosts
            or p.port not in (None, 443)
            or p.username
            or p.password
        ):
            raise ValueError("Only HTTPS URLs on the adapter's allowlisted host are accepted.")

    @classmethod
    @abstractmethod
    def validate_search_url(cls, url): ...

    def _request(self, url, delay=None):
        self.validate_url(url)
        for attempt in range(3):
            wait = (delay or settings.SCRAPE_DELAY_SECONDS) - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            self.last_request = time.monotonic()
            try:
                response = self.client.get(url)
            except httpx.TransportError:
                if attempt == 2:
                    raise ScrapeError("Network timeout or connection failure") from None
                time.sleep(2 ** (attempt + 1))
                continue
            if response.status_code == 429:
                raise ScrapeError("Website rate limited this run (HTTP 429); try again later.")
            if response.status_code >= 500 and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            return response
        raise ScrapeError("Request failed")

    def fetch(self, url):
        # Check each redirect before issuing another request. Never fetch arbitrary hosts.
        for _ in range(5):
            self.validate_url(url)
            p = urlsplit(url)
            origin = f"{p.scheme}://{p.netloc}"
            if origin not in self.robots:
                response = self._request(origin + "/robots.txt")
                if response.status_code == 404:
                    lines = []
                elif response.status_code == 200:
                    lines = response.text.splitlines()
                else:
                    raise ScrapeError("Could not verify robots.txt; scrape stopped.")
                robot = RobotFileParser()
                robot.parse(lines)
                self.robots[origin] = robot
            robot = self.robots[origin]
            if not robot.can_fetch(settings.SCRAPER_USER_AGENT, url):
                raise ScrapeError("This path is disallowed by robots.txt.")
            delay = max(
                settings.SCRAPE_DELAY_SECONDS, robot.crawl_delay(settings.SCRAPER_USER_AGENT) or 0
            )
            response = self._request(url, delay)
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            if response.status_code != 200:
                raise ScrapeError(f"Website returned HTTP {response.status_code}")
            if len(response.content) > 8_000_000:
                raise ScrapeError("Page exceeds the size limit")
            return response.content
        raise ScrapeError("Too many redirects")

    @abstractmethod
    def parse_search(self, html, url) -> SearchPage: ...

    @abstractmethod
    def enrich(self, listing, html) -> ScrapedListing: ...
