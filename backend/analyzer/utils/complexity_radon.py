"""
Цикломатическая сложность через radon cc (мягкие агрегаты для метрик и рекомендаций).
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def summarize_radon_static_tools(radon_per_file: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    radon_per_file: имя файла -> {"avg": float, "max": int} (как в docker _analyzer).
    """
    if not radon_per_file:
        return None

    rows: List[tuple] = []
    all_avgs: List[float] = []
    for fn, d in radon_per_file.items():
        if not isinstance(d, dict):
            continue
        mx = int(d.get("max") or 0)
        av = float(d.get("avg") or 0)
        rows.append((str(fn), mx, av))
        all_avgs.append(av)

    if not rows:
        return None

    rows.sort(key=lambda x: -x[1])
    overall_max = rows[0][1]
    overall_avg = round(sum(all_avgs) / len(all_avgs), 2) if all_avgs else 0.0

    return {
        "tool": "radon",
        "metric": "cyclomatic_complexity",
        "files_sampled": len(rows),
        "max": overall_max,
        "avg": overall_avg,
        "hottest_files": [{"path": fn, "max_complexity": mx} for fn, mx, _ in rows[:5]],
        "hint": (
            "Оценка по ограниченной выборке .py файлов (инструмент radon cc). "
            "Имеет ориентировочный характер."
        ),
    }


def collect_radon_sample(paths: List[str], *, limit: int = 20) -> Dict[str, Dict[str, Any]]:
    """Запускает radon cc --json по первым limit путям (локально, fallback анализ)."""
    out: Dict[str, Dict[str, Any]] = {}
    for fp in paths[:limit]:
        try:
            r = subprocess.run(
                ["radon", "cc", fp, "--json"],
                capture_output=True,
                text=True,
                timeout=25,
            )
            if r.returncode != 0 or not (r.stdout or "").strip():
                continue
            data = json.loads(r.stdout)
            fn = fp.replace("\\", "/")
            if fn not in data:
                continue
            methods = data[fn].get("methods") if isinstance(data[fn], dict) else None
            if not isinstance(methods, list):
                continue
            cx = [int(x.get("complexity", 0)) for x in methods if isinstance(x, dict)]
            if not cx:
                continue
            rel = fn.split("/")[-1] if "/" in fn else fn
            out[rel] = {
                "avg": round(sum(cx) / len(cx), 2),
                "max": max(cx),
            }
        except Exception as e:
            logger.debug("radon skip %s: %s", fp, e)
            continue
    return out


def merge_complexity_into_metrics(metrics: Dict[str, Any], static_tools: Optional[Dict[str, Any]]) -> None:
    """Дополняет metrics полем cyclomatic_complexity из результатов Docker static_tools."""
    if not isinstance(metrics, dict):
        return
    if not static_tools or not isinstance(static_tools, dict):
        return
    radon = static_tools.get("radon")
    if not isinstance(radon, dict) or not radon:
        return
    summary = summarize_radon_static_tools(radon)
    if summary:
        metrics["cyclomatic_complexity"] = summary


def extend_recommendations_with_complexity(recs: List[str], summary: Optional[Dict[str, Any]]) -> None:
    """Добавляет одну мягкую строку про цикломатическую сложность."""
    if not summary:
        return
    max_c = int(summary.get("max") or 0)
    avg_c = float(summary.get("avg") or 0)
    n = int(summary.get("files_sampled") or 0)

    if max_c <= 0 and avg_c <= 0:
        return

    if max_c <= 10 and avg_c <= 7:
        msg = (
            f"ℹ️ Цикломатическая сложность (radon cc, выборка из {n} файлов): "
            f"максимум≈{max_c}, среднее по файлам≈{avg_c:.1f}. Для юнит-тестов обычно комфортный уровень."
        )
    elif max_c <= 15:
        msg = (
            f"ℹ️ Есть заметно разветвлённые функции (max≈{max_c}, среднее≈{avg_c:.1f}). "
            "Упрощение условий или разбиение функций обычно упрощает покрытие юнит-тестами."
        )
    else:
        msg = (
            f"⚠️ Высокая цикломатическая сложность в части кода (max≈{max_c}). "
            "Имеет смысл выделить чистые функции и сократить ветвление — так проще писать изолированные юнит-тесты."
        )

    recs[:] = [
        x
        for x in recs
        if x != "⚠️ Высокая цикломатическая сложность (radon)"
        and "Высокая цикломатическая сложность (radon)" not in x
    ]

    if any("Цикломатическая сложность (radon cc" in x for x in recs):
        return
    recs.append(msg)
