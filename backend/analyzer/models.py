from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from rest_framework import serializers
import uuid


class AnalysisSession(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PROCESSING', 'Processing'),
        ('ANALYZED', 'Analyzed'),
        ('TESTS_GENERATED', 'Tests Generated'),
        ('FAILED', 'Failed'),
    ]

    UPLOAD_MODE_CHOICES = [
        ('FILES', 'Files'),
        ('ARCHIVE', 'Archive'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='analysis_sessions')
    name = models.CharField(max_length=255, default='Untitled Session')
    python_version = models.CharField(max_length=10, default='3.9')
    dependencies = models.JSONField(default=list, blank=True)
    upload_mode = models.CharField(max_length=10, choices=UPLOAD_MODE_CHOICES, default='FILES')
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
            self.expires_at = timezone.now() + timedelta(days=7)
        super().save(*args, **kwargs)

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

    # Конфигурация генерации
    config = models.JSONField(default=dict)

    # Результаты
    generated_tests = models.TextField(blank=True)

    # Статусы задачи
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


class TestGenerationConfigSerializer(serializers.Serializer):
    """Конфигурация генерации тестов"""

    detail_level = serializers.ChoiceField(
        choices=['basic', 'advanced', 'full'],
        default='advanced',
        help_text='Уровень детализации тестов'
    )
    use_mocks = serializers.BooleanField(
        default=True,
        help_text='Использовать моки для внешних зависимостей'
    )
    include_edge_cases = serializers.BooleanField(
        default=True,
        help_text='Включить тесты граничных случаев'
    )
    test_framework = serializers.ChoiceField(
        choices=['pytest', 'unittest'],
        default='pytest',
        help_text='Фреймворк для тестов'
    )
    model = serializers.CharField(
        default='llama3',
        help_text='AI модель для генерации'
    )


class TestGenerationTaskSerializer(serializers.ModelSerializer):
    """Сериализатор задачи генерации тестов"""

    config = serializers.JSONField()

    class Meta:
        model = TestGenerationTask
        fields = [
            'id', 'session', 'config', 'generated_tests',
            'status', 'error_message', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']