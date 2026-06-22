"""
Запуск выбранных сгенерированных тестов на загруженном проекте сессии.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import os
from typing import Any, Dict, Optional
from uuid import UUID

from ..models import AnalysisSession
from .docker_runner import DockerRunner
from .pytest_repair import write_tests_and_run_pytest_report
from .pytest_report import PytestRunReport

logger = logging.getLogger(__name__)


class SessionTestRunError(ValueError):
    pass


def _pytest_available() -> bool:
    return importlib.util.find_spec("pytest") is not None


def _use_docker_for_pytest() -> bool:
    return os.environ.get("SESSION_TESTS_USE_DOCKER", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def _active_test_text(session: AnalysisSession) -> Optional[str]:
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


def _resolve_test_source(session: AnalysisSession, *, stored_id: Optional[str]) -> tuple[str, Optional[str]]:
    if stored_id:
        try:
            uid = UUID(str(stored_id))
        except (TypeError, ValueError) as e:
            raise SessionTestRunError("Некорректный идентификатор версии тестов.") from e
        row = session.stored_generated_tests.filter(id=uid).first()
        if not row:
            raise SessionTestRunError("Версия тестов не найдена в этой сессии.")
        text = (row.generated_tests or "").strip()
        if not text:
            raise SessionTestRunError("Выбранная версия не содержит кода тестов.")
        return text, str(row.id)

    text = (_active_test_text(session) or "").strip()
    if not text:
        raise SessionTestRunError("Нет сгенерированных тестов для запуска.")
    return text, None


def _run_pytest(
    runner: DockerRunner,
    *,
    session: AnalysisSession,
    project_dir: str,
    test_source: str,
    test_filename: str,
) -> tuple[PytestRunReport, str]:
    """Возвращает (отчёт, runner_mode)."""
    if _use_docker_for_pytest():
        try:
            report = runner.run_pytest_in_container(
                project_dir=project_dir,
                test_source=test_source,
                test_filename=test_filename,
                python_version=session.python_version,
                dependencies=session.dependencies or [],
                session_id=str(session.id),
            )
            return report, "docker"
        except Exception as e:
            logger.warning("Docker pytest unavailable, fallback to local: %s", e, exc_info=True)
            if not _pytest_available():
                raise SessionTestRunError(
                    "Не удалось запустить pytest в Docker и pytest не установлен локально. "
                    "Проверьте доступ к Docker или пересоберите backend (`docker compose build backend`)."
                ) from e

    if not _pytest_available():
        raise SessionTestRunError(
            "pytest не установлен. Пересоберите контейнер backend или включите Docker-режим "
            "(SESSION_TESTS_USE_DOCKER=1)."
        )

    report = write_tests_and_run_pytest_report(
        project_dir,
        test_source,
        filename=test_filename,
    )
    return report, "local"


def resolve_session_test_text(
    session: AnalysisSession,
    *,
    stored_id: Optional[str] = None,
    generated_tests: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    if generated_tests is not None:
        text = str(generated_tests).strip()
        if not text:
            raise SessionTestRunError("Пустой текст тестов.")
        return text, stored_id
    return _resolve_test_source(session, stored_id=stored_id)


def run_session_tests(
    session: AnalysisSession,
    *,
    stored_id: Optional[str] = None,
    generated_tests: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Распаковывает проект сессии, записывает тесты и запускает pytest.
    """
    if not session.files.exists():
        raise SessionTestRunError("Сначала загрузите проект (файлы или архив).")

    test_source, selected_id = resolve_session_test_text(
        session,
        stored_id=stored_id,
        generated_tests=generated_tests,
    )

    try:
        ast.parse(test_source)
    except SyntaxError as e:
        raise SessionTestRunError(f"Синтаксическая ошибка в тестах: {e.msg} (строка {e.lineno})") from e

    runner = DockerRunner(timeout=60)
    test_filename = "_session_run_tests.py"
    try:
        project_dir = runner.prepare_project_dir_for_session(session)
    except Exception as e:
        logger.exception("Failed to prepare project dir for session %s", session.id)
        raise SessionTestRunError(f"Не удалось подготовить проект: {e}") from e

    report, runner_mode = _run_pytest(
        runner,
        session=session,
        project_dir=project_dir,
        test_source=test_source,
        test_filename=test_filename,
    )

    payload = report.to_dict()
    payload["stored_id"] = selected_id
    payload["upload_mode"] = session.upload_mode
    payload["project_prepared"] = True
    payload["runner"] = runner_mode
    return payload
