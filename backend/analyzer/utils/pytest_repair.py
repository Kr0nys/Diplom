"""
Запуск pytest по сгенерированному файлу внутри распакованного проекта (PYTHONPATH = корень).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple

from .pytest_report import PYTEST_JUNIT_FILENAME, build_pytest_report, pytest_cli_args

logger = logging.getLogger(__name__)


def write_tests_and_run_pytest(
    project_root: str,
    test_source: str,
    filename: str = "_ai_generated_tests.py",
) -> Tuple[int, str]:
    """
    Пишет тесты в project_root/filename и запускает pytest только для этого файла.
    Возвращает (код возврата, объединённый stdout+stderr, усечённый).
    """
    root = Path(project_root).resolve()
    path = root / filename
    path.write_text(test_source, encoding="utf-8")
    junit_path = root / PYTEST_JUNIT_FILENAME
    if junit_path.exists():
        junit_path.unlink()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    cmd = [sys.executable, "-m", "pytest", *pytest_cli_args(str(path), junit_path=str(junit_path))]
    timeout = int(os.environ.get("AI_PYTEST_TIMEOUT_SEC", "120"))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("pytest repair: timeout after %ss", timeout)
        return 124, f"pytest TIMEOUT after {timeout}s\n"
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    max_out = int(os.environ.get("AI_PYTEST_OUTPUT_MAX", "14000"))
    if len(out) > max_out:
        out = out[:max_out] + "\n… (output truncated)"
    return proc.returncode, out


def write_tests_and_run_pytest_report(
    project_root: str,
    test_source: str,
    filename: str = "_session_run_tests.py",
):
    """Запуск pytest с формированием структурированного отчёта."""
    root = Path(project_root).resolve()
    path = root / filename
    path.write_text(test_source, encoding="utf-8")
    junit_path = root / PYTEST_JUNIT_FILENAME
    if junit_path.exists():
        junit_path.unlink()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    cmd = [sys.executable, "-m", "pytest", *pytest_cli_args(str(path), junit_path=str(junit_path))]
    timeout = int(os.environ.get("AI_PYTEST_TIMEOUT_SEC", "120"))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        out = f"pytest TIMEOUT after {timeout}s\n"
        return build_pytest_report(124, out)

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    max_out = int(os.environ.get("AI_PYTEST_OUTPUT_MAX", "50000"))
    if len(out) > max_out:
        out = out[:max_out] + "\n… (output truncated)"

    junit_bytes = junit_path.read_bytes() if junit_path.exists() else None
    return build_pytest_report(proc.returncode, out, junit_bytes=junit_bytes)
