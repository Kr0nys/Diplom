# Generated manually

import uuid
from django.db import migrations, models
import django.db.models.deletion


def backfill_stored_tests(apps, schema_editor):
    StoredGeneratedTest = apps.get_model("analyzer", "StoredGeneratedTest")
    TestGenerationTask = apps.get_model("analyzer", "TestGenerationTask")
    AnalysisSession = apps.get_model("analyzer", "AnalysisSession")

    for session in AnalysisSession.objects.all():
        tasks = (
            TestGenerationTask.objects.filter(session=session, status="COMPLETED")
            .exclude(generated_tests="")
            .order_by("-created_at")[:5]
        )
        for task in tasks:
            StoredGeneratedTest.objects.get_or_create(
                source_task_id=task.id,
                defaults={
                    "session_id": session.id,
                    "generated_tests": task.generated_tests,
                    "config": dict(task.config or {}),
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0003_analysissession_generating_tests_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="StoredGeneratedTest",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("generated_tests", models.TextField(blank=True, default="")),
                ("config", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stored_generated_tests",
                        to="analyzer.analysissession",
                    ),
                ),
                (
                    "source_task",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="stored_snapshot",
                        to="analyzer.testgenerationtask",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.RunPython(backfill_stored_tests, migrations.RunPython.noop),
    ]
