# backend/analyzer/utils/docker_runner.py

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
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

RESULT_FILENAME = "_result.json"


class DockerRunner:
    """
    Build→Run пайплайн:
    - Build: собираем image с зависимостями (сеть разрешена на build-этапе)
    - Run: запускаем контейнер без сети, с лимитами, read-only rootfs и tmpfs на /tmp
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

        ctx_dir = None
        image_tag = None
        container = None

        try:
            if not project_dir and not file_paths:
                raise ValueError("Either project_dir or file_paths must be provided")

            # 1) Собираем project_dir, если пришли file_paths
            if not project_dir and file_paths:
                project_dir = self._prepare_project_dir_from_files(file_paths, session_id=session_id)

            # 2) Генерируем build context
            ctx_dir = Path(tempfile.mkdtemp(prefix="analyzer_ctx_"))
            src_dir = ctx_dir / "appsrc"
            shutil.copytree(project_dir, src_dir, dirs_exist_ok=True)

            # requirements: если нет в проекте — создаём из dependencies (и добавляем инструменты)
            req_path = self._ensure_requirements(src_dir, dependencies)

            analyzer_script = self._create_analyzer_script()
            (ctx_dir / "_analyzer.py").write_text(analyzer_script, encoding="utf-8")

            dockerfile = self._create_dockerfile(
                python_version=python_version,
                has_requirements=bool(req_path),
            )
            (ctx_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")

            # 3) BUILD (с сетью; это build этап докера)
            image_tag = f"analyzer_job:{(session_id or uuid.uuid4().hex)[:12]}_{uuid.uuid4().hex[:8]}"
            logger.info(f"🐳 Building image {image_tag} (python {python_version})")
            self.client.images.build(path=str(ctx_dir), tag=image_tag, rm=True, pull=True)

            # 4) RUN (без сети + лимиты)
            env = {
                "RUN_COMMAND": run_command,
                "RUN_TIMEOUT_SEC": str(self.timeout),
                "PROJECT_DIR": "/app/project",
                # На Docker Desktop/WSL2 tmpfs+read_only может вести себя непредсказуемо,
                # поэтому пишем результат в writable layer контейнера.
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

    def _prepare_project_dir_from_files(self, file_paths: List[str], session_id: str) -> str:
        base_dir = Path(os.environ.get("ANALYZER_WORKDIR", "/work")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        out_dir = base_dir / f"files_{(session_id or uuid.uuid4().hex)[:12]}_{uuid.uuid4().hex[:8]}"
        out_dir.mkdir(parents=True, exist_ok=True)

        for fp in file_paths:
            src = Path(fp)
            if not src.exists():
                continue
            shutil.copy2(str(src), str(out_dir / src.name))
        return str(out_dir.resolve())

    def _ensure_requirements(self, project_dir: Path, dependencies: List[str]) -> Optional[Path]:
        req = project_dir / "requirements.txt"
        if req.exists():
            return req

        deps = [d.strip() for d in (dependencies or []) if d and d.strip() and not str(d).strip().startswith("#")]
        # инструменты анализа/запуска
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
            "RUN chown -R 1000:1000 /app\n"
            "RUN python -m pip install --upgrade pip\n"
            f"{install_req}"
            "RUN pip install --no-cache-dir radon==6.0.1 flake8==6.1.0 bandit==1.7.5 psutil==5.9.6 pytest==8.2.2 coverage==7.6.1\n"
            "USER 1000:1000\n"
        )

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
from pathlib import Path

try:
    import psutil
except Exception:
    psutil = None

PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", os.getcwd()))
RESULT_PATH = Path(os.environ.get("RESULT_PATH", "/tmp/_result.json"))
RUN_COMMAND = (os.environ.get("RUN_COMMAND") or "").strip()
RUN_TIMEOUT_SEC = int(os.environ.get("RUN_TIMEOUT_SEC") or "60")
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
        for fp in py_files[:500]:
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

        recs = []
        if metrics.get("docstring_ratio", 1) < 0.5:
            recs.append("⚠️ Низкое покрытие docstring")
        if static.get("radon") and max((d.get("max", 0) for d in static["radon"].values()), default=0) > 10:
            recs.append("⚠️ Высокая цикломатическая сложность (radon)")
        if run_metrics.get("enabled") and run_metrics.get("error"):
            recs.append("⚠️ Ошибка выполнения run_command (проверьте entrypoint/зависимости)")
        if not recs:
            recs.append("✅ Существенных проблем не выявлено базовыми правилами")

        report = (
            "=== ОТЧЁТ ===\n"
            f"📁 .py файлов: {metrics.get('files_count', 0)}\n"
            f"📝 Строк: {metrics.get('total_lines', 0)}\n"
            f"🔧 Функций: {metrics.get('functions_count', 0)}\n"
            f"🏗️ Классов: {metrics.get('classes_count', 0)}\n"
            f"📦 Импортов: {metrics.get('imports_count', 0)}\n"
            f"⚡ Async: {metrics.get('async_functions', 0)}\n"
            f"📚 Docstring: {metrics.get('docstring_ratio', 0)*100:.1f}%\n"
            f"⏱️ Анализ: {res['analysis_time_ms']}ms\n"
            f"💾 tracemalloc peak: {res['python_tracemalloc_peak_kb']}KB\n"
            "\n=== РЕКОМЕНДАЦИИ ===\n" + "\n".join(recs)
        )

        result = {
            "status": "success",
            "metrics": metrics,
            "resources": res,
            "static_tools": static,
            "run": run_metrics,
            "recommendations": recs,
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