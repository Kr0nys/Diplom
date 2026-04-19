import ast
import os
import logging
from typing import List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)


class CodeAnalyzer:

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

        all_functions: List[Dict] = []
        all_classes: List[Dict] = []
        all_imports: set = set()
        total_lines: int = 0
        total_files: int = 0
        docstring_count: int = 0

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

                logger.debug(f"✓ Analyzed {os.path.basename(file_path)}: "
                             f"{len(analyzer.functions)} functions, {len(analyzer.classes)} classes")

            except FileNotFoundError:
                logger.error(f"File not found: {file_path}")
            except SyntaxError as e:
                logger.error(f"Syntax error in {os.path.basename(file_path)}: {e}")
            except UnicodeDecodeError as e:
                logger.error(f"Encoding error in {file_path}: {e}")
            except Exception as e:
                logger.error(f"Error analyzing {file_path}: {type(e).__name__}: {e}", exc_info=True)
                continue

        async_count = len([f for f in all_functions if f.get('async')])

        # Формируем текстовый отчёт
        report_lines = [
            "=== ОТЧЁТ АНАЛИЗА КОДА ===",
            f"Всего файлов: {total_files}",
            f"Всего строк: {total_lines}",
            f"Функций: {len(all_functions)}",
            f"Классов: {len(all_classes)}",
            f"Импортов: {len(all_imports)}",
            f"Асинхронных функций: {async_count}",
            f"Docstring coverage: {round(docstring_count / max(len(all_functions) + len(all_classes), 1) * 100, 1)}%",
            "",
            "=== РЕКОМЕНДАЦИИ ==="
        ]

        if len(all_functions) > 50:
            report_lines.append("⚠️ Большое количество функций (>50). Рассмотрите рефакторинг и разделение на модули.")

        if len(all_imports) > 20:
            report_lines.append("⚠️ Много зависимостей. Проверьте необходимость всех импортов.")

        if async_count > 0 and async_count < len(all_functions):
            report_lines.append(
                "ℹ️ Смешанный код (синхронный + асинхронный). Убедитесь в корректности обработки async/await.")

        docstring_ratio = docstring_count / max(len(all_functions) + len(all_classes), 1)
        if docstring_ratio < 0.5:
            report_lines.append("⚠️ Низкое покрытие docstring (<50%). Добавьте документацию к функциям и классам.")

        if not any(line.startswith("⚠️") or line.startswith("ℹ️") for line in report_lines[-5:]):
            report_lines.append("✅ Код выглядит стабильным по базовым метрикам.")

        return {
            'metrics': {
                'files_count': total_files,
                'lines_count': total_lines,
                'functions_count': len(all_functions),
                'classes_count': len(all_classes),
                'imports_count': len(all_imports),
                'async_functions': async_count,
                'docstring_ratio': round(docstring_ratio, 2),
                'functions': all_functions,
                'classes': all_classes,
                'imports': list(all_imports)
            },
            'report': '\n'.join(report_lines)
        }

    class _ASTAnalyzer(ast.NodeVisitor):

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