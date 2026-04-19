# backend/analyzer/serializers.py

from rest_framework import serializers
from .models import AnalysisSession, UploadedFile, TestGenerationTask


class AnalysisSessionCreateSerializer(serializers.ModelSerializer):

    class Meta:
        model = AnalysisSession
        fields = ['id', 'name', 'python_version', 'dependencies']
        read_only_fields = ['id']


class AnalysisSessionSerializer(serializers.ModelSerializer):

    files_count = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisSession
        fields = [
            'id', 'user', 'name', 'python_version', 'dependencies',
            'status', 'uploaded_files', 'metrics', 'report_text',
            'error_message', 'created_at', 'updated_at', 'expires_at',
            'files_count'
        ]
        read_only_fields = [
            'id', 'user', 'created_at', 'updated_at', 'expires_at'
        ]

    def get_files_count(self, obj):
        return obj.files.count()


class UploadedFileSerializer(serializers.ModelSerializer):

    class Meta:
        model = UploadedFile
        fields = ['id', 'session', 'file', 'original_name', 'file_size', 'uploaded_at']
        read_only_fields = ['id', 'uploaded_at']


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