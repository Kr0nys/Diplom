from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from .models import AnalysisSession, TestGenerationTask, UploadedFile, StoredGeneratedTest, STORED_GENERATED_TESTS_MAX
from .serializers import (
    AnalysisSessionSerializer,
    AnalysisSessionCreateSerializer,
    GitHubImportCreateSerializer,
    UploadedFileSerializer,
    TestGenerationTaskSerializer,
    TestGenerationConfigSerializer,
    GeneratedTestsUpdateSerializer,
    RegisterSerializer,
)
from .tasks import analyze_project, generate_tests_task
from .utils.project_tree import build_tree_for_session
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

import ast
import os
import tempfile
import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _session_active_test_text(session: AnalysisSession) -> Optional[str]:
    """
    Тот же приоритет, что у AnalysisSessionSerializer.get_generated_tests:
    снимок из истории, иначе последняя завершённая задача.
    Во время GENERATING/PENDING последней задачи — None (показывать старое не надо).
    """
    latest = session.testgenerationtask_set.order_by("-created_at").first()
    if latest and latest.status in ("GENERATING", "PENDING"):
        return None
    stored = session.stored_generated_tests.order_by("-created_at").first()
    if stored and (stored.generated_tests or "").strip():
        return stored.generated_tests
    task = (
        session.testgenerationtask_set.filter(status="COMPLETED")
        .order_by("-created_at")
        .first()
    )
    return task.generated_tests if task else None


def _session_active_test_task(session: AnalysisSession) -> Optional[TestGenerationTask]:
    """Последняя завершённая задача (для validate/download после выравнивания с get_tests)."""
    return (
        session.testgenerationtask_set.filter(status="COMPLETED")
        .order_by("-created_at")
        .first()
    )


def _syntax_validation_response(source: str, exc: SyntaxError) -> Response:
    """
    Подробное описание ошибки разбора: номер строки, фрагмент кода, указатель столбца, контекст.
    """
    text = source.replace("\r\n", "\n")
    lines = text.split("\n")
    lineno = int(exc.lineno or 1)
    idx = max(0, min(len(lines) - 1, lineno - 1))
    problem_line = lines[idx] if idx < len(lines) else ""

    ctx_start = max(0, idx - 2)
    ctx_end = min(len(lines), idx + 3)
    context = []
    for i in range(ctx_start, ctx_end):
        context.append(
            {
                "number": i + 1,
                "content": lines[i],
                "is_error_line": i == idx,
            }
        )

    off = exc.offset
    pointer_line = None
    if isinstance(off, int) and off >= 1 and problem_line:
        col0 = max(0, min(len(problem_line), off - 1))
        pointer_line = (" " * col0) + "^"

    end_ln = getattr(exc, "end_lineno", None)
    end_off = getattr(exc, "end_offset", None)

    col_part = f", столбец {off}" if isinstance(off, int) else ""
    message_one = f"Синтаксическая ошибка на строке {lineno}{col_part}: {exc.msg}"

    payload = {
        "valid": False,
        "message": message_one,
        "error_type": type(exc).__name__,
        "msg": exc.msg,
        "line": lineno,
        "offset": off,
        "end_lineno": end_ln,
        "end_offset": end_off,
        "problem_line": problem_line,
        "pointer_line": pointer_line,
        "context": context,
    }
    return Response(payload, status=status.HTTP_200_OK)


def _delete_if_empty_session(session: AnalysisSession) -> None:
    """Удалить сессию без загруженных файлов (неудачный импорт до сохранения архива)."""
    if session and not session.files.exists():
        session.delete()


class AnalysisSessionViewSet(viewsets.ModelViewSet):
    queryset = AnalysisSession.objects.all()
    serializer_class = AnalysisSessionSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        return AnalysisSession.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def upload_files(self, request, pk=None):
        """
        Загрузка файлов/архива для сессии.

        Поддерживает:
        - files: список python-файлов (multipart)
        - archive: zip/tar(.gz) архив проекта (multipart)
        """
        session = self.get_object()
        archive = request.FILES.get('archive')
        files = request.FILES.getlist('files')

        if not archive and not files:
            return Response(
                {'error': 'No files provided. Use "files" or "archive".'},
                status=status.HTTP_400_BAD_REQUEST
            )

        uploaded = []
        if archive:
            uploaded_file = UploadedFile.objects.create(
                session=session,
                file=archive,
                original_name=archive.name,
                file_size=archive.size,
                file_type='ARCHIVE',
            )
            uploaded.append({
                'id': uploaded_file.id,
                'name': uploaded_file.original_name,
                'size': uploaded_file.file_size,
                'type': uploaded_file.file_type,
            })
            session.upload_mode = 'ARCHIVE'
            session.uploaded_files = [uploaded_file.file.path]
        else:
            session.upload_mode = 'FILES'
            for file in files:
                file_type = 'PY'
                name_l = (file.name or '').lower()
                if name_l in ('requirements.txt',):
                    file_type = 'REQUIREMENTS'
                elif not name_l.endswith('.py'):
                    file_type = 'OTHER'

                uploaded_file = UploadedFile.objects.create(
                    session=session,
                    file=file,
                    original_name=file.name,
                    file_size=file.size,
                    file_type=file_type,
                )
                uploaded.append({
                    'id': uploaded_file.id,
                    'name': uploaded_file.original_name,
                    'size': uploaded_file.file_size,
                    'type': uploaded_file.file_type,
                })
                session.uploaded_files.append(uploaded_file.file.path)

        session.save(update_fields=['upload_mode', 'uploaded_files', 'updated_at'])

        analyze_project.delay(str(session.id))

        return Response({
            'status': 'uploaded',
            'files': uploaded,
            'task_id': str(session.id)
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='from-github', parser_classes=[JSONParser])
    def create_from_github(self, request):
        """
        Создать сессию и импортировать публичный репозиторий GitHub одним запросом.
        Сессия не создаётся, если ссылка невалидна или скачивание не удалось.
        """
        from .utils.github_importer import download_github_repo_zip, GitHubImportError

        serializer = GitHubImportCreateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        url = data['url']
        ref = (data.get('ref') or '').strip() or None

        try:
            zip_bytes, parsed, resolved_ref = download_github_repo_zip(url, ref_override=ref)
        except GitHubImportError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception('GitHub import failed before session create')
            return Response(
                {'error': f'Не удалось скачать репозиторий: {e}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        filename = f'github-{parsed.owner}-{parsed.repo}-{resolved_ref}.zip'
        original_name = f'github:{parsed.owner}/{parsed.repo}@{resolved_ref}'

        with transaction.atomic():
            session = AnalysisSession.objects.create(
                user=request.user,
                name=data.get('name') or 'Untitled Session',
                python_version=data['python_version'],
                dependencies=data.get('dependencies') or [],
                upload_mode='GITHUB',
                source_url=parsed.original_url,
            )
            uploaded_file = UploadedFile.objects.create(
                session=session,
                file=ContentFile(zip_bytes, name=filename),
                original_name=original_name,
                file_size=len(zip_bytes),
                file_type='ARCHIVE',
            )
            session.uploaded_files = [uploaded_file.file.path]
            session.save(update_fields=['uploaded_files', 'updated_at'])

        analyze_project.delay(str(session.id))

        return Response(
            AnalysisSessionSerializer(session, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], url_path='import-github', parser_classes=[JSONParser])
    def import_github(self, request, pk=None):
        """
        Импорт публичного репозитория GitHub по URL (скачивание zip).
        Тело: { "url": "https://github.com/owner/repo", "ref": "main" (опционально) }
        """
        from .utils.github_importer import download_github_repo_zip, GitHubImportError, parse_github_url

        session = self.get_object()
        empty_before = not session.files.exists()
        data = request.data or {}
        url = (data.get('url') or '').strip()
        ref = (data.get('ref') or '').strip() or None

        if not url:
            if empty_before:
                _delete_if_empty_session(session)
            return Response(
                {'error': 'Укажите ссылку на репозиторий GitHub (поле url).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            parse_github_url(url)
        except GitHubImportError as e:
            if empty_before:
                _delete_if_empty_session(session)
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            zip_bytes, parsed, resolved_ref = download_github_repo_zip(url, ref_override=ref)
        except GitHubImportError as e:
            if empty_before:
                _delete_if_empty_session(session)
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception('GitHub import failed for session %s', session.id)
            if empty_before:
                _delete_if_empty_session(session)
            return Response(
                {'error': f'Не удалось скачать репозиторий: {e}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        filename = f'github-{parsed.owner}-{parsed.repo}-{resolved_ref}.zip'
        original_name = f'github:{parsed.owner}/{parsed.repo}@{resolved_ref}'

        uploaded_file = UploadedFile.objects.create(
            session=session,
            file=ContentFile(zip_bytes, name=filename),
            original_name=original_name,
            file_size=len(zip_bytes),
            file_type='ARCHIVE',
        )

        session.upload_mode = 'GITHUB'
        session.source_url = parsed.original_url
        session.uploaded_files = [uploaded_file.file.path]
        session.save(update_fields=['upload_mode', 'source_url', 'uploaded_files', 'updated_at'])

        analyze_project.delay(str(session.id))

        return Response({
            'status': 'uploaded',
            'files': [{
                'id': uploaded_file.id,
                'name': uploaded_file.original_name,
                'size': uploaded_file.file_size,
                'type': uploaded_file.file_type,
            }],
            'source_url': session.source_url,
            'repository': f'{parsed.owner}/{parsed.repo}',
            'ref': resolved_ref,
            'task_id': str(session.id),
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='reanalyze')
    def reanalyze(self, request, pk=None):
        """
        Повторный запуск анализа по уже загруженным файлам сессии (без повторной загрузки).
        """
        session = self.get_object()
        if session.status == 'PROCESSING':
            return Response(
                {'error': 'Анализ уже выполняется.'},
                status=status.HTTP_409_CONFLICT,
            )
        if session.status == 'GENERATING_TESTS':
            return Response(
                {'error': 'Дождитесь завершения генерации тестов.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not session.files.exists():
            return Response(
                {'error': 'Нет загруженных файлов. Сначала загрузите архив или файлы проекта.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session.touch_retention()
        analyze_project.delay(str(session.id))
        return Response(
            {'status': 'queued', 'message': 'Повторный анализ поставлен в очередь'},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['get'], url_path='project-tree')
    def project_tree(self, request, pk=None):
        """Древо файлов загруженного проекта (из metrics или построение из архива/файлов)."""
        session = self.get_object()
        tree = (session.metrics or {}).get('project_tree')
        if not tree or not tree.get('children'):
            files_qs = session.files.all()
            archive = files_qs.filter(file_type='ARCHIVE').order_by('-uploaded_at').first()
            entries = [(f.original_name, f.file_size) for f in files_qs if f.file_type != 'ARCHIVE']
            tree = build_tree_for_session(
                upload_mode=session.upload_mode or 'FILES',
                archive_path=archive.file.path if archive else None,
                uploaded_entries=entries,
            )
        return Response(
            {
                'tree': tree if tree and tree.get('children') else None,
                'upload_mode': session.upload_mode,
            }
        )

    @action(detail=True, methods=['post'], url_path='set_run_command')
    def set_run_command(self, request, pk=None):
        """Задать/обновить команду запуска проекта (entrypoint)."""
        session = self.get_object()
        run_command = (request.data or {}).get('run_command', '')
        if run_command is None:
            run_command = ''
        run_command = str(run_command).strip()

        session.run_command = run_command
        session.save(update_fields=['run_command', 'updated_at'])
        return Response({'status': 'ok', 'run_command': session.run_command})

    @action(detail=True, methods=['post'], url_path='generate_tests')
    def generate_tests(self, request, pk=None):
        """Запуск генерации тестов с помощью AI"""
        session = self.get_object()

        if session.status not in ['ANALYZED', 'TESTS_GENERATED', 'GENERATING_TESTS']:
            return Response(
                {'error': f'Session status is {session.status}. Must be ANALYZED first.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = TestGenerationConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if session.stored_generated_tests.count() >= STORED_GENERATED_TESTS_MAX:
            return Response(
                {
                    'error': (
                        f'В сессии уже сохранено максимум {STORED_GENERATED_TESTS_MAX} версий тестов. '
                        'Удалите одну или несколько записей в блоке «История генераций», затем запустите снова.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        task = TestGenerationTask.objects.create(
            session=session,
            config=serializer.validated_data
        )

        session.status = 'GENERATING_TESTS'
        session.save(update_fields=['status', 'updated_at'])
        session.touch_retention()

        generate_tests_task.delay(str(task.id))

        return Response({
            'task_id': str(task.id),
            'status': 'pending',
            'message': 'Test generation started'
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['delete'], url_path=r'stored_tests/(?P<stored_id>[0-9a-f-]{36})')
    def delete_stored_test(self, request, pk=None, stored_id=None):
        """Удалить сохранённую версию сгенерированных тестов (освобождает слот под лимит)."""
        session = self.get_object()
        obj = get_object_or_404(StoredGeneratedTest, id=stored_id, session=session)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get', 'patch'], url_path='tests')
    def get_tests(self, request, pk=None):
        """GET: сгенерированные тесты (как в карточке сессии). PATCH: сохранить правки из UI."""
        session = self.get_object()

        if request.method == 'PATCH':
            if session.status == 'GENERATING_TESTS':
                return Response(
                    {'error': 'Дождитесь завершения генерации тестов.'},
                    status=status.HTTP_409_CONFLICT,
                )
            ser = GeneratedTestsUpdateSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            text = ser.validated_data['generated_tests']
            stored_uuid = ser.validated_data.get('stored_id')

            if stored_uuid:
                st = get_object_or_404(StoredGeneratedTest, id=stored_uuid, session=session)
                st.generated_tests = text
                st.save(update_fields=['generated_tests'])
                if st.source_task_id:
                    TestGenerationTask.objects.filter(pk=st.source_task_id).update(
                        generated_tests=text
                    )
            else:
                stored = session.stored_generated_tests.order_by('-created_at').first()
                task_completed = _session_active_test_task(session)
                if stored:
                    stored.generated_tests = text
                    stored.save(update_fields=['generated_tests'])
                    if stored.source_task_id:
                        TestGenerationTask.objects.filter(pk=stored.source_task_id).update(
                            generated_tests=text
                        )
                    elif task_completed:
                        task_completed.generated_tests = text
                        task_completed.save(update_fields=['generated_tests'])
                elif task_completed:
                    task_completed.generated_tests = text
                    task_completed.save(update_fields=['generated_tests'])
                else:
                    return Response(
                        {'error': 'Нет сохранённых тестов для редактирования.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )

            session.touch_retention()
            session.save(update_fields=['updated_at'])
            return Response({'status': 'ok'})

        text = _session_active_test_text(session)
        task = _session_active_test_task(session)

        if text is None and not task:
            return Response({
                'tests': None,
                'status': 'not_generated',
                'message': 'Tests not generated yet'
            })

        return Response({
            'tests': text if text is not None else (task.generated_tests if task else ''),
            'status': task.status if task else 'COMPLETED',
            'config': task.config if task else {},
            'created_at': task.created_at if task else None,
        })

    @action(detail=True, methods=['post'], url_path='tests/validate')
    def validate_tests(self, request, pk=None):
        """
        Проверка синтаксиса Python.

        Если в теле передан generated_tests — проверяется он (удобно для несохранённого черновика в редакторе),
        иначе — актуальный текст тестов сессии (как при скачивании).
        """
        session = self.get_object()

        raw = request.data.get("generated_tests")
        if raw is not None:
            body = str(raw)
        else:
            body = _session_active_test_text(session)
            if body is None:
                body = ""

        if not str(body).strip():
            return Response(
                {'error': 'Нет текста для проверки'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            ast.parse(body)
            return Response({
                'valid': True,
                'message': 'Синтаксис корректен (разбор AST выполнен успешно)'
            })
        except SyntaxError as e:
            return _syntax_validation_response(body, e)

        except Exception as e:
            return Response({
                'valid': False,
                'message': f'Не удалось проверить код: {type(e).__name__}: {e}',
            }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='tests/run', parser_classes=[JSONParser])
    def run_tests_on_project(self, request, pk=None):
        """
        Запуск выбранных сгенерированных тестов на загруженном проекте (pytest).
        Тело: { "stored_id": "uuid" } — версия из истории; без stored_id — актуальные тесты сессии.
        Опционально: "generated_tests" — явный текст (например черновик).
        """
        from .utils.session_test_runner import SessionTestRunError, run_session_tests

        session = self.get_object()
        data = request.data or {}
        stored_id = (data.get('stored_id') or '').strip() or None
        generated_tests = data.get('generated_tests')
        if generated_tests is not None:
            generated_tests = str(generated_tests)

        try:
            result = run_session_tests(
                session,
                stored_id=stored_id,
                generated_tests=generated_tests,
            )
        except SessionTestRunError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception('Test run failed for session %s', session.id)
            return Response(
                {'error': f'Не удалось запустить тесты: {e}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        session.touch_retention()
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='tests/import', parser_classes=[MultiPartParser, FormParser, JSONParser])
    def import_stored_test(self, request, pk=None):
        """
        Загрузить ранее сохранённый файл тестов (.py) обратно в сессию как новую версию.
        multipart: file; или JSON: { "generated_tests": "...", "filename": "tests.py" }
        """
        session = self.get_object()

        if session.stored_generated_tests.count() >= STORED_GENERATED_TESTS_MAX:
            return Response(
                {
                    'error': (
                        f'Достигнут лимит версий ({STORED_GENERATED_TESTS_MAX}). '
                        'Удалите одну из записей в «Истории генераций» или на этой странице.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploaded = request.FILES.get('file')
        if uploaded:
            original_name = (uploaded.name or 'imported_tests.py').strip()
            if not original_name.lower().endswith('.py'):
                return Response(
                    {'error': 'Поддерживаются только файлы с расширением .py'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            max_bytes = int(os.environ.get('IMPORT_TEST_FILE_MAX_BYTES', str(512 * 1024)))
            if uploaded.size > max_bytes:
                return Response(
                    {'error': f'Файл слишком большой (лимит {max_bytes // 1024} КБ).'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raw = uploaded.read()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                return Response(
                    {'error': 'Файл должен быть в кодировке UTF-8.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            source_filename = original_name
        else:
            data = request.data or {}
            text = str(data.get('generated_tests') or '')
            source_filename = (data.get('filename') or 'imported_tests.py').strip() or 'imported_tests.py'

        text = text.replace('\ufeff', '').strip()
        if not text:
            return Response(
                {'error': 'Пустой файл или текст тестов.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            ast.parse(text)
        except SyntaxError as e:
            return Response(
                {'error': f'Синтаксическая ошибка: {e.msg} (строка {e.lineno})'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        row = StoredGeneratedTest.objects.create(
            session=session,
            generated_tests=text,
            config={
                'imported': True,
                'source_filename': source_filename,
                'test_framework': 'pytest',
            },
            source_task=None,
        )
        session.touch_retention()

        return Response(
            {
                'id': str(row.id),
                'created_at': row.created_at,
                'config': row.config,
                'generated_tests': row.generated_tests,
                'source_task_id': None,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get'], url_path='tests/download')
    def download_tests(self, request, pk=None):
        """Скачать тесты как файл"""
        session = self.get_object()

        body = _session_active_test_text(session)
        if body is None or not str(body).strip():
            return Response(
                {'error': 'No tests to download'},
                status=status.HTTP_404_NOT_FOUND
            )

        response = HttpResponse(
            body,
            content_type='text/x-python'
        )
        response['Content-Disposition'] = f'attachment; filename="tests_{session.id}.py"'
        session.touch_retention()
        return response


class CurrentUserView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        u = request.user
        return Response({"id": u.id, "username": u.username})


class RegisterView(APIView):
    """Регистрация пользователя и выдача JWT (автовход)."""
    permission_classes = [AllowAny]

    def post(self, request):
        from rest_framework_simplejwt.tokens import RefreshToken

        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "id": user.id,
                "username": user.username,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


class TestGenerationTaskViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TestGenerationTask.objects.all()
    serializer_class = TestGenerationTaskSerializer

    def get_queryset(self):
        return TestGenerationTask.objects.filter(session__user=self.request.user)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        from django.http import HttpResponse
        task = self.get_object()
        response = HttpResponse(task.generated_tests, content_type='text/plain')
        response['Content-Disposition'] = f'attachment; filename="tests_{task.id}.py"'
        try:
            task.session.touch_retention()
        except Exception:
            pass
        return response