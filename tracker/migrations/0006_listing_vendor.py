from django.db import migrations, models

from tracker.scrapers import vendor_for_adapter


def infer_vendors(apps, schema_editor):
    Listing = apps.get_model("tracker", "Listing")
    database = schema_editor.connection.alias
    updates = []
    for listing in Listing.objects.using(database).only("pk", "adapter").iterator(
        chunk_size=500
    ):
        vendor = vendor_for_adapter(listing.adapter)
        if vendor:
            listing.vendor = vendor
            updates.append(listing)
        if len(updates) >= 500:
            Listing.objects.using(database).bulk_update(updates, ["vendor"])
            updates.clear()
    if updates:
        Listing.objects.using(database).bulk_update(updates, ["vendor"])


class Migration(migrations.Migration):
    dependencies = [("tracker", "0005_vehicle_is_favourite")]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="vendor",
            field=models.CharField(blank=True, db_index=True, max_length=120),
        ),
        migrations.RunPython(infer_vendors, migrations.RunPython.noop),
    ]
