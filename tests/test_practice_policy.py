"""PP-1 — razriješena pedagoška politika (matbot/practice_policy.py).

Audit ovlašćenja pravila (2026-08-09) dokazao je dva pedagoška autoriteta:
prompt pravila vezala su samo model-turnove, a deterministički motori nosili su
vlastitu, razredno slijepu prozu. Politika se sada razrješava JEDNOM iz
pouzdanog kkonteksta i konsumiraju je motor, oba prompta i validatori.
"""
import pytest

from matbot import practice_policy as pp
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.rules import build_shared_math_rules
from matbot.tutor import lesson_context as lesson_context_module

Q_PLUS_PARAMS = {"number_domain": "rational_nonneg",
                 "shapes": ("solve_inequality_additive",)}


def q_plus_policy(lesson_id="6-07-003"):
    return pp.resolve(6, lesson_id, "linear_equation_direct", Q_PLUS_PARAMS,
                      "Nejednačine s razlomcima oblika x ± a < b / > b",
                      "Jednačine, nejednačine i izrazi u Q+")


# ---------------------------------------------------------------------------
# 1) RAZRJEŠENJE POLITIKE
# ---------------------------------------------------------------------------

def test_grade_six_resolves_unknown_member_and_forbids_transposition():
    policy = q_plus_policy()
    assert policy.policy_version == pp.POLICY_VERSION == "PP-1"
    assert policy.equation_method == pp.METHOD_UNKNOWN_MEMBER
    assert pp.METHOD_TRANSPOSITION in policy.forbidden_method_ids
    assert "balance_both_sides" in policy.forbidden_method_ids
    assert policy.visible_number_domain == pp.VISIBLE_DOMAIN_NONNEGATIVE
    assert policy.scan_method_prose is True


@pytest.mark.parametrize("grade", (7, 8, 9))
def test_higher_grades_allow_transposition(grade):
    policy = pp.resolve(grade, "X", "linear_equation_direct",
                        {"number_domain": "integer", "shapes": ("one_step_additive",)},
                        "Linearne jednačine", "Jednačine")
    assert policy.equation_method == pp.METHOD_TRANSPOSITION
    assert policy.forbidden_method_ids == ()
    assert policy.visible_number_domain == pp.VISIBLE_DOMAIN_ANY


def test_lesson_contract_narrows_grade_domain():
    """PRVENSTVO: ugovor lekcije SUŽAVA razrednu politiku — lekcija 6. razreda
    koja izričito deklariše `integer` smije prikazati minus (npr. lekcija o
    pojmu jednačine s klasifikacijom zapisa)."""
    policy = pp.resolve(6, "6-07-001", "linear_equation_direct",
                        {"number_domain": "integer", "shapes": ("classification",)},
                        "Pojam jednačine", "Jednačine, nejednačine i izrazi u Q+")
    assert policy.visible_number_domain == pp.VISIBLE_DOMAIN_ANY


def test_decimal_domain_is_nonnegative_only_in_grade_six():
    six = pp.resolve(6, "L", "linear_equation_direct",
                     {"number_domain": "decimal"}, "t", "o")
    seven = pp.resolve(7, "L", "decimal_arithmetic_direct",
                       {"number_domain": "decimal"}, "t", "o")
    assert six.visible_number_domain == pp.VISIBLE_DOMAIN_NONNEGATIVE
    assert seven.visible_number_domain == pp.VISIBLE_DOMAIN_ANY


def test_arrow_method_resolves_only_for_proportionality_lessons():
    arrow = pp.resolve(8, "8-03-004", "ratio_proportion_direct", {},
                       "Prepoznavanje direktne proporcionalnosti",
                       "Proporcionalnost, Talesova teorema i sličnost")
    function = pp.resolve(8, "8-03-006", "linear_function_direct", {},
                          "Funkcija direktne proporcionalnosti y=kx",
                          "Proporcionalnost, Talesova teorema i sličnost")
    ratio = pp.resolve(6, "6-06-003", "ratio_proportion_direct", {},
                       "Razmjera/omjer", "Postotak, razmjera i aritmetička sredina")
    assert arrow.arrow_method_lesson is True
    assert function.arrow_method_lesson is False
    assert ratio.arrow_method_lesson is False


def test_lesson_context_carries_the_resolved_policy():
    context = lesson_context_module.build(6, "6-07-003")
    policy = context.practice_policy
    assert policy is not None
    assert policy.policy_version == "PP-1"
    assert policy.lesson_id == "6-07-003"
    assert policy.equation_method == pp.METHOD_UNKNOWN_MEMBER
    assert policy.visible_number_domain == pp.VISIBLE_DOMAIN_NONNEGATIVE


# ---------------------------------------------------------------------------
# 2) DETEKTORI — vidljivi negativan literal
# ---------------------------------------------------------------------------

def test_screenshot_negative_rhs_is_detected():
    """Doslovni produkcijski slučaj: $x - \\frac{1}{2} > -\\frac{3}{14}$."""
    found = pp.find_visible_negative_literals(
        r"Riješi nejednačinu: $x - \frac{1}{2} > -\frac{3}{14}$")
    assert found


@pytest.mark.parametrize("text", [
    r"iznose $-\frac{3}{4}$",
    r"$5 \cdot (-3)$",
    r"$x = -7$",
    r"$x > -2$",
    "$-0,5$",
])
def test_negative_literal_forms_are_detected(text):
    assert pp.find_visible_negative_literals(text)


@pytest.mark.parametrize("text", [
    r"Izračunaj: $25 - 17$",
    r"$x - 3 = 5$",
    r"$\frac{7}{8} - \frac{1}{8} = \frac{6}{8}$",
    "Oduzmi $3$ od $10$.",
    r"$(37 - 5) : 4$",
])
def test_binary_subtraction_is_never_a_negative_literal(text):
    assert not pp.find_visible_negative_literals(text)


# ---------------------------------------------------------------------------
# 3) DETEKTORI — metodska proza i napredne operacije
# ---------------------------------------------------------------------------

def test_transposition_prose_is_detected_in_prose_only():
    hit = ("Nepoznatu ostavi, a sve poznate članove prebaci na drugu stranu "
           "sa suprotnim predznakom.")
    assert pp.find_forbidden_method_prose(hit)
    assert pp.find_forbidden_method_prose("Primijeni operaciju na obje strane.")
    assert not pp.find_forbidden_method_prose(
        "Nepoznati sabirak je zbir minus poznati sabirak: $x = 5 - 2$.")


@pytest.mark.parametrize("text", [
    r"Izračunaj $\sin(30^\circ)$.",
    "Sinus ugla je omjer stranica.",
    r"$tg(45) = 1$",
    r"$\log 100 = 2$",
    "Primijeni logaritam na obje strane.",
])
def test_advanced_scope_operations_are_detected(text):
    assert pp.find_advanced_scope_violations(text)


def test_harmless_words_do_not_trip_the_scope_detector():
    assert not pp.find_advanced_scope_violations(
        "Na kosini brda logika kaže da je singl termin iz muzike; "
        r"trougao ima $3$ stranice i $P = \frac{ab}{2}$.")


def test_scope_detector_respects_an_explicit_allowance():
    policy = q_plus_policy()
    allowed = pp.ResolvedPracticePolicy(
        **{**policy.__dict__, "advanced_scope_allowed": ("sin",)})
    assert "sin" not in pp.find_advanced_scope_violations(r"$\sin x$", allowed)


def test_mathsafe_parser_still_renders_sin_the_ban_is_curricular():
    """Odluka I: granica je KURIKULARNA, ne parser-zabrana."""
    cleaned, safe = sanitize_and_validate_math_text(r"Vrijedi $\sin x \le 1$.")
    assert safe and "\\sin" in cleaned


# ---------------------------------------------------------------------------
# 4) PAKETNE PROVJERE + provenijencija metode
# ---------------------------------------------------------------------------

def test_forbidden_method_provenance_fails_even_with_clean_prose():
    codes = pp.package_policy_failures(
        q_plus_policy(), r"Riješi: $x + 2 = 5$", ["$3$"], ["uredan hint"],
        "uredno rješenje", method_id="transposition")
    assert pp.METHOD_PROVENANCE_CODE in codes


def test_hint_method_continuity_is_enforced():
    """Kontinuitet: hint ne smije učiti metodu koju zadatak ne smije koristiti."""
    codes = pp.package_policy_failures(
        q_plus_policy(), r"Riješi: $x + 2 = 5$", ["$3$"],
        ["Prebaci poznati član na drugu stranu."], "", "unknown_member")
    assert pp.FORBIDDEN_METHOD_CODE in codes


def test_clean_unknown_member_package_passes():
    assert pp.package_policy_failures(
        q_plus_policy(),
        r"Riješi nejednačinu: $x + \frac{1}{6} > \frac{37}{24}$",
        ["$x > \\frac{11}{8}$", "$x < \\frac{11}{8}$"],
        ["U ovoj nejednačini $x$ je sabirak."],
        "Granica je zbir minus poznati sabirak.", "unknown_member") == ()


def test_grade_seven_transposition_package_is_allowed():
    policy = pp.resolve(7, "7-02-016", "linear_equation_direct",
                        {"number_domain": "integer"}, "Linearne jednačine",
                        "Cijeli brojevi")
    assert pp.package_policy_failures(
        policy, r"Riješi: $x + 2 = -5$", ["$-7$"],
        ["Prebaci poznati član sa suprotnim predznakom."], "",
        "transposition") == ()


# ---------------------------------------------------------------------------
# 5) RENDER U PROMPTOVE — jedna istina, oba prompta
# ---------------------------------------------------------------------------

def test_grade_six_rules_render_all_six_role_relations():
    text = build_shared_math_rules(6, "Jednačine s razlomcima",
                                   "Jednačine, nejednačine i izrazi u Q+",
                                   "practice")
    for _, relation in pp.UNKNOWN_ROLE_RELATIONS.values():
        assert relation in text
    assert "prebacivanjem preko znaka jednakosti" in text  # izričita zabrana


def test_domain_rules_carry_scope_and_modular_curriculum_lines():
    text = build_shared_math_rules(8, "Talesova teorema",
                                   "Proporcionalnost", "practice")
    assert "Trigonometrijske funkcije" in text and "logaritmi NISU" in text
    assert "kantonima/entitetima" in text and "kasni" in text


def test_exterior_angle_convention_is_declared_for_triangle_lessons():
    text = build_shared_math_rules(
        6, "Zbir uglova trougla", "Trougao", "practice")
    assert "$\\alpha_1$" in text and "indeksom 1" in text


def test_anglework_engine_output_matches_the_declared_convention():
    """Motor uglova emituje $\\alpha_1$/$\\gamma_1$ — sada je to i propisana
    konvencija (ista oznaka u pravilu i u izlazu, nikad $\\alpha'$)."""
    import random
    from matbot.deterministic import anglework
    params = {"kinds": ("exterior_from_interior", "exterior_angle")}
    assert anglework.supports(params)
    seen = ""
    for seed in range(30):
        package = anglework.generate_package(
            lesson_id="X", lesson_title="Vanjski ugao trougla",
            parameters=params, level=1, rng=random.Random(seed))
        seen += package.solution
    assert "\\alpha_1" in seen or "\\gamma_1" in seen
    assert "\\alpha'" not in seen
