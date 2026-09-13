from django.db import models
from django.db.models import Q
from django.utils import timezone


class Source(models.Model):
    name = models.CharField(max_length=120)
    adapter = models.CharField(max_length=40, default="mobile_bg")
    url = models.URLField(max_length=1000, unique=True)
    enabled = models.BooleanField(default=True)
    requested_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class ScrapeRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Complete"
        PARTIAL = "partial", "Incomplete"
        FAILED = "failed", "Failed"

    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="runs")
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.RUNNING)
    pages = models.PositiveIntegerField(default=0)
    seen = models.PositiveIntegerField(default=0)
    created = models.PositiveIntegerField(default=0)
    changed = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list)

    class Meta:
        ordering = ["-started_at"]


class Vehicle(models.Model):
    """A physical car; listings and observations are never deleted by grouping."""

    created_at = models.DateTimeField(default=timezone.now)
    make = models.CharField(max_length=30, default="Audi")
    model = models.CharField(max_length=30, default="Q8")

    def __str__(self):
        return f"{self.make} {self.model} · #{self.pk}"


class Listing(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="listings")
    # Identity belongs to a website, independent of overlapping saved searches.
    adapter = models.CharField(max_length=40)
    external_id = models.CharField(max_length=100)
    url = models.URLField(max_length=1000)
    title = models.CharField(max_length=300)
    vin = models.CharField(max_length=17, blank=True, db_index=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    mileage = models.PositiveIntegerField(null=True, blank=True)
    fuel = models.CharField(max_length=30, blank=True)
    power_hp = models.PositiveSmallIntegerField(null=True, blank=True)
    colour = models.CharField(max_length=60, blank=True)
    seller = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    location = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    images = models.JSONField(default=list)
    current_price = models.DecimalField(max_digits=12, decimal_places=2)
    original_price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="EUR")
    price_note = models.CharField(max_length=200, blank=True)
    previous_price = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    price_changed_at = models.DateTimeField(null=True)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    identity_locked = models.BooleanField(default=False)
    sources = models.ManyToManyField(Source, through="SourceListing", related_name="listings")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["adapter", "external_id"], name="unique_site_listing"),
            models.CheckConstraint(condition=Q(current_price__gt=0), name="positive_current_price"),
        ]

    def __str__(self):
        return self.title


class SourceListing(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="memberships")
    last_seen_run = models.ForeignKey(ScrapeRun, on_delete=models.PROTECT)
    missing_runs = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "listing"], name="unique_source_listing")
        ]


class PriceObservation(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="observations")
    run = models.ForeignKey(ScrapeRun, on_delete=models.PROTECT, related_name="observations")
    observed_at = models.DateTimeField(default=timezone.now)
    price_eur = models.DecimalField(max_digits=12, decimal_places=2)
    original_price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["observed_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["listing", "run"], name="one_observation_per_run")
        ]


class DuplicateCandidate(models.Model):
    a = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="matches_as_a")
    b = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="matches_as_b")
    score = models.PositiveSmallIntegerField()
    reasons = models.JSONField(default=list)
    status = models.CharField(
        max_length=10,
        choices=[("pending", "Review"), ("merged", "Merged"), ("rejected", "Different cars")],
        default="pending",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["a", "b"], name="unique_candidate_pair"),
            models.CheckConstraint(condition=Q(a__lt=models.F("b")), name="ordered_candidate_pair"),
        ]


class GroupingEvent(models.Model):
    created_at = models.DateTimeField(default=timezone.now)
    action = models.CharField(max_length=30)
    listing_ids = models.JSONField(default=list)
    previous_vehicle_ids = models.JSONField(default=list)
    target_vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    reason = models.CharField(max_length=200)
