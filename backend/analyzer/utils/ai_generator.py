import os
import requests
import json
import logging
import re
from typing import Dict, List, Optional, Tuple, Set

logger = logging.getLogger(__name__)

# ✅ Настройки Ollama
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")


class AITestGenerator:
    """
    Генерация юнит-тестов с помощью локальной AI модели (Ollama).

    Поддерживаемые режимы:
    - basic: Простые тесты на основе сигнатур функций
    - advanced: Тесты с моками и граничными случаями (AI)
    - full: Комбинация basic + advanced
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
        detail_level = config.get('detail_level', 'basic')
        force_basic = bool(config.get('force_basic'))
        use_mocks = config.get('use_mocks', False)
        include_edge_cases = config.get('include_edge_cases', True)
        test_framework = config.get('test_framework', 'pytest')

        logger.info(f"🤖 Generating tests: level={detail_level}, mocks={use_mocks}")

        if force_basic or detail_level == 'basic':
            return self._generate_basic_tests(code, metrics, test_framework, include_edge_cases=include_edge_cases)
        if detail_level == 'full':
            basic = self._generate_basic_tests(code, metrics, test_framework, include_edge_cases=include_edge_cases)
            ai = self._generate_ai_tests(code, metrics, config, test_framework) or ""
            ai_clean = self._extract_python_code(ai)
            if not self._is_valid_python(ai_clean):
                ai_clean = ""
            if not ai_clean.strip():
                return basic
            # Если AI фактически вернул fallback/basic-дубль, не дублируем блоки.
            if self._looks_like_duplicate_suite(basic, ai_clean):
                return basic
            return f"{basic}\n\n# ---- AI GENERATED TESTS ----\n\n{ai_clean}".strip()
        else:
            ai = self._generate_ai_tests(code, metrics, config, test_framework)
            ai_clean = self._extract_python_code(ai or "")
            if self._is_valid_python(ai_clean) and len(ai_clean.strip()) > 40:
                return ai_clean
            return self._generate_basic_tests(code, metrics, test_framework, include_edge_cases=include_edge_cases)

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

        def to_module_path(path: str) -> str:
            p = path.strip().replace("\\", "/")
            if p.endswith(".py"):
                p = p[:-3]
            if p.endswith("/__init__"):
                p = p[: -len("/__init__")]
            raw_parts = [x for x in p.split("/") if x and x not in (".", "..")]
            parts = []
            for seg in raw_parts:
                seg_l = seg.lower()
                # Убираем служебные/внешние сегменты из пути импорта.
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

        def should_skip_source_path(path: str) -> bool:
            p = (path or "").replace("\\", "/").lower()
            if not p.endswith(".py"):
                return True
            # Исключаем чужие/служебные директории и уже тестовые файлы.
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
            n = name.lower()
            a = (ann_name or "").lower()
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
            """
            Возвращает:
            - set ожидаемых исключений из docstring (Raises)
            - list примеров вызова из Examples/Example/doctest
            """
            if not doc:
                return set(), []
            raises = set()
            examples = []

            # Raises: ValueError / TypeError / ...
            for m in re.finditer(r"(Raises?|Исключения?)\s*:?\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)", doc, flags=re.IGNORECASE):
                exc_part = m.group(2)
                for ex in re.split(r"\s*,\s*", exc_part):
                    if ex:
                        raises.add(ex.strip())

            # doctest style: >>> func(...)
            for m in re.finditer(r"^\s*>>>\s*(.+)$", doc, flags=re.MULTILINE):
                line = m.group(1).strip()
                if "(" in line and ")" in line:
                    examples.append(line)

            # explicit examples lines
            for m in re.finditer(r"(?:Examples?|Примеры?)\s*:?\s*(.+)", doc, flags=re.IGNORECASE):
                chunk = m.group(1).strip()
                if "(" in chunk and ")" in chunk:
                    examples.append(chunk)

            # dedupe preserve order
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

        tests = ['"""Auto-generated tests (basic mode)"""', ""]

        if framework == "pytest":
            tests.append("import pytest")
            tests.append("")
        else:
            tests.append("import unittest")
            tests.append("")

        try:
            clean_code = code.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").strip()
            file_blocks = split_sources(clean_code)

            imports_by_module = {}
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
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                        # Не генерируем unit-тест на cli/main entrypoint напрямую.
                        if node.name.lower() == "main":
                            continue
                        raw_args = [arg for arg in node.args.args if arg.arg != "self"]
                        args = [a.arg for a in raw_args]
                        arg_ann = [ann_to_name(a.annotation) for a in raw_args]
                        behavior = detect_binary_behavior(node)
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
                        imports_by_module.setdefault(module, []).append(node.name)
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
                            )
                        )
                    elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                        methods: List[FunctionSpec] = []
                        init_arg_count = 0

                        # detect __init__
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
                            imports_by_module.setdefault(module, []).append(node.name)
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
                    '"""Auto-generated tests (basic mode)"""\n\n'
                    "import pytest\n\n"
                    "# Could not detect public functions in uploaded code.\n"
                    "# Generated by Python Test Analyzer"
                )

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
                tests.append(f'    """Basic test for {spec.fn_name}"""')
                if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                    tests.append(f"    obj = {spec.class_name}()")

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
                            tests.append(f'    """{spec.fn_name} should fail on zero divisor"""')
                            if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                                tests.append(f"    obj = {spec.class_name}()")
                            zero_params = list(safe_params)
                            zero_params[1] = "0"
                            tests.append("    with pytest.raises(ZeroDivisionError):")
                            tests.append(f"        {build_call(spec, zero_params)}")
                else:
                    # Domain-oriented rules for common validators/normalizers
                    valid_email = "'user@example.com'"
                    invalid_email = "'invalid-email'"
                    valid_phone = "'+79001234567'"
                    invalid_phone = "'123'"
                    noisy_phone = "'+7 (900) 123-45-67'"
                    if "email" in fn_l and (fn_l.startswith(("is_", "validate_")) or spec.returns.lower() == "bool"):
                        tests.append(f"    assert {build_call(spec, [valid_email])} is True")
                    elif ("phone" in fn_l or "mobile" in fn_l) and (fn_l.startswith(("is_", "validate_")) or spec.returns.lower() == "bool"):
                        tests.append(f"    assert {build_call(spec, [valid_phone])} is True")
                    elif "normalize_phone" in fn_l:
                        tests.append(f"    result = {build_call(spec, [noisy_phone])}")
                        tests.append(f"    assert result == {valid_phone}")
                        tests.append("    assert isinstance(result, str)")
                        tests.append("    assert result.startswith('+')")
                        tests.append("    assert result[1:].isdigit()")
                        tests.append("    assert len(result) >= 10")
                    else:
                        tests.append(f"    result = {maybe_await}")
                        if fn_l.startswith(("is_", "has_", "can_", "validate_")) or spec.returns.lower() == "bool":
                            tests.append("    assert isinstance(result, bool)")
                        elif any(k in fn_l for k in ("calculate", "compute", "price", "discount", "tax", "amount", "total")):
                            tests.append("    assert isinstance(result, (int, float))")
                            tests.append("    assert result == pytest.approx(result)")
                        elif spec.returns.lower() in ("str", "string"):
                            tests.append("    assert isinstance(result, str)")
                        elif spec.returns.lower() in ("int",):
                            tests.append("    assert isinstance(result, int)")
                        else:
                            tests.append("    assert result is not None")

                    # Практичные edge-case проверки для валидаторов/нормализаторов
                    if include_edge_cases and spec.args and ("email" in fn_l or "phone" in fn_l or "normalize" in fn_l):
                        tests.append("")
                        tests.append(f"def test_{spec.fn_name}_invalid_input():")
                        tests.append(f'    """Invalid input scenario for {spec.fn_name}"""')
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

                    # Docstring-driven examples/raises
                    if include_edge_cases and spec.examples:
                        tests.append("")
                        tests.append(f"def test_{spec.fn_name}_doc_examples():")
                        tests.append(f'    """Validate examples from docstring for {spec.fn_name}"""')
                        if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                            tests.append(f"    obj = {spec.class_name}()")
                        for ex in spec.examples[:3]:
                            # безопасно: только вызовы этой функции/метода
                            safe_ex = ex.strip()
                            if spec.fn_name in safe_ex:
                                tests.append(f"    _ = {safe_ex}")

                    known_doc_raises = sorted([r for r in spec.raises if r in ("ValueError", "TypeError", "KeyError", "RuntimeError")])
                    if include_edge_cases and known_doc_raises and spec.args:
                        tests.append("")
                        tests.append(f"def test_{spec.fn_name}_doc_raises():")
                        tests.append(f'    """Validate Raises section for {spec.fn_name}"""')
                        if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                            tests.append(f"    obj = {spec.class_name}()")
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
                    tests.append(f'        """Basic test for {spec.fn_name}"""')
                    if spec.is_method and spec.class_name and not spec.call_expr.startswith(spec.class_name + "."):
                        tests.append(f"        obj = {spec.class_name}()")
                    tests.append(f"        result = {call}")
                    if spec.fn_name.lower().startswith(("is_", "has_", "can_", "validate_")) or spec.returns.lower() == "bool":
                        tests.append("        self.assertIsInstance(result, bool)")
                    else:
                        tests.append("        self.assertIsNotNone(result)")
                    tests.append("")

            tests.append("# Generated by Python Test Analyzer")
            return "\n".join(tests).replace("\ufeff", "").replace("\u200b", "").strip()

        except Exception as e:
            logger.error(f"Basic test generation failed: {e}", exc_info=True)
            return (
                '"""Auto-generated tests (basic mode)"""\n\n'
                "import pytest\n\n"
                f"# Error generating tests: {e}\n"
                "# Generated by Python Test Analyzer"
            )

    def _generate_ai_tests(
            self,
            code: str,
            metrics: Dict,
            config: Dict,
            framework: str
    ) -> str:
        """Генерирует тесты с помощью Ollama AI"""

        # 📝 Формируем промпт
        prompt = self._build_prompt(code, metrics, config, framework)

        try:
            # 🤖 Запрос к Ollama
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 2000,
                        "timeout": self.timeout
                    }
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                result = response.json()
                generated_tests = result.get('response', '')
                logger.info(f"✅ Ollama response: {len(generated_tests)} chars")
                if len(generated_tests) < 100:
                    logger.warning(f"⚠️ Suspiciously short AI response: '{generated_tests[:200]}...'")

                return self._extract_python_code(generated_tests)
            else:
                error_body = response.text[:500] if response.text else 'No body'
                logger.error(f"❌ Ollama API error {response.status_code}: {error_body}")
                fallback = self._generate_basic_tests(code, metrics, framework, include_edge_cases=config.get('include_edge_cases', True))
                return f"# ⚠️ Ollama API error ({response.status_code}). Basic fallback.\n{fallback}"

        except requests.exceptions.ConnectionError:
            logger.error("❌ Cannot connect to Ollama. Is it running at %s?", self.base_url)
            fallback = self._generate_basic_tests(code, metrics, framework, include_edge_cases=config.get('include_edge_cases', True))
            return f"# ⚠️ Ollama недоступен. Сгенерированы базовые тесты.\n{fallback}"

        except requests.exceptions.Timeout:
            logger.error("❌ Ollama request timed out (%ss)", self.timeout)
            fallback = self._generate_basic_tests(code, metrics, framework, include_edge_cases=config.get('include_edge_cases', True))
            return f"# ⚠️ Таймаут запроса к AI. Сгенерированы базовые тесты.\n{fallback}"

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 'unknown'
            logger.error("❌ Ollama HTTP error: %s", status)
            if status == 404:
                return f"# ⚠️ Модель '{self.model}' не найдена в Ollama.\n# Запустите: ollama pull {self.model}\n{self._generate_basic_tests(code, metrics, framework, include_edge_cases=config.get('include_edge_cases', True))}"
            fallback = self._generate_basic_tests(code, metrics, framework, include_edge_cases=config.get('include_edge_cases', True))
            return f"# ⚠️ Ошибка API ({status}). Базовые тесты:\n{fallback}"

        except Exception as e:
            logger.error(f"❌ AI generation unexpected error: {type(e).__name__}: {e}", exc_info=True)
            fallback = self._generate_basic_tests(code, metrics, framework, include_edge_cases=config.get('include_edge_cases', True))
            return f"# ⚠️ Неожиданная ошибка: {e}\n{fallback}"

    def _extract_python_code(self, raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""
        fenced = re.findall(r"```(?:python)?\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            text = "\n\n".join(s.strip() for s in fenced if s.strip())
        # Удаляем Markdown заголовки перед кодом, если модель их вернула.
        lines = text.splitlines()
        while lines and lines[0].strip().startswith("#") and "test" not in lines[0].lower():
            lines.pop(0)
        return "\n".join(lines).strip()

    def _is_valid_python(self, code: str) -> bool:
        if not code or len(code.strip()) < 10:
            return False
        try:
            compile(code, "<generated_tests>", "exec")
            return True
        except Exception:
            return False

    def _looks_like_duplicate_suite(self, first: str, second: str) -> bool:
        def norm(s: str) -> str:
            s = (s or "").strip()
            # Удаляем заголовки секций для более честного сравнения
            s = s.replace("# ---- AI GENERATED TESTS ----", "")
            return "\n".join(line.rstrip() for line in s.splitlines() if line.strip())

        a = norm(first)
        b = norm(second)
        if not a or not b:
            return False
        if a == b:
            return True
        # fallback: сравниваем наборы имен тестов
        test_name_re = re.compile(r"^\s*(?:async\s+def|def)\s+(test_[a-zA-Z0-9_]+)\s*\(", re.MULTILINE)
        a_tests = set(test_name_re.findall(a))
        b_tests = set(test_name_re.findall(b))
        return bool(a_tests) and a_tests == b_tests

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

        # ✅ Часть 1: Статический шаблон
        prompt_parts = []

        prompt_parts.append("You are an expert Python developer specializing in test automation.")
        prompt_parts.append("")
        prompt_parts.append("## Task")
        prompt_parts.append("Generate comprehensive unit tests for the following Python code.")
        prompt_parts.append("")
        prompt_parts.append("## Code to Test")
        prompt_parts.append("```python")

        # ✅ Часть 2: Код (ограничиваем размер)
        prompt_parts.append(code[:8000])

        prompt_parts.append("```")
        prompt_parts.append("")

        # ✅ Часть 3: Метрики
        prompt_parts.append("## Code Metrics")
        prompt_parts.append(f"- Functions: {metrics.get('functions_count', 0)}")
        prompt_parts.append(f"- Classes: {metrics.get('classes_count', 0)}")
        prompt_parts.append(f"- Async functions: {metrics.get('async_functions', 0)}")
        prompt_parts.append(f"- Total lines: {metrics.get('total_lines', 0)}")
        prompt_parts.append("")

        # ✅ Часть 4: Требования
        prompt_parts.append("## Requirements")
        prompt_parts.append(f"1. Use {framework} framework")
        prompt_parts.append("2. Test all public functions and methods")
        prompt_parts.append("3. Include docstrings for each test")
        prompt_parts.append("4. Follow AAA pattern (Arrange, Act, Assert)")
        prompt_parts.append("5. Use descriptive test names (test_function_scenario)")
        prompt_parts.append("")

        # ✅ Часть 5: Дополнительные требования
        prompt_parts.append("## Additional Requirements")
        if use_mocks:
            prompt_parts.append("- Use unittest.mock for mocking external dependencies")
            prompt_parts.append("- Mock database calls, API requests, file I/O")
            prompt_parts.append("- Use @patch decorator for clean mocking")
        if include_edge_cases:
            prompt_parts.append("- Include edge cases:")
            prompt_parts.append("  * Empty inputs")
            prompt_parts.append("  * None values")
            prompt_parts.append("  * Boundary values")
            prompt_parts.append("  * Invalid inputs (should raise exceptions)")
        prompt_parts.append("")

        # ✅ Часть 6: Формат вывода
        prompt_parts.append("## Output Format")
        prompt_parts.append("- Return ONLY the test code")
        prompt_parts.append("- No explanations outside the code")
        prompt_parts.append("- Include necessary imports")
        prompt_parts.append("- Make tests ready to run")
        prompt_parts.append("")
        prompt_parts.append("Generate the tests now:")

        # ✅ Собираем всё вместе
        return '\n'.join(prompt_parts)

    def check_ollama_available(self) -> bool:
        """✅ Проверяет доступность Ollama API"""
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
        """✅ Получает список доступных моделей в Ollama"""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return [m['name'] for m in data.get('models', [])]
        except Exception as e:
            logger.warning(f"⚠️ Could not fetch models: {e}")
        return [self.model]  # fallback