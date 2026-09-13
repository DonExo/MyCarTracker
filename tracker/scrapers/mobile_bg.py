import re
from decimal import Decimal
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..colours import mobile_bg_colour
from .base import BaseScraper, ScrapedListing, ScrapeError, SearchPage

VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.I)
FUEL = {
    "Бензинов": "Petrol",
    "Дизелов": "Diesel",
    "Хибриден": "Hybrid",
    "Електрически": "Electric",
    "Plug-in хибрид": "Plug-in hybrid",
}


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def number(pattern, value):
    m = re.search(pattern, value, re.I)
    return int(re.sub(r"\D", "", m.group(1))) if m else None


def parse_price(value):
    # Prefer EUR on dual-currency adverts; do not accidentally read VAT or an old price.
    for symbol, currency in [(r"€|EUR", "EUR"), (r"лв\.?|BGN", "BGN")]:
        match = re.search(r"(\d[\d\s\u00a0.,]*)\s*(?:" + symbol + r")", value, re.I)
        if match:
            amount = re.sub(r"[\s\u00a0]", "", match.group(1))
            # Accept decimal fractions and thousands separators.
            if re.search(r"[,.]\d{2}$", amount):
                whole, fraction = re.split(r"[,.](?=\d{2}$)", amount)
                amount = re.sub(r"[,.]", "", whole) + "." + fraction
            else:
                amount = re.sub(r"[,.]", "", amount)
            price = Decimal(amount)
            if not 0 < price < Decimal("10000000"):
                raise ScrapeError("Price outside accepted range")
            return price, currency
    raise ScrapeError("No supported asking price found")


def parse_colour(soup):
    """Read the colour from Mobile.bg's labeled detail specifications."""
    for item in soup.select(".mainCarParams .item"):
        label = text(item.select_one(".mpLabel")).casefold().strip(" :")
        if "цвят" in label:
            return mobile_bg_colour(text(item.select_one(".mpInfo")))[:60]
    return ""


class MobileBgScraper(BaseScraper):
    allowed_hosts = {"www.mobile.bg", "mobile.bg"}
    vendor = "mobile.bg"

    @classmethod
    def validate_search_url(cls, url):
        cls.validate_url(url)
        if not re.fullmatch(
            r"/obiavi/avtomobili-dzhipove/audi/q8(?:/p-\d+)?/?", urlsplit(url).path
        ):
            raise ValueError("Start with a Mobile.bg Audi Q8 search URL.")

    def parse_search(self, html, url):
        self.validate_search_url(url)
        soup = BeautifulSoup(html, "html.parser")
        listings, errors = [], []
        cards = soup.select('div.item[id^="ida"]')
        total = number(r"от\s+общо\s+([\d\s]+)", text(soup))
        if not cards and total != 0:
            raise ScrapeError(
                "Search layout changed, or a challenge page was returned; no cards found."
            )
        for card in cards:
            try:
                a = card.select_one('a.title[href*="/obiava-"]')
                if not a:
                    raise ScrapeError("Listing title/link is missing")
                title = text(a)
                if not re.match(r"Audi\s+Q8\b", title, re.I):
                    raise ScrapeError("Unexpected model in Q8 search")
                parsed = urlsplit(urljoin(url, a["href"]))
                canonical = urlunsplit(("https", "www.mobile.bg", parsed.path, "", ""))
                self.validate_url(urljoin(url, a["href"]))
                identity = re.search(r"/obiava-(\d+)", parsed.path)
                if not identity:
                    raise ScrapeError("Listing ID is missing")
                price, currency = parse_price(text(card.select_one(".price > div")))
                params = [text(x) for x in card.select(".params span")]
                specs = " ".join(params)
                colour = ""
                for value in params:
                    colour = mobile_bg_colour(value)
                    if colour:
                        break
                description = text(card.select_one(".info"))
                vin = VIN_RE.search(description)
                images = []
                for img in card.select("img.pic, .small img"):
                    image_url = urljoin(url, img.get("src", ""))
                    if urlsplit(image_url).scheme == "https":
                        images.append(image_url)
                seller = text(card.select_one(".seller .name"))
                listings.append(
                    ScrapedListing(
                        external_id=identity.group(1),
                        url=canonical,
                        title=title[:300],
                        original_price=price,
                        currency=currency,
                        year=number(r"\b((?:19|20)\d{2})\s*г", specs),
                        mileage=number(r"([\d\s]+)\s*км", specs),
                        power_hp=number(r"(\d+)\s*к\.с", specs),
                        colour=colour[:60],
                        fuel=next((FUEL[p] for p in params if p in FUEL), ""),
                        location=text(card.select_one(".location"))[:200],
                        seller="" if seller == "Регион:" else seller[:200],
                        description=description,
                        vin=vin.group().upper() if vin else "",
                        images=list(dict.fromkeys(images)),
                        price_note=text(card.select_one(".price > span"))[:200],
                    )
                )
            except (ValueError, KeyError, ScrapeError) as exc:
                errors.append(f"{card.get('id', 'Unknown card')}: {exc}")
        next_link = next((a for a in soup.find_all("a", href=True) if text(a) == "Напред"), None)
        next_url = urljoin(url, next_link["href"]) if next_link else None
        if next_url:
            self.validate_search_url(next_url)
        return SearchPage(listings, next_url, errors, total)

    def enrich(self, listing, html):
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.select_one("h1")
        if (
            not h1
            or not re.search(r"Audi\s+Q8\b", text(h1), re.I)
            or not soup.select_one(".mainCarParams")
        ):
            raise ScrapeError("Detail page unavailable or layout changed")
        identity = re.search(r"Обява:\s*(\d+)", text(h1))
        if not identity or identity.group(1) != listing.external_id:
            raise ScrapeError("Detail page ID does not match the advert")
        description = text(soup.select_one(".moreInfo .text"))
        vin = VIN_RE.search(description)
        if vin:
            listing.vin = vin.group().upper()
        if description:
            listing.description = description
        colour = parse_colour(soup)
        if colour:
            listing.colour = colour
        phone = re.sub(r"\D", "", text(soup.select_one(".phone")))
        if phone.startswith("00359"):
            phone = phone[2:]
        elif phone.startswith("0") and len(phone) == 10:
            phone = "359" + phone[1:]
        listing.phone = phone[:40]
        return listing
