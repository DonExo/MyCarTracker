import re

from django.db import migrations

from tracker.colours import mobile_bg_colour


CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
INVALID_COLOURS = {"бензинов", "дизелов", "хибриден"}


def translate_existing_colours(apps, schema_editor):
    Listing = apps.get_model("tracker", "Listing")
    database = schema_editor.connection.alias
    updates = []
    for listing in Listing.objects.using(database).only("pk", "colour").iterator(
        chunk_size=500
    ):
        english = mobile_bg_colour(listing.colour)
        if english:
            if english != listing.colour:
                listing.colour = english
                updates.append(listing)
        elif listing.colour.casefold().strip() in INVALID_COLOURS or CYRILLIC_RE.search(
            listing.colour
        ):
            listing.colour = ""
            updates.append(listing)
        if len(updates) >= 500:
            Listing.objects.using(database).bulk_update(updates, ["colour"])
            updates.clear()
    if updates:
        Listing.objects.using(database).bulk_update(updates, ["colour"])


class Migration(migrations.Migration):
    dependencies = [("tracker", "0002_listing_price_note")]

    operations = [migrations.RunPython(translate_existing_colours, migrations.RunPython.noop)]
