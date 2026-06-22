"""
Эвристики для статического сканирования issues (без зависимостей от Django).

print: не душим консольные примеры (агрегируем), но оставляем смысл «для production — logging».
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple


def _const_str(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Str):
        return node.s
    return None


def _is_dunder_main_test(test: ast.AST) -> bool:
    if isinstance(test, ast.Compare):
        if isinstance(test.left, ast.Name) and test.left.id == "__name__":
            if len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1:
                return _const_str(test.comparators[0]) == "__main__"
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And) and test.values:
        return _is_dunder_main_test(test.values[0])
    return False


def _lines_covered_by_subtree(root: ast.AST) -> Set[int]:
    s: Set[int] = set()
    for n in ast.walk(root):
        ln = getattr(n, "lineno", None)
        en = getattr(n, "end_lineno", None) or ln
        if isinstance(ln, int) and ln > 0 and isinstance(en, int) and en >= ln:
            s.update(range(ln, en + 1))
    return s


def dunder_main_guard_lines(tree: ast.Module) -> Set[int]:
    covered: Set[int] = set()
    for stmt in tree.body:
        if not isinstance(stmt, ast.If):
            continue
        if not _is_dunder_main_test(stmt.test):
            continue
        for st in stmt.body:
            covered |= _lines_covered_by_subtree(st)
    return covered


def count_print_calls(tree: ast.AST) -> int:
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            n += 1
    return n


def module_uses_cli_framework(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                base = (a.name or "").split(".")[0]
                if base in ("argparse", "click", "typer"):
                    return True
        if isinstance(node, ast.ImportFrom) and node.module:
            base = node.module.split(".")[0]
            if base in ("argparse", "click", "typer"):
                return True
    return False


def looks_like_cli_entry_filename(rel: str) -> bool:
    name = Path(rel).name.lower()
    return name in (
        "main.py",
        "__main__.py",
        "cli.py",
        "run.py",
        "app.py",
        "console.py",
        "demo.py",
    ) or name.startswith("demo_") or name.endswith("_demo.py")


def _print_console_aggregate_mode(tree: ast.Module, rel_path: str) -> bool:
    thr_raw = (os.environ.get("AI_ISSUES_PRINT_CONSOLE_MIN") or "3").strip()
    try:
        thr = max(2, int(thr_raw))
    except ValueError:
        thr = 3
    if count_print_calls(tree) >= thr:
        return True
    if module_uses_cli_framework(tree):
        return True
    if looks_like_cli_entry_filename(rel_path):
        return True
    return False


def _first_print_line(tree: ast.AST) -> Optional[int]:
    best: Optional[int] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            ln = getattr(node, "lineno", None)
            if isinstance(ln, int) and ln > 0:
                best = ln if best is None else min(best, ln)
    return best


def collect_print_related_issues(
    tree: ast.Module,
    rel_path: str,
    excerpt: Callable[[int], str],
) -> List[Dict]:
    """
    - «Консольный» файл: одно сводное замечание про production/logging.
    - Обычный файл: print_debug на каждый print вне if __name__ == '__main__'.
    """
    out: List[Dict] = []
    pc = count_print_calls(tree)
    if pc == 0:
        return out

    main_ln = dunder_main_guard_lines(tree)

    if _print_console_aggregate_mode(tree, rel_path):
        ln = _first_print_line(tree) or 1
        ex = (excerpt(ln) or "").strip()[:240]
        out.append(
            {
                "path": rel_path,
                "line": ln,
                "rule_id": "print_production_hint",
                "message": (
                    "В файле несколько print() или признаки CLI/консольного примера. "
                    "Для production обычно выделяют слой вывода или переходят на logging; "
                    "оставляйте print, только если это часть UX консольного приложения."
                ),
                "excerpt": ex,
            }
        )
        return out

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "print":
            continue
        ln = getattr(node, "lineno", None)
        if not isinstance(ln, int) or ln < 1:
            continue
        if ln in main_ln:
            continue
        ex = (excerpt(ln) or "").strip()[:240]
        out.append(
            {
                "path": rel_path,
                "line": ln,
                "rule_id": "print_debug",
                "message": "В коде оставлен print() (в production-сборке обычно лучше logging или отдельный слой вывода)",
                "excerpt": ex,
            }
        )
    return out


def _is_re_compile_call(node: ast.Call) -> bool:
    """re.compile(...) — частый анти-паттерн внутри функции (PERF203)."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "compile":
        if isinstance(func.value, ast.Name) and func.value.id == "re":
            return True
    return False


def _is_datetime_now_call(node: ast.AST) -> bool:
    """datetime.now() или datetime.datetime.now() (упрощённо)."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "now":
        return False
    v = node.func.value
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


def _expr_has_lower_call(expr: ast.AST) -> bool:
    for n in ast.walk(expr):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "lower":
            return True
    return False


def _const_numeric(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return True
    if isinstance(node, ast.Num):  # py<3.8
        return True
    return False


def _const_stringish(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.Str):
        return True
    if isinstance(node, ast.JoinedStr):
        return True
    return False


def _expr_involves_string_literal(node: ast.AST) -> bool:
    """Есть ли в выражении строковый литерал или f-string (типично для склейки)."""
    if _const_stringish(node):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _expr_involves_string_literal(node.left) or _expr_involves_string_literal(node.right)
    return False


# Мягкий фильтр для `buf += line` без литералов в правой части
_STR_ACCUM_NAMES = frozenset(
    {"result", "out", "buf", "text", "body", "chunk", "lines", "content", "output", "acc", "sb", "s"}
)


def _for_range_call(for_node: ast.For) -> Optional[ast.Call]:
    it = for_node.iter
    if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range":
        return it
    return None


def collect_performance_hint_issues(
    tree: ast.Module,
    rel_path: str,
    excerpt: Callable[[int], str],
) -> List[Dict]:
    """
    Мягкие эвристики производительности / стиля (без ruff, AST-only).
    """
    out: List[Dict] = []
    seen: Set[tuple] = set()

    def add(rule_id: str, line: int, message: str) -> None:
        if not isinstance(line, int) or line < 1:
            return
        key = (rule_id, line)
        if key in seen:
            return
        seen.add(key)
        ex = (excerpt(line) or "").strip()[:240]
        out.append({"path": rel_path, "line": line, "rule_id": rule_id, "message": message, "excerpt": ex})

    # --- re.compile внутри функции (не на уровне модуля) ---
    func_depth = 0

    class _ReCompileVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nonlocal func_depth
            func_depth += 1
            self.generic_visit(node)
            func_depth -= 1

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            nonlocal func_depth
            func_depth += 1
            self.generic_visit(node)
            func_depth -= 1

        def visit_Call(self, node: ast.Call) -> None:
            if func_depth > 0 and _is_re_compile_call(node):
                ln = getattr(node, "lineno", None)
                if isinstance(ln, int) and ln > 0:
                    add(
                        "regex_recompile",
                        ln,
                        "re.compile() внутри функции вызывается при каждом входе — вынесите на уровень модуля "
                        "или кэшируйте скомпилированный шаблон (снижает нагрузку на CPU при частых вызовах).",
                    )
            self.generic_visit(node)

    _ReCompileVisitor().visit(tree)

    # --- циклы: += строк, «пустой» range-накопитель, datetime.now(), case in title-like ---
    title_like = frozenset(
        {"title", "name", "author", "authors", "description", "text", "subject", "keyword", "label", "headline"}
    )

    def scan_loop_body(loop: ast.AST) -> None:
        body: List[ast.stmt] = []
        if isinstance(loop, (ast.For, ast.While)):
            body = list(loop.body)
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call) and _is_datetime_now_call(sub):
                    ln = getattr(sub, "lineno", None)
                    if isinstance(ln, int) and ln > 0:
                        add(
                            "datetime_now_in_loop",
                            ln,
                            "datetime.now() внутри цикла: системный вызов на каждой итерации; вынесите время "
                            "до цикла или используйте одно значение для согласованности.",
                        )

        if isinstance(loop, ast.For):
            if _for_range_call(loop) is not None and len(loop.body) == 1:
                only = loop.body[0]
                if (
                    isinstance(only, ast.AugAssign)
                    and isinstance(only.op, ast.Add)
                    and _const_numeric(only.value)
                ):
                    ln = getattr(only, "lineno", None) or getattr(loop, "lineno", 1)
                    if isinstance(ln, int) and ln > 0:
                        add(
                            "redundant_loop_accumulator",
                            ln,
                            "Накопление константы в цикле `for _ in range(...): x += k` можно заменить на "
                            "арифметику (меньше итераций, яснее намерение).",
                        )
            for stmt in loop.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, ast.Add):
                    val = stmt.value
                    if not _const_numeric(val):
                        tgt = stmt.target
                        tgt_name = tgt.id if isinstance(tgt, ast.Name) else None
                        looks_str = _expr_involves_string_literal(val) or (
                            isinstance(val, ast.Name) and tgt_name in _STR_ACCUM_NAMES
                        )
                        if looks_str:
                            ln = getattr(stmt, "lineno", None)
                            if isinstance(ln, int) and ln > 0:
                                add(
                                    "loop_string_concat",
                                    ln,
                                    "Склеивание строк через += в цикле даёт квадратичные копирования; "
                                    "рассмотрите list + join(), io.StringIO или f-строки по частям.",
                                )

    class _LoopVisitor(ast.NodeVisitor):
        def visit_For(self, node: ast.For) -> None:
            scan_loop_body(node)
            self.generic_visit(node)

        def visit_While(self, node: ast.While) -> None:
            scan_loop_body(node)
            self.generic_visit(node)

        def visit_Compare(self, node: ast.Compare) -> None:
            for i, op in enumerate(node.ops):
                if not isinstance(op, ast.In):
                    continue
                left = node.left
                comp = node.comparators[i] if i < len(node.comparators) else None
                if comp is None:
                    continue
                if _expr_has_lower_call(left) or _expr_has_lower_call(comp):
                    continue
                if isinstance(comp, ast.Attribute) and comp.attr.lower() in title_like:
                    ln = getattr(node, "lineno", None)
                    if isinstance(ln, int) and ln > 0:
                        add(
                            "case_sensitive_membership",
                            ln,
                            "Проверка вхождения без нормализации регистра: для пользовательского текста "
                            "часто нужны .lower() / casefold() или re.IGNORECASE.",
                        )
            self.generic_visit(node)

    _LoopVisitor().visit(tree)

    return out


def _is_open_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Name) and node.func.id == "open"


def _is_sleep_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "sleep":
        if isinstance(func.value, ast.Name) and func.value.id == "time":
            return True
    if isinstance(func, ast.Name) and func.id == "sleep":
        return True
    return False


def collect_resource_issues(
    tree: ast.Module,
    rel_path: str,
    excerpt: Callable[[int], str],
) -> List[Dict]:
    """
    open() без with и time.sleep() вне CLI/__main__ (эвристики).
    """
    out: List[Dict] = []
    seen: Set[Tuple[str, int]] = set()
    main_ln = dunder_main_guard_lines(tree)
    with_depth = 0

    class _Visitor(ast.NodeVisitor):
        def visit_With(self, node: ast.With) -> None:
            nonlocal with_depth
            with_depth += 1
            self.generic_visit(node)
            with_depth -= 1

        def visit_Call(self, node: ast.Call) -> None:
            ln = getattr(node, "lineno", None)
            if not isinstance(ln, int) or ln < 1:
                self.generic_visit(node)
                return
            if ln in main_ln:
                self.generic_visit(node)
                return

            if with_depth == 0 and _is_open_call(node):
                key = ("open_without_with", ln)
                if key not in seen:
                    seen.add(key)
                    ex = (excerpt(ln) or "").strip()[:240]
                    out.append(
                        {
                            "path": rel_path,
                            "line": ln,
                            "rule_id": "open_without_with",
                            "message": "open() без `with` — файл может не закрыться при исключении; используйте контекстный менеджер.",
                            "excerpt": ex,
                        }
                    )

            if _is_sleep_call(node):
                key = ("sleep_in_code", ln)
                if key not in seen:
                    seen.add(key)
                    ex = (excerpt(ln) or "").strip()[:240]
                    out.append(
                        {
                            "path": rel_path,
                            "line": ln,
                            "rule_id": "sleep_in_code",
                            "message": "time.sleep() в коде — для тестов используйте mock/patch; в сервисах — async/таймауты.",
                            "excerpt": ex,
                        }
                    )

            self.generic_visit(node)

    _Visitor().visit(tree)
    return out


def collect_duplicate_self_call_hints(
    tree: ast.Module,
    rel_path: str,
    excerpt: Callable[[int], str],
) -> List[Dict]:
    """
    Один и тот же `self.method()` вызывается несколько раз в одной функции —
    часто можно сохранить результат в переменную (проще и иногда быстрее).
    """
    out: List[Dict] = []
    seen: Set[Tuple[str, str, int]] = set()
    skip_attr = frozenset(
        {
            "append",
            "add",
            "extend",
            "insert",
            "remove",
            "discard",
            "clear",
            "update",
            "write",
            "read",
            "readline",
            "readlines",
            "close",
            "flush",
            "acquire",
            "release",
            "send",
            "commit",
            "execute",
            "fetchone",
            "fetchall",
        }
    )
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name or node.name.startswith("_"):
            continue
        by_attr: Dict[str, List[int]] = {}
        for sub in ast.walk(node):
            if sub is node:
                continue
            if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Attribute):
                continue
            if not isinstance(sub.func.value, ast.Name) or sub.func.value.id != "self":
                continue
            att = sub.func.attr
            if att.startswith("_") or att in skip_attr:
                continue
            ln = getattr(sub, "lineno", None)
            if not isinstance(ln, int) or ln < 1:
                continue
            by_attr.setdefault(att, []).append(ln)
        for att, lines in sorted(by_attr.items()):
            uniq = sorted(set(lines))
            if len(uniq) < 2:
                continue
            second_ln = uniq[1]
            key = (node.name, att, second_ln)
            if key in seen:
                continue
            seen.add(key)
            ex = (excerpt(second_ln) or "").strip()[:240]
            out.append(
                {
                    "path": rel_path,
                    "line": second_ln,
                    "rule_id": "duplicate_self_method_call",
                    "message": (
                        f"Повторный вызов `self.{att}()` в `{node.name}`: рассмотрите кэш результата "
                        f"в локальной переменной."
                    ),
                    "excerpt": ex,
                }
            )
    return out
