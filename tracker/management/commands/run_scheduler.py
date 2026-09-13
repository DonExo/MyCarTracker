import logging
import signal
import threading

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from tracker.services import run_pending


class Command(BaseCommand):
    help = "Run due daily scrapes and manually queued runs (poll every 30 seconds)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        self.stdout.write("Scheduler started; daily schedule uses Django TIME_ZONE.")
        while not stop.is_set():
            try:
                close_old_connections()
                run_pending()
            except Exception:
                logging.getLogger(__name__).exception("Scheduler iteration failed")
            if options["once"]:
                break
            stop.wait(30)
