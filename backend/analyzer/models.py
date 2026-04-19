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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='analysis_sessions')
    name = models.CharField(max_length=255, default='Untitled Session')
    python_version = models.CharField(max_length=10, default='3.9')
    dependencies = models.JSONField(default=list, blank=True)
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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(AnalysisSession, on_delete=models.CASCADE, related_name='files')

    file = models.FileField(upload_to='uploads/%Y/%m/%d/')
    original_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField()

    uploaded_at = models.DateTimeField(auto_now_add=True)

class TestGenerationTask(models.Model):

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


class TestGenerationConfigSerializer(serializers.Serializer):

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
        default='llama3.2',
        help_text='AI модель для генерации'
    )


class TestGenerationTaskSerializer(serializers.ModelSerializer):

    config = serializers.JSONField()

    class Meta:
        model = TestGenerationTask
        fields = [
            'id', 'session', 'config', 'generated_tests',
            'status', 'error_message', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']