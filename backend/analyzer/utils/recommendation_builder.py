"""
Единый набор рекомендаций по метрикам анализа и агрегату issues.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from .complexity_radon import extend_recommendations_with_complexity

# rule_id -> (порог для предупреждения, текст рекомендации)
_ISSUE_RECOMMENDATIONS: List[tuple] = [
    (
        "todo_fixme",
        1,
        "ℹ️ В коде есть TODO/FIXME — зафиксируйте задачи в трекере или закройте перед релизом.",
    ),
    (
        "print_debug",
        3,
        "⚠️ Много вызовов print() — для production рассмотрите модуль logging с уровнями.",
    ),
    (
        "print_production_hint",
        1,
        "ℹ️ print() вне CLI/демо: для сервисного кода обычно уместнее logging.",
    ),
    (
        "mutable_default",
        1,
        "⚠️ Изменяемые default-аргументы (list/dict/set) — типичный источник багов; используйте None и инициализацию в теле.",
    ),
    (
        "too_many_args",
        2,
        "ℹ️ Функции с большим числом параметров сложнее тестировать — рассмотрите dataclass/конфиг-объект.",
    ),
    (
        "long_function",
        3,
        "ℹ️ Длинные функции усложняют покрытие — выделите чистые подфункции с отдельными unit-тестами.",
    ),
    (
        "bare_except",
        1,
        "⚠️ bare except скрывает ошибки — ловите конкретные исключения и при необходимости логируйте.",
    ),
    (
        "except_exception",
        2,
        "ℹ️ Широкий except Exception — сузьте тип, если это не верхний обработчик в entrypoint.",
    ),
    (
        "loop_string_concat",
        1,
        "ℹ️ Склейка строк в цикле — для больших объёмов используйте list.append + join или StringIO.",
    ),
    (
        "regex_recompile",
        1,
        "ℹ️ re.compile() внутри функции — вынесите шаблон на уровень модуля.",
    ),
    (
        "datetime_now_in_loop",
        1,
        "ℹ️ datetime.now() в цикле — вынесите «текущее время» до цикла для согласованности и скорости.",
    ),
    (
        "open_without_with",
        1,
        "⚠️ open() без контекстного менеджера — используйте with open(...) as f.",
    ),
    (
        "sleep_in_code",
        1,
        "ℹ️ time.sleep() в бизнес-логике — вынесите в тесты (mock/patch) или polling с таймаутом.",
    ),
    (
        "duplicate_self_method_call",
        2,
        "ℹ️ Повторные вызовы self.method() — сохраните результат в переменную, если метод не идемпотентен.",
    ),
    (
        "case_sensitive_membership",
        2,
        "ℹ️ Проверка вхождения без нормализации регистра — для пользовательского текста часто нужны .lower() / casefold().",
    ),
    (
        "redundant_loop_accumulator",
        1,
        "ℹ️ Накопление константы в цикле range — замените на арифметику для ясности и скорости.",
    ),
]


def build_analysis_recommendations(
    metrics: Dict[str, Any],
    issues: Optional[List[Dict]] = None,
    *,
    source: str = "analysis",
) -> List[str]:
    """
    Формирует список текстовых рекомендаций для UI.
    """
    recs: List[str] = []
    issues = issues or []

    fn = int(metrics.get("functions_count") or 0)
    cls = int(metrics.get("classes_count") or 0)
    imp = int(metrics.get("imports_count") or 0)
    async_n = int(metrics.get("async_functions") or 0)
    doc_ratio = float(metrics.get("docstring_ratio") or 0)
    files_n = int(metrics.get("files_count") or 0)
    lines_n = int(metrics.get("lines_count") or metrics.get("total_lines") or 0)

    if fn > 80:
        recs.append(
            "⚠️ Очень много публичных функций (>80). Разбейте домен на модули и покройте критичные точки тестами в первую очередь."
        )
    elif fn > 50:
        recs.append("⚠️ Большое количество функций (>50). Рассмотрите рефакторинг и разделение на модули.")

    if imp > 35:
        recs.append("⚠️ Много внешних импортов — проверьте лишние зависимости и точки для mock в unit-тестах.")
    elif imp > 20:
        recs.append("⚠️ Много зависимостей. Проверьте необходимость всех импортов.")

    if async_n > 0 and fn > 0 and async_n < fn:
        recs.append(
            "ℹ️ Смешанный sync/async код — для тестов используйте pytest-asyncio и изолируйте I/O через mock."
        )
    elif async_n > 0 and async_n == fn and fn > 3:
        recs.append("ℹ️ Проект преимущественно async — убедитесь, что тесты await-ят корутины и не блокируют event loop.")

    if doc_ratio < 0.35:
        recs.append(
            "⚠️ Низкое покрытие docstring (<35%). Docstring с Raises/Examples помогает и анализу, и генерации тестов."
        )
    elif doc_ratio < 0.5:
        recs.append("⚠️ Низкое покрытие docstring (<50%). Добавьте документацию к функциям и классам.")

    if cls > 0 and fn > 0 and cls / max(fn, 1) > 0.15:
        recs.append(
            "ℹ️ Много классов относительно функций — для unit-тестов удобны фикстуры экземпляров и mock зависимостей в __init__."
        )

    if files_n > 25 and lines_n > 3000:
        recs.append(
            "ℹ️ Крупный проект по объёму — начните генерацию тестов с модулей с наибольшей бизнес-логикой (без main/CLI)."
        )

    counts = Counter((i.get("rule_id") or "") for i in issues if i.get("rule_id"))
    for rule_id, threshold, msg in _ISSUE_RECOMMENDATIONS:
        if counts.get(rule_id, 0) >= threshold and msg not in recs:
            recs.append(msg)

    issue_n = len(issues)
    if issue_n >= 25:
        recs.append(
            f"ℹ️ Много замечаний статического анализа ({issue_n}) — пройдитесь по вкладке «Рекомендации», "
            "приоритизируйте mutable_default, bare except и производительность в циклах."
        )

    if fn > 0 and issue_n == 0 and doc_ratio >= 0.5:
        recs.append(
            "ℹ️ Для базового уровня генерации включите «Граничные случаи»; для продвинутого — рецепты AST и опционально LLM-помощник."
        )

    cc = metrics.get("cyclomatic_complexity")
    if isinstance(cc, dict):
        extend_recommendations_with_complexity(recs, cc)

    if not any(x.startswith("⚠️") or x.startswith("ℹ️") for x in recs):
        if source == "docker":
            recs.append("✅ Существенных проблем не выявлено базовыми правилами")
        else:
            recs.append("✅ Код выглядит стабильным по базовым метрикам.")

    return recs
