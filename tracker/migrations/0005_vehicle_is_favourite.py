from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0004_refresh_duplicate_candidate_rules"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehicle",
            name="is_favourite",
            field=models.BooleanField(default=False),
        ),
    ]
