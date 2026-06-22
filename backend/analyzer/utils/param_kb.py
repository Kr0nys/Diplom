"""
Загрузка recipe_kb.json и выбор значений параметров для basic/recipe генераторов.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional


@lru_cache(maxsize=1)
def load_param_kb() -> Dict[str, Any]:
    try:
        path = Path(__file__).with_name("recipe_kb.json")
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"param_value_rules": [], "exception_preference": []}


def pick_rule_for_param(param_name: str, kb: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    kb = kb or load_param_kb()
    n = (param_name or "").lower()
    for rule in kb.get("param_value_rules", []) or []:
        try:
            if re.search(rule.get("name_regex") or "", n):
                return rule
        except re.error:
            continue
    return None


def expr_for_param(
    param_name: str,
    ann: str = "",
    *,
    kind: str = "valid",
    allow_none: bool = False,
) -> Optional[str]:
    """
    kind: valid | edge | invalid
    Возвращает None, если правило не найдено.
    """
    rule = pick_rule_for_param(param_name)
    if not rule:
        return None
    key = kind if kind in ("valid", "edge", "invalid") else "valid"
    raw = rule.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    if str(raw).strip() == "None" and not allow_none:
        return None
    return str(raw).strip()


def prefer_exception(raises: List[str], kb: Optional[Dict] = None) -> Optional[str]:
    kb = kb or load_param_kb()
    rs = list(raises or [])
    if not rs:
        return None
    pref = kb.get("exception_preference", []) or []
    for p in pref:
        if p in rs or any(r.endswith("." + p) for r in rs):
            return p
    for r in rs:
        if "." not in r:
            return r
    return rs[0]
