# backend/analyzer/tasks.py

from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import logging

from .models import AnalysisSession, TestGenerationTask, UploadedFile
from .utils.docker_runner import DockerRunner
from .utils.code_analyzer import CodeAnalyzer
from .utils.ai_generator import AITestGenerator

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def analyze_project(self, session_id: str):
    """
    Основная задача анализа проекта в изолированном Docker-контейнере.

    При неудаче с Docker автоматически переключается на локальный анализ (fallback).
    """
    try:
        session = AnalysisSession.objects.get(id=session_id)
        logger.info(f"🚀 Starting analysis for session {session_id}")

        # Обновляем статус сессии
        session.status = 'PROCESSING'
        session.save(update_fields=['status', 'updated_at'])

        files_qs = session.files.all()
        if not files_qs.exists():
            raise ValueError("No files uploaded for analysis")

        logger.info(f"📁 Found {files_qs.count()} uploaded artifacts for analysis")

        # ✅ Пытаемся запустить анализ в Docker-контейнере
        runner = DockerRunner(timeout=60)

        # Собираем исходники проекта: либо директория из архива, либо список файлов
        project_dir = None
        file_paths = None
        archive_file = files_qs.filter(file_type='ARCHIVE').order_by('-uploaded_at').first()
        try:
            if session.upload_mode == 'ARCHIVE' and archive_file:
                project_dir = runner.prepare_project_dir_from_archive(
                    archive_path=archive_file.file.path,
                    session_id=str(session.id),
                )
            else:
                file_paths = [f.file.path for f in files_qs if f.file_type in ('PY', 'REQUIREMENTS', 'OTHER')]
                if not file_paths:
                    raise ValueError("No source files uploaded for analysis")
        except Exception as e:
            logger.error(f"❌ Failed to prepare project directory: {e}", exc_info=True)
            raise

        result = runner.run_analysis_container(
            project_dir=project_dir,
            file_paths=file_paths,
            python_version=session.python_version,
            dependencies=session.dependencies or [],
            run_command=(session.run_command or '').strip(),
            run_tests=False,
            session_id=str(session.id),
        )

        if result['status'] == 'success':
            analysis_data = result.get('analysis', {})

            session.metrics = analysis_data.get('metrics', {})
            if file_paths is not None:
                session.metrics['files_count'] = len(file_paths)
            if project_dir:
                session.metrics['project_mode'] = 'directory'

            if analysis_data.get('resources'):
                session.metrics['resources'] = analysis_data['resources']

            if analysis_data.get('run'):
                session.metrics['run'] = analysis_data['run']

            session.metrics['mode'] = 'docker'

            session.report_text = analysis_data.get('report', '')
            session.status = 'ANALYZED'
            logger.info(f"✅ Docker analysis completed")

        else:
            fallback_analyzer = CodeAnalyzer()
            fallback_file_paths = file_paths
            if fallback_file_paths is None and project_dir:
                # Fallback: пытаемся собрать .py файлы из project_dir
                import os
                from pathlib import Path
                p = Path(project_dir)
                fallback_file_paths = [str(x) for x in p.rglob('*.py')]
            fallback_result = fallback_analyzer.analyze_code(fallback_file_paths or [])

            # ✅ Добавьте mode в fallback тоже
            fallback_metrics = fallback_result.get('metrics', {})
            fallback_metrics['mode'] = 'fallback'
            fallback_metrics['fallback_reason'] = result.get('error', 'Unknown Docker error')

            session.metrics = fallback_metrics
            session.report_text = fallback_result.get('report', '')
            session.status = 'ANALYZED'

        session.save(update_fields=['metrics', 'report_text', 'status', 'updated_at'])

        return {
            'status': 'success',
            'session_id': session_id,
            'mode': 'docker' if result['status'] == 'success' else 'fallback',
            'metrics_summary': {
                'files': session.metrics.get('files_count', 0),
                'functions': session.metrics.get('functions_count', 0),
                'classes': session.metrics.get('classes_count', 0)
            }
        }

    except AnalysisSession.DoesNotExist:
        logger.error(f"❌ Session {session_id} not found")
        return {'status': 'failed', 'error': 'Session not found'}

    except Exception as exc:
        logger.error(f"❌ Analysis failed for session {session_id}: {exc}", exc_info=True)

        # Обновляем сессию с информацией об ошибке
        try:
            session = AnalysisSession.objects.get(id=session_id)
            session.status = 'FAILED'
            session.error_message = str(exc)
            session.save(update_fields=['status', 'error_message', 'updated_at'])
        except Exception as e:
            logger.error(f"Could not update session status: {e}")

        # Повторяем задачу с экспоненциальной задержкой
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=2)
def generate_tests_task(self, task_id: str):
    """Задача генерации юнит-тестов с помощью AI"""
    from .models import TestGenerationTask, AnalysisSession
    from .utils.ai_generator import AITestGenerator

    try:
        task = TestGenerationTask.objects.get(id=task_id)
        logger.info(f"🧪 Starting test generation for task {task_id}")

        task.status = 'GENERATING'
        task.save(update_fields=['status', 'updated_at'])

        session = task.session
        runner = DockerRunner(timeout=60)
        generator = AITestGenerator(model=task.config.get('model') or None)

        if not generator.check_ollama_available():
            logger.warning("⚠️ Ollama service not available")
            task.config['fallback'] = True
        elif task.config.get('model') and not generator.check_model_available(task.config.get('model')):
            logger.warning(f"⚠️ Model '{task.config.get('model')}' not found in Ollama")
            task.config['fallback'] = True
            task.error_message = f"Model '{task.config.get('model')}' not pulled. Run: ollama pull {task.config.get('model')}"
            task.save(update_fields=['config', 'error_message'])

        # Если AI недоступен/модель не подтянута — принудительно уходим в basic/full-basic
        if task.config.get('fallback') and task.config.get('detail_level') in ('advanced', 'full'):
            task.config['force_basic'] = True
            task.save(update_fields=['config'])

        code_content = ""
        files_qs = session.files.all()
        archive_file = files_qs.filter(file_type='ARCHIVE').order_by('-uploaded_at').first()

        try:
            if session.upload_mode == 'ARCHIVE' and archive_file:
                project_dir = runner.prepare_project_dir_from_archive(
                    archive_path=archive_file.file.path,
                    session_id=str(session.id),
                )
                from pathlib import Path
                p = Path(project_dir)
                py_files = [x for x in p.rglob('*.py') if x.is_file()]
                # игнорируем кэши/венвы если пришли в архиве
                py_files = [x for x in py_files if ".venv" not in str(x) and "__pycache__" not in str(x)]
                py_files = [
                    x for x in py_files
                    if not x.name.startswith('test_')
                    and not x.name.startswith('tests_')
                    and '/tests/' not in str(x).replace('\\', '/')
                ]
                py_files = py_files[:200]  # защита от слишком больших проектов

                for fp in py_files:
                    try:
                        rel = str(fp.relative_to(p)).replace('\\', '/')
                        code = fp.read_text(encoding='utf-8-sig', errors='ignore')
                        if code.strip():
                            code_content += f"\n# File: {rel}\n{code}\n"
                    except Exception as e:
                        logger.warning(f"⚠️ Could not read file {fp}: {e}")
                        continue
            else:
                # Режим files: читаем только .py и requirements.txt как текст
                for uploaded_file in files_qs:
                    try:
                        name_l = (uploaded_file.original_name or '').lower()
                        if not (name_l.endswith('.py') or name_l == 'requirements.txt'):
                            continue
                        if name_l.startswith('test_') or name_l.startswith('tests_'):
                            continue
                        with open(uploaded_file.file.path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                            code_content += f"\n# File: {uploaded_file.original_name}\n{f.read()}\n"
                    except Exception as e:
                        logger.warning(f"⚠️ Could not read file {uploaded_file.original_name}: {e}")
                        continue
        except Exception as e:
            logger.error(f"❌ Failed to collect code for generation: {e}", exc_info=True)
            raise

        if not code_content.strip():
            raise ValueError("No code content available for test generation")

        # Генерация тестов
        logger.info(f"🤖 Generating tests with config: {task.config}")
        detail_level = (task.config.get('detail_level') or '').lower()
        force_basic = bool(task.config.get('force_basic'))
        if force_basic or detail_level == 'basic':
            task.config['generation_mode'] = 'basic'
        elif detail_level in ('advanced', 'full'):
            task.config['generation_mode'] = 'ai'
        else:
            task.config['generation_mode'] = 'basic'

        tests = generator.generate_tests(
            code=code_content[:20000],  # Ограничиваем вход (и для AI, и для basic/full)
            metrics=session.metrics,
            config=task.config
        )

        cleaned_tests = (tests or "").replace('\ufeff', '').replace('\u200b', '').replace('\u200c', '').strip()
        # Финальная валидация синтаксиса: при ошибке откатываемся на безопасный basic.
        try:
            compile(cleaned_tests, "<generated_tests>", "exec")
        except Exception as syn_err:
            logger.warning(f"⚠️ Generated tests invalid python ({syn_err}). Using safe basic fallback.")
            safe_basic = generator._generate_basic_tests(
                code=code_content[:20000],
                metrics=session.metrics,
                framework=task.config.get('test_framework', 'pytest'),
                include_edge_cases=task.config.get('include_edge_cases', True),
            )
            cleaned_tests = (safe_basic or "").replace('\ufeff', '').replace('\u200b', '').replace('\u200c', '').strip()

        if len(cleaned_tests) < 50 and detail_level in ('advanced', 'full') and not force_basic:
            logger.warning(f"⚠️ Generated tests suspiciously short ({len(cleaned_tests)} chars), marking as FAILED")
            task.generated_tests = cleaned_tests
            task.status = 'FAILED'
            task.error_message = f"Generated tests too short ({len(cleaned_tests)} chars), likely AI error"
        else:
            task.generated_tests = cleaned_tests
            task.status = 'COMPLETED'

        task.save(update_fields=['generated_tests', 'status', 'error_message', 'config', 'updated_at'])

        # Обновляем сессию
        session.status = 'TESTS_GENERATED'
        session.save(update_fields=['status', 'updated_at'])

        logger.info(f"✅ Test generation completed: {len(tests)} characters")

        if not tests or not tests.strip():
            logger.error("⚠️ WARNING: Generated tests are EMPTY!")
            logger.error(f"Config used: {task.config}")
            logger.error(f"Code snippet sent to AI: {code_content[:500]}...")

        return {
            'status': 'success',
            'task_id': task_id,
            'tests_length': len(tests)
        }

    except TestGenerationTask.DoesNotExist:
        logger.error(f"❌ Task {task_id} not found")
        return {'status': 'failed', 'error': 'Task not found'}

    except Exception as exc:
        logger.error(f"❌ Test generation failed: {exc}", exc_info=True)

        try:
            task = TestGenerationTask.objects.get(id=task_id)
            task.status = 'FAILED'
            task.error_message = str(exc)
            task.save(update_fields=['status', 'error_message', 'updated_at'])
        except:
            pass

        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))

@shared_task
def cleanup_expired_sessions():
    """
    Задача очистки устаревших сессий (запускается Celery Beat раз в сутки).
    Удаляет сессии и файлы старше 7 дней.
    """
    from django.utils import timezone
    from datetime import timedelta

    expiry_date = timezone.now() - timedelta(days=7)
    old_sessions = AnalysisSession.objects.filter(expires_at__lt=expiry_date)

    deleted_count = 0
    for session in old_sessions:
        # Удаляем связанные файлы с диска
        for uploaded_file in session.files.all():
            try:
                if uploaded_file.file:
                    uploaded_file.file.delete(save=False)
                    logger.debug(f"🗑️ Deleted file: {uploaded_file.file.path}")
            except Exception as e:
                logger.warning(f"⚠️ Could not delete file for session {session.id}: {e}")

        # Удаляем запись сессии из БД
        session.delete()
        deleted_count += 1
        logger.info(f"🗑️ Cleaned up expired session: {session.id}")

    logger.info(f"🧹 Cleanup completed: {deleted_count} sessions removed")
    return {'deleted_sessions': deleted_count}