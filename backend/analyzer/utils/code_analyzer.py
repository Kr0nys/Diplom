import ast
import os
import logging
from typing import List, Dict
from pathlib import Path

from .static_issue_scan import (
    collect_duplicate_self_call_hints,
    collect_performance_hint_issues,
    collect_print_related_issues,
    collect_resource_issues,
)
from .complexity_radon import collect_radon_sample, summarize_radon_static_tools
from .recommendation_builder import build_analysis_recommendations

logger = logging.getLogger(__name__)


class CodeAnalyzer:
    """
    Локальный анализатор кода (fallback режим).

    Используется когда Docker-анализ недоступен.
    Собирает базовые метрики через AST-парсинг.
    """

    # Маппинг популярных импортов к названиям пакетов для pip install
    IMPORT_TO_PACKAGE = {
        'requests': 'requests',
        'numpy': 'numpy',
        'pandas': 'pandas',
        'flask': 'flask',
        'django': 'django',
        'pytest': 'pytest',
        'celery': 'celery',
        'redis': 'redis',
        'sqlalchemy': 'sqlalchemy',
        'pil': 'pillow',
        'cv2': 'opencv-python',
        'sklearn': 'scikit-learn',
        'tensorflow': 'tensorflow',
        'torch': 'torch',
        'bs4': 'beautifulsoup4',
        'yaml': 'pyyaml',
        'jwt': 'pyjwt',
        'PIL': 'pillow',
    }

    def detect_dependencies(self, file_paths: List[str]) -> List[str]:
        """
        Обнаруживает зависимости по import statements в коде.

        Args:
            file_paths: Список путей к Python-файлам

        Returns:
            Список названий пакетов для requirements.txt
        """
        dependencies = set()

        for file_path in file_paths:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    tree = ast.parse(f.read())

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            pkg = self.IMPORT_TO_PACKAGE.get(alias.name.split('.')[0])
                            if pkg:
                                dependencies.add(pkg)

                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            pkg = self.IMPORT_TO_PACKAGE.get(node.module.split('.')[0])
                            if pkg:
                                dependencies.add(pkg)

            except FileNotFoundError:
                logger.error(f"File not found: {file_path}")
            except SyntaxError as e:
                logger.error(f"Syntax error in {os.path.basename(file_path)}: {e}")
            except Exception as e:
                logger.error(f"Error detecting dependencies in {file_path}: {type(e).__name__}: {e}")
                continue

        return list(dependencies)

    def analyze_code(self, file_paths: List[str]) -> Dict:
        """
        Полный анализ кода: сбор метрик и формирование отчёта.

        Args:
            file_paths: Список путей к Python-файлам

        Returns:
            Dict с метриками и текстовым отчётом:
            {
                'metrics': {...},
                'report': '...'
            }
        """
        all_functions: List[Dict] = []
        all_classes: List[Dict] = []
        all_imports: set = set()
        total_lines: int = 0
        total_files: int = 0
        docstring_count: int = 0
        issues: List[Dict] = []

        for file_path in file_paths:
            try:
                logger.debug(f"Analyzing file: {file_path}")

                with open(file_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                    content = f.read()
                    total_lines += len(content.splitlines())

                tree = ast.parse(content)
                total_files += 1

                analyzer = self._ASTAnalyzer()
                analyzer.visit(tree)

                all_functions.extend(analyzer.functions)
                all_classes.extend(analyzer.classes)
                all_imports.update(analyzer.imports)
                docstring_count += analyzer.docstring_count

                try:
                    issues.extend(self._collect_issues_for_file(file_path, content, tree))
                except Exception as e:
                    logger.debug(f"Issue scan failed for {file_path}: {e}")

                logger.debug(f"✓ Analyzed {os.path.basename(file_path)}: "
                             f"{len(analyzer.functions)} functions, {len(analyzer.classes)} classes")

            except FileNotFoundError:
                logger.error(f"❌ File not found: {file_path}")
            except SyntaxError as e:
                logger.error(f"❌ Syntax error in {os.path.basename(file_path)}: {e}")
            except UnicodeDecodeError as e:
                logger.error(f"❌ Encoding error in {file_path}: {e}")
            except Exception as e:
                logger.error(f"❌ Error analyzing {file_path}: {type(e).__name__}: {e}", exc_info=True)
                continue

        async_count = len([f for f in all_functions if f.get('async')])

        docstring_ratio = docstring_count / max(len(all_functions) + len(all_classes), 1)

        radon_pf = collect_radon_sample(file_paths, limit=20)
        cc_summary = summarize_radon_static_tools(radon_pf)
        if cc_summary:
            cc_summary["hint"] = (
                cc_summary.get("hint", "")
                + " Локальный fallback: до 20 файлов."
            ).strip()

        report_lines = [
            f"📁 Всего файлов: {total_files}",
            f"📝 Всего строк: {total_lines}",
            f"🔧 Функций: {len(all_functions)}",
            f"🏗️ Классов: {len(all_classes)}",
            f"📦 Импортов: {len(all_imports)}",
            f"⚡ Асинхронных функций: {async_count}",
            f"📚 Docstring coverage: {round(docstring_count / max(len(all_functions) + len(all_classes), 1) * 100, 1)}%",
        ]
        if cc_summary:
            report_lines.append(
                f"🔀 Цикломатическая сложность (radon, выборка): max≈{cc_summary.get('max')}, avg≈{cc_summary.get('avg')}"
            )

        metrics_dict = {
            'files_count': total_files,
            'lines_count': total_lines,
            'functions_count': len(all_functions),
            'classes_count': len(all_classes),
            'imports_count': len(all_imports),
            'async_functions': async_count,
            'docstring_ratio': round(docstring_ratio, 2),
            'functions': all_functions,
            'classes': all_classes,
            'imports': list(all_imports),
        }
        if cc_summary:
            metrics_dict['cyclomatic_complexity'] = cc_summary
        metrics_dict['recommendations'] = build_analysis_recommendations(metrics_dict, issues)

        return {
            'metrics': metrics_dict,
            'issues': issues,
            'report': '\n'.join(report_lines)
        }

    def _collect_issues_for_file(self, file_path: str, content: str, tree: ast.AST) -> List[Dict]:
        """
        Extracts concrete pointers with file+line+excerpt.
        These are *hints* (no auto-fix), meant to guide manual review.
        """
        rel = Path(file_path).name
        lines = (content or "").splitlines()

        def excerpt(line_no: int) -> str:
            if not line_no or line_no < 1 or line_no > len(lines):
                return ""
            return lines[line_no - 1].strip()[:240]

        out: List[Dict] = []

        # Text-based quick scans (TODO/FIXME)
        for i, ln in enumerate(lines, start=1):
            s = ln.strip()
            if "TODO" in s or "FIXME" in s:
                out.append({"path": rel, "line": i, "rule_id": "todo_fixme", "message": "В коде оставлен маркер TODO/FIXME", "excerpt": s[:240]})

        if isinstance(tree, ast.Module):
            out.extend(collect_print_related_issues(tree, rel, excerpt))
            out.extend(collect_performance_hint_issues(tree, rel, excerpt))
            out.extend(collect_resource_issues(tree, rel, excerpt))
            out.extend(collect_duplicate_self_call_hints(tree, rel, excerpt))

        # AST-based scans
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # too many args
                args = [a.arg for a in node.args.args if a.arg != "self"]
                if len(args) >= 7:
                    out.append({"path": rel, "line": getattr(node, "lineno", 1), "rule_id": "too_many_args", "message": f"Функция `{node.name}` имеет слишком много параметров ({len(args)})", "excerpt": excerpt(getattr(node, "lineno", 1))})

                # mutable defaults
                for d in (node.args.defaults or []):
                    if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                        out.append({"path": rel, "line": getattr(d, "lineno", getattr(node, "lineno", 1)), "rule_id": "mutable_default", "message": f"В `{node.name}` используется изменяемый default-аргумент (list/dict/set)", "excerpt": excerpt(getattr(node, "lineno", 1))})

                # length (best-effort)
                end_ln = getattr(node, "end_lineno", None)
                if isinstance(end_ln, int) and end_ln - getattr(node, "lineno", end_ln) >= 60:
                    out.append({"path": rel, "line": getattr(node, "lineno", 1), "rule_id": "long_function", "message": f"Функция `{node.name}` слишком длинная (~{end_ln - node.lineno} строк)", "excerpt": excerpt(getattr(node, "lineno", 1))})

            if isinstance(node, ast.ExceptHandler):
                t = node.type
                if t is None:
                    out.append({"path": rel, "line": getattr(node, "lineno", 1), "rule_id": "bare_except", "message": "Используется `except:` без указания типа исключения (может скрывать ошибки)", "excerpt": excerpt(getattr(node, "lineno", 1))})
                elif isinstance(t, ast.Name) and t.id == "Exception":
                    out.append({"path": rel, "line": getattr(node, "lineno", 1), "rule_id": "except_exception", "message": "Используется `except Exception:` (часто слишком широко; подумайте о более узких исключениях)", "excerpt": excerpt(getattr(node, "lineno", 1))})

        return out

    class _ASTAnalyzer(ast.NodeVisitor):
        """Внутренний AST-анализатор для обхода дерева кода."""

        def __init__(self):
            self.functions: List[Dict] = []
            self.classes: List[Dict] = []
            self.imports: set = set()
            self.docstring_count: int = 0

        def visit_FunctionDef(self, node):
            self._add_function(node, is_async=False)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            self._add_function(node, is_async=True)
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            self.classes.append({
                'name': node.name,
                'line': node.lineno,
                'method_count': len(methods),
                'has_docstring': bool(ast.get_docstring(node))
            })
            if ast.get_docstring(node):
                self.docstring_count += 1
            self.generic_visit(node)

        def visit_Import(self, node):
            for alias in node.names:
                self.imports.add(alias.name)
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            if node.module:
                self.imports.add(node.module)
            self.generic_visit(node)

        def _add_function(self, node, is_async: bool):
            args = [arg.arg for arg in node.args.args if arg.arg != 'self']
            returns = None
            if node.returns:
                if isinstance(node.returns, ast.Name):
                    returns = node.returns.id
                elif isinstance(node.returns, ast.Constant):
                    returns = node.returns.value

            self.functions.append({
                'name': node.name,
                'line': node.lineno,
                'arg_count': len(args),
                'args': args,
                'return_type': returns,
                'async': is_async,
                'has_docstring': bool(ast.get_docstring(node)),
                'decorators': [self._get_decorator_name(d) for d in node.decorator_list]
            })
            if ast.get_docstring(node):
                self.docstring_count += 1

        def _get_decorator_name(self, decorator) -> str:
            if isinstance(decorator, ast.Name):
                return decorator.id
            elif isinstance(decorator, ast.Attribute):
                return decorator.attr
            elif isinstance(decorator, ast.Call):
                return self._get_decorator_name(decorator.func)
            return 'unknown'