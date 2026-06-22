from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0004_storedgeneratedtest"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysissession",
            name="source_url",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
        migrations.AlterField(
            model_name="analysissession",
            name="upload_mode",
            field=models.CharField(
                choices=[("FILES", "Files"), ("ARCHIVE", "Archive"), ("GITHUB", "GitHub")],
                default="FILES",
                max_length=10,
            ),
        ),
    ]
