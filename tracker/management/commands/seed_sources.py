from django.core.management.base import BaseCommand

from tracker.models import Source


class Command(BaseCommand):
    help = "Idempotently add the initial Mobile.bg Audi Q8 search."

    def handle(self, *args, **options):
        source, _ = Source.objects.get_or_create(
            url="https://www.mobile.bg/obiavi/avtomobili-dzhipove/audi/q8",
            defaults={"name": "Audi Q8 · Mobile.bg", "adapter": "mobile_bg"},
        )
        self.stdout.write(f"Source ready: {source}")
