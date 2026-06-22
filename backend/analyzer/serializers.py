from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from .models import AnalysisSession, UploadedFile, TestGenerationTask, STORED_GENERATED_TESTS_MAX
from .python_versions import ALLOWED_PYTHON_VERSIONS
from .utils.github_importer import GitHubImportError, parse_github_url


def _validate_python_version_value(value):
    v = str(value or "").strip()
    if v not in ALLOWED_PYTHON_VERSIONS:
        allowed = ", ".join(sorted(ALLOWED_PYTHON_VERSIONS, key=lambda x: tuple(map(int, x.split(".")))))
        raise serializers.ValidationError(f"Поддерживаются версии Python: {allowed}.")
    return v


class AnalysisSessionCreateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания сессии анализа"""

    class Meta:
        model = AnalysisSession
        fields = ['id', 'name', 'python_version', 'dependencies', 'run_command']
        read_only_fields = ['id']

    def validate_python_version(self, value):
        return _validate_python_version_value(value)


class GitHubImportCreateSerializer(serializers.Serializer):
    """Создание сессии с импортом репозитория GitHub (атомарно)."""

    name = serializers.CharField(required=False, default='Untitled Session', max_length=255)
    python_version = serializers.CharField(default='3.9')
    dependencies = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    url = serializers.CharField(max_length=500)
    ref = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validate_python_version(self, value):
        return _validate_python_version_value(value)

    def validate_url(self, value):
        raw = (value or '').strip()
        if not raw:
            raise serializers.ValidationError('Укажите ссылку на репозиторий GitHub.')
        try:
            parse_github_url(raw)
        except GitHubImportError as e:
            raise serializers.ValidationError(str(e))
        return raw


class AnalysisSessionSerializer(serializers.ModelSerializer):
    """Сериализатор для просмотра сессии"""

    files_count = serializers.SerializerMethodField()
    uploads_count = serializers.SerializerMethodField()
    generated_tests = serializers.SerializerMethodField()
    latest_test_task = serializers.SerializerMethodField()
    stored_tests = serializers.SerializerMethodField()
    stored_tests_limit = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisSession
        fields = [
            'id', 'user', 'name', 'python_version', 'dependencies', 'upload_mode', 'source_url', 'run_command',
            'status', 'uploaded_files', 'metrics', 'report_text',
            'error_message', 'created_at', 'updated_at', 'expires_at',
            'files_count',
            'uploads_count',
            'generated_tests',
            'latest_test_task',
            'stored_tests',
            'stored_tests_limit',
        ]
        read_only_fields = [
            'id', 'user', 'created_at', 'updated_at', 'expires_at'
        ]

    def get_files_count(self, obj):
        """
        Число .py/проектных файлов по результатам анализа (metrics).
        Если анализа ещё не было — число загруженных артефактов (строки UploadedFile).
        """
        m = obj.metrics or {}
        v = m.get('files_count')
        if isinstance(v, int) and v >= 0:
            return v
        return obj.files.count()

    def get_uploads_count(self, obj):
        """Сколько загруженных артефактов (файл/архив) привязано к сессии — для UI и reanalyze."""
        return obj.files.count()

    def get_stored_tests_limit(self, obj):
        return STORED_GENERATED_TESTS_MAX

    def get_generated_tests(self, obj):
        """Актуальный текст тестов: не отдаём во время новой генерации (без «мелькания» старого)."""
        latest = obj.testgenerationtask_set.order_by('-created_at').first()
        if latest and latest.status in ('GENERATING', 'PENDING'):
            return None
        stored = obj.stored_generated_tests.order_by('-created_at').first()
        if stored and (stored.generated_tests or '').strip():
            return stored.generated_tests
        task = obj.testgenerationtask_set.filter(
            status='COMPLETED'
        ).order_by('-created_at').first()
        return task.generated_tests if task else None

    def get_latest_test_task(self, obj):
        """Возвращает полную информацию о последней задаче генерации"""
        task = obj.testgenerationtask_set.order_by('-created_at').first()
        if task:
            body = task.generated_tests if task.status == 'COMPLETED' else ''
            return {
                'id': str(task.id),
                'status': task.status,
                'config': task.config,
                'created_at': task.created_at,
                'error_message': task.error_message,
                'generated_tests': body,
            }
        return None

    def get_stored_tests(self, obj):
        """История успешных генераций (содержимое + дата), не более лимита на сессию."""
        rows = obj.stored_generated_tests.order_by('-created_at')
        return [
            {
                'id': str(s.id),
                'created_at': s.created_at,
                'config': s.config,
                'generated_tests': s.generated_tests,
                'source_task_id': str(s.source_task_id) if s.source_task_id else None,
            }
            for s in rows
        ]

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
        help_text='Моки для LLM-промпта (влияет только при llm_assist=true)',
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
    pytest_repair = serializers.BooleanField(
        default=True,
        help_text='После генерации: pytest в подготовленной директории проекта и до N раундов правок (ARCHIVE/GITHUB/FILES + pytest)',
    )
    llm_assist = serializers.BooleanField(
        default=False,
        help_text='Включить LLM как помощника (иначе advanced/full генерируются детерминированно по AST/рецептам)',
    )
    model = serializers.CharField(
        default='qwen2.5-coder:7b',
        help_text='AI модель для генерации'
    )
    generation_timeout_sec = serializers.IntegerField(
        default=600,
        min_value=60,
        max_value=1200,
        help_text='Таймаут AI-генерации в секундах'
    )


class GeneratedTestsUpdateSerializer(serializers.Serializer):
    """Ручное редактирование сгенерированного кода тестов в UI."""

    generated_tests = serializers.CharField(
        required=True,
        allow_blank=True,
        max_length=2_000_000,
        help_text='Текст файла с тестами (Python)',
    )
    stored_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text='Если указан — правка конкретной записи из истории; иначе — актуальная версия сессии',
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


class RegisterSerializer(serializers.Serializer):
    """Регистрация нового пользователя."""

    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(write_only=True, min_length=8, max_length=128)
    password_confirm = serializers.CharField(write_only=True, min_length=8, max_length=128)

    def validate_username(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("Укажите имя пользователя.")
        if User.objects.filter(username__iexact=name).exists():
            raise serializers.ValidationError("Пользователь с таким именем уже существует.")
        return name

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Пароли не совпадают."})
        try:
            validate_password(attrs["password"], user=User(username=attrs["username"]))
        except DjangoValidationError as e:
            raise serializers.ValidationError({"password": list(e.messages)})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data["username"],
            password=validated_data["password"],
        )