"""Lesson-independent boundaries for structured difficulty evidence."""
import pytest

from matbot.tutor.schema import DifficultyEvidence, difficulty_evidence_errors


def evidence(**updates):
    values = {
        "reasoning_steps": 2,
        "condition_count": 2,
        "operation_count": 2,
        "representation_change_count": 0,
        "requires_explanation": False,
        "requires_comparison": False,
        "requires_construction": False,
        "requires_proof_or_justification": False,
        "combines_concepts": False,
    }
    values.update(updates)
    return DifficultyEvidence(**values)


@pytest.mark.parametrize("shape", [
    evidence(condition_count=2, operation_count=1),
    evidence(condition_count=1, operation_count=2),
    evidence(combines_concepts=True),
    evidence(requires_comparison=True),
    evidence(representation_change_count=1),
])
def test_bounded_two_requirement_shapes_are_level_two(shape):
    assert difficulty_evidence_errors(shape, 2) == ()


def test_exact_divisibility_by_fifteen_evidence_is_bounded_level_two():
    # Two related conditions (divisible by 3 and by 5) yield the combined
    # conclusion divisible by 15; the validator remains lesson-independent.
    divisibility_by_fifteen = evidence(combines_concepts=True)
    assert difficulty_evidence_errors(divisibility_by_fifteen, 2) == ()
    assert "level_1_is_not_direct_introductory_application" in (
        difficulty_evidence_errors(divisibility_by_fifteen, 1)
    )


@pytest.mark.parametrize(("updates", "code"), [
    ({"requires_construction": True}, "level_2_requires_construction"),
    ({"requires_proof_or_justification": True}, "level_2_requires_proof"),
    ({"reasoning_steps": 3}, "level_2_has_too_many_reasoning_steps"),
    ({"condition_count": 3}, "level_2_has_too_many_conditions"),
    ({"operation_count": 3}, "level_2_has_too_many_operations"),
    ({"representation_change_count": 2},
     "level_2_has_advanced_representation_change"),
    ({"condition_count": 3, "combines_concepts": True},
     "level_2_has_too_many_conditions"),
])
def test_level_two_reports_the_specific_advanced_dimension(updates, code):
    errors = difficulty_evidence_errors(evidence(**updates), 2)
    assert code in errors
    assert "level_2_contains_level_3_requirement" not in errors


@pytest.mark.parametrize("advanced", [
    evidence(requires_construction=True),
    evidence(requires_proof_or_justification=True),
    evidence(reasoning_steps=3),
    evidence(condition_count=3),
    evidence(operation_count=3),
    evidence(representation_change_count=2),
])
def test_level_three_requires_and_accepts_positive_advanced_evidence(advanced):
    assert difficulty_evidence_errors(advanced, 3) == ()


def test_bounded_pair_alone_is_not_misclassified_as_level_three():
    assert "level_3_lacks_advanced_requirement" in difficulty_evidence_errors(
        evidence(combines_concepts=True), 3,
    )
