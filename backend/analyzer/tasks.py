from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import logging
import os

from .models import AnalysisSession, TestGenerationTask, UploadedFile
from .utils.docker_runner import DockerRunner
from .utils.code_analyzer import CodeAnalyzer
from .utils.ai_generator import AITestGenerator
from .utils.complexity_radon import merge_complexity_into_metrics, extend_recommendations_with_complexity
from .utils.project_tree import build_tree_for_session

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

        session.save(update_fields=['status', 'error_message', 'updated_at'])

        files_qs = session.files.all()
        if not files_qs.exists():
            raise ValueError("No files uploaded for analysis")

        logger.info(f"📁 Found {files_qs.count()} uploaded artifacts for analysis")

        archive_file = files_qs.filter(file_type='ARCHIVE').order_by('-uploaded_at').first()
        uploaded_entries = [(f.original_name, f.file_size) for f in files_qs]

        def _attach_project_tree(metrics: dict) -> None:
            tree = build_tree_for_session(
                upload_mode=session.upload_mode or "FILES",
                archive_path=archive_file.file.path if archive_file else None,
                uploaded_entries=uploaded_entries,
                project_dir=project_dir,
            )
            if tree and tree.get("children"):
                metrics["project_tree"] = tree

        runner = DockerRunner(timeout=60)

        project_dir = None
        file_paths = None
        try:
            if session.upload_mode in ('ARCHIVE', 'GITHUB') and archive_file:
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
            if analysis_data.get('issues'):
                session.metrics['issues'] = analysis_data.get('issues') or []
            if analysis_data.get('recommendations') is not None:
                session.metrics['recommendations'] = analysis_data.get('recommendations') or []
            if file_paths is not None:
                session.metrics['files_count'] = len(file_paths)
            if project_dir:
                session.metrics['project_mode'] = 'directory'

            if analysis_data.get('resources'):
                session.metrics['resources'] = analysis_data['resources']

            if analysis_data.get('run'):
                session.metrics['run'] = analysis_data['run']

            session.metrics['mode'] = 'docker'

            merge_complexity_into_metrics(session.metrics, analysis_data.get('static_tools'))
            recs = list(session.metrics.get('recommendations') or [])
            extend_recommendations_with_complexity(recs, session.metrics.get('cyclomatic_complexity'))
            session.metrics['recommendations'] = recs

            _attach_project_tree(session.metrics)

            session.report_text = analysis_data.get('report', '')
            session.status = 'ANALYZED'
            logger.info(f"✅ Docker analysis completed")

        else:
            fallback_analyzer = CodeAnalyzer()
            fallback_file_paths = file_paths
            if fallback_file_paths is None and project_dir:
                from pathlib import Path
                p = Path(project_dir)
                fallback_file_paths = [str(x) for x in p.rglob('*.py')]
            fallback_result = fallback_analyzer.analyze_code(fallback_file_paths or [])

            fallback_metrics = fallback_result.get('metrics', {})
            fallback_metrics['mode'] = 'fallback'
            fallback_metrics['fallback_reason'] = result.get('error', 'Unknown Docker error')
            if fallback_result.get('issues'):
                fallback_metrics['issues'] = fallback_result.get('issues') or []

            session.metrics = fallback_metrics
            _attach_project_tree(session.metrics)
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

        try:
            session = AnalysisSession.objects.get(id=session_id)
            session.status = 'FAILED'
            session.error_message = str(exc)
            session.save(update_fields=['status', 'error_message', 'updated_at'])
        except Exception as e:
            logger.error(f"Could not update session status: {e}")

        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=2)
def generate_tests_task(self, task_id: str):
    """Задача генерации юнит-тестов с помощью AI"""
    try:
        task = TestGenerationTask.objects.get(id=task_id)
        logger.info(f"🧪 Starting test generation for task {task_id}")

        task.status = 'GENERATING'
        task.save(update_fields=['status', 'updated_at'])

        session = task.session
        session.status = 'GENERATING_TESTS'
        session.save(update_fields=['status', 'updated_at'])
        runner = DockerRunner(timeout=60)
        generator_timeout = int(task.config.get('generation_timeout_sec') or 600)
        # Модель задаётся в окружении сервера, не в форме пользователя.
        generator = AITestGenerator(timeout=generator_timeout)
        detail_level = (task.config.get('detail_level') or 'basic').lower()
        if detail_level not in ('basic', 'advanced', 'full'):
            detail_level = 'basic'
            task.config['detail_level'] = 'basic'

        task.config['generation_mode'] = detail_level
        task.save(update_fields=['config', 'updated_at'])

        llm_assist = bool(task.config.get("llm_assist", False))
        if "llm_assist" not in task.config:
            env_def = (os.environ.get("AI_LLM_ASSIST_DEFAULT", "0") or "0").strip().lower()
            llm_assist = env_def in ("1", "true", "yes", "on")

        ai_required = detail_level in ('advanced', 'full') and llm_assist
        if ai_required:
            if not generator.check_provider_available():
                logger.warning("⚠️ Ollama недоступна")
                task.config['fallback'] = True
            elif not generator.check_model_available(generator.model):
                logger.warning(f"⚠️ Модель '{generator.model}' не найдена в Ollama")
                task.config['fallback'] = True
                task.error_message = f"Model '{generator.model}' not available in Ollama"
            task.save(update_fields=['config', 'error_message', 'updated_at'])

            if detail_level == 'advanced' and task.config.get('fallback'):
                task.status = 'FAILED'
                task.error_message = task.error_message or "AI service/model unavailable for advanced mode"
                task.save(update_fields=['status', 'error_message', 'config', 'updated_at'])
                session.status = 'ANALYZED'
                session.save(update_fields=['status', 'updated_at'])
                return {
                    'status': 'failed',
                    'task_id': task_id,
                    'reason': task.error_message,
                }

        code_content = ""
        extracted_project_dir = None
        files_qs = session.files.all()
        archive_file = files_qs.filter(file_type='ARCHIVE').order_by('-uploaded_at').first()

        try:
            if session.upload_mode in ('ARCHIVE', 'GITHUB') and archive_file:
                project_dir = runner.prepare_project_dir_from_archive(
                    archive_path=archive_file.file.path,
                    session_id=str(session.id),
                )
                extracted_project_dir = project_dir
                from pathlib import Path
                p = Path(project_dir)
                py_files = [x for x in p.rglob('*.py') if x.is_file()]
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

        if not extracted_project_dir:
            try:
                extracted_project_dir = runner.prepare_project_dir_for_session(session)
            except Exception as e:
                logger.warning("⚠️ Project dir for pytest repair unavailable: %s", e)

        logger.info(f"🤖 Generating tests with config: {task.config}")
        # Лимит контекста: слишком большой — таймауты Ollama, слишком маленький — теряются models.py и типы.
        env_limit_full = os.environ.get("AI_INPUT_LIMIT_FULL")
        env_limit_adv = os.environ.get("AI_INPUT_LIMIT_ADVANCED")
        input_limit = 20000
        if detail_level == 'full':
            input_limit = int(env_limit_full or 20000)
        elif detail_level == 'advanced':
            input_limit = int(env_limit_adv or 12000)

        tests = generator.generate_tests(
            code=code_content[:input_limit],
            metrics=session.metrics,
            config=task.config
        )

        cleaned_tests = (tests or "").replace('\ufeff', '').replace('\u200b', '').replace('\u200c', '').strip()
        if detail_level == 'advanced' and "AI_GENERATION_FAILED" in cleaned_tests:
            task.generated_tests = cleaned_tests
            task.status = 'FAILED'
            task.error_message = cleaned_tests
            task.save(update_fields=['generated_tests', 'status', 'error_message', 'config', 'updated_at'])
            session.status = 'ANALYZED'
            session.save(update_fields=['status', 'updated_at'])
            logger.warning("⚠️ Advanced mode strict failure: AI generation did not produce acceptable unique suite.")
            return {
                'status': 'failed',
                'task_id': task_id,
                'tests_length': len(cleaned_tests),
                'reason': cleaned_tests,
            }

        # Синтаксис: при ошибке в advanced — fail, в остальных — откат на basic.
        try:
            compile(cleaned_tests, "<generated_tests>", "exec")
        except Exception as syn_err:
            if detail_level == 'advanced':
                logger.warning(f"⚠️ Advanced mode invalid python ({syn_err})")
                task.generated_tests = cleaned_tests
                task.status = 'FAILED'
                task.error_message = f"AI generated invalid python: {syn_err}"
                task.save(update_fields=['generated_tests', 'status', 'error_message', 'config', 'updated_at'])
                session.status = 'ANALYZED'
                session.save(update_fields=['status', 'updated_at'])
                return {
                    'status': 'failed',
                    'task_id': task_id,
                    'tests_length': len(cleaned_tests),
                    'reason': task.error_message,
                }
            logger.warning(f"⚠️ Generated tests invalid python ({syn_err}). Using safe basic fallback.")
            safe_basic = generator._generate_basic_tests(
                code=code_content[:input_limit],
                metrics=session.metrics,
                framework=task.config.get('test_framework', 'pytest'),
                include_edge_cases=task.config.get('include_edge_cases', True),
            )
            cleaned_tests = (safe_basic or "").replace('\ufeff', '').replace('\u200b', '').replace('\u200c', '').strip()

        if extracted_project_dir and task.config.get("pytest_repair", True):
            off = os.environ.get("AI_PYTEST_REPAIR", "1").strip().lower()
            if off not in ("0", "false", "off", "no") and task.config.get("test_framework", "pytest") == "pytest":
                try:
                    cleaned_tests = generator.run_pytest_repair_loop(
                        code_content[:input_limit],
                        cleaned_tests,
                        extracted_project_dir,
                        session.metrics or {},
                        task.config,
                        "pytest",
                    )
                except Exception as e:
                    logger.warning("pytest repair loop skipped: %s", e, exc_info=True)

        if len(cleaned_tests) < 50 and detail_level in ('advanced', 'full'):
            logger.warning(f"⚠️ Generated tests suspiciously short ({len(cleaned_tests)} chars), marking as FAILED")
            task.generated_tests = cleaned_tests
            task.status = 'FAILED'
            task.error_message = f"Generated tests too short ({len(cleaned_tests)} chars), likely AI error"
            session.status = 'ANALYZED'
            session.save(update_fields=['status', 'updated_at'])
        else:
            task.generated_tests = cleaned_tests
            task.status = 'COMPLETED'

        task.save(update_fields=['generated_tests', 'status', 'error_message', 'config', 'updated_at'])

        if task.status == 'COMPLETED':
            session.status = 'TESTS_GENERATED'
            session.save(update_fields=['status', 'updated_at'])
            from .models import StoredGeneratedTest
            StoredGeneratedTest.objects.update_or_create(
                source_task=task,
                defaults={
                    'session': session,
                    'generated_tests': task.generated_tests,
                    'config': dict(task.config or {}),
                },
            )

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
            sess = task.session
            sess.status = 'ANALYZED'
            sess.save(update_fields=['status', 'updated_at'])
        except Exception:
            pass

        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))

@shared_task
def cleanup_expired_sessions():
    """
    Задача очистки устаревших сессий (по расписанию Celery Beat).

    Не сканирует диск по «датам файлов» в цикле: выбирает записи в БД по expires_at / created_at,
    затем удаляет связанные загрузки. Нагрузка кратковременная и только в момент запуска задачи.
    """
    from django.utils import timezone
    from django.db.models import Q
    from django.conf import settings
    from datetime import timedelta

    now = timezone.now()
    retention_days = int(getattr(settings, 'FILE_RETENTION_DAYS', 7) or 7)
    fallback_created_before = now - timedelta(days=retention_days)

    # Раньше сравнивали expires_at с (now - 7d) — удаление откладывалось лишь на неделю.
    old_sessions = AnalysisSession.objects.filter(
        Q(expires_at__lt=now) | Q(expires_at__isnull=True, created_at__lt=fallback_created_before)
    )

    deleted_count = 0
    for session in old_sessions:
        for uploaded_file in session.files.all():
            try:
                if uploaded_file.file:
                    uploaded_file.file.delete(save=False)
                    logger.debug(f"🗑️ Deleted file: {uploaded_file.file.path}")
            except Exception as e:
                logger.warning(f"⚠️ Could not delete file for session {session.id}: {e}")

        session.delete()
        deleted_count += 1
        logger.info(f"🗑️ Cleaned up expired session: {session.id}")

    logger.info(f"🧹 Cleanup completed: {deleted_count} sessions removed")
    return {'deleted_sessions': deleted_count}