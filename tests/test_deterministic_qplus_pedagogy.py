"""PHASE A — Q+ (6. razred) ima VLASTITU pedagogiju, ne posuđenu od Q.

ŽIVI NALAZ (audit ovlašćenja pravila, 2026-08-09): `arithmetic._hint_domain` je
domen `rational_nonneg` preslikavao u `rational_signed` pri izboru pravila i
zamke, pa je zadatak 6. razreda mogao dobiti uputu o suprotnim brojevima i
pravilu znakova — pojmove koje taj kurikulum još ne poznaje (oblast je
„… u Q+“). Isti korijen („posuđeno od rational_signed“) živio je i u izboru
operanada za dijeljenje: `_exact_in_domain` je provjeravao samo REZULTAT, pa je
Q+ dijeljenje dobijalo predznačene operande.

Ovi testovi zaključavaju obje strane: Q+ nikad ne dobija predznačenu
pedagogiju, a Q (7. razred i dalje) je NE gubi.
"""
import pytest

from matbot.deterministic import arithmetic
from matbot.tutor import lesson_context as lesson_context_module

# Pojmovi koje kurikulum 6. razreda u Q+ još ne poznaje.
SIGNED_CONCEPTS = ("suprotn", "znak", "negativ")
# Vidljiv NEGATIVAN literal (operator „ - “ u lancu uvijek ima razmak oko sebe,
# pa ga ovi oblici ne mogu lažno pogoditi).
NEGATIVE_LITERALS = ("-\\frac", "(-")

OPERATIONS = ("add", "subtract", "multiply", "divide")


def q_plus_parameters(operation, shape="single_operation"):
    return {"allowed_operations": (operation,), "expression_shape": shape,
            "number_domain": "rational_nonneg"}


def signed_parameters(operation, shape="single_operation"):
    return {"allowed_operations": (operation,), "expression_shape": shape,
            "number_domain": "rational_signed"}


def help_text(package):
    return " ".join(package.hints) + " " + package.solution


def visible_text(package):
    return package.question + " " + help_text(package)


# ---------------------------------------------------------------------------
# 1) Q+ — NIJEDAN PREDZNAČENI POJAM I NIJEDAN NEGATIVAN LITERAL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("level", (1, 2, 3))
def test_q_plus_hints_never_use_signed_number_guidance(operation, level):
    parameters = q_plus_parameters(operation)
    assert arithmetic.supports(parameters)
    for _ in range(25):
        package = arithmetic.generate_package(
            lesson_id="6-XX-XXX", lesson_title="Q+ vježba",
            parameters=parameters, level=level)
        lowered = help_text(package).lower()
        for concept in SIGNED_CONCEPTS:
            assert concept not in lowered, (operation, level, package.hints[0])
        for literal in NEGATIVE_LITERALS:
            assert literal not in visible_text(package), (operation, level,
                                                          package.question)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_q_plus_rule_hint_is_its_own_text_not_the_signed_one(operation):
    """Zabrana preslikavanja je STRUKTURNA, ne samo posljedica formulacije."""
    assert ("rational_nonneg", operation) in arithmetic._RULE_HINT
    assert ("rational_nonneg", operation) in arithmetic._PITFALL
    assert arithmetic._RULE_HINT[("rational_nonneg", operation)] != \
        arithmetic._RULE_HINT[("rational_signed", operation)]


def test_q_plus_multi_factor_shape_also_stays_nonnegative():
    parameters = q_plus_parameters("multiply", shape="multi_factor")
    assert arithmetic.supports(parameters)
    for level in (1, 2, 3):
        for _ in range(20):
            package = arithmetic.generate_package(
                lesson_id="6-XX-XXX", lesson_title="Q+ vježba",
                parameters=parameters, level=level)
            for literal in NEGATIVE_LITERALS:
                assert literal not in visible_text(package)


# ---------------------------------------------------------------------------
# 2) KONTROLA — Q (7. razred i dalje) ZADRŽAVA PREDZNAČENU PEDAGOGIJU
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operation", OPERATIONS)
def test_signed_rational_lessons_keep_signed_number_guidance(operation):
    parameters = signed_parameters(operation)
    assert arithmetic.supports(parameters)
    rule = arithmetic._RULE_HINT[("rational_signed", operation)].lower()
    assert any(concept in rule for concept in SIGNED_CONCEPTS)
    seen_negative = False
    for _ in range(40):
        package = arithmetic.generate_package(
            lesson_id="7-03-XXX", lesson_title="Q vježba",
            parameters=parameters, level=2)
        assert package.hints[0] == arithmetic._RULE_HINT[
            ("rational_signed", operation)]
        seen_negative = seen_negative or any(
            literal in package.question for literal in NEGATIVE_LITERALS)
    assert seen_negative, "predznačena lekcija mora smjeti pokazati negativan broj"


# ---------------------------------------------------------------------------
# 3) STVARNA LEKCIJA 6. RAZREDA (kroz server-vlasnički kontekst)
# ---------------------------------------------------------------------------

def test_grade_six_rational_lesson_only_speaks_q_plus():
    """6-04-014 „Brojevni izrazi s razlomcima“ — oblast je Q+."""
    context = lesson_context_module.build(6, "6-04-014")
    contract = context.semantic_contract
    assert contract.parameters["number_domain"] == "rational_nonneg"
    assert arithmetic.supports(contract.parameters)
    for level in (1, 2, 3):
        for _ in range(20):
            package = arithmetic.generate_package(
                lesson_id=context.topic_id, lesson_title=context.title,
                parameters=contract.parameters, level=level)
            lowered = help_text(package).lower()
            for concept in SIGNED_CONCEPTS:
                assert concept not in lowered, (level, package.hints[0])
            for literal in NEGATIVE_LITERALS:
                assert literal not in visible_text(package), (level,
                                                              package.question)
