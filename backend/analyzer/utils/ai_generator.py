import os
import ast
import requests
import json
import logging
import re
import difflib
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .param_kb import expr_for_param, pick_rule_for_param, prefer_exception

logger = logging.getLogger(__name__)

# Настройки Ollama (переменные окружения)
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")


class AITestGenerator:
    """
    Генерация юнит-тестов: детерминированные шаблоны (basic), рецепты AST (advanced/full)
    и опциональный LLM через Ollama (llm_assist).

    Режимы detail_level:
    - basic: AST-шаблоны без рецептов и без LLM
    - advanced: basic + рецепты; при llm_assist — дополнительный блок от LLM
    - full: то же, что advanced, но всегда включает basic даже при успешных рецептах
    """

    def __init__(self, model: str = None, timeout: int = 120):
        self.model = model or OLLAMA_MODEL
        self.timeout = timeout
        self.base_url = OLLAMA_BASE_URL

    def generate_tests(
            self,
            code: str,
            metrics: Dict = None,
            config: Dict = None
    ) -> str:
        """Генерирует тесты на основе кода и конфигурации."""

        config = config or {}
        detail_level = (config.get('detail_level', 'basic') or 'basic').lower()
        use_mocks = config.get('use_mocks', False)
        include_edge_cases = config.get('include_edge_cases', True)
        test_framework = config.get('test_framework', 'pytest')
        llm_assist = bool(config.get("llm_assist", False))
        if "llm_assist" not in config:
            env_def = (os.environ.get("AI_LLM_ASSIST_DEFAULT", "0") or "0").strip().lower()
            llm_assist = env_def in ("1", "true", "yes", "on")

        logger.info(f"🤖 Generating tests: level={detail_level}, ollama model={self.model}, mocks={use_mocks}")

        def _filter_basic_if_ai_covers_same_targets(basic_suite: str, ai_suite: str) -> str:
            """
            Удаляем целиком AST-узлы test_<fn>_basic, если AI уже вызывает <fn>(...),
            чтобы не оставлять тело функции без def (оно затем «прилипает» к class _Txn).
            """
            b = (basic_suite or "").replace("\r\n", "\n").strip()
            a = (ai_suite or "").replace("\r\n", "\n").strip()
            if not b or not a:
                return basic_suite
            called = set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", a))
            if not called:
                return basic_suite
            unparse = getattr(ast, "unparse", None)
            if unparse:
                try:
                    tree = ast.parse(b)
                except SyntaxError:
                    tree = None
                if tree is not None:
                    new_body: List[ast.stmt] = []
                    for node in tree.body:
                        drop = False
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            name = node.name
                            if name.startswith("test_") and name.endswith("_basic") and len(name) > len("test__basic"):
                                mid = name[len("test_") : -len("_basic")]
                                if mid in called:
                                    drop = True
                        if not drop:
                            new_body.append(node)
                    if len(new_body) < len(tree.body):
                        try:
                            mod = ast.Module(body=new_body, type_ignores=getattr(tree, "type_ignores", []))
                            return unparse(mod).strip()
                        except Exception:
                            pass
            # Запасной путь: построчно, если ast.unparse недоступен
            lines = b.split("\n")
            out: List[str] = []
            i = 0
            def_re = re.compile(r"^(?:async\s+)?def\s+test_([a-zA-Z0-9_]+)_basic\s*\(")
            while i < len(lines):
                line = lines[i]
                m = def_re.match(line)
                if not m:
                    out.append(line)
                    i += 1
                    continue
                fn = m.group(1)
                if fn not in called:
                    out.append(line)
                    i += 1
                    continue
                i += 1
                while i < len(lines):
                    nxt = lines[i]
                    if re.match(r"^(?:async\s+)?def\s+test_[a-zA-Z0-9_]+\s*\(", nxt) or re.match(r"^class\s+\w+", nxt):
                        break
                    if nxt and not nxt.startswith((" ", "\t")) and not nxt.lstrip().startswith(("@", "#")):
                        break
                    i += 1
                continue
            return "\n".join(out).strip()

        def _replace_suite_title(suite: str, title: str) -> str:
            lines = (suite or "").splitlines()
            if lines:
                first = lines[0].strip()
                if first.startswith('"""') and (
                    "Auto-generated tests" in first
                    or "Автоматически сгенерированные тесты" in first
                ):
                    lines[0] = f'"""{title}"""'
                    return "\n".join(lines)
            return f'"""{title}"""\n\n{suite}'.strip()

        def _build_ai_failure_marker(reason: str) -> str:
            return f"# AI_GENERATION_FAILED: {reason}"

        def _looks_like_ai_fallback(raw: str, clean: str) -> bool:
            """
            Только явные ответы-заглушки от нашего кода (не эвристика «fallback»+«basic»:
            в нормальных тестах часто встречаются имена вида test_*_basic).
            """
            r = raw or ""
            c = clean or ""
            low = (r + "\n" + c).lower()
            if r.lstrip().startswith("# ⚠️"):
                return True
            if "ai_generation_failed:" in low:
                return True
            if ("basic fallback" in low or "использованы базовые тесты" in low) and (
                "⚠" in r[:120] or "ollama" in low or "таймаут" in low
            ):
                return True
            return False

        if detail_level not in ('basic', 'advanced', 'full'):
            detail_level = 'basic'

        if detail_level == 'basic':
            basic = self._generate_basic_tests(code, metrics, test_framework, include_edge_cases=include_edge_cases)
            return self._sanitize_generated_test_code(basic, code)
        if detail_level == 'full':
            basic = self._sanitize_generated_test_code(
                self._generate_basic_tests(code, metrics, test_framework, include_edge_cases=include_edge_cases),
                code,
            )
            recipe = ""
            try:
                from .recipe_generator import generate_recipe_tests

                recipe = generate_recipe_tests(code, metrics or {}, config, test_framework) or ""
                recipe = self._sanitize_generated_test_code(recipe, code) if recipe.strip() else ""
            except Exception as exc:
                logger.warning("recipe generator failed: %s", exc, exc_info=True)
                recipe = ""

            merged_base = basic
            if recipe.strip() and not self._looks_like_duplicate_suite(basic, recipe):
                merged_base = self._merge_basic_and_recipe(basic, recipe, code)

            if not llm_assist:
                return _replace_suite_title(merged_base, "Автоматически сгенерированные тесты (полный режим, рецепты)")

            full_cfg = dict(config)
            full_cfg["detail_level"] = "full"
            full_cfg["basic_reference"] = basic[:800]
            ai = self._generate_ai_tests(code, metrics, full_cfg, test_framework) or ""
            ai_clean = self._pick_clean_from_model_output(ai, code)
            if not self._is_valid_python(ai_clean):
                ai_clean = ""
            if not ai_clean.strip() or _looks_like_ai_fallback(ai, ai_clean):
                diag = "# ---- ДИАГНОСТИКА AI ----\n# AI не применён (llm_assist включён, но провайдер вернул пустой/невалидный ответ)."
                merged = f"{merged_base}\n\n{diag}".strip()
                return _replace_suite_title(merged, "Автоматически сгенерированные тесты (полный режим, рецепты + AI пропущен)")
            if self._looks_like_duplicate_suite(merged_base, ai_clean):
                diag = "# ---- ДИАГНОСТИКА AI ----\n# Ответ AI был дубликатом и отфильтрован."
                merged = f"{merged_base}\n\n{diag}".strip()
                return _replace_suite_title(merged, "Автоматически сгенерированные тесты (полный режим, рецепты + дубликаты AI отфильтрованы)")
            merged = self._sanitize_generated_test_code(
                f"{_filter_basic_if_ai_covers_same_targets(merged_base, ai_clean)}\n\n"
                f"# ---- ТЕСТЫ, СГЕНЕРИРОВАННЫЕ AI ----\n\n{ai_clean}".strip(),
                code,
            )
            return _replace_suite_title(merged, "Автоматически сгенерированные тесты (полный режим, рецепты + AI)")
        else:
            adv_cfg = dict(config)
            if detail_level == 'advanced':
                basic = self._sanitize_generated_test_code(
                    self._generate_basic_tests(code, metrics, test_framework, include_edge_cases=include_edge_cases),
                    code,
                )
                recipe = ""
                try:
                    from .recipe_generator import generate_recipe_tests

                    recipe = generate_recipe_tests(code, metrics or {}, adv_cfg, test_framework) or ""
                    if recipe.strip():
                        recipe = self._sanitize_generated_test_code(recipe, code)
                except Exception as exc:
                    logger.warning("recipe generator failed: %s", exc, exc_info=True)
                    recipe = ""

                merged_deterministic = self._merge_basic_and_recipe(basic, recipe, code)

                if not llm_assist:
                    return _replace_suite_title(
                        merged_deterministic,
                        "Автоматически сгенерированные тесты (продвинутый режим)",
                    )

                adv_cfg["basic_reference"] = basic[:800]
                ai = self._generate_ai_tests(code, metrics, adv_cfg, test_framework)
                ai_clean = self._pick_clean_from_model_output(ai or "", code)
                if (
                    self._is_valid_python(ai_clean)
                    and len(ai_clean.strip()) > 40
                    and not _looks_like_ai_fallback(ai or "", ai_clean)
                    and not self._looks_like_duplicate_suite(merged_deterministic, ai_clean)
                ):
                    base_for_ai = _filter_basic_if_ai_covers_same_targets(merged_deterministic, ai_clean)
                    merged_ai = self._sanitize_generated_test_code(
                        f"{base_for_ai}\n\n# ---- ТЕСТЫ, СГЕНЕРИРОВАННЫЕ AI ----\n\n{ai_clean}".strip(),
                        code,
                    )
                    return _replace_suite_title(
                        merged_ai,
                        "Автоматически сгенерированные тесты (продвинутый режим + AI)",
                    )
                return _replace_suite_title(
                    merged_deterministic,
                    "Автоматически сгенерированные тесты (продвинутый режим)",
                )

            # Неизвестный detail_level — откатываемся на basic
            basic = self._sanitize_generated_test_code(
                self._generate_basic_tests(code, metrics, test_framework, include_edge_cases=include_edge_cases),
                code,
            )
            return basic

    def _generate_basic_tests(
            self,
            code: str,
            metrics: Dict,
            framework: str,
            include_edge_cases: bool = True,
    ) -> str:
        """Генерирует запускаемые базовые тесты (без AI)."""

        import ast
        from dataclasses import dataclass

        def split_sources(raw: str):
            """
            Разбивает объединённый code_content на блоки файлов:
            # File: path/to/file.py
            <code>
            """
            blocks = []
            pattern = re.compile(r"^\s*# File:\s+(.+?)\s*$", flags=re.MULTILINE)
            matches = list(pattern.finditer(raw))
            if not matches:
                return [("uploaded_code.py", raw)]
            for i, m in enumerate(matches):
                path = m.group(1).strip()
                start = m.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
                chunk = raw[start:end].strip()
                if chunk:
                    blocks.append((path, chunk))
            return blocks

        def to_module_path(path: str) -> Optional[str]:
            p = path.strip().replace("\\", "/")
            pl = p.lower()
            if "site-packages" in pl or "site_packages" in pl or "dist-packages" in pl:
                return None
            if p.endswith(".py"):
                p = p[:-3]
            if p.endswith("/__init__"):
                p = p[: -len("/__init__")]
            raw_parts = [x for x in p.split("/") if x and x not in (".", "..")]
            parts = []
            for seg in raw_parts:
                seg_l = seg.lower()
                if seg_l in ("tests", "test", "testing"):
                    return None
                if seg_l in {
                    "venv", ".venv", "env", "__pycache__", "site-packages", "site_packages",
                    "node_modules", ".git", ".idea", ".vscode",
                }:
                    continue
                if seg_l in ("build", "dist"):
                    continue
                seg = re.sub(r"[^0-9a-zA-Z_]", "_", seg)
                if not seg:
                    continue
                if seg[0].isdigit():
                    seg = f"m_{seg}"
                parts.append(seg)
            return ".".join(parts) if parts else None

        def should_skip_source_path(path: str) -> bool:
            p = (path or "").replace("\\", "/").lower()
            if not p.endswith(".py"):
                return True
            # Исключаем venv, тесты и служебные каталоги
            blocked_parts = (
                "/venv/", "/.venv/", "/env/", "/site-packages/", "/site_packages/",
                "/__pycache__/", "/node_modules/", "/.git/", "/.idea/", "/.vscode/",
                "/build/", "/dist/",
            )
            if any(x in f"/{p}/" for x in blocked_parts):
                return True
            base_name = p.split("/")[-1]
            if base_name.startswith("test_") or base_name.startswith("tests_") or base_name.endswith("_test.py"):
                return True
            if "/tests/" in f"/{p}/":
                return True
            return False

        def ann_to_name(ann) -> str:
            if ann is None:
                return ""
            if isinstance(ann, ast.Name):
                return ann.id
            if isinstance(ann, ast.Attribute):
                return ann.attr
            if isinstance(ann, ast.Subscript):
                base = ann_to_name(ann.value)
                if base:
                    return base
            return ""

        def const_for_arg(name: str, ann_name: str = ""):
            kb_val = expr_for_param(name, ann_name, kind="valid")
            if kb_val is not None:
                return kb_val
            n = name.lower()
            a = (ann_name or "").lower()
            if n == "date_str" or (n in ("start_date", "end_date") and a in ("str", "string")):
                return "'2024-01-15'"
            if "isbn" in n:
                return "'978-0-596-52068-7'"
            if a in ("str", "string"):
                return "'sample'"
            if a in ("int",):
                return "2"
            if a in ("float", "double"):
                return "2.5"
            if a in ("bool",):
                return "True"
            if a in ("dict", "mapping"):
                return "{'k': 'v'}"
            if a in ("list", "tuple", "set", "sequence"):
                if "book" in n:
                    return "[]"
                return "[1, 2, 3]"
            if "email" in n:
                return "'user@example.com'"
            if "phone" in n or "mobile" in n:
                return "'+1 (202) 555-0101'"
            if any(k in n for k in ("name", "title", "text", "msg")):
                return "'sample'"
            if any(k in n for k in ("flag", "enabled", "ok", "valid")):
                return "True"
            if any(k in n for k in ("items", "list", "arr", "values")):
                return "[1, 2, 3]"
            if any(k in n for k in ("data", "obj", "payload", "config")):
                return "{'k': 'v'}"
            if any(k in n for k in ("ratio", "rate", "percent", "price", "amount")):
                return "2.5"
            if any(k in n for k in ("path", "file")):
                return "'/tmp/data.txt'"
            return "2"

        def invalid_const_for_arg(name: str, ann_name: str = ""):
            kb_inv = expr_for_param(name, ann_name, kind="invalid")
            if kb_inv is not None:
                return kb_inv
            n = name.lower()
            a = (ann_name or "").lower()
            if a in ("str", "string"):
                return "''"
            if a in ("int", "float", "double"):
                return "'bad-number'"
            if "email" in n:
                return "'invalid-email'"
            if "phone" in n or "mobile" in n:
                return "'123'"
            if any(k in n for k in ("name", "title", "text", "msg")):
                return "''"
            return "None"

        def contract_invalid_const_for_arg(name: str, ann_name: str = ""):
            """
            Возвращает типосовместимые, но доменно-невалидные значения.
            Нужен для проверок ожидаемых ValueError (а не случайных TypeError).
            """
            n = name.lower()
            a = (ann_name or "").lower()
            if "discount" in n or "tax" in n or "rate" in n or "percent" in n:
                return "-1"
            if "price" in n or "amount" in n or "total" in n:
                return "-10"
            if "phone" in n or "mobile" in n:
                return "'123'"
            if "email" in n:
                return "'invalid-email'"
            if a in ("str", "string"):
                return "''"
            if a in ("int",):
                return "-1"
            if a in ("float", "double"):
                return "-1.0"
            return invalid_const_for_arg(name, ann_name)

        def pytest_raises_expr(exc_names: List[str]) -> str:
            uniq = [x for x in exc_names if x]
            if not uniq:
                return "Exception"
            if len(uniq) == 1:
                return uniq[0]
            return f"({', '.join(uniq)})"

        def choose_rate_literal(spec: "FunctionSpec", kind: str) -> str:
            """
            kind: "tax" | "discount"
            - percent style -> 20.0
            - fraction style -> 0.2
            Для tax по умолчанию safer fraction (0.2), т.к. tax_rate часто задают как долю.
            """
            arg_names = [a.lower() for a in spec.args]
            has_percent_named_arg = any("percent" in a for a in arg_names)
            has_rate_named_arg = any(("rate" in a) or ("tax" in a) for a in arg_names)
            if spec.rate_style == "percent" or has_percent_named_arg:
                return "20.0"
            if spec.rate_style == "fraction":
                return "0.2"
            if kind == "tax" and has_rate_named_arg:
                return "0.2"
            return "20.0" if kind == "discount" else "0.2"

        @dataclass
        class FunctionSpec:
            module: str
            fn_name: str
            args: List[str]
            arg_ann: List[str]
            behavior: str
            is_async: bool
            raises: Set[str]
            returns: str
            is_method: bool = False
            class_name: str = ""
            call_expr: str = ""
            docstring: str = ""
            examples: List[str] = None
            rate_style: str = "unknown"

        def build_call(spec: FunctionSpec, arg_values: List[str]) -> str:
            """
            Строит вызов функции безопасно, без replace по полной строке.
            Это предотвращает поломку имени функции при совпадении с именем аргумента.
            """
            args_joined = ", ".join(arg_values)
            if spec.is_method:
                if spec.call_expr.startswith(spec.class_name + "."):
                    return f"{spec.class_name}.{spec.fn_name}({args_joined})" if arg_values else f"{spec.class_name}.{spec.fn_name}()"
                return f"obj.{spec.fn_name}({args_joined})" if arg_values else f"obj.{spec.fn_name}()"
            return f"{spec.fn_name}({args_joined})" if arg_values else f"{spec.fn_name}()"

        def parse_docstring_hints(doc: str) -> Tuple[Set[str], List[str]]:
            """Исключения и примеры вызовов из docstring."""
            if not doc:
                return set(), []
            raises = set()
            examples = []

            # Секция Raises / Исключения в docstring
            for m in re.finditer(r"(Raises?|Исключения?)\s*:?\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)", doc, flags=re.IGNORECASE):
                exc_part = m.group(2)
                for ex in re.split(r"\s*,\s*", exc_part):
                    if ex:
                        raises.add(ex.strip())

            # Примеры в формате doctest (>>> ...)
            for m in re.finditer(r"^\s*>>>\s*(.+)$", doc, flags=re.MULTILINE):
                line = m.group(1).strip()
                if "(" in line and ")" in line:
                    examples.append(line)

            # Строки после Examples / Примеры
            for m in re.finditer(r"(?:Examples?|Примеры?)\s*:?\s*(.+)", doc, flags=re.IGNORECASE):
                chunk = m.group(1).strip()
                if "(" in chunk and ")" in chunk:
                    examples.append(chunk)

            seen = set()
            uniq_examples = []
            for e in examples:
                if e not in seen:
                    seen.add(e)
                    uniq_examples.append(e)

            return raises, uniq_examples

        @dataclass
        class ClassSpec:
            module: str
            class_name: str
            init_arg_count: int
            methods: List[FunctionSpec]

        def detect_binary_behavior(fn_node):
            if not fn_node.body:
                return None
            ret = fn_node.body[0]
            if isinstance(ret, ast.Return) and isinstance(ret.value, ast.BinOp):
                op = ret.value.op
                if isinstance(op, ast.Add):
                    return "add"
                if isinstance(op, ast.Sub):
                    return "sub"
                if isinstance(op, ast.Mult):
                    return "mul"
                if isinstance(op, ast.Div):
                    return "div"
            return None

        def infer_rate_style(fn_node, rate_arg_names: List[str]) -> str:
            """
            Эвристика для аргументов tax/rate/discount:
            - fraction — ставка 0..1
            - percent — ставка 0..100
            """
            if not rate_arg_names:
                return "unknown"
            saw_100 = False
            saw_rate_div_100 = False
            saw_cmp_1 = False
            for n in ast.walk(fn_node):
                if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
                    if float(n.value) == 100.0:
                        saw_100 = True
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
                    if isinstance(n.right, ast.Constant) and isinstance(n.right.value, (int, float)) and float(n.right.value) == 100.0:
                        if isinstance(n.left, ast.Name) and n.left.id in rate_arg_names:
                            saw_rate_div_100 = True
                if isinstance(n, ast.Compare):
                    left_name = n.left.id if isinstance(n.left, ast.Name) else ""
                    comps = [c.value for c in n.comparators if isinstance(c, ast.Constant) and isinstance(c.value, (int, float))]
                    if left_name in rate_arg_names and any(float(v) == 1.0 for v in comps):
                        saw_cmp_1 = True
            if saw_rate_div_100:
                return "percent"
            if saw_cmp_1 and not saw_100:
                return "fraction"
            if saw_100:
                return "percent"
            return "unknown"

        tests = ['"""Автоматически сгенерированные тесты (базовый режим)"""', ""]

        if framework == "pytest":
            tests.append("import pytest")
            tests.append("")
        else:
            tests.append("import unittest")
            tests.append("")

        try:
            clean_code = code.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").strip()
            file_blocks = split_sources(clean_code)

            fn_specs: List[FunctionSpec] = []
            class_specs: List[ClassSpec] = []

            for path, src in file_blocks:
                path_l = (path or "").lower().replace("\\", "/")
                if should_skip_source_path(path_l):
                    continue
                try:
                    tree = ast.parse(src)
                except Exception:
                    continue
                module = to_module_path(path)
                if not module:
                    continue
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                        # Не тестируем main() как обычную функцию
                        if node.name.lower() == "main":
                            continue
                        raw_args = [arg for arg in node.args.args if arg.arg != "self"]
                        args = [a.arg for a in raw_args]
                        arg_ann = [ann_to_name(a.annotation) for a in raw_args]
                        behavior = detect_binary_behavior(node)
                        rate_arg_names = [a for a in args if any(k in a.lower() for k in ("tax", "rate", "discount", "percent"))]
                        rate_style = infer_rate_style(node, rate_arg_names)
                        raises = set()
                        for n in ast.walk(node):
                            if isinstance(n, ast.Raise):
                                exc = getattr(n, "exc", None)
                                if isinstance(exc, ast.Call):
                                    exc_name = getattr(exc.func, "id", None) or getattr(exc.func, "attr", None)
                                    if exc_name:
                                        raises.add(exc_name)
                                elif isinstance(exc, ast.Name):
                                    raises.add(exc.id)

                        returns = ann_to_name(node.returns)
                        node_doc = ast.get_docstring(node) or ""
                        doc_raises, doc_examples = parse_docstring_hints(node_doc)
                        raises.update(doc_raises)
                        fn_specs.append(
                            FunctionSpec(
                                module=module,
                                fn_name=node.name,
                                args=args,
                                arg_ann=arg_ann,
                                behavior=behavior or "",
                                is_async=isinstance(node, ast.AsyncFunctionDef),
                                raises=raises,
                                returns=returns,
                                is_method=False,
                                class_name="",
                                call_expr=f"{node.name}({', '.join(args)})" if args else f"{node.name}()",
                                docstring=node_doc,
                                examples=doc_examples,
                                rate_style=rate_style,
                            )
                        )
                    elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                        methods: List[FunctionSpec] = []
                        init_arg_count = 0

                        for b in node.body:
                            if isinstance(b, ast.FunctionDef) and b.name == "__init__":
                                init_arg_count = max(len([a for a in b.args.args if a.arg != "self"]), 0)

                        for b in node.body:
                            if not isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                continue
                            if b.name.startswith("_"):
                                continue
                            decorators = {getattr(d, "id", "") for d in b.decorator_list if isinstance(d, ast.Name)}
                            is_static = "staticmethod" in decorators
                            is_class = "classmethod" in decorators
                            raw_args = [arg for arg in b.args.args]
                            if not is_static and raw_args and raw_args[0].arg in ("self", "cls"):
                                raw_args = raw_args[1:]
                            args = [a.arg for a in raw_args]
                            arg_ann = [ann_to_name(a.annotation) for a in raw_args]
                            behavior = detect_binary_behavior(b) or ""
                            rate_arg_names = [a for a in args if any(k in a.lower() for k in ("tax", "rate", "discount", "percent"))]
                            rate_style = infer_rate_style(b, rate_arg_names)
                            raises = set()
                            for n in ast.walk(b):
                                if isinstance(n, ast.Raise):
                                    exc = getattr(n, "exc", None)
                                    if isinstance(exc, ast.Call):
                                        exc_name = getattr(exc.func, "id", None) or getattr(exc.func, "attr", None)
                                        if exc_name:
                                            raises.add(exc_name)
                                    elif isinstance(exc, ast.Name):
                                        raises.add(exc.id)

                            returns = ann_to_name(b.returns)
                            method_doc = ast.get_docstring(b) or ""
                            doc_raises, doc_examples = parse_docstring_hints(method_doc)
                            raises.update(doc_raises)
                            call_prefix = node.name if (is_static or is_class) else "obj"
                            methods.append(
                                FunctionSpec(
                                    module=module,
                                    fn_name=b.name,
                                    args=args,
                                    arg_ann=arg_ann,
                                    behavior=behavior,
                                    is_async=isinstance(b, ast.AsyncFunctionDef),
                                    raises=raises,
                                    returns=returns,
                                    is_method=True,
                                    class_name=node.name,
                                    call_expr=f"{call_prefix}.{b.name}({', '.join(args)})" if args else f"{call_prefix}.{b.name}()",
                                    docstring=method_doc,
                                    examples=doc_examples,
                                    rate_style=rate_style,
                                )
                            )
                        if methods:
                            class_specs.append(
                                ClassSpec(
                                    module=module,
                                    class_name=node.name,
                                    init_arg_count=init_arg_count,
                                    methods=methods,
                                )
                            )

            if not fn_specs:
                return (
                    '"""Автоматически сгенерированные тесты (базовый режим)"""\n\n'
                    "import pytest\n\n"
                    "# Не удалось найти публичные функции в загруженном коде.\n"
                    "# Сгенерировано Python Test Gen"
                )

            imports_by_module: Dict[str, List[str]] = {}
            for spec in fn_specs:
                imports_by_module.setdefault(spec.module, []).append(spec.fn_name)

            for module, names in sorted(imports_by_module.items()):
                uniq = sorted(set(names))
                tests.append(f"from {module} import {', '.join(uniq)}")
            tests.append("")

            def generate_pytest_for_spec(spec: FunctionSpec):
                params = [const_for_arg(a, an) for a, an in zip(spec.args, spec.arg_ann)]
                invalid_params = [invalid_const_for_arg(a, an) for a, an in zip(spec.args, spec.arg_ann)]
                call = build_call(spec, params)
                maybe_await = f"await {call}" if spec.is_async else call
                fn_l = spec.fn_name.lower()
                raises_zero_div = "ZeroDivisionError" in spec.raises

                if spec.is_async:
                    tests.append("@pytest.mark.asyncio")
                    tests.append(f"async def test_{spec.fn_name}_basic():")
                else:
                    tests.append(f"def test_{spec.fn_name}_basic():")
                tests.append(f'    """Базовый тест для {spec.fn_name}"""')
                if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                    tests.append(f"    obj = {spec.class_name}()")
                tests.append("    # Подготовка")

                if spec.behavior == "add" and len(spec.args) >= 2:
                        tests.append("    cases = [(1, 2, 3), (-1, 5, 4), (0, 0, 0)]")
                        tests.append("    for a, b, expected in cases:")
                        if spec.is_method:
                            prefix = spec.class_name if spec.call_expr.startswith(spec.class_name + ".") else "obj"
                            tests.append(f"        assert {prefix}.{spec.fn_name}(a, b) == expected")
                        else:
                            tests.append(f"        assert {spec.fn_name}(a, b) == expected")
                elif spec.behavior == "sub" and len(spec.args) >= 2:
                        tests.append("    cases = [(5, 2, 3), (1, 3, -2), (0, 0, 0)]")
                        tests.append("    for a, b, expected in cases:")
                        if spec.is_method:
                            prefix = spec.class_name if spec.call_expr.startswith(spec.class_name + ".") else "obj"
                            tests.append(f"        assert {prefix}.{spec.fn_name}(a, b) == expected")
                        else:
                            tests.append(f"        assert {spec.fn_name}(a, b) == expected")
                elif spec.behavior == "mul" and len(spec.args) >= 2:
                        tests.append("    cases = [(2, 3, 6), (-1, 3, -3), (0, 5, 0)]")
                        tests.append("    for a, b, expected in cases:")
                        if spec.is_method:
                            prefix = spec.class_name if spec.call_expr.startswith(spec.class_name + ".") else "obj"
                            tests.append(f"        assert {prefix}.{spec.fn_name}(a, b) == expected")
                        else:
                            tests.append(f"        assert {spec.fn_name}(a, b) == expected")
                elif (spec.behavior == "div" and len(spec.args) >= 2) or ("ZeroDivisionError" in spec.raises and len(spec.args) >= 2):
                        safe_params = list(params)
                        safe_params[1] = "2"
                        safe_call = build_call(spec, safe_params)
                        tests.append(f"    assert {safe_call} == {safe_params[0]} / {safe_params[1]}")
                        if include_edge_cases or raises_zero_div:
                            tests.append("")
                            tests.append(f"def test_{spec.fn_name}_zero_division():")
                            tests.append(f'    """{spec.fn_name}: деление на ноль должно вызывать ошибку"""')
                            if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                                tests.append(f"    obj = {spec.class_name}()")
                            zero_params = list(safe_params)
                            zero_params[1] = "0"
                            tests.append("    with pytest.raises(ZeroDivisionError):")
                            tests.append(f"        {build_call(spec, zero_params)}")
                else:
                    # Шаблоны для типичных валидаторов и нормализаторов
                    valid_email = "'user@example.com'"
                    invalid_email = "'invalid-email'"
                    valid_phone = "'+79001234567'"
                    invalid_phone = "'123'"
                    noisy_phone = "'+7 (900) 123-45-67'"
                    if fn_l == "validate_isbn":
                        good_isbn = "'978-0-596-52068-7'"
                        bad_isbn = "'not-an-isbn'"
                        tests.append("    # Действие и проверка")
                        tests.append(f"    assert {build_call(spec, [good_isbn])} is True")
                        tests.append(f"    assert {build_call(spec, [bad_isbn])} is False")
                    elif fn_l == "parse_date":
                        tests.append("    # Действие")
                        day_lit = "'2024-01-15'"
                        tests.append(f"    result = {build_call(spec, [day_lit])}")
                        tests.append("    # Проверка")
                        tests.append("    assert result.year == 2024")
                        tests.append("    assert result.month == 1")
                        tests.append("    assert result.day == 15")
                    elif fn_l == "format_book_list":
                        tests.append("    from types import SimpleNamespace")
                        tests.append("    books = [SimpleNamespace(title='T', author='A')]")
                        tests.append(f"    result = {build_call(spec, ['books'])}")
                        tests.append("    assert 'T' in result")
                        tests.append("    assert 'A' in result")
                    elif "email" in fn_l and (fn_l.startswith(("is_", "validate_")) or spec.returns.lower() == "bool"):
                        tests.append("    # Действие и проверка")
                        tests.append(f"    assert {build_call(spec, [valid_email])} is True")
                    elif fn_l == "validate_date":
                        tests.append("    # Действие")
                        valid_date = "'2024-01-15T09:00:00'"
                        tests.append(f"    result = {build_call(spec, [valid_date])}")
                        tests.append("    # Проверка")
                        tests.append("    assert hasattr(result, 'year')")
                        tests.append("    assert result.year == 2024")
                    elif ("phone" in fn_l or "mobile" in fn_l) and (fn_l.startswith(("is_", "validate_")) or spec.returns.lower() == "bool"):
                        tests.append("    # Действие и проверка")
                        tests.append(f"    assert {build_call(spec, [valid_phone])} is True")
                    elif fn_l == "validate_amount":
                        tests.append("    # Действие")
                        tests.append(f"    result = {build_call(spec, ['100.0'])}")
                        tests.append("    # Проверка")
                        tests.append("    assert isinstance(result, float)")
                        tests.append("    assert result == pytest.approx(100.0)")
                    elif fn_l == "validate_category":
                        tests.append("    # Действие")
                        valid_category = "'Food'"
                        tests.append(f"    result = {build_call(spec, [valid_category])}")
                        tests.append("    # Проверка")
                        tests.append("    assert isinstance(result, str)")
                        tests.append("    assert result == 'food'")
                    elif "normalize_phone" in fn_l:
                        tests.append("    # Действие")
                        tests.append(f"    result = {build_call(spec, [noisy_phone])}")
                        tests.append("    # Проверка")
                        tests.append(f"    assert result == {valid_phone}")
                        tests.append("    assert isinstance(result, str)")
                        tests.append("    assert result.startswith('+')")
                        tests.append("    assert result[1:].isdigit()")
                        tests.append("    assert len(result) >= 10")
                    else:
                        if fn_l.startswith(("is_", "has_", "can_", "validate_")) or spec.returns.lower() == "bool":
                            tests.append("    # Действие")
                            tests.append(f"    result = {maybe_await}")
                            tests.append("    # Проверка")
                            tests.append("    assert isinstance(result, bool)")
                        elif fn_l == "calculate_discounted_price" and len(spec.args) >= 2:
                            discount_rate = choose_rate_literal(spec, "discount")
                            expected_discount = "80.0" if discount_rate == "20.0" else "99.8"
                            tests.append("    # Действие")
                            tests.append(f"    result = {build_call(spec, ['100.0', discount_rate])}")
                            tests.append("    # Проверка")
                            tests.append(f"    assert result == pytest.approx({expected_discount})")
                        elif fn_l == "calculate_tax" and len(spec.args) >= 2:
                            tax_rate = choose_rate_literal(spec, "tax")
                            expected_tax = "20.0" if tax_rate == "0.2" else "20.0"
                            tests.append("    # Действие")
                            tests.append(f"    result = {build_call(spec, ['100.0', tax_rate])}")
                            tests.append("    # Проверка")
                            tests.append("    assert result == pytest.approx(20.0)")
                        elif fn_l == "calculate_final_price" and len(spec.args) >= 3:
                            tests.append("    # Действие")
                            tests.append(f"    result = {build_call(spec, ['100.0', '0.0', '0.0'])}")
                            tests.append("    # Проверка")
                            tests.append("    assert result == pytest.approx(100.0)")
                        elif any(k in fn_l for k in ("calculate", "compute", "price", "discount", "tax", "amount", "total")):
                            tests.append("    # Действие")
                            tests.append(f"    result = {maybe_await}")
                            tests.append("    # Проверка")
                            tests.append("    assert isinstance(result, (int, float))")
                        elif fn_l.startswith("filter_"):
                            tests.append("    # Действие")
                            tests.append(f"    items = [{{'category': 'food'}}, {{'category': 'other'}}]")
                            filter_args = []
                            if len(spec.args) >= 1:
                                filter_args.append("items")
                            if len(spec.args) >= 2:
                                filter_args.append("'food'")
                            while len(filter_args) < len(spec.args):
                                idx = len(filter_args)
                                filter_args.append(params[idx])
                            tests.append(f"    result = {build_call(spec, filter_args)}")
                            tests.append("    # Проверка")
                            tests.append("    assert isinstance(result, list)")
                        elif "summary" in fn_l:
                            tests.append("    # Действие")
                            tests.append(f"    items = [{{'category': 'food', 'amount': 10.0}}, {{'category': 'food', 'amount': 5.0}}]")
                            summary_args = ["items"] if spec.args else []
                            while len(summary_args) < len(spec.args):
                                idx = len(summary_args)
                                summary_args.append(params[idx])
                            tests.append(f"    result = {build_call(spec, summary_args) if spec.args else maybe_await}")
                            tests.append("    # Проверка")
                            tests.append("    assert isinstance(result, dict)")
                        elif spec.returns.lower() in ("str", "string"):
                            tests.append("    # Действие")
                            tests.append(f"    result = {maybe_await}")
                            tests.append("    # Проверка")
                            tests.append("    assert isinstance(result, str)")
                        elif spec.returns.lower() in ("int",):
                            tests.append("    # Действие")
                            tests.append(f"    result = {maybe_await}")
                            tests.append("    # Проверка")
                            tests.append("    assert isinstance(result, int)")
                        else:
                            tests.append("    # Действие")
                            tests.append(f"    result = {maybe_await}")
                            tests.append("    # Проверка")
                            tests.append("    assert result is not None")

                    if include_edge_cases and spec.args:
                        specialized = (
                            fn_l in ("validate_isbn", "parse_date")
                            or "email" in fn_l
                            or "phone" in fn_l
                            or "mobile" in fn_l
                            or spec.behavior in ("add", "sub", "mul", "div")
                        )
                        if not specialized:
                            a0 = spec.args[0]
                            an0 = spec.arg_ann[0] if spec.arg_ann else ""
                            rule = pick_rule_for_param(a0)
                            if rule:
                                edge_v = expr_for_param(a0, an0, kind="edge", allow_none=True)
                                inv_v = expr_for_param(a0, an0, kind="invalid", allow_none=True)
                                if edge_v is not None and str(edge_v) != str(params[0]):
                                    edge_params = list(params)
                                    edge_params[0] = edge_v
                                    tests.append("")
                                    if spec.is_async:
                                        tests.append("@pytest.mark.asyncio")
                                        tests.append(f"async def test_{spec.fn_name}_kb_edge():")
                                    else:
                                        tests.append(f"def test_{spec.fn_name}_kb_edge():")
                                    tests.append(f'    """Граничное значение из KB для аргумента `{a0}`"""')
                                    if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                                        tests.append(f"    obj = {spec.class_name}()")
                                    edge_call = build_call(spec, edge_params)
                                    if spec.is_async:
                                        tests.append(f"    result = await {edge_call}")
                                    else:
                                        tests.append(f"    result = {edge_call}")
                                    if spec.returns.lower() == "bool":
                                        tests.append("    assert isinstance(result, bool)")
                                    else:
                                        tests.append("    assert True")
                                allowed = list(rule.get("invalid_raises_any_of") or [])
                                pick_raises = [r for r in (spec.raises or []) if r in allowed]
                                exc = prefer_exception(pick_raises or allowed)
                                if inv_v is not None and exc and allowed:
                                    inv_params = list(params)
                                    inv_params[0] = inv_v
                                    tests.append("")
                                    if spec.is_async:
                                        tests.append("@pytest.mark.asyncio")
                                        tests.append(f"async def test_{spec.fn_name}_kb_invalid_{exc.lower()}():")
                                    else:
                                        tests.append(f"def test_{spec.fn_name}_kb_invalid_{exc.lower()}():")
                                    tests.append(f'    """Некорректное значение из KB для `{a0}` ожидает {exc}"""')
                                    if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                                        tests.append(f"    obj = {spec.class_name}()")
                                    inv_call = build_call(spec, inv_params)
                                    tests.append(f"    with pytest.raises({exc}):")
                                    if spec.is_async:
                                        tests.append(f"        await {inv_call}")
                                    else:
                                        tests.append(f"        {inv_call}")

                    if include_edge_cases and spec.args and ("email" in fn_l or "phone" in fn_l or "normalize" in fn_l):
                        tests.append("")
                        tests.append(f"def test_{spec.fn_name}_invalid_input():")
                        tests.append(f'    """Сценарий с некорректным входом для {spec.fn_name}"""')
                        bad_call = build_call(spec, invalid_params)
                        if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                            tests.append(f"    obj = {spec.class_name}()")
                        if "email" in fn_l and (fn_l.startswith(("is_", "validate_")) or spec.returns.lower() == "bool"):
                            tests.append(f"    assert {build_call(spec, [invalid_email])} is False")
                        elif ("phone" in fn_l or "mobile" in fn_l) and (fn_l.startswith(("is_", "validate_")) or spec.returns.lower() == "bool"):
                            tests.append(f"    assert {build_call(spec, [invalid_phone])} is False")
                        elif "normalize" in fn_l or "ValueError" in spec.raises:
                            tests.append("    with pytest.raises((ValueError, TypeError)):")
                            tests.append(f"        {build_call(spec, [invalid_phone])}")
                        elif fn_l.startswith(("is_", "validate_")) or spec.returns.lower() == "bool":
                            tests.append(f"    assert {bad_call} is False")
                        else:
                            tests.append("    with pytest.raises((ValueError, TypeError)):")
                            tests.append(f"        {bad_call}")

                    if include_edge_cases and spec.examples:
                        tests.append("")
                        tests.append(f"def test_{spec.fn_name}_doc_examples():")
                        tests.append(f'    """Проверка примеров из docstring для {spec.fn_name}"""')
                        if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                            tests.append(f"    obj = {spec.class_name}()")
                        for ex in spec.examples[:3]:
                            safe_ex = ex.strip()
                            if spec.fn_name in safe_ex:
                                tests.append(f"    _ = {safe_ex}")

                    known_doc_raises = sorted([r for r in spec.raises if r in ("ValueError", "TypeError", "KeyError", "RuntimeError")])
                    if include_edge_cases and known_doc_raises and spec.args:
                        tests.append("")
                        tests.append(f"def test_{spec.fn_name}_doc_raises():")
                        tests.append(f'    """Проверка секции Raises из docstring для {spec.fn_name}"""')
                        if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                            tests.append(f"    obj = {spec.class_name}()")
                        if "discount" in fn_l and len(spec.args) >= 2:
                            high = "1.5" if spec.rate_style == "fraction" else "150.0"
                            tests.append(f"    with pytest.raises({pytest_raises_expr(known_doc_raises)}):")
                            tests.append(f"        {build_call(spec, ['100.0', '-5.0'])}")
                            tests.append(f"    with pytest.raises({pytest_raises_expr(known_doc_raises)}):")
                            tests.append(f"        {build_call(spec, ['100.0', high])}")
                        elif "tax" in fn_l and len(spec.args) >= 2:
                            high = "1.5" if spec.rate_style == "fraction" else "150.0"
                            tests.append(f"    with pytest.raises({pytest_raises_expr(known_doc_raises)}):")
                            tests.append(f"        {build_call(spec, ['100.0', '-1.0'])}")
                            if spec.rate_style in ("fraction", "percent"):
                                tests.append(f"    with pytest.raises({pytest_raises_expr(known_doc_raises)}):")
                                tests.append(f"        {build_call(spec, ['100.0', high])}")
                        else:
                            tests.append(f"    with pytest.raises({pytest_raises_expr(known_doc_raises)}):")
                            tests.append(f"        {build_call(spec, [contract_invalid_const_for_arg(a, an) for a, an in zip(spec.args, spec.arg_ann)])}")
                tests.append("")

            for spec in fn_specs:
                if framework == "pytest":
                    generate_pytest_for_spec(spec)
                else:
                    params = [const_for_arg(a, an) for a, an in zip(spec.args, spec.arg_ann)]
                    call = build_call(spec, params)
                    tests.append(f"class Test{spec.fn_name.capitalize()}(unittest.TestCase):")
                    tests.append(f"    def test_{spec.fn_name}_basic(self):")
                    tests.append(f'        """Базовый тест для {spec.fn_name}"""')
                    if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                        tests.append(f"        obj = {spec.class_name}()")
                    tests.append(f"        result = {call}")
                    if spec.fn_name.lower().startswith(("is_", "has_", "can_", "validate_")) or spec.returns.lower() == "bool":
                        tests.append("        self.assertIsInstance(result, bool)")
                    else:
                        tests.append("        self.assertIsNotNone(result)")
                    tests.append("")

            tests.append("# Сгенерировано Python Test Gen")
            return "\n".join(tests).replace("\ufeff", "").replace("\u200b", "").strip()

        except Exception as e:
            logger.error(f"Basic test generation failed: {e}", exc_info=True)
            return (
                '"""Автоматически сгенерированные тесты (базовый режим)"""\n\n'
                "import pytest\n\n"
                f"# Ошибка генерации тестов: {e}\n"
                "# Сгенерировано Python Test Gen"
            )

    def _generate_ai_tests(
            self,
            code: str,
            metrics: Dict,
            config: Dict,
            framework: str
    ) -> str:
        """Генерирует тесты с помощью Ollama."""
        prompt = self._build_prompt(code, metrics, config, framework)
        return self._generate_ai_tests_ollama(prompt, code, metrics, config, framework)

    def _generate_ai_tests_ollama(self, prompt: str, code: str, metrics: Dict, config: Dict, framework: str) -> str:
        def call_ollama(
            local_prompt: str,
            temperature: float = 0.2,
            num_predict: int = 900,
            timeout_sec: Optional[int] = None,
        ) -> Tuple[Optional[str], Optional[str]]:
            try:
                response = requests.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": local_prompt,
                        "stream": False,
                        "options": {
                            "temperature": temperature,
                            "num_predict": num_predict,
                            "timeout": self.timeout,
                        },
                    },
                    timeout=timeout_sec or self.timeout,
                )
                if response.status_code == 200:
                    result = response.json()
                    return result.get('response', ''), None
                return None, f"Ollama API error {response.status_code}: {(response.text or '')[:300]}"
            except Exception as e:
                return None, f"{type(e).__name__}: {e}"

        try:
            mode = (config.get("detail_level") or "").lower()
            num_predict = 800 if mode == "full" else 900
            generated_tests, err = call_ollama(prompt, temperature=0.2, num_predict=num_predict)
            if (not (generated_tests or "").strip()) and err is None:
                logger.warning("⚠️ Ollama returned empty body; retrying once")
                generated_tests, err = call_ollama(prompt, temperature=0.15, num_predict=num_predict)
            if generated_tests:
                logger.info(f"✅ Ollama response: {len(generated_tests)} chars")
                clean = self._pick_clean_from_model_output(generated_tests, code)
                # В advanced — второй запрос, если первый ответ слишком короткий или не компилируется
                if mode == "advanced" and (len(clean.strip()) < 120 or not self._is_valid_python(clean)):
                    repair_prompt = (
                        "You produced invalid or weak tests. Rewrite from scratch.\n"
                        "Requirements:\n"
                        "- Return only runnable python test code.\n"
                        "- Use ONLY imports from provided project files and pytest/unittest.\n"
                        "- No placeholders (...), no 'is not None' unless unavoidable.\n"
                        "- Include edge cases and at least one negative test.\n"
                        "- Write all comments and test docstrings in Russian.\n\n"
                        f"{prompt}"
                    )
                    repair_timeout = max(120, int(self.timeout or 120))
                    repaired, rep_err = call_ollama(
                        repair_prompt, temperature=0.1, num_predict=1000, timeout_sec=repair_timeout
                    )
                    if repaired:
                        rep_clean = self._pick_clean_from_model_output(repaired, code)
                        if self._is_valid_python(rep_clean) and len(rep_clean.strip()) >= 120:
                            return rep_clean
                    if rep_err:
                        logger.warning(f"⚠️ Ollama repair attempt failed: {rep_err}")
                    if self._is_valid_python(clean) and self._has_test_definitions(clean):
                        return clean
                return clean

            logger.error(f"❌ {err or 'Unknown Ollama error'}")
            fallback = self._generate_basic_tests(code, metrics, framework, include_edge_cases=config.get('include_edge_cases', True))
            return f"# ⚠️ {err or 'Ошибка Ollama API'}. Использованы базовые тесты.\n{fallback}"
        except requests.exceptions.ConnectionError:
            logger.error("❌ Cannot connect to Ollama. Is it running at %s?", self.base_url)
            fallback = self._generate_basic_tests(code, metrics, framework, include_edge_cases=config.get('include_edge_cases', True))
            return f"# ⚠️ Ollama недоступен. Сгенерированы базовые тесты.\n{fallback}"
        except requests.exceptions.Timeout:
            logger.error("❌ Ollama request timed out (%ss)", self.timeout)
            fallback = self._generate_basic_tests(code, metrics, framework, include_edge_cases=config.get('include_edge_cases', True))
            return f"# ⚠️ Таймаут запроса к AI. Сгенерированы базовые тесты.\n{fallback}"
        except Exception as e:
            logger.error(f"❌ AI generation unexpected error: {type(e).__name__}: {e}", exc_info=True)
            fallback = self._generate_basic_tests(code, metrics, framework, include_edge_cases=config.get('include_edge_cases', True))
            return f"# ⚠️ Неожиданная ошибка: {e}\n{fallback}"

    _TEST_DEF_RE = re.compile(r"(?:async\s+)?def\s+test_[a-zA-Z0-9_]+\s*\(", re.MULTILINE)

    def _has_test_definitions(self, code: str) -> bool:
        return bool(code and self._TEST_DEF_RE.search(code))

    def _extract_python_code(self, raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""
        fenced = re.findall(
            r"```(?:python|py)?\s*\r?\n(.*?)```",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if fenced:
            text = "\n\n".join(s.strip() for s in fenced if s.strip())
        else:
            # Ответ обрезан: открыт ```python без закрывающих ```
            m_open = re.search(r"```(?:python|py)?\s*\r?\n", text, flags=re.IGNORECASE)
            if m_open and text[m_open.end() :].count("```") == 0:
                tail = text[m_open.end() :].strip()
                if tail:
                    text = tail
        low_start = text[:32].lower().lstrip()
        if low_start.startswith("python\n") or low_start.startswith("python\r\n"):
            text = re.sub(r"^python\s*\r?\n", "", text, count=1, flags=re.IGNORECASE).strip()
        lines = text.splitlines()
        while lines and lines[0].strip().startswith("#") and "test" not in lines[0].lower():
            lines.pop(0)
        return "\n".join(lines).strip()

    def _recover_compilable_python(self, raw: str, source_code: str) -> str:
        """Достаёт из ответа модели максимально длинный фрагмент, который компилируется и содержит тесты."""
        raw = (raw or "").strip()
        if not raw:
            return ""
        chunks: List[str] = []
        chunks.append(self._extract_python_code(raw))
        for m in re.finditer(r"```(?:python|py)?\s*\r?\n(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE):
            b = (m.group(1) or "").strip()
            if b:
                chunks.append(b)
        lines = raw.splitlines()
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("import ") or s.startswith("from ") or self._TEST_DEF_RE.match(s):
                chunks.append("\n".join(lines[i:]).strip())
                break
        chunks.append(raw)
        best = ""
        for ch in chunks:
            if not ch:
                continue
            san = self._sanitize_generated_test_code(ch, source_code)
            if not self._is_valid_python(san):
                repaired = self._repair_truncated_suite(san)
                if repaired and self._is_valid_python(repaired):
                    san = repaired
            if not self._is_valid_python(san) or not self._has_test_definitions(san):
                continue
            if len(san) > len(best):
                best = san
        return best

    def _pick_clean_from_model_output(self, raw: str, source_code: str) -> str:
        """Объединяет извлечение по fence и восстановление — чтобы не терять валидный код."""
        ex = self._sanitize_generated_test_code(
            (self._extract_python_code(raw) or "").strip(),
            source_code,
        )
        rec = (self._recover_compilable_python(raw, source_code) or "").strip()
        ex_ok = self._is_valid_python(ex) and self._has_test_definitions(ex)
        rec_ok = self._is_valid_python(rec) and self._has_test_definitions(rec)
        if rec_ok and ex_ok:
            return rec if len(rec) >= len(ex) else ex
        if rec_ok:
            return rec
        if ex_ok:
            return ex
        return rec or ex

    def _extract_project_modules(self, code: str) -> Set[str]:
        modules: Set[str] = set()
        pattern = re.compile(r"^\s*# File:\s+(.+?)\s*$", flags=re.MULTILINE)
        for m in pattern.finditer(code or ""):
            path = m.group(1).strip().replace("\\", "/")
            if not path.endswith(".py"):
                continue
            p = path[:-3]
            if p.endswith("/__init__"):
                p = p[:-len("/__init__")]
            parts = []
            for seg in [x for x in p.split("/") if x and x not in (".", "..")]:
                seg_l = seg.lower()
                if seg_l in {
                    "venv", ".venv", "env", "__pycache__", "site-packages", "site_packages",
                    "node_modules", ".git", ".idea", ".vscode", "build", "dist", "tests",
                }:
                    parts = []
                    break
                seg = re.sub(r"[^0-9a-zA-Z_]", "_", seg)
                if not seg:
                    continue
                if seg[0].isdigit():
                    seg = f"m_{seg}"
                parts.append(seg)
            if parts:
                modules.add(".".join(parts))
        return modules

    def _to_module_path_from_file_marker(self, path: str) -> str:
        p = (path or "").strip().replace("\\", "/")
        if p.endswith(".py"):
            p = p[:-3]
        if p.endswith("/__init__"):
            p = p[: -len("/__init__")]
        raw_parts = [x for x in p.split("/") if x and x not in (".", "..")]
        parts: List[str] = []
        for seg in raw_parts:
            seg_l = seg.lower()
            if seg_l in {
                "venv", ".venv", "env", "__pycache__", "site-packages", "site_packages",
                "node_modules", ".git", ".idea", ".vscode", "build", "dist",
            }:
                continue
            seg = re.sub(r"[^0-9a-zA-Z_]", "_", seg)
            if not seg:
                continue
            if seg[0].isdigit():
                seg = f"m_{seg}"
            parts.append(seg)
        return ".".join(parts) if parts else "your_module"

    def _build_symbol_index(self, source_code: str) -> Dict[str, str]:
        idx: Dict[str, str] = {}
        if not source_code:
            return idx
        pattern = re.compile(r"^\s*# File:\s+(.+?)\s*$", flags=re.MULTILINE)
        matches = list(pattern.finditer(source_code))
        if not matches:
            return idx
        for mi, m in enumerate(matches):
            fpath = m.group(1).strip()
            start = m.end()
            end = matches[mi + 1].start() if mi + 1 < len(matches) else len(source_code)
            chunk = (source_code[start:end] or "").strip()
            if not chunk:
                continue
            try:
                tree = ast.parse(chunk)
            except Exception:
                continue
            mod = self._to_module_path_from_file_marker(fpath)
            for node in tree.body:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name and not node.name.startswith("_"):
                    idx.setdefault(node.name, mod)
        return idx

    def _auto_add_missing_imports(self, suite: str, source_code: str) -> str:
        text = (suite or "").replace("\r\n", "\n").strip()
        if not text:
            return text
        try:
            tree = ast.parse(text)
        except Exception:
            return suite

        imported: Set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.asname or a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    imported.add(a.asname or a.name)

        used: Set[str] = set()

        class NameVisitor(ast.NodeVisitor):
            def visit_Name(self, node: ast.Name):
                if isinstance(node.ctx, ast.Load):
                    used.add(node.id)

        NameVisitor().visit(tree)

        needs_datetime = "datetime" in used and "datetime" not in imported
        symbol_index = self._build_symbol_index(source_code)
        missing_project_syms = sorted([s for s in used if s not in imported and s in symbol_index])
        if not missing_project_syms and not needs_datetime:
            return suite

        lines = text.split("\n")
        insert_at = 0
        if lines and lines[0].strip().startswith(('"""', "'''")):
            q = lines[0].strip()[:3]
            insert_at = 1
            while insert_at < len(lines):
                if lines[insert_at].strip().endswith(q):
                    insert_at += 1
                    break
                insert_at += 1
        while insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1
        while insert_at < len(lines) and (lines[insert_at].lstrip().startswith(("import ", "from ")) or not lines[insert_at].strip()):
            if lines[insert_at].strip() and not lines[insert_at].lstrip().startswith(("import ", "from ")):
                break
            insert_at += 1

        new_import_lines: List[str] = []
        if needs_datetime:
            new_import_lines.append("from datetime import datetime")

        by_mod: Dict[str, List[str]] = {}
        for sym in missing_project_syms:
            by_mod.setdefault(symbol_index[sym], []).append(sym)
        for mod, syms in sorted(by_mod.items()):
            uniq = sorted(set(syms), key=str.lower)
            new_import_lines.append(f"from {mod} import {', '.join(uniq)}")

        if not new_import_lines:
            return suite
        lines[insert_at:insert_at] = new_import_lines + [""]
        return "\n".join(lines).strip()

    def _strip_truncated_tail_assignment(self, suite: str) -> str:
        """
        Убирает с конца файла обрыв вида `result = get` (синтаксически валидно, но тест незавершён).
        """
        t = (suite or "").replace("\r\n", "\n").rstrip()
        if not t:
            return suite
        lines = t.split("\n")
        suspicious_rhs = frozenset(
            {"get", "se", "re", "su", "fi", "tr", "ca", "bal", "col", "filt", "sum", "cat"}
        )
        allowed_rhs = frozenset(
            {"True", "False", "None", "self", "pytest", "datetime", "mock", "MagicMock", "ANY", "patch"}
        )
        while lines:
            raw = lines[-1]
            if "(" in raw or "[" in raw or "{" in raw or ")" in raw or "]" in raw or "}" in raw:
                break
            core = raw.split("#", 1)[0].strip()
            m = re.match(r"^(\w+)\s*=\s*([A-Za-z_][\w]*)\s*$", core)
            if not m:
                break
            lhs, rhs = m.group(1), m.group(2)
            if rhs in allowed_rhs or rhs.isdigit():
                break
            if rhs in suspicious_rhs:
                lines.pop()
                continue
            if lhs == "result" and rhs == "get":
                lines.pop()
                continue
            break
        return "\n".join(lines).rstrip()

    def _repair_truncated_suite(self, suite: str) -> str:
        """Удаляет с конца незавершённые def test_* пока код не компилируется (обрезка ответа LLM)."""
        t = (suite or "").replace("\r\n", "\n").strip()
        if not t:
            return t
        for _ in range(80):
            if self._is_valid_python(t):
                t2 = self._strip_truncated_tail_assignment(t)
                if self._is_valid_python(t2):
                    return t2
                return t
            lines = t.split("\n")
            cut = -1
            for i in range(len(lines) - 1, -1, -1):
                s = lines[i].strip()
                if re.match(r"^(?:async\s+)?def\s+test_[a-zA-Z0-9_]+\s*\(", s):
                    cut = i
                    break
            if cut <= 0:
                while lines and not self._is_valid_python("\n".join(lines).rstrip()):
                    lines.pop()
                return "\n".join(lines).rstrip()
            t = "\n".join(lines[:cut]).rstrip()
        return t

    class _TestBodyHasCheckVisitor(ast.NodeVisitor):
        """assert / with pytest.raises / with unittest.mock.patch(...)"""

        def __init__(self):
            self.ok = False

        def visit_Assert(self, node: ast.AST) -> None:
            self.ok = True

        def visit_With(self, node: ast.With) -> None:
            self.ok = True
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

    def _strip_test_functions_without_assertions(self, suite: str) -> str:
        """Удаляет обрубленные тесты без ни одного assert / with pytest.raises."""
        text = (suite or "").replace("\r\n", "\n")
        if not text.strip():
            return suite
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return suite
        lines = text.split("\n")
        drops: List[Tuple[int, int]] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            vis = self._TestBodyHasCheckVisitor()
            for stmt in node.body:
                vis.visit(stmt)
            if vis.ok:
                continue
            end_ln = getattr(node, "end_lineno", None)
            if end_ln is None:
                continue
            drops.append((node.lineno - 1, end_ln))
        for start, end in sorted(drops, reverse=True):
            del lines[start:end]
            if start < len(lines) and lines[start].strip() == "":
                pass
        out = "\n".join(lines).strip()
        return out if out else suite

    def _prune_unused_simple_imports(self, suite: str) -> str:
        """Удаляет неиспользуемый `patch` из unittest.mock (нет @patch / patch().)."""
        text = (suite or "").replace("\r\n", "\n")
        uses_patch = bool(re.search(r"(?:^|\s)@patch\(|[^\w]patch\(", text))
        lines_out: List[str] = []
        for line in text.split("\n"):
            st = line.strip()
            if st == "from unittest.mock import patch" and not uses_patch:
                continue
            if st.startswith("from unittest.mock import ") and "patch" in st and not uses_patch:
                rhs = st.split("import", 1)[1]
                names = [x.strip() for x in rhs.replace("(", "").replace(")", "").split(",") if x.strip()]
                names = [n.split()[0] for n in names]
                names = [n for n in names if n != "patch"]
                if not names:
                    continue
                indent = line[: len(line) - len(line.lstrip())]
                lines_out.append(f"{indent}from unittest.mock import {', '.join(names)}")
                continue
            lines_out.append(line)
        return "\n".join(lines_out)

    def _apply_pytest_approx_heuristic(self, suite: str) -> str:
        """Для денежных float-сравнений: assert result == expected_result -> pytest.approx(...)."""
        if not re.search(r"(^|\n)\s*import\s+pytest\b", suite):
            return suite
        lines = suite.replace("\r\n", "\n").split("\n")
        out: List[str] = []
        for line in lines:
            m = re.match(
                r"^(\s*)assert\s+(\w+)\s+==\s+(expected_\w+)\s*(#.*)?$",
                line,
            )
            if m:
                ind, left, right = m.group(1), m.group(2), m.group(3)
                comment = m.group(4) or ""
                out.append(f"{ind}assert {left} == pytest.approx({right}){comment}")
                continue
            m2 = re.match(r"^(\s*)assert\s+(\w+)\s+==\s+([\d.]+)\s*(#.*)?$", line)
            if m2 and "." in m2.group(3):
                ind, left, num = m2.group(1), m2.group(2), m2.group(3)
                comment = m2.group(4) or ""
                out.append(f"{ind}assert {left} == pytest.approx({float(num)}){comment}")
                continue
            out.append(line)
        return "\n".join(out)

    def _sanitize_txn_stub_class_body(self, suite: str) -> str:
        """
        Удаляет из class _Txn всё кроме методов (Assign/Assert в теле класса выполняются при импорте).
        """
        src = (suite or "").replace("\r\n", "\n").strip()
        if "class _Txn" not in src:
            return suite
        unparse = getattr(ast, "unparse", None)
        if not unparse:
            return suite
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return suite
        new_mod: List[ast.stmt] = []
        changed = False
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "_Txn":
                kept = [st for st in node.body if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if len(kept) != len(node.body):
                    changed = True
                    node = ast.ClassDef(
                        name=node.name,
                        bases=node.bases,
                        keywords=getattr(node, "keywords", []),
                        body=kept,
                        decorator_list=node.decorator_list,
                    )
            new_mod.append(node)
        if not changed:
            return suite
        try:
            return unparse(ast.Module(body=new_mod, type_ignores=getattr(tree, "type_ignores", []))).strip()
        except Exception:
            return suite

    def _remove_unused_txn_class_def(self, suite: str) -> str:
        """Удаляет class _Txn, если ни одного вызова _Txn(...) нет (избегает пустого stub-класса)."""
        src = (suite or "").replace("\r\n", "\n").strip()
        if "class _Txn" not in src or "_Txn(" in src:
            return suite
        unparse = getattr(ast, "unparse", None)
        if not unparse:
            return suite
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return suite
        new_body = [n for n in tree.body if not (isinstance(n, ast.ClassDef) and n.name == "_Txn")]
        if len(new_body) == len(tree.body):
            return suite
        try:
            return unparse(ast.Module(body=new_body, type_ignores=getattr(tree, "type_ignores", []))).strip()
        except Exception:
            return suite

    def _fix_calculate_balance_literal_asserts(self, suite: str) -> str:
        """
        Исправляет типичную ошибку LLM: ожидание суммы как «доход минус расход по модулю»,
        тогда как calculator.calculate_balance делает income - expense по сырым amount
        (отрицательный расход даёт income - (-50) = 50).
        """
        src = (suite or "").replace("\r\n", "\n").strip()
        if "calculate_balance" not in src or "Transaction" not in src:
            return suite
        unparse = getattr(ast, "unparse", None)
        if not unparse:
            return suite
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return suite

        def const_num(n: ast.AST) -> Optional[float]:
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
                return float(n.value)
            if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
                inner = const_num(n.operand)
                return -inner if inner is not None else None
            return None

        def type_is_income(n: ast.AST) -> Optional[bool]:
            if isinstance(n, ast.Attribute):
                if n.attr == "INCOME":
                    return True
                if n.attr == "EXPENSE":
                    return False
            return None

        def txn_from_call(call: ast.Call) -> Optional[Tuple[float, bool]]:
            fn = call.func
            is_txn = False
            if isinstance(fn, ast.Name) and fn.id == "Transaction":
                is_txn = True
            elif isinstance(fn, ast.Attribute) and fn.attr == "Transaction":
                is_txn = True
            if not is_txn:
                return None
            amt: Optional[float] = None
            inc: Optional[bool] = None
            for kw in call.keywords:
                if not kw.arg:
                    continue
                if kw.arg == "amount":
                    amt = const_num(kw.value)
                elif kw.arg in ("trans_type", "type"):
                    ti = type_is_income(kw.value)
                    if ti is not None:
                        inc = ti
                elif kw.arg == "is_income":
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool):
                        inc = bool(kw.value.value)
            if amt is None and len(call.args) >= 2:
                amt = const_num(call.args[1])
            if inc is None and len(call.args) >= 3:
                ti = type_is_income(call.args[2])
                if ti is not None:
                    inc = ti
            if amt is None or inc is None:
                return None
            return (amt, inc)

        def extract_txn_rows(value: ast.AST) -> Optional[List[Tuple[float, bool]]]:
            if isinstance(value, ast.Call):
                one = txn_from_call(value)
                return [one] if one else None
            if isinstance(value, ast.List):
                rows: List[Tuple[float, bool]] = []
                for elt in value.elts:
                    if isinstance(elt, ast.Call):
                        t = txn_from_call(elt)
                        if not t:
                            return None
                        rows.append(t)
                    else:
                        return None
                return rows
            return None

        def extract_txn_list_from_names(lst: ast.List, env: Dict[str, List[Tuple[float, bool]]]) -> Optional[List[Tuple[float, bool]]]:
            out: List[Tuple[float, bool]] = []
            for elt in lst.elts:
                if isinstance(elt, ast.Name) and elt.id in env:
                    parts = env[elt.id]
                    if len(parts) != 1:
                        return None
                    out.append(parts[0])
                elif isinstance(elt, ast.Call):
                    t = txn_from_call(elt)
                    if not t:
                        return None
                    out.append(t)
                else:
                    return None
            return out

        def balance_of(rows: List[Tuple[float, bool]]) -> float:
            inc = sum(a for a, i in rows if i)
            exp = sum(a for a, i in rows if not i)
            return inc - exp

        def collect_arg_rows(arg0: ast.AST, env: Dict[str, List[Tuple[float, bool]]]) -> Optional[List[Tuple[float, bool]]]:
            if isinstance(arg0, ast.Name) and arg0.id in env:
                return env[arg0.id]
            if isinstance(arg0, ast.List):
                flat: List[Tuple[float, bool]] = []
                for elt in arg0.elts:
                    if isinstance(elt, ast.Name) and elt.id in env:
                        flat.extend(env[elt.id])
                    elif isinstance(elt, ast.Call):
                        t = txn_from_call(elt)
                        if not t:
                            return None
                        flat.append(t)
                    else:
                        return None
                return flat
            return None

        def maybe_fix_assert(node: ast.Assert, env: Dict[str, List[Tuple[float, bool]]]) -> None:
            t = node.test
            if not isinstance(t, ast.Compare) or len(t.ops) != 1 or not isinstance(t.ops[0], ast.Eq):
                return
            if not isinstance(t.left, ast.Call) or not isinstance(t.left.func, ast.Name):
                return
            if t.left.func.id != "calculate_balance":
                return
            args = t.left.args
            kw = {k.arg: k.value for k in t.left.keywords if k.arg}
            if len(args) >= 1:
                arg0 = args[0]
            else:
                arg0 = kw.get("transactions") or kw.get("txns") or kw.get("data")
            if arg0 is None:
                return
            rows = collect_arg_rows(arg0, env)
            if not rows:
                return
            expected = balance_of(rows)
            if not t.comparators:
                return
            cmp0 = t.comparators[0]
            cur = const_num(cmp0)
            if cur is not None:
                if abs(cur - expected) > 1e-6:
                    t.comparators[0] = ast.Constant(value=float(expected))
                return
            if (
                isinstance(cmp0, ast.Call)
                and isinstance(cmp0.func, ast.Attribute)
                and cmp0.func.attr == "approx"
                and isinstance(cmp0.func.value, ast.Name)
                and cmp0.func.value.id == "pytest"
                and cmp0.args
            ):
                inner = const_num(cmp0.args[0])
                if inner is not None and abs(inner - expected) > 1e-6:
                    cmp0.args[0] = ast.Constant(value=float(expected))

        def process_body(stmts: List[ast.stmt], env: Dict[str, List[Tuple[float, bool]]]) -> None:
            for st in stmts:
                if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                    rows = extract_txn_rows(st.value)
                    if rows is None and isinstance(st.value, ast.List):
                        rows = extract_txn_list_from_names(st.value, env)
                    if rows is not None:
                        env[st.targets[0].id] = rows
                elif isinstance(st, ast.Assert):
                    maybe_fix_assert(st, env)
                elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    process_body(st.body, {})
                elif isinstance(st, ast.ClassDef):
                    for child in st.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            process_body(child.body, {})

        process_body(tree.body, {})
        try:
            return unparse(tree).strip()
        except Exception:
            return suite

    def _finance_is_transaction_call(self, node: ast.Call) -> bool:
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == "Transaction":
            return True
        return bool(isinstance(fn, ast.Attribute) and fn.attr == "Transaction")

    def _finance_arg_numeric_kind(self, n: ast.AST) -> Optional[str]:
        if isinstance(n, ast.Constant):
            if isinstance(n.value, bool):
                return None
            if isinstance(n.value, int):
                return "int"
            if isinstance(n.value, float):
                return "float"
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub) and isinstance(n.operand, ast.Constant):
            if isinstance(n.operand.value, int):
                return "int"
            if isinstance(n.operand.value, float):
                return "float"
        return None

    def _finance_trans_type_node(self, n: ast.AST) -> Optional[bool]:
        if isinstance(n, ast.Attribute) and n.attr in ("INCOME", "EXPENSE"):
            return n.attr == "INCOME"
        return None

    def _finance_str_const(self, n: ast.AST) -> bool:
        return isinstance(n, ast.Constant) and isinstance(n.value, str)

    def _finance_lower_str_const(self, n: ast.AST) -> ast.AST:
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return ast.Constant(value=n.value.lower())
        return n

    def _finance_transform_transaction_call(self, node: ast.Call, next_id: List[int]) -> ast.Call:
        """Исправляет Transaction(100.0, 150.0, type, 'Cat', dt) -> именованные поля с id и lower(category)."""
        if not self._finance_is_transaction_call(node):
            return node
        args = list(node.args)
        kws = list(node.keywords)
        kw_by = {k.arg: k for k in kws if k.arg}
        for i, kw in enumerate(kws):
            if kw.arg == "category" and self._finance_str_const(kw.value):
                kws[i] = ast.keyword(arg="category", value=self._finance_lower_str_const(kw.value))
        has_id_kw = "id" in kw_by
        has_amt_kw = "amount" in kw_by
        misordered = (
            len(args) >= 5
            and not has_id_kw
            and not has_amt_kw
            and self._finance_arg_numeric_kind(args[0]) == "float"
            and self._finance_arg_numeric_kind(args[1]) == "float"
            and self._finance_trans_type_node(args[2]) is not None
            and self._finance_str_const(args[3])
        )
        if misordered:
            nid = next_id[0]
            next_id[0] += 1
            cat = self._finance_lower_str_const(args[3])
            new_kw: List[ast.keyword] = [
                ast.keyword("id", ast.Constant(value=nid)),
                ast.keyword("amount", args[1]),
                ast.keyword("trans_type", args[2]),
                ast.keyword("category", cat),
                ast.keyword("date", args[4]),
            ]
            for j in range(5, len(args)):
                new_kw.append(ast.keyword("description", args[j]))
            for kw in kws:
                if kw.arg and kw.arg not in {"id", "amount", "trans_type", "category", "date", "description"}:
                    new_kw.append(kw)
            return ast.Call(func=node.func, args=[], keywords=new_kw)
        if len(args) >= 4 and self._finance_arg_numeric_kind(args[0]) == "int" and self._finance_str_const(args[3]):
            args[3] = self._finance_lower_str_const(args[3])
            return ast.Call(func=node.func, args=args, keywords=kws)
        return ast.Call(func=node.func, args=args, keywords=kws)

    def _finance_lower_category_compare(self, node: ast.Compare) -> None:
        if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq) or len(node.comparators) != 1:
            return
        if not isinstance(node.left, ast.Attribute) or node.left.attr != "category":
            return
        cmp0 = node.comparators[0]
        if isinstance(cmp0, ast.Constant) and isinstance(cmp0.value, str):
            cmp0.value = cmp0.value.lower()

    def _finance_is_none_expr(self, n: ast.AST) -> bool:
        if isinstance(n, ast.Constant) and n.value is None:
            return True
        return bool(isinstance(n, ast.Name) and n.id == "None")

    def _finance_const_float(self, n: ast.AST) -> Optional[float]:
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub) and isinstance(n.operand, ast.Constant):
            if isinstance(n.operand.value, (int, float)):
                return -float(n.operand.value)
        return None

    def _finance_txn_row_from_call(self, node: ast.Call) -> Optional[Tuple[float, bool, str]]:
        """(amount, is_income, category_lower) из вызова Transaction."""
        if not self._finance_is_transaction_call(node):
            return None
        args = node.args
        kwx = {k.arg: k.value for k in node.keywords if k.arg}
        amt: Optional[float] = None
        inc: Optional[bool] = None
        cat: Optional[str] = None
        if "amount" in kwx:
            amt = self._finance_const_float(kwx["amount"])
        if "trans_type" in kwx:
            inc = self._finance_trans_type_node(kwx["trans_type"])
        if "category" in kwx and self._finance_str_const(kwx["category"]):
            cat = str(kwx["category"].value).lower()
        if amt is None and len(args) >= 2:
            amt = self._finance_const_float(args[1])
        if inc is None and len(args) >= 3:
            inc = self._finance_trans_type_node(args[2])
        if cat is None and len(args) >= 4 and self._finance_str_const(args[3]):
            cat = str(args[3].value).lower()
        if amt is None or inc is None or cat is None:
            return None
        return (amt, inc, cat)

    def _finance_collect_txn_rows_from_function(
        self, fn: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    ) -> List[Tuple[float, bool, str]]:
        rows: List[Tuple[float, bool, str]] = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                r = self._finance_txn_row_from_call(n)
                if r:
                    rows.append(r)
        return rows

    def _finance_simulate_category_summary(self, rows: List[Tuple[float, bool, str]]) -> Dict[str, Dict[str, float]]:
        summary: Dict[str, Dict[str, float]] = {}
        for amt, is_inc, cat in rows:
            if cat not in summary:
                summary[cat] = {"income": 0.0, "expense": 0.0}
            if is_inc:
                summary[cat]["income"] += amt
            else:
                summary[cat]["expense"] += amt
        return summary

    def _finance_replace_none_first_arg(self, node: ast.Call) -> ast.Call:
        if not isinstance(node.func, ast.Name):
            return node
        if node.func.id not in ("calculate_balance", "filter_by_category", "get_category_summary"):
            return node
        if not node.args or not self._finance_is_none_expr(node.args[0]):
            return node
        new_args = [ast.List(elts=[])] + list(node.args[1:])
        return ast.Call(func=node.func, args=new_args, keywords=node.keywords)

    def _finance_fix_filter_category_literal(self, node: ast.Call, rows: List[Tuple[float, bool, str]]) -> ast.Call:
        if not isinstance(node.func, ast.Name) or node.func.id != "filter_by_category":
            return node
        if len(node.args) < 2:
            return node
        arg2 = node.args[1]
        if not isinstance(arg2, ast.Constant) or not isinstance(arg2.value, str):
            return node
        s = arg2.value.strip().lower()
        if s not in ("income", "expense"):
            return node
        inc_cats = [c for _, i, c in rows if i]
        exp_cats = [c for _, i, c in rows if not i]
        repl = inc_cats[0] if s == "income" and inc_cats else (exp_cats[0] if s == "expense" and exp_cats else None)
        if not repl:
            return node
        new_args = list(node.args)
        new_args[1] = ast.Constant(value=repl)
        return ast.Call(func=node.func, args=new_args, keywords=node.keywords)

    def _finance_fix_transposed_category_equal(self, test: ast.AST, rows: List[Tuple[float, bool, str]]) -> None:
        """assert ... .category == 'income' (тип вместо категории) -> реальная категория из транзакций."""
        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            return
        left, right = test.left, test.comparators[0]
        if not isinstance(left, ast.Attribute) or left.attr != "category":
            return
        if not isinstance(right, ast.Constant) or not isinstance(right.value, str):
            return
        s = right.value.lower()
        if s not in ("income", "expense"):
            return
        inc_cats = [c for _, i, c in rows if i]
        exp_cats = [c for _, i, c in rows if not i]
        repl = inc_cats[0] if s == "income" and inc_cats else (exp_cats[0] if s == "expense" and exp_cats else None)
        if repl:
            right.value = repl

    def _finance_fix_summary_assert_test(self, test: ast.AST, rows: List[Tuple[float, bool, str]]) -> Optional[ast.AST]:
        sim = self._finance_simulate_category_summary(rows)
        if not sim:
            return None
        cats = sorted(sim.keys())
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.In):
            left, op, rights = test.left, test.ops[0], test.comparators
            if isinstance(left, ast.Constant) and isinstance(left.value, str) and left.value.lower() in ("income", "expense"):
                var = rights[0] if rights else None
                if var is None:
                    return None
                parts = [ast.Compare(left=ast.Constant(value=k), ops=[ast.In()], comparators=[var]) for k in cats]
                if not parts:
                    return None
                if len(parts) == 1:
                    return parts[0]
                return ast.BoolOp(op=ast.And(), values=parts)
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
            left, right = test.left, test.comparators[0]
            if isinstance(left, ast.Subscript) and isinstance(left.value, ast.Name):
                sl = left.slice
                key = sl.value if isinstance(sl, ast.Constant) and isinstance(sl.value, str) else None
                if key and key.lower() in ("income", "expense"):
                    want_inc = key.lower() == "income"
                    x = self._finance_const_float(right)
                    if isinstance(right, ast.Call) and isinstance(right.func, ast.Attribute) and right.func.attr == "approx":
                        if right.args:
                            x = self._finance_const_float(right.args[0])
                    if x is None:
                        return None
                    for cat in cats:
                        v = sim[cat]["income"] if want_inc else sim[cat]["expense"]
                        if abs(v - x) < 1e-3:
                            inner = ast.Subscript(
                                value=left.value,
                                slice=ast.Constant(value=cat),
                                ctx=ast.Load(),
                            )
                            new_left = ast.Subscript(
                                value=inner,
                                slice=ast.Constant(value="income" if want_inc else "expense"),
                                ctx=ast.Load(),
                            )
                            return ast.Compare(left=new_left, ops=[ast.Eq()], comparators=[right])
        return None

    def _finance_find_get_category_summary_result_var(
        self, fn: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    ) -> Optional[str]:
        for st in fn.body:
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                if isinstance(st.value, ast.Call) and isinstance(st.value.func, ast.Name):
                    if st.value.func.id == "get_category_summary":
                        return st.targets[0].id
        return None

    def _finance_fix_summary_dict_key_casing(
        self, test: ast.AST, rows: List[Tuple[float, bool, str]], summary_var: Optional[str]
    ) -> None:
        """'Salary' in result / result['Salary'] -> ключи как в get_category_summary (регистр из Transaction)."""
        if not summary_var or not rows:
            return
        sim = self._finance_simulate_category_summary(rows)
        if not sim:
            return

        def canon_key(s: str) -> Optional[str]:
            for k in sim:
                if k.lower() == s.lower():
                    return k
            return None

        for n in ast.walk(test):
            if isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], ast.In):
                left, rights = n.left, n.comparators
                if isinstance(left, ast.Constant) and isinstance(left.value, str) and rights:
                    if isinstance(rights[0], ast.Name) and rights[0].id == summary_var:
                        c = canon_key(left.value)
                        if c is not None and left.value != c:
                            left.value = c
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == summary_var:
                sl = n.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    k = sl.value
                    c = canon_key(k)
                    if c is not None and k != c:
                        sl.value = c

    def _finance_dedupe_identical_asserts_in_body(self, body: List[ast.stmt]) -> List[ast.stmt]:
        if len(body) < 2:
            return body
        out: List[ast.stmt] = [body[0]]
        for st in body[1:]:
            if isinstance(st, ast.Assert) and isinstance(out[-1], ast.Assert):
                try:
                    if ast.dump(st, include_attributes=False) == ast.dump(out[-1], include_attributes=False):
                        continue
                except Exception:
                    pass
            out.append(st)
        return out

    def _finance_dedupe_asserts_in_tree(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.body[:] = self._finance_dedupe_identical_asserts_in_body(list(node.body))

    def _polish_finance_generated_tests(self, suite: str) -> str:
        """
        Типичные ошибки LLM в finance-тестах:
        - позиционный Transaction(100.0, 150.0, ...) вместо (id, amount, ...)
        - категории в нижнем регистре (validate_category / filter_by_category)
        - assert по .category к нижнему регистру
        - tracker._transactions -> get_transactions()
        - None вместо списка транзакций; путаница income/expense с категорией; неверные ключи get_category_summary
        """
        src = (suite or "").replace("\r\n", "\n").strip()
        markers = (
            "Transaction(",
            "calculate_balance(",
            "filter_by_category(",
            "get_category_summary(",
            "._transactions",
        )
        if not any(m in src for m in markers):
            return suite
        unparse = getattr(ast, "unparse", None)
        if not unparse:
            return re.sub(r"\._transactions\b", ".get_transactions()", src)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return re.sub(r"\._transactions\b", ".get_transactions()", src)

        next_id = [1]

        class _TxFix(ast.NodeTransformer):
            def __init__(self, outer: "AITestGenerator", nid: List[int]):
                super().__init__()
                self._outer = outer
                self._nid = nid

            def visit_Call(self, node: ast.Call) -> ast.AST:
                node = self.generic_visit(node)
                return self._outer._finance_transform_transaction_call(node, self._nid)

        tree = _TxFix(self, next_id).visit(tree)

        class _ApiFix(ast.NodeTransformer):
            def __init__(self, outer: "AITestGenerator"):
                super().__init__()
                self._outer = outer
                self._rows: List[Tuple[float, bool, str]] = []
                self._summary_var: Optional[str] = None

            def _visit_scoped(self, node: ast.AST) -> ast.AST:
                old_r = self._rows
                old_s = self._summary_var
                self._rows = self._outer._finance_collect_txn_rows_from_function(node)  # type: ignore[arg-type]
                self._summary_var = self._outer._finance_find_get_category_summary_result_var(node)  # type: ignore[arg-type]
                node = self.generic_visit(node)
                self._rows = old_r
                self._summary_var = old_s
                return node

            def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
                return self._visit_scoped(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
                return self._visit_scoped(node)

            def visit_Call(self, node: ast.Call) -> ast.AST:
                node = self.generic_visit(node)
                node = self._outer._finance_replace_none_first_arg(node)
                node = self._outer._finance_fix_filter_category_literal(node, self._rows)
                return node

            def visit_Assert(self, node: ast.Assert) -> ast.AST:
                node = self.generic_visit(node)
                nt = self._outer._finance_fix_summary_assert_test(node.test, self._rows)
                if nt is not None:
                    node.test = nt
                else:
                    self._outer._finance_fix_transposed_category_equal(node.test, self._rows)
                self._outer._finance_fix_summary_dict_key_casing(node.test, self._rows, self._summary_var)
                return node

        tree = _ApiFix(self).visit(tree)
        self._finance_dedupe_asserts_in_tree(tree)

        for n in ast.walk(tree):
            if isinstance(n, ast.Compare):
                self._finance_lower_category_compare(n)

        try:
            out = unparse(tree).strip()
        except Exception:
            out = src
        return re.sub(r"\._transactions\b", ".get_transactions()", out)

    def _finance_body_has_add_transaction_call(self, body: List[ast.stmt]) -> bool:
        for st in body:
            for n in ast.walk(st):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_transaction":
                    return True
        return False

    def _ensure_validators_validation_error_import(self, suite: str) -> str:
        """Добавляет ValidationError в from validators import ... если имя используется в коде."""
        if not re.search(r"\bValidationError\b", suite):
            return suite
        if re.search(r"from\s+validators\s+import\s+[^\n#]*\bValidationError\b", suite):
            return suite
        lines = suite.replace("\r\n", "\n").split("\n")
        for i, line in enumerate(lines):
            m = re.match(r"^(\s*from validators import\s+)(.+?)\s*(#.*)?$", line)
            if not m:
                continue
            pre, names, comment = m.group(1), m.group(2).strip().rstrip(","), m.group(3) or ""
            if "ValidationError" in names:
                return suite
            tail = f" {comment}".rstrip() if comment else ""
            lines[i] = f"{pre}{names}, ValidationError{tail}"
            return "\n".join(lines)
        for i, line in enumerate(lines):
            if line.lstrip().startswith(("import ", "from ")):
                lines.insert(i, "from validators import ValidationError")
                return "\n".join(lines)
        return suite

    def _fix_pytest_raises_valueerror_for_validation(self, suite: str) -> str:
        """
        LLM часто пишет pytest.raises(ValueError) для add_transaction, тогда как tracker
        пробрасывает validators.ValidationError.
        """
        if "pytest.raises" not in suite or "ValueError" not in suite or "add_transaction" not in suite:
            return suite
        unparse = getattr(ast, "unparse", None)
        if not unparse:
            return suite
        try:
            tree = ast.parse(suite)
        except SyntaxError:
            return suite
        changed = [False]

        class _RaisesFix(ast.NodeTransformer):
            def __init__(self, outer: "AITestGenerator", ch: List[bool]):
                super().__init__()
                self._outer = outer
                self._ch = ch

            def visit_With(self, node: ast.With) -> ast.AST:
                self.generic_visit(node)
                for it in node.items:
                    ce = it.context_expr
                    if not isinstance(ce, ast.Call):
                        continue
                    fn = ce.func
                    if not isinstance(fn, ast.Attribute) or fn.attr != "raises":
                        continue
                    if not isinstance(fn.value, ast.Name) or fn.value.id != "pytest":
                        continue
                    if not ce.args:
                        continue
                    a0 = ce.args[0]
                    if not isinstance(a0, ast.Name) or a0.id != "ValueError":
                        continue
                    if not self._outer._finance_body_has_add_transaction_call(node.body):
                        continue
                    ce.args[0] = ast.Name(id="ValidationError", ctx=ast.Load())
                    self._ch[0] = True
                return node

        tree = _RaisesFix(self, changed).visit(tree)
        if not changed[0]:
            return suite
        try:
            out = unparse(tree).strip()
        except Exception:
            return suite
        return self._ensure_validators_validation_error_import(out)

    def _finance_heuristics_enabled(self, suite: str) -> bool:
        """
        Доменные эвристики (finance, калькуляторы, Transaction) полезны не для всех репозиториев.
        AI_TEST_HEURISTICS_PROFILE:
          - auto (по умолчанию): включать только если в тексте тестов есть типичные сигналы
          - finance / on / 1: всегда включать
          - generic / off / 0: отключить (чистая генерация + только общий sanitize)
        """
        p = (os.environ.get("AI_TEST_HEURISTICS_PROFILE") or "auto").strip().lower()
        if p in ("generic", "none", "0", "false", "off"):
            return False
        if p in ("finance", "all", "on", "1", "true"):
            return True
        s = suite or ""
        return any(
            sig in s
            for sig in (
                "FinanceTracker",
                "Transaction(",
                "get_category_summary(",
                "calculate_balance(",
                "filter_by_category(",
            )
        )

    def _polish_test_suite(self, suite: str) -> str:
        """Постобработка сгенерированного файла: синтаксис, обрубки, импорты, approx."""
        t = (suite or "").strip()
        if not t:
            return t
        t = self._repair_truncated_suite(t)
        if self._finance_heuristics_enabled(t):
            t = self._polish_finance_generated_tests(t)
        t = self._convert_dict_transactions_to_stub(t)
        t = self._sanitize_txn_stub_class_body(t)
        t = self._remove_unused_txn_class_def(t)
        t = self._fix_calculate_balance_literal_asserts(t)
        if re.search(r"\bdatetime\.now\s*\(\s*\)", t) and re.search(r"(^|\n)\s*from\s+datetime\s+import\s+datetime\b", t):
            t = re.sub(r"\bdatetime\.now\s*\(\s*\)", "datetime(2024, 5, 1, 12, 0, 0)", t)
        if "datetime." in t and not re.search(r"(^|\n)\s*from\s+datetime\s+import\s+datetime\b", t) and not re.search(
            r"(^|\n)\s*import\s+datetime\b", t
        ):
            lines = t.split("\n")
            ins = 0
            if lines and lines[0].strip().startswith(('"""', "'''")):
                q = lines[0].strip()[:3]
                ins = 1
                while ins < len(lines):
                    if lines[ins].strip().endswith(q):
                        ins += 1
                        break
                    ins += 1
            while ins < len(lines) and not lines[ins].strip():
                ins += 1
            lines.insert(ins, "from datetime import datetime")
            t = "\n".join(lines).strip()
        if self._is_valid_python(t):
            t2 = self._strip_test_functions_without_assertions(t)
            if self._is_valid_python(t2) and self._has_test_definitions(t2):
                t = t2
        t_merged = self._merge_from_imports_block(t)
        if self._is_valid_python(t_merged):
            t = t_merged
        t = self._prune_unused_simple_imports(t)
        t = self._apply_pytest_approx_heuristic(t)
        t = self._strip_truncated_tail_assignment(t)
        t = self._fix_pytest_raises_valueerror_for_validation(t)
        if self._is_valid_python(t) and self._has_test_definitions(t):
            return t
        t2 = self._repair_truncated_suite(suite.strip())
        return t2 if self._is_valid_python(t2) else suite.strip()

    def _convert_dict_transactions_to_stub(self, suite: str) -> str:
        """
        Если LLM сгенерировал transactions как list[dict], но код ожидает объект с атрибутами/методом
        (t.amount, t.category, t.is_income()), конвертируем dict -> локальный stub _Txn.
        Это делает тесты запускаемыми и логически ближе к контракту.
        """
        src = (suite or "").replace("\r\n", "\n").strip()
        if not src or "{" not in src:
            return suite
        if re.search(r"\bTransaction\b", src):
            return suite
        if not any(fn in src for fn in ("calculate_balance(", "filter_by_category(", "get_category_summary(")):
            return suite
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return suite

        class NeedsTxnVisitor(ast.NodeVisitor):
            def __init__(self):
                self.dict_list_assigns: List[Tuple[int, int, str, List[Dict[str, ast.AST]]]] = []

            def visit_ClassDef(self, node: ast.ClassDef):
                return

            def visit_FunctionDef(self, node: ast.FunctionDef):
                if node.name.startswith("test_"):
                    self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                if node.name.startswith("test_"):
                    self.generic_visit(node)

            def visit_Assign(self, node: ast.Assign):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    return
                target_name = node.targets[0].id
                if target_name not in ("transactions", "items"):
                    return
                if not isinstance(node.value, ast.List):
                    return
                dicts: List[Dict[str, ast.AST]] = []
                for elt in node.value.elts:
                    if not isinstance(elt, ast.Dict):
                        return
                    keys = []
                    kv: Dict[str, ast.AST] = {}
                    for k, v in zip(elt.keys, elt.values):
                        if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                            return
                        kv[str(k.value)] = v
                        keys.append(str(k.value))
                    dicts.append(kv)
                union_keys = set().union(*[set(d.keys()) for d in dicts]) if dicts else set()
                if not ({"category", "amount", "is_income"} & union_keys):
                    return
                start = node.lineno - 1
                end = getattr(node, "end_lineno", node.lineno) - 1
                self.dict_list_assigns.append((start, end, target_name, dicts))

        v = NeedsTxnVisitor()
        v.visit(tree)
        if not v.dict_list_assigns:
            return suite

        unparse = getattr(ast, "unparse", None)
        if not unparse:
            return suite

        lines = src.split("\n")
        insert_at = 0
        if lines and lines[0].strip().startswith(('"""', "'''")):
            q = lines[0].strip()[:3]
            insert_at = 1
            while insert_at < len(lines):
                if lines[insert_at].strip().endswith(q):
                    insert_at += 1
                    break
                insert_at += 1
        while insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1
        while insert_at < len(lines) and lines[insert_at].lstrip().startswith(("import ", "from ")):
            insert_at += 1
        stub = [
            "class _Txn:",
            "    def __init__(self, amount=0.0, category='', is_income=False):",
            "        self.amount = float(amount)",
            "        self.category = str(category).lower().strip()",
            "        self._is_income = bool(is_income)",
            "",
            "    def is_income(self):",
            "        return self._is_income",
            "",
        ]
        if "class _Txn" not in src:
            lines[insert_at:insert_at] = stub + [""]

        for start, end, target_name, dicts in sorted(v.dict_list_assigns, reverse=True):
            new_elts: List[str] = []
            for d in dicts:
                amount = unparse(d.get("amount", ast.Constant(value=0.0)))
                cat = unparse(d.get("category", ast.Constant(value="")))
                inc = unparse(d.get("is_income", ast.Constant(value=False)))
                new_elts.append(f"_Txn(amount={amount}, category={cat}, is_income={inc})")
            indent = re.match(r"^(\s*)", lines[start]).group(1)
            rep = [f"{indent}{target_name} = ["]
            rep += [f"{indent}    {e}," for e in new_elts]
            rep += [f"{indent}]"]
            lines[start : end + 1] = rep

        return "\n".join(lines).strip()

    def _merge_from_imports_block(self, suite: str) -> str:
        """Сливает подряд идущие Import/ImportFrom в начале модуля (одна строка на абсолютный модуль)."""
        src = (suite or "").replace("\r\n", "\n")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return suite
        lines = src.split("\n")
        idx = 0
        if tree.body:
            first = tree.body[0]
            if isinstance(first, ast.Expr):
                v = first.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    idx = 1
        imp_nodes: List[ast.stmt] = []
        while idx < len(tree.body) and isinstance(tree.body[idx], (ast.Import, ast.ImportFrom)):
            imp_nodes.append(tree.body[idx])
            idx += 1
        if len(imp_nodes) < 1:
            return suite
        by_mod: Dict[str, Set[str]] = {}
        standalone: List[str] = []
        unparse = getattr(ast, "unparse", None)

        def unp(node: ast.AST) -> str:
            if unparse:
                return unparse(node).strip()
            return ""

        for node in imp_nodes:
            if isinstance(node, ast.Import):
                if unparse:
                    standalone.append(unp(node))
                else:
                    parts = []
                    for a in node.names:
                        parts.append(f"{a.name}" + (f" as {a.asname}" if a.asname else ""))
                    standalone.append("import " + ", ".join(parts))
                continue
            if isinstance(node, ast.ImportFrom):
                if not node.module:
                    if unparse:
                        standalone.append(unp(node))
                    continue
                mod = node.module
                for alias in node.names:
                    if alias.name == "*":
                        by_mod.setdefault(mod, set()).add("*")
                    else:
                        part = alias.name if not alias.asname else f"{alias.name} as {alias.asname}"
                        by_mod.setdefault(mod, set()).add(part)
        merged_lines: List[str] = []
        for mod in sorted(by_mod.keys()):
            names = sorted(by_mod[mod], key=str.lower)
            if names == ["*"]:
                merged_lines.append(f"from {mod} import *")
            else:
                merged_lines.append(f"from {mod} import {', '.join(names)}")
        merged_lines.extend(standalone)
        first_ln = imp_nodes[0].lineno - 1
        last_ln = getattr(imp_nodes[-1], "end_lineno", imp_nodes[-1].lineno) - 1
        new_lines = lines[:first_ln] + merged_lines + lines[last_ln + 1 :]
        return "\n".join(new_lines)

    def _sanitize_generated_test_code(self, suite: str, source_code: str) -> str:
        """
        Постобработка сгенерированного файла:
        фильтрация импортов, dedup test_*, синтаксис и polish.
        """
        text = (suite or "").replace("\r\n", "\n").strip()
        if not text:
            return text

        blocked_import_tokens = (
            "venv.", ".venv.", "site_packages", "site-packages", "__pycache__",
            "node_modules", ".idea.", ".vscode.",
        )
        project_modules = self._extract_project_modules(source_code)
        project_roots = {m.split(".")[0] for m in project_modules if m}

        out: List[str] = []
        seen_imports: Set[str] = set()
        i = 0
        lines = text.split("\n")
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            low = stripped.lower()

            if stripped.startswith("from ") or stripped.startswith("import "):
                if any(tok in low for tok in blocked_import_tokens):
                    i += 1
                    continue
                if stripped.startswith("from "):
                    m = re.match(r"from\s+([a-zA-Z0-9_\.]+)\s+import\s+", stripped)
                    if m:
                        mod = m.group(1)
                        root = mod.split(".")[0]
                        if "." in mod and root not in project_roots and not mod.startswith(("pytest", "unittest", "typing", "datetime", "pathlib")):
                            i += 1
                            continue
                if stripped not in seen_imports:
                    seen_imports.add(stripped)
                    out.append(line)
                i += 1
                continue
            out.append(line)
            i += 1

        deduped: List[str] = []
        seen_tests: Set[str] = set()
        j = 0
        while j < len(out):
            line = out[j]
            m = re.match(r"^(?:async\s+)?def\s+(test_[a-zA-Z0-9_]+)\s*\(", line.strip())
            if not m:
                deduped.append(line)
                j += 1
                continue
            test_name = m.group(1)
            if test_name in seen_tests:
                j += 1
                while j < len(out):
                    nxt = out[j]
                    stripped = nxt.strip()
                    if not stripped:
                        j += 1
                        continue
                    if re.match(r"^(?:async\s+)?def\s+test_[a-zA-Z0-9_]+\s*\(", stripped):
                        break
                    if re.match(r"^class\s+\w+", stripped):
                        break
                    if stripped.startswith(("import ", "from ", "@")):
                        break
                    if nxt.startswith((" ", "\t")):
                        j += 1
                        continue
                    break
                continue
            seen_tests.add(test_name)
            deduped.append(line)
            j += 1

        cleaned = "\n".join(deduped).strip()
        if not self._is_valid_python(cleaned):
            return text
        cleaned = self._auto_add_missing_imports(cleaned, source_code)
        polished = self._polish_test_suite(cleaned)
        if self._is_valid_python(polished) and self._has_test_definitions(polished):
            return polished
        return cleaned

    def _is_valid_python(self, code: str) -> bool:
        if not code or len(code.strip()) < 10:
            return False
        try:
            compile(code, "<generated_tests>", "exec")
            return True
        except Exception:
            return False

    def _merge_basic_and_recipe(self, basic: str, recipe: str, code: str) -> str:
        """Объединяет basic и recipe, если рецепты валидны и не дублируют basic целиком."""
        basic = (basic or "").strip()
        recipe = (recipe or "").strip()
        if not recipe or not self._is_valid_python(recipe) or not self._has_test_definitions(recipe):
            return basic
        if not basic:
            return self._sanitize_generated_test_code(recipe, code)
        if self._looks_like_duplicate_suite(basic, recipe):
            return self._sanitize_generated_test_code(recipe, code)
        merged = (
            f"{basic}\n\n"
            f"# ---- ТЕСТЫ ИЗ РЕЦЕПТОВ (детерминированно; analyzer.utils.recipe_generator) ----\n"
            f"# ---- База знаний: recipe_kb.json + recipes/<profile>.json (env AI_RECIPE_PROFILE) ----\n\n"
            f"{recipe}"
        ).strip()
        return self._sanitize_generated_test_code(merged, code)

    def _looks_like_duplicate_suite(self, first: str, second: str) -> bool:
        def norm(s: str) -> str:
            s = (s or "").strip()
            s = s.replace("# ---- AI GENERATED TESTS ----", "")
            s = s.replace("# ---- ТЕСТЫ, СГЕНЕРИРОВАННЫЕ AI ----", "")
            s = s.replace("# ---- ТЕСТЫ ИЗ РЕЦЕПТОВ", "")
            s = re.sub(r'""".*?"""', "", s, flags=re.DOTALL)
            return "\n".join(line.rstrip() for line in s.splitlines() if line.strip())

        a = norm(first)
        b = norm(second)
        if not a or not b:
            return False
        if a == b:
            return True
        test_name_re = re.compile(r"^\s*(?:async\s+def|def)\s+(test_[a-zA-Z0-9_]+)\s*\(", re.MULTILINE)
        a_tests = set(test_name_re.findall(a))
        b_tests = set(test_name_re.findall(b))
        if not (a_tests and a_tests == b_tests):
            return False
        similarity = difflib.SequenceMatcher(None, a, b).ratio()
        return similarity >= 0.94

    def _pytest_repair_user_block(
        self,
        code: str,
        current_tests: str,
        pytest_output: str,
        digest: str,
        metrics: Dict,
        config: Dict,
    ) -> str:
        max_py = int(os.environ.get("AI_PYTEST_OUTPUT_MAX", "14000"))
        po = (pytest_output or "")[:max_py]
        return "\n".join(
            [
                "Исправь падения pytest. Верни ТОЛЬКО полный запускаемый Python-модуль с тестами (импорты + тесты).",
                "Без текста вне кода; без markdown-ограждений вокруг всего ответа.",
                "Все комментарии и docstring тестов — на русском языке.",
                f"- Metrics: {metrics.get('functions_count', 0)} functions, {metrics.get('classes_count', 0)} classes",
                f"- Generation detail_level: {config.get('detail_level', '')}",
                "",
                "## Pytest output",
                "```",
                po,
                "```",
                "",
                "## Current test file (rewrite fully if needed)",
                "```python",
                (current_tests or "")[:15000],
                "```",
                "",
                "## Auto-extracted API hints (signatures, raises)",
                digest or "(digest unavailable)",
                "",
                "## Source reference (truncated)",
                "```python",
                (code or "")[:10000],
                "```",
            ]
        )

    def refine_tests_with_pytest_output(
        self,
        code: str,
        current_tests: str,
        pytest_output: str,
        metrics: Dict,
        config: Dict,
        framework: str,
    ) -> str:
        if framework != "pytest" or not (current_tests or "").strip():
            return ""
        try:
            from .contract_digest import build_contract_digest

            lim = int(os.environ.get("AI_CONTRACT_DIGEST_CHARS", "4500"))
            digest = build_contract_digest(code, max_chars=lim)
        except Exception as exc:
            logger.warning("contract digest for repair failed: %s", exc)
            digest = ""
        user = self._pytest_repair_user_block(code, current_tests, pytest_output, digest, metrics, config)

        prompt = (
            "You are an expert Python engineer. Fix failing pytest tests. "
            "Write comments and docstrings in Russian.\n\n"
            + user
        )
        num_predict = int(os.environ.get("OLLAMA_NUM_PREDICT_REPAIR") or "4096")
        repair_timeout = max(120, int(self.timeout or 120))
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.12,
                        "num_predict": num_predict,
                        "timeout": repair_timeout,
                    },
                },
                timeout=repair_timeout,
            )
            if response.status_code != 200:
                logger.warning("ollama refine HTTP %s", response.status_code)
                return ""
            result = response.json()
            raw = result.get("response", "") or ""
            clean = self._pick_clean_from_model_output(raw, code)
            if self._is_valid_python(clean) and self._has_test_definitions(clean):
                return clean
            return ""
        except Exception as e:
            logger.warning("ollama refine failed: %s", e)
            return ""

    def run_pytest_repair_loop(
        self,
        code: str,
        tests: str,
        project_root: str,
        metrics: Dict,
        config: Dict,
        framework: str,
    ) -> str:
        """Пишет тесты в распакованный проект, гоняет pytest, при падении — второй запрос к LLM (до N раундов)."""
        if framework != "pytest" or not (tests or "").strip() or not (project_root or "").strip():
            return tests
        try:
            from .pytest_repair import write_tests_and_run_pytest
        except ImportError as e:
            logger.warning("pytest repair unavailable: %s", e)
            return tests

        rounds_raw = os.environ.get("AI_PYTEST_REPAIR_ROUNDS", "2")
        try:
            rounds = max(0, int(rounds_raw))
        except ValueError:
            rounds = 2

        current = tests
        for r in range(rounds):
            rc, out = write_tests_and_run_pytest(project_root, current)
            if rc == 0:
                logger.info("pytest repair: pass (round %s)", r)
                return current
            logger.info("pytest repair: failures round %s rc=%s", r, rc)
            nxt = self.refine_tests_with_pytest_output(code, current, out, metrics, config, framework)
            if not (nxt or "").strip():
                logger.warning("pytest repair: empty refine output (round %s)", r)
                break
            nxt = self._sanitize_generated_test_code(nxt.strip(), code)
            if not self._is_valid_python(nxt) or not self._has_test_definitions(nxt):
                logger.warning("pytest repair: invalid refined output (round %s)", r)
                break
            if nxt.strip() == current.strip():
                logger.warning("pytest repair: no model progress (round %s)", r)
                break
            current = nxt
        return current

    def _build_prompt(
            self,
            code: str,
            metrics: Dict,
            config: Dict,
            framework: str
    ) -> str:
        """Строит промпт для AI модели — НАДЁЖНАЯ ВЕРСИЯ (список + join)"""

        use_mocks = config.get('use_mocks', False)
        include_edge_cases = config.get('include_edge_cases', True)

        prompt_parts = []

        prompt_parts.append("You are an expert Python developer specializing in test automation.")
        prompt_parts.append("")
        prompt_parts.append("## Task")
        prompt_parts.append("Generate comprehensive unit tests for the following Python code.")
        prompt_parts.append("")
        prompt_parts.append("## Code to Test")
        prompt_parts.append("```python")

        code_limit = int(os.environ.get("AI_PROMPT_CODE_CHARS", "3500"))
        prompt_parts.append(code[:code_limit])

        prompt_parts.append("```")
        prompt_parts.append("")

        prompt_parts.append("## Code Metrics")
        prompt_parts.append(f"- Functions: {metrics.get('functions_count', 0)}")
        prompt_parts.append(f"- Classes: {metrics.get('classes_count', 0)}")
        prompt_parts.append(f"- Async functions: {metrics.get('async_functions', 0)}")
        prompt_parts.append(f"- Total lines: {metrics.get('total_lines', 0)}")
        prompt_parts.append("")

        try:
            from .contract_digest import build_contract_digest

            dlim = int(os.environ.get("AI_CONTRACT_DIGEST_CHARS", "4500"))
            digest = build_contract_digest(code, max_chars=dlim)
        except Exception as exc:
            logger.debug("contract digest for prompt skipped: %s", exc)
            digest = ""
        if (digest or "").strip():
            prompt_parts.append("## Auto-extracted API hints (from AST)")
            prompt_parts.append(
                "Prefer these signatures and raised types; do not invent parameters or exceptions absent from the source."
            )
            prompt_parts.append(digest)
            prompt_parts.append("")

        prompt_parts.append("## Requirements")
        prompt_parts.append(f"1. Use {framework} framework")
        prompt_parts.append("2. Test all public functions and methods")
        prompt_parts.append("3. Include docstrings for each test (in Russian)")
        prompt_parts.append("4. Follow AAA pattern (Arrange, Act, Assert)")
        prompt_parts.append("5. Use descriptive test names (test_function_scenario)")
        prompt_parts.append("6. Avoid trivial assertions like `is not None` and `assert x == approx(x)`")
        prompt_parts.append("7. Prefer exact expected values for pure/calculation functions")
        prompt_parts.append("8. Import ONLY from modules present in the provided code files")
        prompt_parts.append("9. Do not import from venv/site-packages/internal environment paths")
        prompt_parts.append("10. Write all inline comments and test docstrings in Russian")
        basic_ref = (config.get("basic_reference") or "").strip()
        if basic_ref:
            prompt_parts.append("11. IMPORTANT: Do not duplicate this baseline suite; generate complementary advanced tests")
        prompt_parts.append("")
        prompt_parts.append("## Domain correctness")
        prompt_parts.append("- Every test function MUST end with at least one `assert` or `with pytest.raises(...)` — never truncate mid-function")
        prompt_parts.append("- For money/float comparisons use `pytest.approx(...)` (avoid bare `==` on floats)")
        prompt_parts.append("- If method M accepts model object O: use **valid** instances of O built so O.__init__ succeeds; put constructor validation tests in tests for class O, not in tests for M")
        prompt_parts.append("- Example: invalid Book(price=-1) raises in Book.__init__ — do not wrap that in `inventory.add_book` unless add_book is what should validate")
        prompt_parts.append("- Import only what you use; do not import `patch` unless you use `@patch` or `patch(`")
        prompt_parts.append("- Merge imports from the same module into a single `from module import a, b` line")
        prompt_parts.append("- Pass **only** argument types the code accepts: never pass `None` unless the parameter is `Optional[...]`, has default `None`, or the implementation clearly handles `None`")
        prompt_parts.append("- Prefer empty lists `[]`, empty dicts `{}`, or minimal valid values instead of `None` for collections")
        prompt_parts.append("- For `pytest.raises(...)`, use the **exact** exception class (or tuple) that appears in `raise` in the source under test — do not assume `ValueError` if the code raises a project-specific error")
        prompt_parts.append("- Assertions on dict keys, categories, or string fields must use the **same** spelling and casing as the implementation stores (read attribute assignments and dict keys in the source)")
        prompt_parts.append("- When calling constructors or factories, use the **same** parameter names and order as in the source (positional vs keyword); do not guess argument semantics")

        prompt_parts.append("## Additional Requirements")
        if use_mocks:
            prompt_parts.append("- Use unittest.mock only for non-deterministic external dependencies (I/O, network, DB, clock)")
            prompt_parts.append("- Do not mock small pure validators unless necessary; prefer real calls")
            prompt_parts.append("- Use @patch decorator when mocking; patch the name as imported in the module under test")
        if include_edge_cases:
            prompt_parts.append("- Include edge cases where they match the real contract:")
            prompt_parts.append("  * Empty inputs (lists/strings) when the API accepts them")
            prompt_parts.append("  * `None` **only** if the API is typed or documented to accept None")
            prompt_parts.append("  * Boundary values (min/max, empty string, zero)")
            prompt_parts.append("  * Invalid inputs that should raise — use the real exception type from the source")
        prompt_parts.append("")

        prompt_parts.append("## Output Format")
        prompt_parts.append("- Return ONLY the test code")
        prompt_parts.append("- No explanations outside the code")
        prompt_parts.append("- Include necessary imports")
        prompt_parts.append("- Make tests ready to run")
        prompt_parts.append("- Generated tests must be meaningfully different from basic templates")
        if basic_ref:
            prompt_parts.append("- You must avoid reusing same test names from baseline")
        prompt_parts.append("")
        if basic_ref:
            prompt_parts.append("## Baseline Suite To Avoid Duplicating")
            prompt_parts.append("```python")
            prompt_parts.append(basic_ref[:3000])
            prompt_parts.append("```")
        prompt_parts.append("")
        prompt_parts.append("Generate the tests now:")

        return '\n'.join(prompt_parts)

    def check_provider_available(self) -> bool:
        """Проверяет доступность Ollama."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except requests.exceptions.ConnectionError:
            logger.warning("⚠️ Cannot connect to Ollama (ConnectionError)")
            return False
        except requests.exceptions.Timeout:
            logger.warning("⚠️ Ollama request timed out")
            return False
        except Exception as e:
            logger.warning(f"⚠️ Ollama check failed: {type(e).__name__}: {e}")
            return False

    def check_ollama_available(self) -> bool:
        """Alias для check_provider_available."""
        return self.check_provider_available()

    def check_model_available(self, model_name: str = None) -> bool:
        model = model_name or self.model
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                available_models = [m['name'] for m in data.get('models', [])]
                return any(model in m or m.startswith(model + ':') for m in available_models)
            return False
        except Exception as e:
            logger.warning(f"⚠️ Could not check model '{model}': {e}")
            return False

    def get_available_models(self) -> List[str]:
        """Список моделей, установленных в Ollama."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return [m['name'] for m in data.get('models', [])]
        except Exception as e:
            logger.warning(f"⚠️ Could not fetch models: {e}")
        return [self.model]