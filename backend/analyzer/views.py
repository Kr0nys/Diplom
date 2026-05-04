from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from .models import AnalysisSession, TestGenerationTask, UploadedFile
from .serializers import (
    AnalysisSessionSerializer,
    AnalysisSessionCreateSerializer,
    UploadedFileSerializer,
    TestGenerationTaskSerializer,
    TestGenerationConfigSerializer
)
from .tasks import analyze_project, generate_tests_task
from django.http import HttpResponse

import ast
import tempfile
import subprocess
import logging

logger = logging.getLogger(__name__)

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

        # Запускаем задачу анализа
        analyze_project.delay(str(session.id))

        return Response({
            'status': 'uploaded',
            'files': uploaded,
            'task_id': str(session.id)
        }, status=status.HTTP_200_OK)

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

        # Проверка статуса
        if session.status not in ['ANALYZED', 'TESTS_GENERATED']:
            return Response(
                {'error': f'Session status is {session.status}. Must be ANALYZED first.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Валидация конфигурации
        serializer = TestGenerationConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Создаём задачу
        task = TestGenerationTask.objects.create(
            session=session,
            config=serializer.validated_data
        )

        # Запускаем асинхронно
        generate_tests_task.delay(str(task.id))

        return Response({
            'task_id': str(task.id),
            'status': 'pending',
            'message': 'Test generation started'
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'], url_path='tests')
    def get_tests(self, request, pk=None):
        """Получить сгенерированные тесты"""
        session = self.get_object()

        task = session.testgenerationtask_set.filter(
            status='COMPLETED'
        ).order_by('-created_at').first()

        if not task:
            return Response({
                'tests': None,
                'status': 'not_generated',
                'message': 'Tests not generated yet'
            })

        return Response({
            'tests': task.generated_tests,
            'status': task.status,
            'config': task.config,
            'created_at': task.created_at
        })

    @action(detail=True, methods=['post'], url_path='tests/validate')
    def validate_tests(self, request, pk=None):
        """Валидация синтаксиса тестов"""
        session = self.get_object()

        task = session.testgenerationtask_set.filter(
            status='COMPLETED'
        ).order_by('-created_at').first()

        if not task or not task.generated_tests:
            return Response(
                {'error': 'No tests to validate'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            ast.parse(task.generated_tests)
            return Response({
                'valid': True,
                'message': 'Tests are syntactically valid'
            })
        except SyntaxError as e:
            return Response({
                'valid': False,
                'message': f'Syntax error: {e}',
                'line': e.lineno
            })

    @action(detail=True, methods=['get'], url_path='tests/download')
    def download_tests(self, request, pk=None):
        """Скачать тесты как файл"""
        session = self.get_object()

        task = session.testgenerationtask_set.filter(
            status='COMPLETED'
        ).order_by('-created_at').first()

        if not task or not task.generated_tests:
            return Response(
                {'error': 'No tests to download'},
                status=status.HTTP_404_NOT_FOUND
            )

        response = HttpResponse(
            task.generated_tests,
            content_type='text/x-python'
        )
        response['Content-Disposition'] = f'attachment; filename="tests_{session.id}.py"'
        return response


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
        return response