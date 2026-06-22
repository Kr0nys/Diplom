"""
Авто-извлечение нейтрального «контракта» из исходников для промпта LLM.
Не зависит от домена (finance и т.д.): только AST проекта.
"""

from __future__ import annotations

import ast
import re
from typing import List, Optional, Set, Tuple

_FILE_HDR = re.compile(r"^\s*# File:\s+(.+?)\s*$", re.MULTILINE)


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


def _raises_in_body(body: List[ast.stmt]) -> Set[str]:
    found: Set[str] = set()
    for st in body:
        for n in ast.walk(st):
            if isinstance(n, ast.Raise) and n.exc:
                h = _exc_hint(n.exc)
                if h:
                    found.add(h)
    return found


def _format_args(args: ast.arguments, unparse) -> str:
    parts: List[str] = []
    posonly = getattr(args, "posonlyargs", []) or []
    for a in posonly:
        parts.append(a.arg)
    if posonly:
        parts.append("/")
    for a in args.args:
        parts.append(a.arg)
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        parts.append("*")
    for a in args.kwonlyargs:
        parts.append(a.arg)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return ", ".join(parts)


def _sig_line(name: str, node: ast.FunctionDef | ast.AsyncFunctionDef, unparse) -> str:
    pref = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    args_s = _format_args(node.args, unparse)
    ret = ""
    if node.returns:
        try:
            ret = " -> " + unparse(node.returns)
        except Exception:
            ret = ""
    return f"{pref}{name}({args_s}){ret}"


def build_contract_digest(raw: str, max_chars: int = 4500) -> str:
    """
    Краткий Markdown: сигнатуры публичных функций/методов и типичные raise в теле.
    """
    unparse = getattr(ast, "unparse", None)
    if not unparse:
        return ""

    lines: List[str] = []
    total = 0
    chunks = _split_source_files(raw)

    for path, src in chunks:
        if total >= max_chars:
            break
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            lines.append(f"- `{path}`: (parse error: {e})")
            total += 80
            continue

        block: List[str] = [f"### `{path}`"]

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                try:
                    sig = _sig_line(node.name, node, unparse)
                except Exception:
                    sig = f"def {node.name}(...)"
                rs = sorted(_raises_in_body(node.body))
                rs_s = f" | raises: {', '.join(rs)}" if rs else ""
                line = f"- `{sig}`{rs_s}"
                block.append(line)
                total += len(line) + 1
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                block.append(f"- class `{node.name}`:")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                        try:
                            sig = _sig_line(item.name, item, unparse)
                        except Exception:
                            sig = f"def {item.name}(...)"
                        rs = sorted(_raises_in_body(item.body))
                        rs_s = f" | raises: {', '.join(rs)}" if rs else ""
                        line = f"  - `{sig}`{rs_s}"
                        block.append(line)
                        total += len(line) + 1
            if total >= max_chars:
                break

        if len(block) > 1:
            lines.extend(block)
            lines.append("")

    out = "\n".join(lines).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 20] + "\n… (truncated)"
    return out
