from urllib.parse import urlsplit

from .mobile_bg import MobileBgScraper

REGISTRY = {"mobile_bg": MobileBgScraper}


def adapter_for_url(url):
    parsed = urlsplit(url)
    for key, cls in REGISTRY.items():
        if parsed.hostname in cls.allowed_hosts:
            cls.validate_search_url(url)
            return key
    raise ValueError(
        "This website has no scraper yet. Add a dedicated adapter before saving its URL."
    )
