"""
Древо каталогов загруженного проекта (архив или набор файлов) для UI.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "env",
        "node_modules",
        ".idea",
        ".vscode",
        "dist",
        "build",
        "site-packages",
        "__MACOSX",
    }
)

IGNORED_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db"})


def _should_skip_part(part: str) -> bool:
    return part.lower() in IGNORED_DIR_NAMES or part.startswith(".")


def _insert_path(root: Dict[str, Any], rel_path: str, *, size: Optional[int] = None) -> None:
    rel = (rel_path or "").replace("\\", "/").strip("/")
    if not rel or ".." in PurePosixPath(rel).parts:
        return
    parts = PurePosixPath(rel).parts
    if any(_should_skip_part(p) for p in parts):
        return

    node = root
    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1
        children: List[Dict[str, Any]] = node.setdefault("children", [])
        sub_path = "/".join(parts[: i + 1])

        if is_last:
            existing = next((c for c in children if c.get("name") == part and c.get("type") == "file"), None)
            if existing:
                if size is not None:
                    existing["size"] = size
                return
            children.append(
                {
                    "name": part,
                    "type": "file",
                    "path": sub_path,
                    **({"size": size} if size is not None else {}),
                }
            )
            children.sort(key=_sort_key)
            return

        folder = next((c for c in children if c.get("name") == part and c.get("type") == "folder"), None)
        if not folder:
            folder = {"name": part, "type": "folder", "path": sub_path, "children": []}
            children.append(folder)
            children.sort(key=_sort_key)
        node = folder


def _sort_key(node: Dict[str, Any]) -> Tuple[int, str]:
    return (0 if node.get("type") == "folder" else 1, (node.get("name") or "").lower())


def _finalize_root(name: str = "project") -> Dict[str, Any]:
    return {"name": name, "type": "folder", "path": "", "children": []}


def build_tree_from_paths(entries: List[Tuple[str, Optional[int]]], *, root_name: str = "upload") -> Dict[str, Any]:
    """entries: [(relative_or_flat_name, size_bytes|None), ...]"""
    root = _finalize_root(root_name)
    for rel, size in entries:
        _insert_path(root, rel, size=size)
    return root


def build_tree_from_directory(root_dir: str | Path, *, max_files: int = 1200, root_name: str = "project") -> Dict[str, Any]:
    base = Path(root_dir).resolve()
    root = _finalize_root(root_name or base.name or "project")
    count = 0

    for fp in sorted(base.rglob("*")):
        if count >= max_files:
            break
        if not fp.is_file():
            continue
        if fp.name in IGNORED_FILE_NAMES:
            continue
        try:
            rel = fp.relative_to(base).as_posix()
        except ValueError:
            continue
        parts = PurePosixPath(rel).parts
        if any(_should_skip_part(p) for p in parts):
            continue
        try:
            size = fp.stat().st_size
        except OSError:
            size = None
        _insert_path(root, rel, size=size)
        count += 1

    return root


def build_tree_from_archive(archive_path: str | Path, *, max_files: int = 1200) -> Dict[str, Any]:
    ap = Path(archive_path)
    name_l = ap.name.lower()
    root = _finalize_root(ap.stem or "archive")
    count = 0

    def add_member(member_path: str, size: Optional[int]) -> None:
        nonlocal count
        p = member_path.replace("\\", "/").strip("/")
        if not p or p.endswith("/"):
            return
        if count >= max_files:
            return
        _insert_path(root, p, size=size)
        count += 1

    if name_l.endswith(".zip"):
        with zipfile.ZipFile(ap, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                add_member(info.filename, info.file_size)
    elif name_l.endswith(".tar") or name_l.endswith(".tar.gz") or name_l.endswith(".tgz"):
        mode = "r:gz" if (name_l.endswith(".tar.gz") or name_l.endswith(".tgz")) else "r"
        with tarfile.open(ap, mode) as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                add_member(member.name, member.size)
    else:
        return root

    return root


def build_tree_for_session(*, upload_mode: str, archive_path: Optional[str], uploaded_entries: List[Tuple[str, Optional[int]]], project_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        if project_dir:
            return build_tree_from_directory(project_dir)
        if upload_mode in ("ARCHIVE", "GITHUB") and archive_path:
            return build_tree_from_archive(archive_path)
        if uploaded_entries:
            return build_tree_from_paths(uploaded_entries)
    except Exception:
        return None
    return None
