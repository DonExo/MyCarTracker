from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Count, Exists, F, Max, Min, OuterRef, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import SourceForm
from .matching import merge_vehicles, pending_candidates, split_listing
from .models import (
    DuplicateCandidate,
    GroupingEvent,
    Listing,
    PriceObservation,
    ScrapeRun,
    Source,
    SourceListing,
    Vehicle,
)


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def listings_with_status():
    return Listing.objects.annotate(
        is_active=Exists(SourceListing.objects.filter(listing_id=OuterRef("pk"), active=True))
    )


def vehicle_queryset():
    active = Q(listings__memberships__active=True)
    dropped = active & Q(listings__previous_price__isnull=False) & Q(
        listings__current_price__lt=F("listings__previous_price")
    )
    return Vehicle.objects.annotate(
        active_count=Count("listings", filter=active, distinct=True),
        total_count=Count("listings", distinct=True),
        price_min=Min("listings__current_price", filter=active),
        price_max=Max("listings__current_price", filter=active),
        price_drop=Max(
            F("listings__previous_price") - F("listings__current_price"), filter=dropped
        ),
        last_seen=Max("listings__last_seen"),
    ).filter(total_count__gt=0)


@login_required
def dashboard(request):
    qs = vehicle_queryset()
    stats = {
        "cars": qs.filter(active_count__gt=0).count(),
        "adverts": listings_with_status().filter(is_active=True).count(),
        "duplicates": qs.filter(active_count__gt=1).count(),
        "review": pending_candidates().count(),
    }
    status = request.GET.get("status", "active")
    if status != "all":
        qs = qs.filter(active_count__gt=0)
    search = request.GET.get("q", "").strip()[:200]
    advert_filters = {}
    year_from = request.GET.get("year_from", "")
    year_to = request.GET.get("year_to", "")
    if year_from.isdigit():
        advert_filters["year__gte"] = int(year_from)
    if year_to.isdigit():
        advert_filters["year__lte"] = int(year_to)
    fuel = request.GET.get("fuel", "")
    if fuel:
        advert_filters["fuel"] = fuel
    colour = request.GET.get("colour", "")
    if colour:
        advert_filters["colour"] = colour
    vendor = request.GET.get("vendor", "")
    if vendor:
        advert_filters["vendor"] = vendor
    advert_search = Q()
    if search:
        advert_search = (
            Q(title__icontains=search)
            | Q(vin__icontains=search)
            | Q(location__icontains=search)
        )
    if advert_filters or search:
        matching_adverts = Listing.objects.filter(vehicle_id=OuterRef("pk"), **advert_filters)
        if status != "all":
            matching_adverts = matching_adverts.filter(memberships__active=True)
        if search:
            matching_adverts = matching_adverts.filter(advert_search)
        qs = qs.filter(Exists(matching_adverts))
    price_from = request.GET.get("price_from", "").strip()
    price_to = request.GET.get("price_to", "").strip()
    try:
        price_from_value = Decimal(price_from) if price_from else None
        if price_from_value is not None and (
            not price_from_value.is_finite()
            or price_from_value < 0
            or price_from_value >= Decimal("10000000")
        ):
            price_from_value = None
            price_from = ""
    except InvalidOperation:
        price_from_value = None
        price_from = ""
    try:
        price_to_value = Decimal(price_to) if price_to else None
        if price_to_value is not None and (
            not price_to_value.is_finite()
            or price_to_value < 0
            or price_to_value >= Decimal("10000000")
        ):
            price_to_value = None
            price_to = ""
    except InvalidOperation:
        price_to_value = None
        price_to = ""
    if price_from_value is not None or price_to_value is not None:
        price_filters = Q()
        if price_from_value is not None:
            price_filters &= Q(price_max__gte=price_from_value)
        if price_to_value is not None:
            price_filters &= Q(price_min__lte=price_to_value)
        qs = qs.filter(price_filters)
    if (
        year_from.isdigit()
        and year_to.isdigit()
        and int(year_from) > int(year_to)
    ) or (
        price_from_value is not None
        and price_to_value is not None
        and price_from_value > price_to_value
    ):
        qs = qs.none()
    favourite = request.GET.get("favourite") == "1"
    if favourite:
        qs = qs.filter(is_favourite=True)
    mode = request.GET.get("mode", "")
    if mode == "duplicates":
        qs = qs.filter(active_count__gt=1)
    elif mode == "drops":
        drops = Listing.objects.filter(
            vehicle_id=OuterRef("pk"),
            current_price__lt=F("previous_price"),
            price_changed_at__gte=timezone.now() - timedelta(days=7),
        )
        qs = qs.filter(Exists(drops))
    sort = request.GET.get("sort", "price")
    order = {
        "recent": "-last_seen",
        "price": "price_min",
        "price_desc": "-price_min",
        "drop": "-price_drop",
    }.get(sort, "price_min")
    qs = qs.order_by("-is_favourite", order, "pk").prefetch_related(
        Prefetch(
            "listings",
            queryset=listings_with_status().order_by("-is_active", "current_price", "pk"),
            to_attr="adverts",
        )
    )
    page = Paginator(qs, 24).get_page(request.GET.get("page"))
    price_choices = list(range(10000, 100001, 10000))
    for vehicle in page:
        vehicle.hero = vehicle.adverts[0]
    params = request.GET.copy()
    params.pop("page", None)
    context = {
        "page": page,
        "stats": stats,
        "query_params": params.urlencode(),
        "years": Listing.objects.exclude(year=None)
        .values_list("year", flat=True)
        .distinct()
        .order_by("year"),
        "colours": Listing.objects.exclude(colour="")
        .values_list("colour", flat=True)
        .distinct()
        .order_by("colour"),
        "fuels": Listing.objects.exclude(fuel="")
        .values_list("fuel", flat=True)
        .distinct()
        .order_by("fuel"),
        "vendors": Listing.objects.exclude(vendor="")
        .values_list("vendor", flat=True)
        .distinct()
        .order_by("vendor"),
        "price_choices": price_choices,
        "q": search,
        "year_from": year_from,
        "year_to": year_to,
        "price_from": price_from,
        "price_to": price_to,
        "fuel": fuel,
        "colour": colour,
        "vendor": vendor,
        "mode": mode,
        "favourite": favourite,
        "sort": sort,
        "status": status,
        "latest_run": ScrapeRun.objects.select_related("source").first(),
    }
    template = (
        "tracker/vehicle_table.html"
        if request.headers.get("HX-Request")
        else "tracker/dashboard.html"
    )
    return render(request, template, context)


@login_required
@require_POST
def toggle_favourite(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    vehicle.is_favourite = not vehicle.is_favourite
    vehicle.save(update_fields=["is_favourite"])
    messages.success(
        request,
        "Added to favourites."
        if vehicle.is_favourite
        else "Removed from favourites.",
    )
    destination = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        destination, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        destination = ""
    return redirect(destination or "dashboard")


@login_required
def vehicle_detail(request, pk):
    vehicle = get_object_or_404(vehicle_queryset(), pk=pk)
    listings = list(
        listings_with_status().filter(vehicle=vehicle).order_by("-is_active", "current_price")
    )
    history = PriceObservation.objects.filter(listing__vehicle=vehicle).select_related(
        "listing", "run"
    )
    # Downsample to the last actual observation per calendar day; retain full DB history.
    series = []
    for listing in listings:
        daily = {}
        for observation in history.filter(listing=listing):
            daily[timezone.localtime(observation.observed_at).date().isoformat()] = float(
                observation.price_eur
            )
        series.append(
            {
                "label": f"{listing.adapter} · {listing.external_id}",
                "points": [{"date": d, "price": p} for d, p in daily.items()],
            }
        )
    return render(
        request,
        "tracker/detail.html",
        {
            "vehicle": vehicle,
            "hero": listings[0],
            "listings": listings,
            "series": series,
            "history": Paginator(history.order_by("-observed_at", "-pk"), 50).get_page(
                request.GET.get("page")
            ),
            "events": GroupingEvent.objects.filter(target_vehicle=vehicle).order_by("-created_at")[
                :20
            ],
        },
    )


@login_required
def sources(request):
    form = SourceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Search saved. Use Scrape now for the first collection.")
        return redirect("sources")
    return render(
        request,
        "tracker/sources.html",
        {
            "sources": Source.objects.order_by("pk"),
            "form": form,
            "scrape_hour": settings.SCRAPE_HOUR,
            "time_zone": settings.TIME_ZONE,
        },
    )


@login_required
@require_POST
def source_action(request, pk):
    with transaction.atomic():
        source = get_object_or_404(Source.objects.select_for_update(), pk=pk)
        action = request.POST.get("action")
        if action == "queue":
            if not source.enabled:
                messages.error(request, "Resume this search before scraping it.")
            elif source.runs.filter(status="running").exists():
                messages.info(request, "This search is already being scraped.")
            else:
                source.requested_at = timezone.now()
                source.save(update_fields=["requested_at"])
                messages.success(
                    request, "Scrape queued. The scheduler checks for requests every 30 seconds."
                )
        elif action == "toggle":
            source.enabled = not source.enabled
            source.save(update_fields=["enabled"])
            messages.success(
                request,
                "Search resumed."
                if source.enabled
                else "Search paused. Any running scrape will finish.",
            )
        else:
            return HttpResponse("Unknown action", status=400)
    return redirect("sources")


@login_required
def runs(request):
    return render(
        request,
        "tracker/runs.html",
        {
            "page": Paginator(ScrapeRun.objects.select_related("source"), 30).get_page(
                request.GET.get("page")
            )
        },
    )


@login_required
def duplicates(request):
    return render(
        request,
        "tracker/duplicates.html",
        {"page": Paginator(pending_candidates(), 20).get_page(request.GET.get("page"))},
    )


@login_required
@require_POST
def review_duplicate(request, pk):
    with transaction.atomic():
        candidate = get_object_or_404(
            DuplicateCandidate.objects.select_for_update(), pk=pk, status="pending"
        )
        action = request.POST.get("action")
        if action == "merge":
            try:
                merge_vehicles(
                    candidate.a.vehicle_id,
                    candidate.b.vehicle_id,
                    f"Reviewed by {request.user.get_username()}",
                    manual=True,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("duplicates")
            candidate.status = "merged"
            messages.success(request, "Adverts grouped. Both histories are preserved.")
        elif action == "reject":
            candidate.status = "rejected"
            messages.success(
                request, "Kept as different cars. This pair will not be suggested again."
            )
        else:
            return HttpResponse("Unknown action", status=400)
        candidate.save(update_fields=["status"])
    return redirect("duplicates")


@login_required
@require_POST
def split(request, pk):
    listing = get_object_or_404(Listing, pk=pk)
    try:
        vehicle = split_listing(pk)
        messages.success(request, "Advert separated into its own car. Its history is intact.")
        return redirect("vehicle", pk=vehicle.pk)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("vehicle", pk=listing.vehicle_id)
