"""
Локальные проверки генератора без Ollama (unittest).
Запуск: python -m analyzer.tests_ai_generator_smoke из каталога backend/
"""
import unittest
from unittest.mock import patch

from analyzer.utils.ai_generator import AITestGenerator


class AiGeneratorSmokeTests(unittest.TestCase):
    def test_advanced_accepts_response_with_basic_in_name_and_fallback_word(self):
        """Раньше ложно считалось fallback из-за test_*_basic + слово 'fallback'."""
        raw = '''```python
import pytest

def test_math():
    """fallback path should still run"""
    assert 1 + 1 == 2

def test_edge_basic():
    assert True
```
'''
        with patch.object(AITestGenerator, "_generate_ai_tests", return_value=raw):
            g = AITestGenerator(timeout=60)
            out = g.generate_tests(
                "def add(a, b):\n    return a + b\n",
                {"functions_count": 1, "classes_count": 0, "async_functions": 0, "total_lines": 3},
                {
                    "detail_level": "advanced",
                    "use_mocks": False,
                    "include_edge_cases": True,
                    "test_framework": "pytest",
                },
            )
        self.assertNotIn("AI_GENERATION_FAILED", out)
        self.assertIn("def test_", out)

    def test_full_rejects_injected_ollama_fallback_marker(self):
        fallback_body = "# ⚠️ ReadTimeout: x. Basic fallback.\nimport pytest\ndef test_x():\n    assert 1\n"
        with patch.object(AITestGenerator, "_generate_ai_tests", return_value=fallback_body):
            g = AITestGenerator(timeout=60)
            out = g.generate_tests(
                "def add(a, b):\n    return a + b\n",
                {"functions_count": 1, "classes_count": 0, "async_functions": 0, "total_lines": 3},
                {
                    "detail_level": "full",
                    "llm_assist": True,
                    "use_mocks": False,
                    "include_edge_cases": True,
                    "test_framework": "pytest",
                },
            )
        self.assertIn("ДИАГНОСТИКА AI", out)
        self.assertIn("Автоматически сгенерированные тесты", out)

    def test_basic_mode_docstrings_are_russian(self):
        g = AITestGenerator(timeout=60)
        out = g.generate_tests(
            "def add(a, b):\n    return a + b\n",
            {"functions_count": 1, "classes_count": 0, "async_functions": 0, "total_lines": 3},
            {
                "detail_level": "basic",
                "include_edge_cases": False,
                "test_framework": "pytest",
            },
        )
        self.assertIn("Автоматически сгенерированные тесты", out)
        self.assertIn("Базовый тест для add", out)
        self.assertIn("# Подготовка", out)

    def test_pick_clean_from_markdown_fence(self):
        g = AITestGenerator(timeout=60)
        raw = "Here:\n```python\nimport pytest\ndef test_one():\n    assert 1\n```\n"
        src = "def f():\n    return 1\n"
        clean = g._pick_clean_from_model_output(raw, src)
        self.assertIn("def test_one", clean)
        compile(clean, "<t>", "exec")

    def test_recover_from_plain_code_after_prose(self):
        g = AITestGenerator(timeout=60)
        raw = "Sure.\n\nimport pytest\n\ndef test_z():\n    assert 2 == 2\n"
        src = "def f():\n    return 1\n"
        clean = g._pick_clean_from_model_output(raw, src)
        self.assertIn("def test_z", clean)
        compile(clean, "<t>", "exec")

    def test_unclosed_markdown_fence_still_extracts(self):
        g = AITestGenerator(timeout=60)
        raw = "```python\nimport pytest\n\ndef test_truncated():\n    assert 1\n"
        src = "def f():\n    return 1\n"
        clean = g._pick_clean_from_model_output(raw, src)
        self.assertIn("def test_truncated", clean)
        compile(clean, "<t>", "exec")

    def test_polish_strips_test_without_assert(self):
        g = AITestGenerator(timeout=60)
        suite = (
            'import pytest\n'
            "def test_ok():\n    assert 1\n"
            "def test_no_assert():\n    setup = 2\n"
        )
        out = g._polish_test_suite(suite)
        self.assertNotIn("test_no_assert", out)
        compile(out, "<t>", "exec")

    def test_merge_duplicate_from_imports(self):
        g = AITestGenerator(timeout=60)
        suite = (
            "import pytest\n"
            "from utils.calculations import calculate_final_price\n"
            "from utils.calculations import calculate_tax\n"
            "def test_x():\n    assert calculate_tax(100.0, 0.2) == 20.0\n"
        )
        out = g._merge_from_imports_block(suite)
        self.assertIn("from utils.calculations import calculate_final_price, calculate_tax", out)
        compile(out, "<t>", "exec")

    def test_prune_unused_patch_import(self):
        g = AITestGenerator(timeout=60)
        suite = (
            "import pytest\nfrom unittest.mock import patch\n"
            "def test_x():\n    assert 1\n"
        )
        out = g._prune_unused_simple_imports(suite)
        self.assertNotIn("unittest.mock", out)

    def test_advanced_merges_basic_and_recipe(self):
        g = AITestGenerator(timeout=60)
        code = (
            "# File: calc.py\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def validate_amount(x):\n"
            "    return float(x) >= 0\n"
        )
        out = g.generate_tests(
            code,
            {"functions_count": 2, "classes_count": 0, "async_functions": 0, "total_lines": 6},
            {
                "detail_level": "advanced",
                "include_edge_cases": True,
                "test_framework": "pytest",
                "llm_assist": False,
            },
        )
        self.assertIn("test_add_basic", out)
        self.assertIn("test_validate_amount_smoke", out)
        self.assertIn("ТЕСТЫ ИЗ РЕЦЕПТОВ", out)

    def test_merge_basic_and_recipe_skips_duplicate_recipe(self):
        g = AITestGenerator(timeout=60)
        basic = "def test_x():\n    assert 1\n"
        recipe = "def test_x():\n    assert 1\n"
        merged = g._merge_basic_and_recipe(basic, recipe, "def f(): pass")
        self.assertEqual(merged.strip(), recipe.strip())

    def test_prepare_project_dir_preserves_nested_paths(self):
        import tempfile
        from pathlib import Path
        from analyzer.utils.docker_runner import DockerRunner

        with tempfile.TemporaryDirectory() as tmp:
            src_root = Path(tmp) / "src"
            nested = src_root / "pkg"
            nested.mkdir(parents=True)
            mod = nested / "mod.py"
            mod.write_text("x = 1\n", encoding="utf-8")
            req = src_root / "requirements.txt"
            req.write_text("pytest\n", encoding="utf-8")

            runner = DockerRunner(timeout=30)
            out_dir = runner._prepare_project_dir_from_files(
                [str(mod), str(req)],
                session_id="testsession",
                relative_names=["pkg/mod.py", "requirements.txt"],
            )
            out = Path(out_dir)
            self.assertTrue((out / "pkg" / "mod.py").is_file())
            self.assertTrue((out / "requirements.txt").is_file())

    def test_approx_heuristic_expected_result(self):
        g = AITestGenerator(timeout=60)
        suite = (
            "import pytest\n"
            "def test_x():\n"
            "    expected_result = 120.0\n"
            "    result = 120.0\n"
            "    assert result == expected_result\n"
        )
        out = g._apply_pytest_approx_heuristic(suite)
        self.assertIn("pytest.approx(expected_result)", out)


if __name__ == "__main__":
    unittest.main()
