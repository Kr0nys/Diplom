"""
Разбор результатов pytest: JUnit XML + резервный парсинг вывода терминала.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PYTEST_JUNIT_FILENAME = "_pytest_junit.xml"


@dataclass
class PytestRunReport:
    exit_code: int
    output: str
    summary: Dict[str, Any] = field(default_factory=dict)
    failures: List[Dict[str, Any]] = field(default_factory=list)
    cases: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "output": self.output,
            "passed": self.exit_code == 0,
            "summary": self.summary,
            "failures": self.failures,
            "cases": self.cases,
        }


def pytest_cli_args(test_target: str, *, junit_path: str) -> List[str]:
    """Аргументы pytest: подробный вывод + JUnit для структурированного отчёта."""
    return [
        test_target,
        "-v",
        "--tb=short",
        "--no-header",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit_path}",
    ]


def build_pytest_report(
    exit_code: int,
    output: str,
    *,
    junit_bytes: Optional[bytes] = None,
) -> PytestRunReport:
    parsed = parse_junit_xml(junit_bytes) if junit_bytes else None
    if not parsed:
        parsed = parse_terminal_summary(output, exit_code=exit_code)

    summary = parsed.get("summary") or {}
    failures = parsed.get("failures") or []
    cases = parsed.get("cases") or []

    if not summary and exit_code == 0 and not cases:
        summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}

    return PytestRunReport(
        exit_code=exit_code,
        output=output,
        summary=summary,
        failures=failures,
        cases=cases,
    )


def parse_junit_xml(data: bytes) -> Optional[Dict[str, Any]]:
    if not data:
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None

    suites: List[ET.Element] = []
    if root.tag == "testsuites":
        suites = list(root.findall("testsuite"))
    elif root.tag == "testsuite":
        suites = [root]
    if not suites:
        return None

    total = failed = errors = skipped = 0
    duration = 0.0
    cases: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for suite in suites:
        total += int(suite.get("tests") or 0)
        failed += int(suite.get("failures") or 0)
        errors += int(suite.get("errors") or 0)
        skipped += int(suite.get("skipped") or 0)
        duration += float(suite.get("time") or 0)

        for tc in suite.findall("testcase"):
            classname = (tc.get("classname") or "").strip()
            name = (tc.get("name") or "").strip()
            nodeid = f"{classname}::{name}" if classname else name
            case_time = float(tc.get("time") or 0)

            failure_el = tc.find("failure")
            error_el = tc.find("error")
            skipped_el = tc.find("skipped")

            if failure_el is not None:
                status = "failed"
                detail_el = failure_el
            elif error_el is not None:
                status = "error"
                detail_el = error_el
            elif skipped_el is not None:
                status = "skipped"
                detail_el = skipped_el
            else:
                status = "passed"
                detail_el = None

            case = {
                "name": nodeid,
                "status": status,
                "duration_seconds": case_time,
            }
            cases.append(case)

            if detail_el is not None and status in ("failed", "error", "skipped"):
                message = (detail_el.get("message") or "").strip()
                traceback = (detail_el.text or "").strip()
                reason = message or _first_line(traceback) or status
                enriched = {
                    **case,
                    "message": message,
                    "reason": reason,
                    "traceback": traceback,
                }
                if status in ("failed", "error"):
                    failures.append(enriched)

    passed = max(0, total - failed - errors - skipped)
    return {
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "errors": errors,
            "duration_seconds": round(duration, 3),
        },
        "cases": cases,
        "failures": failures,
    }


def parse_terminal_summary(output: str, *, exit_code: int) -> Dict[str, Any]:
    text = output or ""
    summary = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "duration_seconds": None,
    }

    for pattern, key in (
        (r"(\d+)\s+passed", "passed"),
        (r"(\d+)\s+failed", "failed"),
        (r"(\d+)\s+error", "errors"),
        (r"(\d+)\s+skipped", "skipped"),
    ):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            summary[key] = int(m.group(1))

    dur = re.search(r"in\s+([\d.]+)s", text)
    if dur:
        try:
            summary["duration_seconds"] = round(float(dur.group(1)), 3)
        except ValueError:
            pass

    summary["total"] = summary["passed"] + summary["failed"] + summary["skipped"] + summary["errors"]

    failures = _parse_failures_from_terminal(text)
    cases = _parse_cases_from_terminal(text)

    if summary["total"] == 0 and cases:
        summary["total"] = len(cases)
        summary["passed"] = sum(1 for c in cases if c["status"] == "passed")
        summary["failed"] = sum(1 for c in cases if c["status"] == "failed")
        summary["errors"] = sum(1 for c in cases if c["status"] == "error")
        summary["skipped"] = sum(1 for c in cases if c["status"] == "skipped")

    if exit_code != 0 and summary["total"] == 0 and not failures:
        failures.append(
            {
                "name": "pytest",
                "status": "error",
                "reason": _infer_collection_error(text) or f"pytest завершился с кодом {exit_code}",
                "message": "",
                "traceback": text.strip()[:4000],
            }
        )

    return {"summary": summary, "cases": cases, "failures": failures}


def _parse_cases_from_terminal(text: str) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for line in (text or "").splitlines():
        m = re.match(
            r"^(?P<path>[^\s]+::[^\s]+)\s+(?P<status>PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\s*(?:\[\s*\d+%\s*\])?",
            line.strip(),
            re.IGNORECASE,
        )
        if not m:
            continue
        status_raw = m.group("status").upper()
        status_map = {
            "PASSED": "passed",
            "FAILED": "failed",
            "SKIPPED": "skipped",
            "ERROR": "error",
            "XFAIL": "skipped",
            "XPASS": "passed",
        }
        cases.append({"name": m.group("path"), "status": status_map.get(status_raw, status_raw.lower())})
    return cases


def _parse_failures_from_terminal(text: str) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []
    if not text:
        return failures

    blocks = re.split(r"\n=+\s*FAILURES\s*=+\n", text, maxsplit=1, flags=re.IGNORECASE)
    if len(blocks) < 2:
        blocks = re.split(r"\n=+\s*ERRORS\s*=+\n", text, maxsplit=1, flags=re.IGNORECASE)
    if len(blocks) < 2:
        return failures

    body = blocks[1]
    chunks = re.split(r"\n(?=_+ .* _+)\n", body)

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.splitlines()
        header = lines[0].strip("_ ").strip() if lines else "unknown"
        name = header
        if header.startswith("____"):
            name = header.strip("_ ").strip()

        traceback = "\n".join(lines[1:]).strip() if len(lines) > 1 else chunk
        reason = _extract_short_reason(traceback) or _first_line(traceback) or "ошибка"
        failures.append(
            {
                "name": name,
                "status": "failed",
                "reason": reason,
                "message": reason,
                "traceback": traceback,
            }
        )

    return failures


def _extract_short_reason(traceback: str) -> str:
    for line in reversed((traceback or "").splitlines()):
        s = line.strip()
        if s.startswith("E ") and len(s) > 2:
            return s[2:].strip()
        if "AssertionError" in s or "Error:" in s or "Exception:" in s:
            return s
    return ""


def _infer_collection_error(text: str) -> Optional[str]:
    for line in (text or "").splitlines():
        s = line.strip()
        if "ERROR collecting" in s or "ImportError" in s or "ModuleNotFoundError" in s:
            return s
        if s.startswith("E ") and len(s) > 2:
            return s[2:].strip()
    return None


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s[:500]
    return ""
