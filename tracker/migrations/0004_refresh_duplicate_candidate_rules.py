from django.db import migrations


def refresh_pending_candidates(apps, schema_editor):
    Candidate = apps.get_model("tracker", "DuplicateCandidate")
    database = schema_editor.connection.alias

    for candidate in (
        Candidate.objects.using(database)
        .filter(status="pending")
        .select_related("a", "b")
        .iterator()
    ):
        a, b = candidate.a, candidate.b
        if a.vin and b.vin:
            if a.vin != b.vin:
                candidate.delete(using=database)
                continue
            candidate.score = 100
            candidate.reasons = ["Matching VIN"]
        else:
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
            matched = [(reason, weight) for is_match, reason, weight in checks if is_match]
            if len(matched) < 3:
                candidate.delete(using=database)
                continue
            candidate.score = sum(weight for _, weight in matched)
            candidate.reasons = [reason for reason, _ in matched]

        candidate.save(using=database, update_fields=["score", "reasons"])


class Migration(migrations.Migration):
    dependencies = [("tracker", "0003_translate_mobile_bg_colours")]

    operations = [migrations.RunPython(refresh_pending_candidates, migrations.RunPython.noop)]
