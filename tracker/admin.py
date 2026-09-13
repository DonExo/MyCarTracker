from django.contrib import admin

from .models import (
    DuplicateCandidate,
    GroupingEvent,
    Listing,
    PriceObservation,
    ScrapeRun,
    Source,
    Vehicle,
)


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    from .forms import SourceForm

    form = SourceForm
    list_display = ["name", "adapter", "enabled", "last_success_at"]
    readonly_fields = ["adapter", "last_attempt_at", "last_success_at", "requested_at"]


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Listing)
class ListingAdmin(ReadOnlyAdmin):
    list_display = ["title", "vin", "current_price", "last_seen", "vehicle"]
    search_fields = ["title", "vin", "external_id"]
    list_filter = ["fuel", "year", "adapter"]


for model in [Vehicle, PriceObservation, ScrapeRun, DuplicateCandidate, GroupingEvent]:
    admin.site.register(model, ReadOnlyAdmin)

admin.site.site_header = "MyCarTracker"
