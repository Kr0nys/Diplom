import docker
import os
import json
import logging
import shutil
import time
import uuid
import tarfile
import io
from typing import List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)

RESULT_FILENAME = '_result.json'
PROTECTED_PREFIXES = ['analyzer_', 'docker_', 'k8s_', 'python-test-gen-']


class DockerRunner:


    def __init__(self, timeout: int = 60):
        try:
            self.client = docker.from_env()
            self.timeout = timeout
            logger.info("Docker client initialized")
        except docker.errors.DockerException as e:
            logger.error(f"Docker init failed: {e}")
            raise

    def run_analysis_container(
            self,
            file_paths: List[str],
            python_version: str = '3.9',
            dependencies: List[str] = None,
            run_tests: bool = False
    ) -> Dict:

        work_dir = None
        container = None

        try:
            work_dir = f"/tmp/analyzer_{uuid.uuid4().hex[:8]}"
            os.makedirs(work_dir, exist_ok=True)
            logger.info(f"Created work dir: {work_dir}")

            for file_path in file_paths:
                dest = os.path.join(work_dir, os.path.basename(file_path))
                shutil.copy2(file_path, dest)
                logger.debug(f"Copied: {os.path.basename(file_path)}")

            if dependencies:
                req_path = os.path.join(work_dir, 'requirements.txt')
                deps = [d.strip() for d in dependencies if d.strip() and not d.startswith('#')]
                deps += ['radon==6.0.1', 'flake8==6.1.0', 'bandit==1.7.5']
                with open(req_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(set(deps)))

            analyzer_script = self._create_advanced_analyzer()
            analyzer_path = os.path.join(work_dir, '_analyzer.py')
            with open(analyzer_path, 'w', encoding='utf-8') as f:
                f.write(analyzer_script)

            container = self.client.containers.run(
                image=f'python:{python_version}-slim',
                command='sleep 300',
                network_mode='none',
                mem_limit='256m',
                cpu_quota=25000,
                pids_limit=30,
                detach=True,
                tty=False,
                user='0:0',
                cap_drop=['ALL'],
                security_opt=['no-new-privileges:true'],
            )
            logger.info(f"Container started: {container.short_id}")

            container_work_dir = '/tmp/analysis'

            logger.info(f"Copying files into container at {container_work_dir}...")

            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
                for fname in os.listdir(work_dir):
                    fpath = os.path.join(work_dir, fname)
                    tar.add(fpath, arcname=fname)
            tar_buffer.seek(0)

            container.exec_run(f'mkdir -p {container_work_dir}', user='root')

            container.put_archive(container_work_dir, tar_buffer.getvalue())
            logger.info(f"Files copied to {container_work_dir} in container")

            check = container.exec_run(f'ls -la {container_work_dir}/', user='root')
            logger.debug(f"Container contents: {check.output.decode('utf-8', errors='ignore')[:500]}")

            logger.info(f"Running analysis...")
            exec_result = container.exec_run(
                cmd=f'python {container_work_dir}/_analyzer.py',
                user='0:0',
                workdir=container_work_dir,
                stream=False
            )

            logs = exec_result.output.decode('utf-8', errors='ignore') if exec_result.output else ''
            exit_code = exec_result.exit_code if hasattr(exec_result, 'exit_code') else 0

            if logs.strip():
                logger.info(f"📋 Container output:\n{logs[:3000]}")

            analysis_data = {}
            try:
                result_bits, _ = container.get_archive(f'{container_work_dir}/{RESULT_FILENAME}')

                result_buffer = io.BytesIO()
                for chunk in result_bits:
                    result_buffer.write(chunk)
                result_buffer.seek(0)

                with tarfile.open(fileobj=result_buffer, mode='r') as tar:
                    for member in tar.getmembers():
                        if member.name.endswith(RESULT_FILENAME):
                            f = tar.extractfile(member)
                            if f:
                                analysis_data = json.load(f)
                                logger.info(f"Results loaded from container")
                                break
            except Exception as e:
                logger.warning(f"Could not extract results: {e}")
                host_result = os.path.join(work_dir, RESULT_FILENAME)
                if os.path.exists(host_result):
                    with open(host_result, 'r', encoding='utf-8') as f:
                        analysis_data = json.load(f)
                    logger.info(f"Results loaded from host fallback")

            container.remove(force=True)
            container = None

            return {
                'status': 'success' if exit_code == 0 else 'failed',
                'exit_code': exit_code,
                'logs': logs[-10000:],
                'analysis': analysis_data,
                'resources': analysis_data.get('resources', {})
            }

        except docker.errors.DockerException as e:
            logger.error(f"Docker error: {e}")
            return {'status': 'failed', 'error': f'Docker: {str(e)}'}
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return {'status': 'failed', 'error': f'Error: {str(e)}'}
        finally:
            if container:
                try:
                    container.remove(force=True)
                except:
                    pass
            if work_dir and os.path.exists(work_dir):
                try:
                    shutil.rmtree(work_dir)
                except:
                    pass

    def _cleanup_old_containers(self):
        try:
            old = self.client.containers.list(all=True, filters={'status': 'exited'})
            for c in old[:10]:
                if any(c.name.startswith(p) for p in PROTECTED_PREFIXES):
                    continue
                if c.name.startswith('analysis_'):
                    c.remove(force=True)
        except Exception as e:
            logger.debug(f"Cleanup failed: {e}")

    def _create_advanced_analyzer(self) -> str:
        return r'''#!/usr/bin/env python3
import ast, os, sys, json, traceback, time, tracemalloc, resource, subprocess
from pathlib import Path

APP = Path(os.getcwd())
OUT = APP / '_result.json'

def log(m): print(f"[ANALYZER] {m}", file=sys.stderr, flush=True)

class CodeAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.functions, self.classes, self.imports = [], [], set()
        self.total_lines, self.docstring_count = 0, 0

    def analyze_file(self, fp):
        try:
            with open(fp, 'r', encoding='utf-8-sig', errors='ignore') as f:
                c = f.read()
                self.total_lines += len(c.splitlines())
                self.visit(ast.parse(c))
            return True
        except Exception as e:
            log(f"✗ {fp.name}: {e}")
            return False

    def visit_FunctionDef(self, n): self._add(n, False); self.generic_visit(n)
    def visit_AsyncFunctionDef(self, n): self._add(n, True); self.generic_visit(n)
    def visit_ClassDef(self, n):
        self.classes.append({'name': n.name, 'line': n.lineno, 'has_docstring': bool(ast.get_docstring(n))})
        if ast.get_docstring(n): self.docstring_count += 1
        self.generic_visit(n)
    def visit_Import(self, n):
        for a in n.names: self.imports.add(a.name.split('.')[0])
        self.generic_visit(n)
    def visit_ImportFrom(self, n):
        if n.module: self.imports.add(n.module.split('.')[0])
        self.generic_visit(n)
    def _add(self, n, async_flag):
        args = [a.arg for a in n.args.args if a.arg != 'self']
        self.functions.append({'name': n.name, 'line': n.lineno, 'args': args, 'is_async': async_flag, 'has_docstring': bool(ast.get_docstring(n))})
        if ast.get_docstring(n): self.docstring_count += 1
    def summary(self):
        return {
            'functions_count': len(self.functions), 'classes_count': len(self.classes),
            'imports_count': len(self.imports), 'async_functions': len([f for f in self.functions if f['is_async']]),
            'total_lines': self.total_lines, 'docstring_ratio': round(self.docstring_count / max(len(self.functions)+len(self.classes),1), 2)
        }

def run_static_tools(files):
    results = {'radon': {}, 'flake8': {}, 'bandit': {}}
    for fp in files[:3]:
        fn = fp.name
        try:
            r = subprocess.run(['radon', 'cc', str(fp), '--json'], capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and r.stdout.strip():
                d = json.loads(r.stdout)
                if fn in d:
                    m = d[fn].get('methods', [])
                    cx = [x['complexity'] for x in m]
                    results['radon'][fn] = {'avg': round(sum(cx)/len(cx),2) if cx else 0, 'max': max(cx) if cx else 0}
        except: pass
        try:
            r = subprocess.run(['flake8', str(fp), '--count'], capture_output=True, text=True, timeout=20)
            results['flake8'][fn] = {'count': len([l for l in r.stdout.split('\n') if l.strip() and ':' in l])}
        except: pass
    return results

def main():
    start = time.time()
    tracemalloc.start()
    try:
        log(f"CWD={os.getcwd()}, APP={APP}, exists={APP.exists()}")
        try: log(f"Files: {[f.name for f in APP.iterdir()]}")
        except Exception as e: log(f"List error: {e}")

        req = APP / 'requirements.txt'
        if req.exists(): os.system('pip install -q -r requirements.txt 2>&1')

        files = [f for f in APP.glob('*.py') if f.name not in ('_analyzer.py', '_result.json')]
        log(f"Found {len(files)} files: {[f.name for f in files]}")

        if not files:
            r = {'metrics': {'files_count':0,'functions_count':0,'classes_count':0,'imports_count':0,'async_functions':0,'total_lines':0}, 'report':'No files', 'resources':{'analyzed_files':0}}
            with open(OUT,'w') as f: json.dump(r,f)
            return 0

        a = CodeAnalyzer()
        for fp in files:
            try: a.analyze_file(fp)
            except Exception as e: log(f"✗ {fp.name}: {e}")

        m = a.summary()
        static = run_static_tools(files)

        end = time.time()
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        res = {
            'analyzed_files': len(files),
            'analysis_time_ms': round((end-start)*1000,2),
            'memory_peak_kb': round(peak/1024,2)
        }

        recs = []
        if m.get('docstring_ratio',1) < 0.5: recs.append("Низкое покрытие docstring")
        if static.get('radon') and max((d.get('max',0) for d in static['radon'].values()), default=0) > 10: recs.append("Высокая сложность")
        if not recs: recs.append("Код стабилен")

        rpt = f"=== ОТЧЁТ ===\nФайлов:{len(files)}\nСтрок:{m['total_lines']}\nФункций:{m['functions_count']}\nКлассов:{m['classes_count']}\nИмпортов:{m['imports_count']}\n⚡ Async:{m['async_functions']}\nDocstring:{m['docstring_ratio']*100:.1f}%\nВремя:{res['analysis_time_ms']}ms\nПамять:{res['memory_peak_kb']}KB\n\n=== РЕКОМЕНДАЦИИ ===\n" + '\n'.join(recs)

        result = {'metrics': m, 'report': rpt, 'resources': res, 'static_tools': static, 'recommendations': recs, 'status': 'success'}
        with open(OUT, 'w', encoding='utf-8') as f: json.dump(result, f, indent=2, default=str)
        log("Saved")
        print(json.dumps({'status':'ok','summary':m}))
        return 0
    except Exception as e:
        log(f"CRASH: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)
        try:
            with open(OUT,'w') as f: json.dump({'error':str(e),'traceback':traceback.format_exc()},f)
        except: pass
        return 1

if __name__ == '__main__': sys.exit(main())
'''