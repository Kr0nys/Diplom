"""Допустимые версии Python для анализа в Docker (FROM python:X-slim)."""

from __future__ import annotations

from typing import FrozenSet, Tuple

ALLOWED_PYTHON_VERSIONS: FrozenSet[str] = frozenset({"3.9", "3.10", "3.11", "3.12"})

DEFAULT_PYTHON_VERSION = "3.9"


def normalize_python_version(value: str, *, default: str = DEFAULT_PYTHON_VERSION) -> str:
    v = str(value or "").strip()
    return v if v in ALLOWED_PYTHON_VERSIONS else default


def python_version_choices() -> Tuple[Tuple[str, str], ...]:
    ordered = sorted(ALLOWED_PYTHON_VERSIONS, key=lambda x: tuple(map(int, x.split("."))))
    return tuple((v, f"Python {v}") for v in ordered)
