"""Masovna svojstva SVIH kompajliranih determinističkih lekcija.

Za razliku od tests/test_deterministic_capability_engines.py (sintetički
parametri), ovdje se svaka STVARNA kompajlirana lekcija provlači kroz svoj
STVARNI LessonContext i puni serverski validacioni lanac — bajt za bajt isti
kod koji objava koristi (preflight s ugovorom, orakli, dokaz težine, mathsafe,
mathcheck, naslovni semantički zahtjev).

Broj sjemena po (lekcija, nivo) podiže se varijablom MATBOT_FUZZ_SEEDS za
offline fuzz kampanje; podrazumijevano ostaje malo da CI bude brz.
"""
import os
import random

import pytest

from matbot import deterministic as registry
from matbot import lesson_fidelity
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.semantics import contracts as semantic_contracts
from matbot.tutor import package_preflight
from matbot.tutor.lesson_context import build
from matbot.tutor.schema import difficulty_evidence_errors, validate_task
from matbot.tutor.task_identity import canonical_signature

SEEDS = int(os.environ.get("MATBOT_FUZZ_SEEDS", "4"))

_DETERMINISTIC_LESSONS = sorted(
    (lesson_id, contract.family_id)
    for lesson_id, contract in semantic_contracts.all_contracts().items()
    if contract.blocking
    and registry.GENERATORS.get(contract.family_id) is not None
    and registry.GENERATORS[contract.family_id].supports(dict(contract.parameters))
)

CASES = [(lesson_id, family_id, level)
         for lesson_id, family_id in _DETERMINISTIC_LESSONS
         for level in (1, 2, 3)]


def test_expansion_reached_the_intended_scale():
    """Batch #2: 154 determinističke lekcije u 25 porodica, sva četiri razreda."""
    assert len(_DETERMINISTIC_LESSONS) >= 154, len(_DETERMINISTIC_LESSONS)
    grades = {lesson_id[0] for lesson_id, _family in _DETERMINISTIC_LESSONS}
    assert grades == {"6", "7", "8", "9"}, grades
    families = {family for _lesson, family in _DETERMINISTIC_LESSONS}
    assert len(families) >= 25, sorted(families)


def _context(lesson_id):
    context = build(int(lesson_id[0]), lesson_id)
    assert context is not None and context.semantic_contract is not None
    return context


def _generate(context, level, seed):
    module = registry.GENERATORS[context.semantic_contract.family_id]
    return module.generate_package(
        lesson_id=context.topic_id, lesson_title=context.title,
        parameters=dict(context.semantic_contract.parameters), level=level,
        rng=random.Random(seed * 7919 + level))


@pytest.mark.parametrize("lesson_id,family_id,level", CASES,
                         ids=[f"{lesson}-L{level}"
                              for lesson, _family, level in CASES])
def test_every_compiled_lesson_produces_fully_valid_packages(
        lesson_id, family_id, level):
    context = _context(lesson_id)
    requirement = lesson_fidelity.semantic_task_requirement(context.title)
    for seed in range(SEEDS):
        package = _generate(context, level, seed)
        payload = package.task_payload()

        validate_task(payload)
        issues = package_preflight.collect_package_issues(
            payload, contract=context.semantic_contract)
        assert issues == (), (lesson_id, level, seed,
                              package_preflight.describe_issues(issues))
        assert difficulty_evidence_errors(
            payload.difficulty_evidence, level) == ()
        assert payload.selected_lesson_id == lesson_id
        assert payload.target_difficulty_level == level
        # Tačno jedna označena opcija; identitet paketa je stabilan po sjemenu.
        assert payload.correct_option_index == 0
        assert len({option.text for option in payload.options}) == 4

        if requirement is not None:
            assert requirement.failure_for(payload.text) is None, payload.text

        for text in (package.solution, *package.hints):
            cleaned, safe = sanitize_and_validate_math_text(text)
            assert safe, (lesson_id, text)
            assert find_numeric_inconsistencies(cleaned) == [], (lesson_id, text)
        assert package.display_answer not in package.hints[0]


@pytest.mark.parametrize("lesson_id,family_id",
                         _DETERMINISTIC_LESSONS,
                         ids=[lesson for lesson, _f in _DETERMINISTIC_LESSONS])
def test_new_tasks_can_actually_differ(lesson_id, family_id):
    """Uzastopna generisanja daju bar dva kanonski RAZLIČITA zadatka —
    „Daj mi novi zadatak“ ima odakle birati."""
    context = _context(lesson_id)
    signatures = set()
    for seed in range(8):
        package = _generate(context, 2, seed)
        option_texts = [option.text for option in
                        package.task_payload().options]
        signatures.add(canonical_signature(package.question, option_texts))
    assert len(signatures) >= 2, (lesson_id, signatures)


def test_seed_determinism_over_compiled_lessons():
    for lesson_id, _family in _DETERMINISTIC_LESSONS[::9]:
        context = _context(lesson_id)
        first = _generate(context, 3, 5)
        second = _generate(context, 3, 5)
        assert first.question == second.question, lesson_id
        assert first.option_texts == second.option_texts, lesson_id
