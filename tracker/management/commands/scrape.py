from django.core.management.base import BaseCommand, CommandError

from tracker.models import Source
from tracker.services import scrape_lock, scrape_source


class Command(BaseCommand):
    help = "Scrape all enabled sources or one source immediately."

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int)
        parser.add_argument("--max-pages", type=int)

    def handle(self, *args, **options):
        if options["max_pages"] is not None and options["max_pages"] < 1:
            raise CommandError("--max-pages must be positive")
        sources = Source.objects.filter(enabled=True)
        if options["source"]:
            sources = sources.filter(pk=options["source"])
        if not sources.exists():
            raise CommandError("No enabled source found. Run seed_sources first.")
        failed = False
        with scrape_lock() as acquired:
            if not acquired:
                raise CommandError("Another scrape is already running")
            for source in sources:
                run = scrape_source(source, max_pages=options["max_pages"])
                self.stdout.write(
                    f"Run {run.pk}: {run.status}; {run.seen} adverts, {run.created} new, {run.changed} price changes"
                )
                for error in run.errors:
                    self.stderr.write(error)
                failed |= run.status != "success"
        if failed:
            raise CommandError("At least one scrape was incomplete; inspect run history.")
