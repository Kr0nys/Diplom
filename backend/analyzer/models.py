from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import uuid


class AnalysisSession(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PROCESSING', 'Processing'),
        ('ANALYZED', 'Analyzed'),
        ('GENERATING_TESTS', 'Generating Tests'),
        ('TESTS_GENERATED', 'Tests Generated'),
        ('FAILED', 'Failed'),
    ]

    UPLOAD_MODE_CHOICES = [
        ('FILES', 'Files'),
        ('ARCHIVE', 'Archive'),
        ('GITHUB', 'GitHub'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='analysis_sessions')
    name = models.CharField(max_length=255, default='Untitled Session')
    python_version = models.CharField(max_length=10, default='3.9')
    dependencies = models.JSONField(default=list, blank=True)
    upload_mode = models.CharField(max_length=10, choices=UPLOAD_MODE_CHOICES, default='FILES')
    source_url = models.URLField(max_length=500, blank=True, default='')
    run_command = models.CharField(max_length=500, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    report_text = models.TextField(blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    uploaded_files = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.expires_at:
            days = int(getattr(settings, 'FILE_RETENTION_DAYS', 7) or 7)
            self.expires_at = timezone.now() + timedelta(days=days)
        super().save(*args, **kwargs)

    def touch_retention(self) -> None:
        """
        Продлевает срок хранения сессии на FILE_RETENTION_DAYS от текущего момента.
        Используется как «таймер по неактивности»: любое взаимодействие продлевает expires_at.
        """
        days = int(getattr(settings, 'FILE_RETENTION_DAYS', 7) or 7)
        self.expires_at = timezone.now() + timedelta(days=days)
        self.save(update_fields=['expires_at', 'updated_at'])

class UploadedFile(models.Model):
    FILE_TYPE_CHOICES = [
        ('PY', 'Python file'),
        ('ARCHIVE', 'Archive'),
        ('REQUIREMENTS', 'Requirements'),
        ('OTHER', 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(AnalysisSession, on_delete=models.CASCADE, related_name='files')

    file = models.FileField(upload_to='uploads/%Y/%m/%d/')
    original_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField()
    file_type = models.CharField(max_length=20, choices=FILE_TYPE_CHOICES, default='PY')

    uploaded_at = models.DateTimeField(auto_now_add=True)

class TestGenerationTask(models.Model):
    """Задача генерации тестов"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(AnalysisSession, on_delete=models.CASCADE, related_name='testgenerationtask_set')

    config = models.JSONField(default=dict)

    generated_tests = models.TextField(blank=True)

    STATUS_CHOICES = [
        ('PENDING', 'Ожидает'),
        ('GENERATING', 'Генерируется'),
        ('COMPLETED', 'Завершено'),
        ('FAILED', 'Ошибка'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"TestTask {self.id} - {self.status}"


STORED_GENERATED_TESTS_MAX = 5


class StoredGeneratedTest(models.Model):
    """
    Сохранённая успешная генерация тестов в рамках сессии (история).
    Всего не более STORED_GENERATED_TESTS_MAX на сессию перед новой генерацией.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        AnalysisSession,
        on_delete=models.CASCADE,
        related_name="stored_generated_tests",
    )
    source_task = models.OneToOneField(
        TestGenerationTask,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stored_snapshot",
    )
    generated_tests = models.TextField(blank=True, default="")
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"StoredTests {self.id} @ {self.created_at}"