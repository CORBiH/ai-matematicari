"""IZOLACIJA FAZE 0 — dokaz da mjerenje nije dodirnulo proizvod.

Faza 0 smije dodati SAMO mjerenje. Ovaj fajl to dokazuje mehanički, umjesto da
se oslanja na obećanje u opisu commita — upravo je takvo obećanje (c7552b8,
„semantika je sada ista na sva četiri mjesta: prompt …“) bilo netačno i
proizvelo mrtav prompt blok koji je preživio četiri dana i jednu kampanju.

Četiri invarijante:
  1. nijedan modul iz `matbot/` ne uvozi `tools.practice_eval`;
  2. registar provjera kampanje (`checks._CHECKS`) je nepromijenjen — mjerač
     tvrdnji iz Faze 0 NIJE uvezan, pa ne može promijeniti nijedan rezultat;
  3. zamrznuti FINAL40 talas je bajt za bajt onaj koji je proizveo dokaze;
  4. moduli Faze 0 ne dosežu ni LLM ni mrežu.
"""
from __future__ import annotations

import ast
import hashlib
import warnings
from pathlib import Path

from tools.practice_eval import checks as check_lib

ROOT = Path(__file__).resolve().parent.parent

PHASE0_MODULES = (
    "tools/practice_eval/hint_evidence.py",
    "tools/practice_eval/hintsemantics.py",
    "tools/practice_eval/release_contract.py",
)


def _imported_modules(relative_path: str) -> set:
    # Zatečeni izvori nose po koji `\c` u docstringu s LaTeX komandom; parsiranje
    # ih prijavljuje kao DeprecationWarning. To je postojeći sadržaj proizvoda i
    # ne tiče se ove kapije — utišava se samo ovdje, ne globalno.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ---------------------------------------------------------------------------
# 1. PROIZVOD NE ZNA ZA EVALUATOR
# ---------------------------------------------------------------------------

def test_no_product_module_imports_the_evaluator_package():
    offenders = []
    for path in sorted((ROOT / "matbot").rglob("*.py")):
        for name in _imported_modules(str(path.relative_to(ROOT)).replace("\\", "/")):
            if name.startswith("tools"):
                offenders.append(f"{path.relative_to(ROOT)} → {name}")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# 2. REGISTAR PROVJERA KAMPANJE JE NEPROMIJENJEN
# ---------------------------------------------------------------------------

# Zatečeno stanje na kandidatu c17538a. Faza 0 ne dodaje i ne uklanja nijednu
# provjeru: uvezivanje mjerača tvrdnji je Faza 2 i traži vlastitu kalibraciju
# lažnih pozitiva nad živim talasom.
FROZEN_CHECK_NAMES = (
    'bosnian', 'correct_option_stable', 'free_text_grading_no_oracle', 'geometry_ok',
    'help_nonempty', 'hint_differs', 'hint_no_leak', 'identical_response',
    'lesson_matches', 'math_safe', 'no_answer_leak', 'no_control_chars',
    'no_fallback_text', 'no_leak', 'no_new_task', 'no_verdict', 'not_published',
    'not_safe_error', 'numeric_consistent', 'options_ok', 'package_clean',
    'published', 'request_equivalent_reformulation', 'response_schema',
    'reveal_absent', 'reveal_present', 'solution_complete', 'state_unchanged',
    'stays_in_lesson', 'task_completed', 'task_differs', 'task_not_completed',
    'task_preserved', 'task_published', 'task_self_contained', 'terminology_clean',
    'verdict_correct', 'verdict_incorrect', 'zero_calls',
)


def test_the_campaign_check_registry_is_unchanged_by_phase_zero():
    assert tuple(sorted(check_lib._CHECKS)) == FROZEN_CHECK_NAMES


def test_the_phase_zero_proposition_measure_is_deliberately_not_wired():
    """Mjerenje bez uvezivanja: Faza 0 ne smije promijeniti nijedan rezultat."""
    from tools.practice_eval import hintsemantics
    assert hintsemantics.measure not in check_lib._CHECKS.values()
    assert not any(name.startswith("proposition") for name in check_lib._CHECKS)
    assert "hintsemantics" not in _imported_modules("tools/practice_eval/checks.py")
    assert "tools.practice_eval.hintsemantics" not in _imported_modules(
        "tools/practice_eval/runner.py")


# ---------------------------------------------------------------------------
# 3. ZAMRZNUT TALAS JE ONAJ KOJI JE PROIZVEO DOKAZE
# ---------------------------------------------------------------------------

# Isti digest koji `sdk_calls.json` kampanje final40_c17538a_20260810-141121
# nosi kao `wave_sha256`. Ako se talas promijeni, dokazi iz `hint_evidence`
# više ne opisuju ono što bi kampanja danas pokrenula.
FINAL40_WAVE_SHA256 = "bfd0a9abb55051cc32246099c4b661d6b142dfcc3a53f50bed9d57f07c692575"


def test_the_frozen_final40_wave_is_untouched():
    path = ROOT / "tools" / "practice_eval" / "scenarios" / "family" / "wave_final40.jsonl"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == FINAL40_WAVE_SHA256


# ---------------------------------------------------------------------------
# 4. NIJEDAN MODUL FAZE 0 NE DOSEŽE MODEL NI MREŽU
# ---------------------------------------------------------------------------

_FORBIDDEN_IMPORTS = ("openai", "requests", "httpx", "urllib", "socket",
                      "matbot.llm", "http.client")


def test_phase_zero_modules_cannot_reach_a_model_or_the_network():
    for relative_path in PHASE0_MODULES:
        imported = _imported_modules(relative_path)
        for forbidden in _FORBIDDEN_IMPORTS:
            assert not any(name == forbidden or name.startswith(forbidden + ".")
                           for name in imported), (relative_path, forbidden, imported)


def test_phase_zero_modules_exist_where_the_phase_allows_them():
    for relative_path in PHASE0_MODULES:
        assert (ROOT / relative_path).is_file(), relative_path
        assert relative_path.startswith("tools/practice_eval/")
