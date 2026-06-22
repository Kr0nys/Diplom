from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
import os
from typing import Dict, List, Optional, Sequence, Tuple

from .param_kb import expr_for_param, load_param_kb, pick_rule_for_param, prefer_exception


_FILE_HDR = re.compile(r"^\s*# File:\s+(.+?)\s*$", re.MULTILINE)


def _auto_recipe_profile(code: str) -> str:
    low = (code or "").lower()
    finance_markers = [
        "currency", "vat", "tax", "invoice", "amount", "balance", "decimal", "money",
        "руб", "₽", "$", "€",
    ]
    api_markers = [
        "fastapi", "flask", "django", "pydantic", "starlette", "router", "endpoint",
        "serializer", "repository", "service", "selector", "httpx", "testclient",
        "api/v", "restframework", "drf", "sqlalchemy", "async def get", "def create(",
        "user_create", "user_update", "schema_in", "schema_out",
    ]
    finance_score = sum(1 for m in finance_markers if m in low)
    api_score = sum(1 for m in api_markers if m in low)
    if finance_score >= 3:
        return "finance"
    if api_score >= 3:
        return "api"
    return "generic"


def _read_recipe_file(name: str) -> Dict:
    path = Path(__file__).with_name("recipes") / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _merge_recipe_profiles(base: Dict, overlay: Dict) -> Dict:
    """generic + domain profile: callable_rules и test_templates объединяются."""
    out = dict(base or {})
    if overlay.get("profile"):
        out["profile"] = overlay["profile"]

    rules_by_id: Dict[str, Dict] = {}
    for rule in (base.get("callable_rules") or []) + (overlay.get("callable_rules") or []):
        rid = rule.get("id") or str(rule.get("qualname_regex") or "")
        if rid:
            rules_by_id[rid] = rule
    out["callable_rules"] = list(rules_by_id.values())

    templates: Dict[str, Dict] = {}
    templates.update(base.get("test_templates") or {})
    templates.update(overlay.get("test_templates") or {})
    out["test_templates"] = templates
    return out


def _load_recipe_profile(code: str) -> Dict:
    """
    Loads higher-level callable recipes.
    Profile selection:
      - env AI_RECIPE_PROFILE: generic|finance|api|auto (default auto)
      - auto: heuristic based on code markers (finance > api > generic)
    Всегда мержится с generic.json как базой.
    """
    base = _read_recipe_file("generic")
    if not base:
        base = {"profile": "generic", "callable_rules": [], "test_templates": {}}

    want = (os.environ.get("AI_RECIPE_PROFILE", "auto") or "auto").strip().lower()
    if want == "auto":
        want = _auto_recipe_profile(code)
    if want in ("generic", ""):
        return base
    overlay = _read_recipe_file(want)
    if not overlay:
        return base
    return _merge_recipe_profiles(base, overlay)


def _split_source_files(raw: str) -> List[Tuple[str, str]]:
    raw = (raw or "").replace("\r\n", "\n")
    matches = list(_FILE_HDR.finditer(raw))
    if not matches:
        return [("uploaded_code.py", raw)]
    out: List[Tuple[str, str]] = []
    for i, m in enumerate(matches):
        path = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        chunk = raw[start:end].strip()
        if chunk:
            out.append((path, chunk))
    return out


def _to_module_path(path: str) -> Optional[str]:
    """
    Преобразует путь файла в dotted module path для import.
    Возвращает None, если файл не следует импортировать (чужие деревья, пустой путь).
    """
    p = (path or "").strip().replace("\\", "/")
    pl = p.lower()
    if "site-packages" in pl or "site_packages" in pl or "dist-packages" in pl:
        return None
    if p.endswith(".py"):
        p = p[:-3]
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    raw_parts = [x for x in p.split("/") if x and x not in (".", "..")]
    parts: List[str] = []
    for seg in raw_parts:
        seg_l = seg.lower()
        if seg_l in ("tests", "test", "testing"):
            return None
        if seg_l in {
            "venv",
            ".venv",
            "env",
            "__pycache__",
            "site-packages",
            "site_packages",
            "node_modules",
            ".git",
            ".idea",
            ".vscode",
        }:
            continue
        if seg_l in ("build", "dist"):
            # только как сегмент пути (не режем весь импорт), но пропускаем шумные каталоги
            continue
        seg = re.sub(r"[^0-9a-zA-Z_]", "_", seg)
        if not seg:
            continue
        if seg[0].isdigit():
            seg = f"m_{seg}"
        parts.append(seg)
    return ".".join(parts) if parts else None


def _strip_leading_module(qualified: str, mod: str) -> str:
    """Убирает префикс `mod.` если он есть (чтобы не дублировать при f\"{mod}.{ctor}.method\")."""
    pref = (mod or "").strip() + "."
    q = (qualified or "").strip()
    if mod and q.startswith(pref):
        return q[len(pref) :]
    return q


def _body_uses_datetime_now(body: List[ast.stmt]) -> bool:
    for st in body:
        for n in ast.walk(st):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "now":
                v = n.func.value
                if isinstance(v, ast.Name) and v.id == "datetime":
                    return True
                if (
                    isinstance(v, ast.Attribute)
                    and isinstance(v.value, ast.Name)
                    and v.value.id == "datetime"
                    and v.attr == "datetime"
                ):
                    return True
    return False


def _index_class_constructors(code: str) -> Dict[str, Tuple[str, ast.ClassDef, Optional[ast.FunctionDef]]]:
    """Имя класса -> (module, ClassDef, __init__ или None). Последнее объявление перекрывает предыдущее."""
    out: Dict[str, Tuple[str, ast.ClassDef, Optional[ast.FunctionDef]]] = {}
    for path, chunk in _split_source_files(code):
        mod = _to_module_path(path)
        if not mod or not chunk.strip():
            continue
        try:
            tree = ast.parse(chunk)
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name and not node.name.startswith("_"):
                init_fn: Optional[ast.FunctionDef] = None
                for b in node.body:
                    if isinstance(b, ast.FunctionDef) and b.name == "__init__":
                        init_fn = b
                        break
                out[node.name] = (mod, node, init_fn)
    return out


def _ctor_expr_for_class(
    cls_name: str, idx: Dict[str, Tuple[str, ast.ClassDef, Optional[ast.FunctionDef]]]
) -> Optional[str]:
    hit = idx.get(cls_name)
    if not hit:
        return None
    mod, cls_node, init_fn = hit
    if init_fn is not None:
        inner = _build_call_args(init_fn, drop_first=True)
    else:
        dc = _dataclass_ctor_args(cls_node)
        inner = dc if dc is not None else ""
    inner = inner.strip().strip(",")
    return f"{mod}.{cls_node.name}({inner})"


def _smoke_assert_line(returns_ann: str, *, returns_value: bool) -> str:
    """Слабая, но полезная проверка вместо assert True (по аннотации возврата)."""
    a = (returns_ann or "").replace("typing.", "").lower()
    if not returns_value and not (returns_ann or "").strip():
        return "pass  # дымовой тест: нет возвращаемого значения (неявный None)"
    if re.search(r"\bnone\b", a) and "optional" not in a:
        if not returns_value:
            return "pass  # аннотация возврата None"
    if "bool" in a:
        return "assert isinstance(result, bool)"
    if "list" in a or "sequence[" in a:
        return "assert isinstance(result, list)"
    if "dict" in a or "mapping" in a:
        return "assert isinstance(result, dict)"
    if "str" in a and "stream" not in a:
        return "assert isinstance(result, str)"
    if re.search(r"\bdatetime\b", a):
        return "assert hasattr(result, 'year')"
    if "float" in a and "int" not in a.split("[")[0]:
        return "assert isinstance(result, (int, float))"
    if "int" in a:
        return "assert isinstance(result, int)"
    if not returns_value:
        return "pass  # дымовой тест: вызов завершился (нет осмысленного возврата)"
    return "assert result is not None"


def _edge_raises_for_value(rule: Optional[Dict], edge_expr: str) -> Optional[str]:
    if not rule:
        return None
    if str(rule.get("edge") or "").strip() != str(edge_expr).strip():
        return None
    xs = rule.get("edge_raises_any_of") or rule.get("invalid_raises_any_of") or []
    if isinstance(xs, str):
        xs = [xs]
    for x in xs:
        if x in ("ValueError", "TypeError", "KeyError"):
            return x
    return None


def _annotation_text(node: Optional[ast.AST]) -> str:
    if node is None:
        return ""
    unparse = getattr(ast, "unparse", None)
    if not unparse:
        return ""
    try:
        return (unparse(node) or "").strip()
    except Exception:
        return ""


def _is_optional_annotation(ann: str) -> bool:
    a = (ann or "").replace("typing.", "")
    return ("Optional[" in a) or ("| None" in a) or ("None |" in a) or ("Union[" in a and "None" in a)


TMP_PATH_PLACEHOLDER = "__PTA_TMP_PATH__"


def _function_returns_value(fn: ast.AST) -> bool:
    """Есть ли return с ненулевым значением (не bare / не только None)."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and n.value is not None:
            if isinstance(n.value, ast.Constant) and n.value.value is None:
                continue
            return True
    return False


def _is_dataclass(class_node: ast.ClassDef) -> bool:
    for d in class_node.decorator_list or []:
        if isinstance(d, ast.Name) and d.id == "dataclass":
            return True
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "dataclass":
            return True
    return False


def _dataclass_ctor_args(class_node: ast.ClassDef) -> Optional[str]:
    if not _is_dataclass(class_node):
        return None
    parts: List[str] = []
    unparse = getattr(ast, "unparse", None)
    for st in class_node.body:
        if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            name = st.target.id
            if name.startswith("_"):
                continue
            ann = _annotation_text(st.annotation)
            if st.value is not None:
                try:
                    if unparse:
                        parts.append(f"{name}={unparse(st.value)}")
                except Exception:
                    pass
            else:
                parts.append(f"{name}={_value_expr_for_param(name, ann, allow_none=False)}")
    return ", ".join(parts) if parts else None


def _list_element_class_name(ann: str) -> Optional[str]:
    a = (ann or "").replace("typing.", "")
    m = re.search(r"(?:List|list)\[([^]]+)\]", a)
    if not m:
        return None
    inner = m.group(1).strip()
    inner = inner.replace("Optional[", "").rstrip("]")
    inner = inner.split("|")[0].strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", inner):
        return None
    builtins = {"str", "int", "float", "bool", "dict", "list", "set", "tuple", "Any", "None"}
    if inner in builtins:
        return None
    return inner


def _value_expr_for_param(name: str, ann: str, *, allow_none: bool) -> str:
    n = (name or "").lower()
    a = (ann or "").replace("typing.", "")

    kb_hit = expr_for_param(name, ann, kind="valid", allow_none=allow_none)
    if kb_hit is not None:
        return kb_hit

    if "pathlib.path" in a.lower() or re.search(r"(^|[^A-Za-z_])Path([^A-Za-z_]|$)", a):
        return TMP_PATH_PLACEHOLDER

    # Имена, похожие на пути, id или суммы
    if "path" in n or n.endswith("_path") or n in ("filepath", "file_path", "dest", "target"):
        return TMP_PATH_PLACEHOLDER
    if (n in {"id", "pk"} or n.endswith("_id")) and "str" not in a:
        return "1"
    if "isbn" in n:
        return "'978-0-596-52068-7'"
    if "amount" in n or "price" in n or "total" in n:
        return "1.0" if ("float" in a or "Decimal" in a) else "1"

    # По аннотации типа
    if "bool" in a:
        return "True"
    if "int" in a and "str" not in a:
        return "1"
    if "float" in a:
        return "1.5"
    if "bytes" in a:
        return "b'x'"
    if "dict" in a or "Dict[" in a or "Mapping" in a:
        return "{}"
    if "list" in a or "List[" in a or "Sequence" in a:
        return "[]"
    if "set" in a or "Set[" in a:
        return "set()"
    if "tuple" in a or "Tuple[" in a:
        return "()"
    if "str" in a:
        return "'x'"

    # По имени параметра (is_*, has_*, can_*)
    if n.startswith("is_") or n.startswith("has_") or n.startswith("can_"):
        return "True"
    if n.endswith("_flag"):
        return "False"

    # None — только если контракт это допускает
    if allow_none:
        return "None"

    return "0"


@dataclass(frozen=True)
class _CallableTarget:
    module: str
    qualname: str  # "fn" или "Cls.method"
    call_expr: str  # выражение вызова в тесте
    kind: str  # "function" | "method"
    args: Sequence[ast.arg]
    defaults: Sequence[ast.AST]
    kwonlyargs: Sequence[ast.arg]
    kw_defaults: Sequence[Optional[ast.AST]]
    raises: Sequence[str]
    returns_ann: str = ""
    uses_datetime_now: bool = False
    returns_value: bool = True


def _collect_targets(code: str) -> List[_CallableTarget]:
    targets: List[_CallableTarget] = []
    class_index = _index_class_constructors(code)
    for path, chunk in _split_source_files(code):
        if not chunk.strip():
            continue
        try:
            tree = ast.parse(chunk)
        except Exception:
            continue
        mod = _to_module_path(path)
        if not mod:
            continue

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name and not node.name.startswith("_"):
                if node.name == "main":
                    continue
                args = [a for a in node.args.args]  # includes self for methods only; here functions
                raises = sorted(_raises_in_body(node.body))
                targets.append(
                    _CallableTarget(
                        module=mod,
                        qualname=node.name,
                        call_expr=node.name,
                        kind="function",
                        args=args,
                        defaults=list(node.args.defaults or []),
                        kwonlyargs=list(node.args.kwonlyargs or []),
                        kw_defaults=list(node.args.kw_defaults or []),
                        raises=raises,
                        returns_ann=_annotation_text(node.returns),
                        uses_datetime_now=_body_uses_datetime_now(node.body),
                        returns_value=_function_returns_value(node),
                    )
                )
            if isinstance(node, ast.ClassDef) and node.name and not node.name.startswith("_"):
                # Сигнатура __init__ для создания экземпляра класса
                init_fn: Optional[ast.FunctionDef] = None
                for b in node.body:
                    if isinstance(b, ast.FunctionDef) and b.name == "__init__":
                        init_fn = b
                        break

                for b in node.body:
                    if not isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if not b.name or b.name.startswith("_"):
                        continue
                    is_static = any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in (b.decorator_list or []))
                    is_class = any(isinstance(d, ast.Name) and d.id == "classmethod" for d in (b.decorator_list or []))
                    if is_static or is_class:
                        continue  # classmethod/staticmethod пока пропускаем

                    # Как создать экземпляр для вызова метода
                    if init_fn is not None:
                        ctor_expr = f"{node.name}({_build_call_args(init_fn, drop_first=True)})".strip()
                        ctor_expr = re.sub(r"\(\s*\)", "()", ctor_expr)
                    else:
                        cq = _ctor_expr_for_class(node.name, class_index) or f"{node.name}()"
                        ctor_expr = _strip_leading_module(cq, mod)

                    args = list(b.args.args or [])
                    raises = sorted(_raises_in_body(getattr(b, "body", []) or []))
                    targets.append(
                        _CallableTarget(
                            module=mod,
                            qualname=f"{node.name}.{b.name}",
                            call_expr=f"{ctor_expr}.{b.name}",
                            kind="method",
                            args=args,
                            defaults=list(b.args.defaults or []),
                            kwonlyargs=list(b.args.kwonlyargs or []),
                            kw_defaults=list(b.args.kw_defaults or []),
                            raises=raises,
                            returns_ann=_annotation_text(b.returns),
                            uses_datetime_now=_body_uses_datetime_now(b.body),
                            returns_value=_function_returns_value(b),
                        )
                    )
    return targets


def _exc_hint(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: List[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return _exc_hint(node.func)
    return None


def _raises_in_body(body: List[ast.stmt]) -> List[str]:
    found: set[str] = set()
    for st in body:
        for n in ast.walk(st):
            if isinstance(n, ast.Raise) and n.exc:
                h = _exc_hint(n.exc)
                if h:
                    found.add(h)
    return sorted(found)


def _ann_looks_numeric(ann: str) -> bool:
    a = (ann or "").replace("typing.", "")
    return any(x in a for x in ("int", "float", "Decimal"))


def _ann_looks_collection(ann: str) -> bool:
    a = (ann or "").replace("typing.", "")
    return any(x in a for x in ("list", "List[", "Sequence", "Iterable", "tuple", "Tuple["))


def _required_args_slice(t: _CallableTarget) -> List[ast.arg]:
    args = list(t.args or [])
    if t.kind == "method" and args:
        args = args[1:]
    defaults = list(t.defaults or [])
    required_count = max(0, len(args) - len(defaults))
    return args[:required_count]


def _format_call(
    t: _CallableTarget,
    kb: Dict,
    forced_first: Optional[str],
    class_idx: Dict[str, Tuple[str, ast.ClassDef, Optional[ast.FunctionDef]]],
) -> Tuple[str, bool]:
    """Build `module.obj.method(a, b, ...)`; bool = whether tmp_path fixture is required."""
    req = _required_args_slice(t)
    exprs: List[str] = []
    for i, a in enumerate(req):
        if i == 0 and forced_first is not None:
            exprs.append(forced_first)
            continue
        ann = _annotation_text(a.annotation)
        allow_none = _is_optional_annotation(ann)
        ann_s = ann.replace("typing.", "")

        if t.qualname.lower() == "format_book_list" and (a.arg or "").lower() == "books":
            exprs.append(
                "[SimpleNamespace(title='T', author='A'), SimpleNamespace(title='U', author='B')]"
            )
            continue

        elem = _list_element_class_name(ann_s)
        if elem:
            ce = _ctor_expr_for_class(elem, class_idx)
            if ce:
                exprs.append(f"[{ce}]")
                continue

        typed_hit = False
        for token in ("TaskStorage", "TaskAnalyzer", "Book", "Task"):
            if re.search(rf"\b{re.escape(token)}\b", ann_s):
                ce = _ctor_expr_for_class(token, class_idx)
                if ce:
                    exprs.append(ce)
                    typed_hit = True
                    break
        if typed_hit:
            continue

        if (a.arg or "").lower() == "book" or re.search(r"\bBook\b", ann_s):
            ce = _ctor_expr_for_class("Book", class_idx)
            if ce:
                exprs.append(ce)
                continue
            exprs.append("None")
            continue

        if (a.arg or "").lower() in ("books", "book_list") and (
            "list" in ann_s.lower() or "sequence" in ann_s.lower() or not ann_s.strip()
        ):
            ce = _ctor_expr_for_class("Book", class_idx)
            if ce:
                exprs.append(f"[{ce}]")
                continue

        rule = pick_rule_for_param(a.arg, kb)
        if rule and "valid" in rule:
            exprs.append(str(rule["valid"]))
        else:
            exprs.append(_value_expr_for_param(a.arg, ann, allow_none=allow_none))
    joined = f"{t.module}.{t.call_expr}({', '.join(exprs)})"
    needs_tmp = TMP_PATH_PLACEHOLDER in joined
    joined = joined.replace(TMP_PATH_PLACEHOLDER, "tmp_path / 'pta_task_store.json'")
    return joined, needs_tmp


def _build_call_args(fn: ast.FunctionDef, *, drop_first: bool) -> str:
    # Только обязательные позиционные аргументы
    args = list(fn.args.args or [])
    if drop_first and args:
        args = args[1:]
    defaults = list(fn.args.defaults or [])
    required_count = max(0, len(args) - len(defaults))
    required_args = args[:required_count]

    pieces: List[str] = []
    for a in required_args:
        ann = _annotation_text(a.annotation)
        allow_none = _is_optional_annotation(ann)
        pieces.append(_value_expr_for_param(a.arg, ann, allow_none=allow_none))
    return ", ".join(pieces)


def generate_recipe_tests(code: str, metrics: Dict, config: Dict, framework: str) -> str:
    """
    Детерминированная генерация: разбор блоков # File: ... и smoke-тесты для публичных callable.
    """
    _ = metrics
    framework = (framework or "pytest").strip().lower()
    include_edge_cases = bool(config.get("include_edge_cases", True))

    if framework != "pytest":
        return ""

    kb = load_param_kb()
    recipes = _load_recipe_profile(code)
    class_idx = _index_class_constructors(code)
    targets = [t for t in _collect_targets(code) if t.module]
    targets = targets[:80]

    needs_tmp_any = any(_format_call(t, kb, None, class_idx)[1] for t in targets)

    modules = sorted({t.module for t in targets if t.module})
    needs_ns = any(t.qualname.lower() == "format_book_list" for t in targets)
    needs_patch = any(t.uses_datetime_now for t in targets)

    out: List[str] = []
    out.append("# --- Тесты из рецептов (AST, детерминированно; без LLM в этом блоке) ---")
    out.append(f"# Профиль рецептов: {recipes.get('profile', 'generic')!r}")
    out.append("# База знаний: analyzer.utils.recipe_kb.json + analyzer.utils.recipes/*.json")
    out.append("")
    out.append("import pytest")
    out.append("")
    if needs_patch:
        out.append("from datetime import datetime")
        out.append("from unittest.mock import patch")
        out.append("")
    if needs_ns:
        out.append("from types import SimpleNamespace")
        out.append("")
    for mod in modules:
        out.append(f"import {mod}")
    out.append("")

    def _fc(t: _CallableTarget, forced: Optional[str]) -> Tuple[str, bool]:
        return _format_call(t, kb, forced, class_idx)

    def _first_required_arg_meta(t: _CallableTarget) -> Tuple[Optional[str], str, str]:
        args = list(t.args or [])
        if t.kind == "method" and args:
            args = args[1:]
        defaults = list(t.defaults or [])
        required_count = max(0, len(args) - len(defaults))
        if required_count < 1:
            return None, "", ""
        a0 = args[0]
        name = a0.arg
        ann = _annotation_text(a0.annotation)
        return name, ann, (name or "").lower()

    def _match_callable_rules(t: _CallableTarget) -> List[str]:
        names = [t.qualname.lower(), t.call_expr.lower()]
        adds: List[str] = []
        for rule in recipes.get("callable_rules", []) or []:
            rgx = rule.get("qualname_regex") or ""
            try:
                if any(re.search(rgx, n) for n in names):
                    adds.extend(rule.get("adds_tests") or [])
            except re.error:
                continue
        # убираем дубликаты, порядок сохраняем
        seen = set()
        matched: List[str] = []
        for a in adds:
            if a and a not in seen:
                seen.add(a)
                matched.append(a)
        return matched

    def _forced_first_arg_from_template(tpl: Dict, a0_name: str, a0_ann: str, a0_low: str) -> Optional[str]:
        if "force_first_arg" in tpl:
            return str(tpl["force_first_arg"])
        for pair in (tpl.get("force_first_arg_if_ann_contains_any") or []):
            try:
                needle, val = pair
            except Exception:
                continue
            if needle and needle in (a0_ann or ""):
                return str(val)
        for pair in (tpl.get("force_first_arg_if_name_regex") or []):
            try:
                rgx, val = pair
            except Exception:
                continue
            try:
                if rgx and re.search(rgx, a0_low):
                    return str(val)
            except re.error:
                continue
        return None

    for t in targets:
        safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", t.qualname).strip("_").lower()
        if not safe_name:
            continue
        # пустая строка между разными целями — удобнее читать отчёт

        raw_expr, needs_tmp = _fc(t, None)
        assert_ln = _smoke_assert_line(t.returns_ann, returns_value=t.returns_value)
        stmt = f"result = {raw_expr}" if t.returns_value else raw_expr

        sig = f"def test_{safe_name}_smoke(tmp_path):" if needs_tmp else f"def test_{safe_name}_smoke():"
        out.append(sig)
        out.append('    """Дымовой тест: стабильные значения по умолчанию и хотя бы одна осмысленная проверка."""')
        if t.uses_datetime_now:
            out.append(f"    with patch('{t.module}.datetime') as _p_dt:")
            out.append("        _p_dt.now.return_value = datetime(2030, 6, 1, 12, 0, 0)")
            out.append("        " + stmt)
            out.append("        " + assert_ln)
        else:
            out.append("    " + stmt)
            out.append("    " + assert_ln)
        out.append("")

        if include_edge_cases:
            # Граничный случай: только при include_edge_cases и уверенном правиле
            args = list(t.args or [])
            if t.kind == "method" and args:
                args = args[1:]
            defaults = list(t.defaults or [])
            required_count = max(0, len(args) - len(defaults))
            if required_count >= 1:
                a0 = args[0]
                n0 = (a0.arg or "").lower()
                ann0 = _annotation_text(a0.annotation)
                rule0 = pick_rule_for_param(a0.arg, kb)
                if "str" in ann0 or "name" in n0 or "text" in n0:
                    es = "''"
                    er = _edge_raises_for_value(rule0, es) if rule0 else None
                    if er and (t.qualname.lower().startswith("parse_") or (rule0 and rule0.get("edge_raises_any_of"))):
                        fc_es, tmp_es = _fc(t, es)
                        sig_es = "(tmp_path)" if tmp_es else "()"
                        out.append(f"def test_{safe_name}_edge_empty_string{sig_es}:")
                        out.append(f'    """Граничный случай: пустая строка -> {er} (KB / parse_*)."""')
                        out.append(f"    with pytest.raises({er}):")
                        out.append(f"        {fc_es}")
                        out.append("")
                    elif t.qualname.lower().startswith("validate_"):
                        fc_es, tmp_es = _fc(t, es)
                        sig_es = "(tmp_path)" if tmp_es else "()"
                        out.append(f"def test_{safe_name}_edge_empty_string{sig_es}:")
                        out.append('    """Граничный случай: пустая строка; валидаторы обычно возвращают bool."""')
                        out.append(f"    result = {fc_es}")
                        out.append("    assert isinstance(result, bool)")
                        out.append("    assert result is False")
                        out.append("")
                    else:
                        fc_es, tmp_es = _fc(t, es)
                        sig_es = "(tmp_path)" if tmp_es else "()"
                        out.append(f"def test_{safe_name}_edge_empty_string{sig_es}:")
                        out.append('    """Граничный случай: пустая строка (поведение зависит от проекта)."""')
                        out.append(f"    _ = {fc_es}")
                        out.append("")
                elif _ann_looks_collection(ann0) or n0.endswith("s") or "items" in n0:
                    fc_el, tmp_el = _fc(t, "[]")
                    sig_el = "(tmp_path)" if tmp_el else "()"
                    out.append(f"def test_{safe_name}_edge_empty_list{sig_el}:")
                    out.append('    """Граничный случай: пустая коллекция в качестве первого аргумента."""')
                    out.append(f"    result = {fc_el}")
                    out.append("    " + _smoke_assert_line(t.returns_ann, returns_value=t.returns_value))
                    out.append("")
                elif rule0 and str(rule0.get("edge") or "").strip():
                    fc_ed, tmp_ed = _fc(t, str(rule0["edge"]))
                    sig_ed = "(tmp_path)" if tmp_ed else "()"
                    out.append(f"def test_{safe_name}_edge_rule{sig_ed}:")
                    out.append('    """Граничный случай: значение edge из правила KB."""')
                    out.append(f"    result = {fc_ed}")
                    out.append("    " + _smoke_assert_line(t.returns_ann, returns_value=t.returns_value))
                    out.append("")

                # Негативный тест — только если в коде реально объявлено исключение
                exc = prefer_exception(t.raises, kb)
                if rule0 and str(rule0.get("invalid") or "").strip() and exc:
                    allowed = rule0.get("invalid_raises_any_of") or []
                    if (not allowed) or (exc in allowed):
                        fc_iv, tmp_iv = _fc(t, str(rule0["invalid"]))
                        sig_iv = "(tmp_path)" if tmp_iv else "()"
                        out.append(f"def test_{safe_name}_invalid_raises_{exc.lower()}{sig_iv}:")
                        out.append('    """Негативный сценарий: некорректное значение по правилу должно вызывать исключение (если реализовано)."""')
                        out.append(f"    with pytest.raises({exc}):")
                        out.append(f"        {fc_iv}")
                        out.append("")

            # Шаблоны рецептов (детерминированно, по возможности)
            template_ids = _match_callable_rules(t)
            if template_ids:
                a0_name, a0_ann, a0_low = _first_required_arg_meta(t)
                req_args = _required_args_slice(t)
                templates = recipes.get("test_templates", {}) or {}
                for tid in template_ids:
                    tpl = templates.get(tid) or {}
                    if not tpl:
                        continue
                    if tpl.get("roundtrip"):
                        continue
                    doc = tpl.get("doc") or f"Рецепт: {tid}"

                    if tpl.get("requires_first_arg") and len(req_args) < 1:
                        continue
                    if tpl.get("requires_first_ann_numeric") and not _ann_looks_numeric(a0_ann):
                        continue

                    # Нужно, чтобы исключение существовало в исходниках
                    req_exc = tpl.get("requires_exception")
                    if req_exc and (req_exc not in (t.raises or [])) and not any(r.endswith("." + req_exc) for r in (t.raises or [])):
                        continue

                    forced0 = None
                    if a0_name:
                        forced0 = _forced_first_arg_from_template(tpl, a0_name, a0_ann, a0_low)

                    if tid == "empty_input_for_str_or_list" and forced0 == "''" and t.qualname.lower().startswith("parse_"):
                        continue

                    if forced0 is None and (
                        tpl.get("force_first_arg_if_name_regex")
                        or tpl.get("force_first_arg_if_ann_contains_any")
                        or "force_first_arg" in tpl
                    ):
                        continue

                    if tpl.get("raises") and forced0 is None:
                        continue

                    call_expr, tmp_tpl = _fc(t, forced0)

                    fn_name = f"test_{safe_name}_recipe_{tid}"
                    fn_name = re.sub(r"__+", "_", fn_name)
                    sig_tpl = "(tmp_path)" if tmp_tpl else "()"
                    out.append(f"def {fn_name}{sig_tpl}:")
                    out.append(f'    """{doc}"""')

                    if tpl.get("raises"):
                        ex = str(tpl["raises"])
                        out.append(f"    with pytest.raises({ex}):")
                        out.append(f"        {call_expr}")
                        out.append("")
                        continue

                    if tid == "falsey_input_if_str" and forced0 == "''" and t.qualname.lower().startswith("validate_"):
                        out.append(f"    result = {call_expr}")
                        out.append("    assert isinstance(result, bool)")
                        out.append("    assert result is False")
                        out.append("")
                        continue
                    out.append(f"    result = {call_expr}")
                    if tpl.get("repeat_call"):
                        out.append(f"    _ = {call_expr}")
                    for line in (tpl.get("body_lines") or ["assert True"]):
                        out.append(f"    {line}")
                    out.append("")

    out.append("# Сгенерировано Python Test Gen")
    return "\n".join(out).replace("\ufeff", "").replace("\u200b", "").strip()

