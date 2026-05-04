from rest_framework import serializers
from .models import AnalysisSession, UploadedFile, TestGenerationTask


class AnalysisSessionCreateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания сессии анализа"""

    class Meta:
        model = AnalysisSession
        fields = ['id', 'name', 'python_version', 'dependencies', 'run_command']
        read_only_fields = ['id']


class AnalysisSessionSerializer(serializers.ModelSerializer):
    """Сериализатор для просмотра сессии"""

    files_count = serializers.SerializerMethodField()
    generated_tests = serializers.SerializerMethodField()
    latest_test_task = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisSession
        fields = [
            'id', 'user', 'name', 'python_version', 'dependencies', 'upload_mode', 'run_command',
            'status', 'uploaded_files', 'metrics', 'report_text',
            'error_message', 'created_at', 'updated_at', 'expires_at',
            'files_count',
            'generated_tests',
            'latest_test_task',
        ]
        read_only_fields = [
            'id', 'user', 'created_at', 'updated_at', 'expires_at'
        ]

    def get_files_count(self, obj):
        return obj.files.count()

    def get_generated_tests(self, obj):
        """Возвращает код тестов из последней завершённой задачи"""
        task = obj.testgenerationtask_set.filter(
            status='COMPLETED'
        ).order_by('-created_at').first()
        return task.generated_tests if task else None

    def get_latest_test_task(self, obj):
        """Возвращает полную информацию о последней задаче генерации"""
        task = obj.testgenerationtask_set.order_by('-created_at').first()
        if task:
            return {
                'id': str(task.id),
                'status': task.status,
                'config': task.config,
                'created_at': task.created_at,
                'error_message': task.error_message,
                'generated_tests': task.generated_tests,
            }
        return None

class UploadedFileSerializer(serializers.ModelSerializer):
    """Сериализатор для загруженных файлов"""

    class Meta:
        model = UploadedFile
        fields = ['id', 'session', 'file', 'original_name', 'file_size', 'uploaded_at']
        read_only_fields = ['id', 'uploaded_at']


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