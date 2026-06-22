import docker
import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..python_versions import normalize_python_version
from .pytest_report import PYTEST_JUNIT_FILENAME, PytestRunReport, build_pytest_report, pytest_cli_args

logger = logging.getLogger(__name__)

RESULT_FILENAME = "_result.json"


class DockerRunner:
    """
    Сборка образа и запуск анализа в изолированном контейнере.

    На этапе build сеть разрешена (установка зависимостей).
    На этапе run — без сети, с лимитами памяти/CPU и read-only rootfs.
    """

    def __init__(self, timeout: int = 60):
        self.client = docker.from_env()
        self.timeout = timeout

    def prepare_project_dir_from_archive(self, archive_path: str, session_id: str) -> str:
        """
        Распаковывает архив проекта (zip/tar/tar.gz) в временную директорию и возвращает путь.
        Директория создаётся локально (в контейнере celery_worker).
        """
        base_dir = Path(os.environ.get("ANALYZER_WORKDIR", "/work")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        out_dir = base_dir / f"proj_{session_id.replace('-', '')[:12]}_{uuid.uuid4().hex[:8]}"
        out_dir.mkdir(parents=True, exist_ok=True)

        ap = Path(archive_path)
        name_l = ap.name.lower()
        logger.info(f"📦 Extracting archive {ap.name} -> {out_dir}")

        if name_l.endswith(".zip"):
            with zipfile.ZipFile(ap, "r") as z:
                self._safe_extract_zip(z, out_dir)
        elif name_l.endswith(".tar") or name_l.endswith(".tar.gz") or name_l.endswith(".tgz"):
            mode = "r:gz" if (name_l.endswith(".tar.gz") or name_l.endswith(".tgz")) else "r"
            with tarfile.open(ap, mode) as t:
                self._safe_extract_tar(t, out_dir)
        else:
            raise ValueError(f"Unsupported archive type: {ap.name}")

        return self._detect_project_root(out_dir)

    def _safe_extract_zip(self, zf: zipfile.ZipFile, out_dir: Path) -> None:
        base = out_dir.resolve()
        for member in zf.infolist():
            name = member.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            if name.startswith("/") or ".." in Path(name).parts:
                logger.warning("⚠️ Skipping suspicious zip member: %s", name)
                continue
            target = (out_dir / name).resolve()
            if not str(target).startswith(str(base)):
                logger.warning("⚠️ Skipping zip member outside output dir: %s", name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    def _safe_extract_tar(self, tf: tarfile.TarFile, out_dir: Path) -> None:
        base = out_dir.resolve()
        for member in tf.getmembers():
            name = (member.name or "").replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            if name.startswith("/") or ".." in Path(name).parts:
                logger.warning("⚠️ Skipping suspicious tar member: %s", name)
                continue
            target = (out_dir / name).resolve()
            if not str(target).startswith(str(base)):
                logger.warning("⚠️ Skipping tar member outside output dir: %s", name)
                continue
            if member.islnk() or member.issym():
                logger.warning("⚠️ Skipping tar link: %s", name)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if not src:
                continue
            with src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    def _detect_project_root(self, out_dir: Path) -> str:
        """
        Определяет корень проекта после распаковки.
        Частый случай: архив содержит одну корневую папку + мусор (__MACOSX/.DS_Store).
        """
        ignore_names = {"__MACOSX", ".DS_Store"}
        children = [p for p in out_dir.iterdir() if p.name not in ignore_names]

        top_level_dirs = [p for p in children if p.is_dir() and p.name not in ignore_names]
        top_level_files = [p for p in children if p.is_file() and p.name not in ignore_names]

        # Если в корне нет файлов и есть ровно 1 директория — считаем её проектом
        if not top_level_files and len(top_level_dirs) == 1:
            return str(top_level_dirs[0].resolve())

        # Если есть ровно 1 директория + только игнорируемые файлы — тоже ок
        if len(top_level_dirs) == 1 and all((p.name in ignore_names) for p in out_dir.iterdir() if p.is_file()):
            return str(top_level_dirs[0].resolve())

        return str(out_dir.resolve())

    def prepare_project_dir_for_session(self, session) -> str:
        """
        Готовит директорию проекта сессии: распаковка архива или копирование загруженных файлов.
        """
        session_id = str(getattr(session, "id", "") or "")
        files_qs = session.files.all()
        archive_file = files_qs.filter(file_type="ARCHIVE").order_by("-uploaded_at").first()
        upload_mode = getattr(session, "upload_mode", "") or "FILES"

        if upload_mode in ("ARCHIVE", "GITHUB") and archive_file:
            return self.prepare_project_dir_from_archive(
                archive_path=archive_file.file.path,
                session_id=session_id,
            )

        file_paths: List[str] = []
        relative_names: List[str] = []
        for f in files_qs:
            if f.file_type not in ("PY", "REQUIREMENTS", "OTHER"):
                continue
            name_l = (f.original_name or "").lower().replace("\\", "/")
            if name_l.startswith("test_") or name_l.startswith("tests_"):
                continue
            if not (name_l.endswith(".py") or name_l == "requirements.txt"):
                continue
            file_paths.append(f.file.path)
            relative_names.append((f.original_name or Path(f.file.path).name).replace("\\", "/"))
        if not file_paths:
            raise ValueError("Нет файлов проекта для запуска тестов.")
        return self._prepare_project_dir_from_files(
            file_paths,
            session_id=session_id,
            relative_names=relative_names,
        )

    def run_pytest_in_container(
        self,
        *,
        project_dir: str,
        test_source: str,
        test_filename: str = "_session_run_tests.py",
        python_version: str = "3.9",
        dependencies: Optional[List[str]] = None,
        session_id: str = "",
    ) -> PytestRunReport:
        """
        Собирает образ с проектом и зависимостями, запускает pytest для одного файла тестов.
        """
        dependencies = dependencies or []
        python_version = normalize_python_version(python_version)
        root = Path(project_dir).resolve()
        test_path = root / test_filename
        test_path.write_text(test_source, encoding="utf-8")

        ctx_dir: Optional[Path] = None
        image_tag: Optional[str] = None
        container = None
        timeout = int(os.environ.get("AI_PYTEST_TIMEOUT_SEC", "120"))
        max_out = int(os.environ.get("AI_PYTEST_OUTPUT_MAX", "50000"))
        junit_in_container = f"/app/project/{PYTEST_JUNIT_FILENAME}"

        try:
            ctx_dir = Path(tempfile.mkdtemp(prefix="pytest_ctx_"))
            src_dir = ctx_dir / "project"
            shutil.copytree(root, src_dir, dirs_exist_ok=True)

            req_path = self._ensure_requirements(src_dir, dependencies)
            dockerfile = self._create_pytest_dockerfile(
                python_version=python_version,
                has_requirements=bool(req_path),
            )
            (ctx_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")

            sid = (session_id or uuid.uuid4().hex).replace("-", "")[:12]
            image_tag = f"pytest_job:{sid}_{uuid.uuid4().hex[:8]}"
            logger.info("🐳 Building pytest image %s (python %s)", image_tag, python_version)
            self.client.images.build(path=str(ctx_dir), tag=image_tag, rm=True, pull=True)

            logger.info("🐳 Running pytest in container")
            test_target = f"/app/project/{test_filename}"
            container = self.client.containers.run(
                image=image_tag,
                command=["python", "-m", "pytest", *pytest_cli_args(test_target, junit_path=junit_in_container)],
                detach=True,
                environment={"PYTHONPATH": "/app/project"},
                working_dir="/app/project",
                network_mode="none",
                mem_limit="512m",
                cpu_quota=50000,
                pids_limit=128,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                user="1000:1000",
            )

            wait_res = container.wait(timeout=timeout + 15)
            exit_code = int(wait_res.get("StatusCode", 1))
            logs = (container.logs(stdout=True, stderr=True) or b"").decode("utf-8", errors="ignore")
            if len(logs) > max_out:
                logs = logs[:max_out] + "\n… (output truncated)"

            junit_bytes = self._extract_file_from_container(container, junit_in_container)
            return build_pytest_report(exit_code, logs, junit_bytes=junit_bytes)

        except docker.errors.DockerException as e:
            logger.error("Docker pytest error: %s", e, exc_info=True)
            raise RuntimeError(f"Docker: {e}") from e
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            if image_tag is not None:
                try:
                    self.client.images.remove(image=image_tag, force=True)
                except Exception:
                    pass
            if ctx_dir is not None and ctx_dir.exists():
                try:
                    shutil.rmtree(ctx_dir, ignore_errors=True)
                except Exception:
                    pass

    def _extract_file_from_container(self, container, path_in_container: str) -> Optional[bytes]:
        try:
            bits, _ = container.get_archive(path_in_container)
            buf = io.BytesIO()
            for chunk in bits:
                buf.write(chunk)
            buf.seek(0)
            with tarfile.open(fileobj=buf, mode="r") as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        src = tar.extractfile(member)
                        if src:
                            return src.read()
        except Exception as e:
            logger.warning("Could not extract %s from container: %s", path_in_container, e)
        return None

    def run_analysis_container(
        self,
        *,
        project_dir: Optional[str] = None,
        file_paths: Optional[List[str]] = None,
        python_version: str = "3.9",
        dependencies: Optional[List[str]] = None,
        run_command: str = "",
        run_tests: bool = False,
        session_id: str = "",
    ) -> Dict:
        """
        Запускает анализ проекта.

        Вход:
        - project_dir: директория проекта (предпочтительно)
        - file_paths: список файлов (legacy режим; будет собран во временную директорию)
        """
        dependencies = dependencies or []
        run_command = (run_command or "").strip()
        python_version = normalize_python_version(python_version)

        ctx_dir = None
        image_tag = None
        container = None

        try:
            if not project_dir and not file_paths:
                raise ValueError("Either project_dir or file_paths must be provided")

            if not project_dir and file_paths:
                project_dir = self._prepare_project_dir_from_files(file_paths, session_id=session_id)

            ctx_dir = Path(tempfile.mkdtemp(prefix="analyzer_ctx_"))
            src_dir = ctx_dir / "appsrc"
            shutil.copytree(project_dir, src_dir, dirs_exist_ok=True)

            # Если requirements.txt нет — создаём из списка зависимостей сессии
            req_path = self._ensure_requirements(src_dir, dependencies)

            analyzer_script = self._create_analyzer_script()
            (ctx_dir / "_analyzer.py").write_text(analyzer_script, encoding="utf-8")
            self._write_analyzer_support_modules(ctx_dir)

            dockerfile = self._create_dockerfile(
                python_version=python_version,
                has_requirements=bool(req_path),
            )
            (ctx_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")

            image_tag = f"analyzer_job:{(session_id or uuid.uuid4().hex)[:12]}_{uuid.uuid4().hex[:8]}"
            logger.info(f"🐳 Building image {image_tag} (python {python_version})")
            self.client.images.build(path=str(ctx_dir), tag=image_tag, rm=True, pull=True)

            env = {
                "RUN_COMMAND": run_command,
                "RUN_TIMEOUT_SEC": str(self.timeout),
                "PROJECT_DIR": "/app/project",
                # На Docker Desktop/WSL2 tmpfs+read_only ведёт себя нестабильно — пишем в writable layer.
                "RESULT_PATH": f"/app/{RESULT_FILENAME}",
            }

            logger.info("🐳 Running sandbox container (no network, limited resources)")
            container = self.client.containers.run(
                image=image_tag,
                command=["python", "/app/_analyzer.py"],
                detach=True,
                network_mode="none",
                mem_limit="512m",
                cpu_quota=50000,  # ~50% одного CPU
                pids_limit=64,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                environment=env,
                user="1000:1000",
                working_dir="/app/project",
            )

            wait_res = container.wait(timeout=self.timeout + 10)
            exit_code = int(wait_res.get("StatusCode", 1))
            logs = (container.logs(stdout=True, stderr=True) or b"").decode("utf-8", errors="ignore")

            analysis_data = self._extract_result_json(container, f"/app/{RESULT_FILENAME}")

            status_str = "success" if exit_code == 0 else "failed"
            return {
                "status": status_str,
                "exit_code": exit_code,
                "logs": logs[-20000:],
                "analysis": analysis_data,
                "resources": (analysis_data or {}).get("resources", {}),
            }

        except docker.errors.DockerException as e:
            logger.error(f"❌ Docker error: {e}", exc_info=True)
            return {"status": "failed", "error": f"Docker: {str(e)}"}
        except Exception as e:
            logger.error(f"❌ Unexpected error: {e}", exc_info=True)
            return {"status": "failed", "error": f"Error: {str(e)}"}
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            if image_tag is not None:
                try:
                    self.client.images.remove(image=image_tag, force=True)
                except Exception:
                    pass
            if ctx_dir is not None and ctx_dir.exists():
                try:
                    shutil.rmtree(ctx_dir, ignore_errors=True)
                except Exception:
                    pass

    def _prepare_project_dir_from_files(
        self,
        file_paths: List[str],
        session_id: str,
        *,
        relative_names: Optional[List[str]] = None,
    ) -> str:
        base_dir = Path(os.environ.get("ANALYZER_WORKDIR", "/work")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        out_dir = base_dir / f"files_{(session_id or uuid.uuid4().hex)[:12]}_{uuid.uuid4().hex[:8]}"
        out_dir.mkdir(parents=True, exist_ok=True)

        for idx, fp in enumerate(file_paths):
            src = Path(fp)
            if not src.exists():
                continue
            rel = (
                relative_names[idx]
                if relative_names and idx < len(relative_names)
                else src.name
            )
            rel = (rel or src.name).replace("\\", "/").lstrip("/")
            safe_parts = [p for p in rel.split("/") if p and p not in (".", "..")]
            if not safe_parts:
                safe_parts = [src.name]
            dest = out_dir.joinpath(*safe_parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dest))
        return str(out_dir.resolve())

    def _ensure_requirements(self, project_dir: Path, dependencies: List[str]) -> Optional[Path]:
        req = project_dir / "requirements.txt"
        if req.exists():
            return req

        deps = [d.strip() for d in (dependencies or []) if d and d.strip() and not str(d).strip().startswith("#")]
        deps += [
            "radon==6.0.1",
            "flake8==6.1.0",
            "bandit==1.7.5",
            "psutil==5.9.6",
            "pytest==8.2.2",
            "coverage==7.6.1",
        ]

        if not deps:
            return None

        req.write_text("\n".join(sorted(set(deps))), encoding="utf-8")
        return req

    def _extract_result_json(self, container, result_path: str) -> Dict:
        try:
            bits, _ = container.get_archive(result_path)
            buf = io.BytesIO()
            for chunk in bits:
                buf.write(chunk)
            buf.seek(0)
            with tarfile.open(fileobj=buf, mode="r") as tar:
                for member in tar.getmembers():
                    if member.name.endswith(RESULT_FILENAME):
                        f = tar.extractfile(member)
                        if f:
                            return json.load(f)
        except Exception as e:
            logger.warning(f"⚠️ Could not extract {RESULT_FILENAME}: {e}")
        return {}

    def _create_dockerfile(self, *, python_version: str, has_requirements: bool) -> str:
        install_req = ""
        if has_requirements:
            install_req = "RUN pip install --no-cache-dir -r /app/project/requirements.txt\n"

        return (
            f"FROM python:{python_version}-slim\n"
            "WORKDIR /app\n"
            "RUN useradd -m -u 1000 appuser\n"
            "COPY appsrc/ /app/project/\n"
            "COPY _analyzer.py /app/_analyzer.py\n"
            "COPY _static_issue_scan.py /app/_static_issue_scan.py\n"
            "COPY _complexity_radon.py /app/_complexity_radon.py\n"
            "COPY _recommendation_builder.py /app/_recommendation_builder.py\n"
            "RUN chown -R 1000:1000 /app\n"
            "RUN python -m pip install --upgrade pip\n"
            f"{install_req}"
            "RUN pip install --no-cache-dir radon==6.0.1 flake8==6.1.0 bandit==1.7.5 psutil==5.9.6 pytest==8.2.2 coverage==7.6.1\n"
            "USER 1000:1000\n"
        )

    def _create_pytest_dockerfile(self, *, python_version: str, has_requirements: bool) -> str:
        install_req = ""
        if has_requirements:
            install_req = "RUN pip install --no-cache-dir -r /app/project/requirements.txt\n"

        return (
            f"FROM python:{python_version}-slim\n"
            "WORKDIR /app\n"
            "RUN useradd -m -u 1000 appuser\n"
            "COPY project/ /app/project/\n"
            "RUN chown -R 1000:1000 /app\n"
            "RUN python -m pip install --upgrade pip\n"
            f"{install_req}"
            "RUN pip install --no-cache-dir pytest==8.2.2\n"
            "USER 1000:1000\n"
            "WORKDIR /app/project\n"
        )

    def _write_analyzer_support_modules(self, ctx_dir: Path) -> None:
        base = Path(__file__).resolve().parent
        (ctx_dir / "_static_issue_scan.py").write_text(
            (base / "static_issue_scan.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (ctx_dir / "_complexity_radon.py").write_text(
            (base / "complexity_radon.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        rec_src = (base / "recommendation_builder.py").read_text(encoding="utf-8")
        rec_src = rec_src.replace("from .complexity_radon import", "from _complexity_radon import")
        (ctx_dir / "_recommendation_builder.py").write_text(rec_src, encoding="utf-8")

    def _create_analyzer_script(self) -> str:
        return r'''#!/usr/bin/env python3
import ast
import json
import os
import subprocess
import sys
import time
import traceback
import tracemalloc
import importlib.util
from pathlib import Path

try:
    import psutil
except Exception:
    psutil = None

PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", os.getcwd()))
RESULT_PATH = Path(os.environ.get("RESULT_PATH", "/tmp/_result.json"))
RUN_COMMAND = (os.environ.get("RUN_COMMAND") or "").strip()
RUN_TIMEOUT_SEC = int(os.environ.get("RUN_TIMEOUT_SEC") or "60")

_ISSUE_SCAN = None
_REC_BUILDER = None


def _issue_scan_mod():
    global _ISSUE_SCAN
    if _ISSUE_SCAN is not None:
        return _ISSUE_SCAN
    p = Path(__file__).resolve().parent / "_static_issue_scan.py"
    spec = importlib.util.spec_from_file_location("_static_issue_scan", str(p))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load issue scan module: {p}")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    _ISSUE_SCAN = m
    return m


def _rec_builder_mod():
    global _REC_BUILDER
    if _REC_BUILDER is not None:
        return _REC_BUILDER
    p = Path(__file__).resolve().parent / "_recommendation_builder.py"
    spec = importlib.util.spec_from_file_location("_recommendation_builder", str(p))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load recommendation builder: {p}")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    _REC_BUILDER = m
    return m


def _merge_radon_complexity(metrics, static_tools):
    try:
        from _complexity_radon import summarize_radon_static_tools
        radon = (static_tools or {}).get("radon") if isinstance(static_tools, dict) else None
        if isinstance(radon, dict) and radon:
            summary = summarize_radon_static_tools(radon)
            if summary:
                metrics["cyclomatic_complexity"] = summary
    except Exception:
        pass


IGNORED_DIRS = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".idea", ".vscode",
    "dist", "build", "site-packages",
}

def log(m):
    print(f"[ANALYZER] {m}", file=sys.stderr, flush=True)

def should_skip_path(fp: Path) -> bool:
    try:
        rel = fp.relative_to(PROJECT_DIR)
    except Exception:
        rel = fp
    parts = {p.lower() for p in rel.parts}
    if any(p in IGNORED_DIRS for p in parts):
        return True
    # Не анализируем уже сгенерированные тесты и миграции как часть продуктового кода
    name = fp.name.lower()
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    if "tests" in parts or "migrations" in parts:
        return True
    return False

class CodeAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.functions, self.classes, self.imports = [], [], set()
        self.total_lines, self.docstring_count = 0, 0

    def analyze_file(self, fp: Path):
        try:
            c = fp.read_text(encoding="utf-8-sig", errors="ignore")
            self.total_lines += len(c.splitlines())
            self.visit(ast.parse(c))
            return True
        except Exception as e:
            log(f"✗ {fp}: {e}")
            return False

    def visit_FunctionDef(self, n): self._add(n, False); self.generic_visit(n)
    def visit_AsyncFunctionDef(self, n): self._add(n, True); self.generic_visit(n)
    def visit_ClassDef(self, n):
        self.classes.append({"name": n.name, "line": n.lineno, "has_docstring": bool(ast.get_docstring(n))})
        if ast.get_docstring(n): self.docstring_count += 1
        self.generic_visit(n)
    def visit_Import(self, n):
        for a in n.names: self.imports.add(a.name.split(".")[0])
        self.generic_visit(n)
    def visit_ImportFrom(self, n):
        if n.module: self.imports.add(n.module.split(".")[0])
        self.generic_visit(n)
    def _add(self, n, async_flag):
        args = [a.arg for a in n.args.args if a.arg != "self"]
        self.functions.append({"name": n.name, "line": n.lineno, "args": args, "is_async": async_flag, "has_docstring": bool(ast.get_docstring(n))})
        if ast.get_docstring(n): self.docstring_count += 1
    def summary(self):
        return {
            "functions_count": len(self.functions),
            "classes_count": len(self.classes),
            "imports_count": len(self.imports),
            "async_functions": len([f for f in self.functions if f["is_async"]]),
            "total_lines": self.total_lines,
            "docstring_ratio": round(self.docstring_count / max(len(self.functions)+len(self.classes), 1), 2),
        }

def collect_issues(fp: Path, text: str, tree: ast.AST):
    """
    Concrete pointers: file:line + rule_id + short message + excerpt.
    Hints only (no auto-fix).
    """
    rel = str(fp.relative_to(PROJECT_DIR)) if fp.is_absolute() else str(fp)
    lines = (text or "").splitlines()
    out = []
    def ex(n):
        if not n or n < 1 or n > len(lines): return ""
        return lines[n-1].strip()[:240]
    # text scan (TODO/FIXME)
    for i, ln in enumerate(lines, start=1):
        s = ln.strip()
        if "TODO" in s or "FIXME" in s:
            out.append({"path": rel, "line": i, "rule_id": "todo_fixme", "message": "В коде оставлен маркер TODO/FIXME", "excerpt": s[:240]})
    sis = _issue_scan_mod()
    if isinstance(tree, ast.Module):
        out.extend(sis.collect_print_related_issues(tree, rel, ex))
        out.extend(sis.collect_performance_hint_issues(tree, rel, ex))
        out.extend(sis.collect_resource_issues(tree, rel, ex))
        out.extend(sis.collect_duplicate_self_call_hints(tree, rel, ex))
    # AST scan
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in n.args.args if a.arg != "self"]
            if len(args) >= 7:
                out.append({"path": rel, "line": getattr(n, "lineno", 1), "rule_id": "too_many_args", "message": f"Функция `{n.name}` имеет слишком много параметров ({len(args)})", "excerpt": ex(getattr(n, "lineno", 1))})
            for d in (n.args.defaults or []):
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    out.append({"path": rel, "line": getattr(n, "lineno", 1), "rule_id": "mutable_default", "message": f"В `{n.name}` используется изменяемый default-аргумент", "excerpt": ex(getattr(n, "lineno", 1))})
            end_ln = getattr(n, "end_lineno", None)
            if isinstance(end_ln, int) and end_ln - getattr(n, "lineno", end_ln) >= 60:
                out.append({"path": rel, "line": getattr(n, "lineno", 1), "rule_id": "long_function", "message": f"Функция `{n.name}` слишком длинная (~{end_ln - n.lineno} строк)", "excerpt": ex(getattr(n, "lineno", 1))})
        if isinstance(n, ast.ExceptHandler):
            t = n.type
            if t is None:
                out.append({"path": rel, "line": getattr(n, "lineno", 1), "rule_id": "bare_except", "message": "Используется `except:` без указания типа исключения", "excerpt": ex(getattr(n, "lineno", 1))})
            elif isinstance(t, ast.Name) and t.id == "Exception":
                out.append({"path": rel, "line": getattr(n, "lineno", 1), "rule_id": "except_exception", "message": "Используется `except Exception:` (часто слишком широко)", "excerpt": ex(getattr(n, "lineno", 1))})
    return out

def run_static_tools(files):
    results = {"radon": {}, "flake8": {}, "bandit": {}}
    for fp in files[:10]:
        fn = str(fp.relative_to(PROJECT_DIR))
        try:
            r = subprocess.run(["radon", "cc", str(fp), "--json"], capture_output=True, text=True, timeout=25)
            if r.returncode == 0 and r.stdout.strip():
                d = json.loads(r.stdout)
                if fn in d:
                    m = d[fn].get("methods", [])
                    cx = [x.get("complexity", 0) for x in m if isinstance(x, dict)]
                    results["radon"][fn] = {"avg": round(sum(cx)/len(cx), 2) if cx else 0, "max": max(cx) if cx else 0}
        except Exception:
            pass
        try:
            r = subprocess.run(["flake8", str(fp)], capture_output=True, text=True, timeout=25)
            results["flake8"][fn] = {"count": len([l for l in r.stdout.splitlines() if l.strip()])}
        except Exception:
            pass
        try:
            r = subprocess.run(["bandit", "-q", "-f", "json", str(fp)], capture_output=True, text=True, timeout=25)
            if r.stdout.strip():
                bj = json.loads(r.stdout)
                results["bandit"][fn] = {"issues": len(bj.get("results", []) or [])}
        except Exception:
            pass
    return results

def run_command_metrics(cmd: str):
    if not cmd:
        return {"enabled": False}

    start_wall = time.time()
    proc = None
    peak_rss = 0
    try:
        proc = subprocess.Popen(cmd, shell=True, cwd=str(PROJECT_DIR), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        p = psutil.Process(proc.pid) if psutil else None
        while True:
            if proc.poll() is not None:
                break
            if time.time() - start_wall > RUN_TIMEOUT_SEC:
                proc.kill()
                raise TimeoutError(f"Run command timed out after {RUN_TIMEOUT_SEC}s")
            if p:
                try:
                    rss = p.memory_info().rss
                    peak_rss = max(peak_rss, rss)
                except Exception:
                    pass
            time.sleep(0.05)

        out, err = proc.communicate(timeout=2)
        return {
            "enabled": True,
            "command": cmd,
            "exit_code": proc.returncode,
            "wall_time_ms": round((time.time() - start_wall) * 1000, 2),
            "stdout_tail": (out or "")[-4000:],
            "stderr_tail": (err or "")[-4000:],
            "peak_rss_kb": round(peak_rss / 1024, 2) if peak_rss else None,
        }
    except Exception as e:
        return {
            "enabled": True,
            "command": cmd,
            "error": f"{type(e).__name__}: {e}",
            "wall_time_ms": round((time.time() - start_wall) * 1000, 2),
        }
    finally:
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

def main():
    start = time.time()
    tracemalloc.start()
    try:
        py_files = [p for p in PROJECT_DIR.rglob("*.py") if p.is_file() and not should_skip_path(p)]

        a = CodeAnalyzer()
        issues = []
        for fp in py_files[:500]:
            try:
                text = fp.read_text(encoding="utf-8-sig", errors="ignore")
                tree = ast.parse(text)
                issues.extend(collect_issues(fp, text, tree))
            except Exception:
                pass
            a.analyze_file(fp)

        metrics = a.summary()
        metrics["files_count"] = len(py_files)

        static = run_static_tools(py_files)

        run_metrics = run_command_metrics(RUN_COMMAND)

        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        res = {
            "analysis_time_ms": round((time.time() - start) * 1000, 2),
            "python_tracemalloc_peak_kb": round(peak / 1024, 2),
        }

        _merge_radon_complexity(metrics, static)
        recs = _rec_builder_mod().build_analysis_recommendations(metrics, issues, source="docker")
        if run_metrics.get("enabled") and run_metrics.get("error"):
            run_msg = "⚠️ Ошибка выполнения run_command (проверьте entrypoint/зависимости)"
            if run_msg not in recs:
                recs.insert(0, run_msg)

        report = (
            f"📁 .py файлов: {metrics.get('files_count', 0)}\n"
            f"📝 Строк: {metrics.get('total_lines', 0)}\n"
            f"🔧 Функций: {metrics.get('functions_count', 0)}\n"
            f"🏗️ Классов: {metrics.get('classes_count', 0)}\n"
            f"📦 Импортов: {metrics.get('imports_count', 0)}\n"
            f"⚡ Async: {metrics.get('async_functions', 0)}\n"
            f"📚 Docstring: {metrics.get('docstring_ratio', 0)*100:.1f}%\n"
            f"⏱️ Анализ: {res['analysis_time_ms']}ms\n"
            f"💾 tracemalloc peak: {res['python_tracemalloc_peak_kb']}KB\n"
        )

        result = {
            "status": "success",
            "metrics": metrics,
            "resources": res,
            "static_tools": static,
            "run": run_metrics,
            "recommendations": recs,
            "issues": issues,
            "report": report,
        }
        RESULT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"status": "ok", "summary": metrics}))
        return 0
    except Exception as e:
        tb = traceback.format_exc()
        err = {"status": "failed", "error": str(e), "traceback": tb}
        try:
            RESULT_PATH.write_text(json.dumps(err, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        log(f"💥 CRASH: {type(e).__name__}: {e}")
        log(tb)
        return 1

if __name__ == "__main__":
    sys.exit(main())
'''