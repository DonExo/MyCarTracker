from itertools import combinations

from django.db import transaction
from django.db.models import F, Q

from .models import DuplicateCandidate, GroupingEvent, Listing, Vehicle


def similarity(a, b):
    if a.vin and b.vin:
        return (100, ["Matching VIN"]) if a.vin == b.vin else (0, [])

    checks = [
        (
            a.mileage is not None
            and b.mileage is not None
            and abs(a.mileage - b.mileage) <= 3000,
            "Mileage within 3,000 km",
            40,
        ),
        (a.year is not None and a.year == b.year, "Same year", 20),
        (
            bool(a.colour and b.colour)
            and a.colour.strip().casefold() == b.colour.strip().casefold(),
            "Same colour",
            20,
        ),
        (a.current_price == b.current_price, "Same price", 20),
    ]
    reasons = [reason for matched, reason, _ in checks if matched]
    score = sum(weight for matched, _, weight in checks if matched)
    return score, reasons


def _possible_duplicate_filter(listing):
    """Fetch only adverts that could match at least three available facts."""
    possible = Q(vin=listing.vin) if listing.vin else Q()
    checks = []
    if listing.mileage is not None:
        checks.append(
            Q(
                mileage__gte=max(0, listing.mileage - 3000),
                mileage__lte=listing.mileage + 3000,
            )
        )
    if listing.year is not None:
        checks.append(Q(year=listing.year))
    if listing.colour:
        checks.append(Q(colour__iexact=listing.colour.strip()))
    checks.append(Q(current_price=listing.current_price))

    if len(checks) >= 3:
        for group in combinations(checks, 3):
            combination_filter = Q()
            for check in group:
                combination_filter &= check
            possible |= combination_filter

    return possible if listing.vin or len(checks) >= 3 else None


@transaction.atomic
def merge_vehicles(target_id, other_id, reason, manual=False):
    if target_id == other_id:
        return
    # A stable lock order prevents simultaneous reviews deadlocking.
    list(
        Vehicle.objects.select_for_update()
        .filter(pk__in=sorted([target_id, other_id]))
        .order_by("pk")
    )
    members = Listing.objects.filter(vehicle_id__in=[target_id, other_id])
    vins = set(members.exclude(vin="").values_list("vin", flat=True))
    if len(vins) > 1:
        raise ValueError("These groups contain conflicting VINs and cannot be merged.")
    moved = list(Listing.objects.filter(vehicle_id=other_id).values_list("pk", flat=True))
    Listing.objects.filter(pk__in=moved).update(vehicle_id=target_id)
    if manual:
        Listing.objects.filter(vehicle_id=target_id).update(identity_locked=True)
    GroupingEvent.objects.create(
        action="manual_merge" if manual else "vin_merge",
        listing_ids=moved,
        previous_vehicle_ids=[other_id],
        target_vehicle_id=target_id,
        reason=reason,
    )
    DuplicateCandidate.objects.filter(
        a__vehicle_id=target_id, b__vehicle_id=target_id, status="pending"
    ).update(status="merged")


@transaction.atomic
def split_listing(listing_id):
    listing = Listing.objects.select_for_update().get(pk=listing_id)
    old = listing.vehicle_id
    if Listing.objects.filter(vehicle_id=old).count() < 2:
        raise ValueError("This advert is already in its own group.")
    others = list(
        Listing.objects.filter(vehicle_id=old).exclude(pk=listing_id).values_list("pk", flat=True)
    )
    vehicle = Vehicle.objects.create()
    listing.vehicle = vehicle
    listing.identity_locked = True
    listing.save(update_fields=["vehicle", "identity_locked"])
    for other in others:
        a, b = sorted([listing_id, other])
        DuplicateCandidate.objects.update_or_create(
            a_id=a,
            b_id=b,
            defaults={"score": 0, "reasons": ["Manually separated"], "status": "rejected"},
        )
    GroupingEvent.objects.create(
        action="split",
        listing_ids=[listing_id],
        previous_vehicle_ids=[old],
        target_vehicle=vehicle,
        reason="Manually separated",
    )
    return vehicle


def match_listing(listing):
    if listing.vin and not listing.identity_locked:
        matches = (
            Listing.objects.filter(vin=listing.vin, identity_locked=False)
            .exclude(vehicle_id=listing.vehicle_id)
            .order_by("first_seen", "pk")
        )
        for other in matches:
            a, b = sorted([listing.pk, other.pk])
            if DuplicateCandidate.objects.filter(a_id=a, b_id=b, status="rejected").exists():
                continue
            try:
                merge_vehicles(other.vehicle_id, listing.vehicle_id, "Identical VIN")
            except ValueError:
                continue
            listing.refresh_from_db()
            break
    possible = _possible_duplicate_filter(listing)
    if possible is None:
        peers = Listing.objects.none()
    else:
        peers = Listing.objects.exclude(vehicle_id=listing.vehicle_id).filter(possible)
    for peer in peers:
        score, reasons = similarity(listing, peer)
        same_vin = bool(listing.vin and peer.vin and listing.vin == peer.vin)
        if not same_vin and len(reasons) < 3:
            continue
        a, b = sorted([listing.pk, peer.pk])
        candidate, created = DuplicateCandidate.objects.get_or_create(
            a_id=a, b_id=b, defaults={"score": score, "reasons": reasons}
        )
        if not created and candidate.status == "pending":
            candidate.score, candidate.reasons = score, reasons
            candidate.save(update_fields=["score", "reasons"])
    # Remove stale review tasks after regrouping, while preserving explicit rejections.
    DuplicateCandidate.objects.filter(status="pending", a__vehicle_id=F("b__vehicle_id")).update(
        status="merged"
    )


def pending_candidates():
    return (
        DuplicateCandidate.objects.filter(status="pending")
        .exclude(a__vehicle_id=F("b__vehicle_id"))
        .select_related("a", "b")
        .order_by("-score", "-created_at")
    )
