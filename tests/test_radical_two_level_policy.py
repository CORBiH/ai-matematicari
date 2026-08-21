r"""DVONIVOVSKA KURIKULARNA SPOSOBNOST KORIJENA — zapis naspram operacije.

ZAŠTO POSTOJI. Politika je do sada imala JEDNU sposobnost („smije li se korijen
pojaviti"), pa su dva različita kurikularna pitanja dijelila jedan prekidač:

  • smije li učenik korijen VIDJETI / PREPOZNATI;
  • smije li ga RAČUNATI / SVODITI kao traženi postupak.

Za 6. i 8. razred odgovor je isti na oba pitanja, pa se razlika nije vidjela.
Za 7. razred nije: kanonski ishod lekcije o skupu Q traži „shvatiti potrebu
PROŠIRIVANJA skupa racionalnih brojeva", a to se predaje tako što se $\sqrt{2}$
pokaže kao neprimjer — dok korjenovanje kao postupak stiže tek u 8. razredu
(oblast „Realni brojevi, korijeni i stepeni", 18 lekcija).

Živi dokaz oba smjera:
  • G7-E4 (cross-curriculum audit): 7. razred je na „kvadrat površine
    $20\,\text{cm}^2$" dobio $a=\sqrt{20}=2\sqrt{5}$ kao redovan postupak;
  • pokušaj paušalne zabrane je oborio 18 testova jer je rušio i legitimno
    prepoznavanje iracionalnih brojeva.

Ovi testovi zaključavaju da su to od sada DVIJE nezavisne sposobnosti, obje
izvedene iz kanonskih tačaka uvođenja, bez ijedne ručno održavane tabele.
"""
from pathlib import Path

import pytest

from matbot import practice_policy as pp
from matbot import prompts, rules

ROOT = Path(__file__).resolve().parent.parent
SQRT = "\\sqrt"


def policy_for(grade, lesson_id="", title="Test", oblast="Test"):
    return pp.resolve(grade=grade, lesson_id=lesson_id or "{}-01-001".format(grade),
                      lesson_title=title, oblast=oblast)


# ---------------------------------------------------------------------------
# 1) MATRICA SPOSOBNOSTI — izvedena, ne prepisana
# ---------------------------------------------------------------------------

MATRIX = [(6, False, False), (7, True, False), (8, True, True), (9, True, True)]


@pytest.mark.parametrize("grade,display,operation", MATRIX)
def test_capability_matrix(grade, display, operation):
    assert pp.radical_notation_allowed_for_grade(grade) is display
    assert pp.radical_operation_allowed_for_grade(grade) is operation
    resolved = policy_for(grade)
    assert resolved.radical_notation_allowed is display
    assert resolved.radical_operation_allowed is operation


def test_both_boundaries_are_derived_from_canonical_introduction_points():
    """JEDNA istina po sposobnosti — nema druge, ručno održavane liste."""
    assert pp.RADICAL_NOTATION_GRADE == 7
    assert pp.RADICAL_OPERATION_GRADE == 8
    assert pp.RADICAL_CURRICULUM_GRADE == pp.RADICAL_OPERATION_GRADE
    assert pp._RADICAL_FORBIDDEN_GRADES == tuple(
        range(pp._LOWEST_SUPPORTED_GRADE, pp.RADICAL_NOTATION_GRADE))
    assert pp._RADICAL_OPERATION_FORBIDDEN_GRADES == tuple(
        range(pp._LOWEST_SUPPORTED_GRADE, pp.RADICAL_OPERATION_GRADE))


def test_operation_is_never_wider_than_notation():
    """Invarijanta modela: računati se smije samo ono što se smije i vidjeti."""
    for grade in (6, 7, 8, 9):
        if pp.radical_operation_allowed_for_grade(grade):
            assert pp.radical_notation_allowed_for_grade(grade), grade


def test_no_per_lesson_exception_exists():
    source = (ROOT / "matbot" / "practice_policy.py").read_text(encoding="utf-8")
    import re
    assert not re.search(r"\d-\d{2}-\d{3}", source)


# ---------------------------------------------------------------------------
# 2) 7. RAZRED — ZAPIS JE DOZVOLJEN (prepoznavanje/klasifikacija)
# ---------------------------------------------------------------------------

G7_Q = dict(lesson_id="7-03-001", title="Skup racionalnih brojeva Q",
            oblast="Racionalni brojevi")


@pytest.mark.parametrize("surface", [
    "Koji od ovih brojeva NIJE racionalan? $" + SQRT + "{2}$",
    "Broj $" + SQRT + "{2}$ ne može se zapisati kao razlomak cijelih brojeva.",
    "$" + SQRT + "{2}$",
    "Zato skup $Q$ nije dovoljan: postoje brojevi poput $" + SQRT + "{2}$.",
])
def test_grade_seven_may_display_a_radical(surface):
    """Prepoznavanje iracionalnog broja NE SMIJE pasti."""
    policy = policy_for(7, **G7_Q)
    codes = pp.text_policy_failures(policy, surface)
    assert pp.GRADE_CAPABILITY_CODE not in codes
    assert pp.RADICAL_OPERATION_CODE not in codes


def test_grade_seven_recognition_with_a_radical_ANSWER_is_currently_blocked():
    """IZMJERENA GRANICA MODELA, iskazana kao invarijanta — ne prikrivena.

    Kad je korijen DISTRAKTOR, a tacan odgovor racionalan, paket prolazi (test
    ispod). Kad je korijen SAM TACAN ODGOVOR („Koji broj NIJE racionalan?“ ->
    korijen iz 2), trenutna politika ga odbija, iako ga ucenik samo PREPOZNAJE.

    Zasto se to ipak ne „popravlja“ regexom: iz polja paketa se ne moze
    razlikovati odgovor koji je PRIKAZAN i treba ga izabrati od odgovora koji je
    IZVEDEN korjenovanjem — oba zavrse u `expected_answer`. Razlika je u tome je
    li vrijednost dobijena OPERACIJOM, a to nijedno postojece polje ne tvrdi.

    Mjereno nad 85 zamrznutih artefakata izdanja: 7. razred ima TACNO JEDAN
    paket s korijenom i on je operacioni (korijen iz 3 cm kao `expected_answer`);
    paket s korijenom kao tacnim odgovorom PREPOZNAVANJA ne postoji. Lazni
    pozitiv je dakle stvaran ali neizmjeren, a propusteni operacioni paket je
    izmjeren. Granica je zabiljezena uz minimalno prosirenje seme koje bi je
    zatvorilo."""
    policy = policy_for(7, **G7_Q)
    codes = pp.package_policy_failures(
        policy,
        question="Koji broj NIJE racionalan?",
        option_texts=["$" + SQRT + "{2}$", "$\frac{3}{4}$", "$0{,}25$", "$-5$"],
        hints=["Racionalan broj se moze zapisati kao razlomak."],
        solution="$" + SQRT + "{2}$ nije racionalan jer nije razlomak.",
        method_id="",
        expected_answer="$" + SQRT + "{2}$")
    assert pp.RADICAL_OPERATION_CODE in codes


def test_grade_seven_recognition_package_with_rational_answer_survives():
    policy = policy_for(7, **G7_Q)
    assert pp.package_policy_failures(
        policy,
        question="Koji broj JESTE racionalan?",
        option_texts=["$" + SQRT + "{2}$", "$\\frac{3}{4}$", "$\\pi$", "$" + SQRT + "{7}$"],
        hints=["Pogledaj koji se broj može zapisati kao razlomak."],
        solution="$\\frac{3}{4}$ je racionalan; $" + SQRT + "{2}$ i $\\pi$ nisu.",
        method_id="",
        expected_answer="$\\frac{3}{4}$") == ()


# ---------------------------------------------------------------------------
# 3) 7. RAZRED — OPERACIJA NIJE DOZVOLJENA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expected", [
    "$" + SQRT + "{20}$",
    "$2" + SQRT + "{5}$",
    "$" + SQRT + "{3}\\,\\text{cm}$",
    "$" + SQRT + "{2}$",
])
def test_grade_seven_answer_may_not_require_a_radical(expected):
    """Nalaz G7-E4 i jedini paket 7. razreda s korijenom u zamrznutom korpusu."""
    policy = policy_for(7)
    assert pp.answer_policy_failures(policy, expected) == (pp.RADICAL_OPERATION_CODE,)


def test_grade_seven_operational_package_is_rejected():
    policy = policy_for(7, lesson_id="7-05-019",
                        title="Površina pravougaonika i kvadrata - obnova",
                        oblast="Četverougao, obim i površina")
    codes = pp.package_policy_failures(
        policy,
        question="Kvadrat ima površinu $20\\,\\text{cm}^2$. Kolika mu je stranica?",
        option_texts=["$2" + SQRT + "{5}\\,\\text{cm}$", "$5\\,\\text{cm}$",
                      "$4\\,\\text{cm}$", "$10\\,\\text{cm}$"],
        hints=["Stranicu dobijaš korjenovanjem površine."],
        solution="$a=" + SQRT + "{20}=2" + SQRT + "{5}$",
        method_id="",
        expected_answer="$2" + SQRT + "{5}\\,\\text{cm}$")
    assert pp.RADICAL_OPERATION_CODE in codes


# ---------------------------------------------------------------------------
# 4) 6. RAZRED NEPROMIJENJEN, 8-9 NEPROMIJENJENI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("surface", [
    "Rezultat je $" + SQRT + "{20}$.",
    "$" + SQRT + "{2}$ nije racionalan.",
])
def test_grade_six_rejects_a_radical_anywhere(surface):
    """6. razred nema NI zapis — zatečena zaštita ostaje netaknuta."""
    assert pp.GRADE_CAPABILITY_CODE in pp.text_policy_failures(policy_for(6), surface)


def test_grade_six_prompt_still_carries_the_full_ban():
    text = prompts.build_explain_instructions(
        6, "Površina pravougaonika i kvadrata", "Četverougao, obim i površina")
    assert "NIJE gradivo ovog razreda" in text


@pytest.mark.parametrize("grade", [8, 9])
def test_higher_grades_keep_full_radical_computation(grade):
    policy = policy_for(grade)
    assert pp.answer_policy_failures(policy, "$2" + SQRT + "{5}$") == ()
    assert pp.text_policy_failures(policy, "$" + SQRT + "{20}=2" + SQRT + "{5}$") == ()
    assert pp.package_policy_failures(
        policy, question="Izračunaj $" + SQRT + "{144}$.",
        option_texts=["$12$"], hints=["Traži broj čiji je kvadrat $144$."],
        solution="$" + SQRT + "{144}=12$", method_id="",
        expected_answer="$12$") == ()


# ---------------------------------------------------------------------------
# 5) PROMPT — svaki razred dobija tačno svoje pravilo
# ---------------------------------------------------------------------------

OPERATION_MARKER = "SMIJE SE SPOMENUTI, NE I RAČUNATI"
DISPLAY_BAN_MARKER = "NIJE gradivo ovog razreda"


def test_grade_seven_explain_states_the_distinction():
    text = prompts.build_explain_instructions(
        7, "Površina pravougaonika i kvadrata - obnova",
        "Četverougao, obim i površina")
    assert OPERATION_MARKER in text
    assert DISPLAY_BAN_MARKER not in text          # zapis NIJE zabranjen
    assert "ne rješavaj" in text or "ne računaj" in text
    assert text.count(OPERATION_MARKER) == 1


@pytest.mark.parametrize("grade", [6, 8, 9])
def test_other_grades_never_receive_the_grade_seven_rule(grade):
    text = prompts.build_explain_instructions(
        grade, "Površina pravougaonika i kvadrata", "Četverougao, obim i površina")
    assert OPERATION_MARKER not in text


@pytest.mark.parametrize("mode", ["explain", "practice", "kontrolni"])
def test_the_grade_seven_rule_is_shared_by_every_grade_rule_mode(mode):
    assert OPERATION_MARKER in rules.build_shared_math_rules(
        7, "Površina trougla", "Četverougao, obim i površina", mode=mode)


def test_quick_is_untouched_by_the_two_level_policy():
    """Quick namjerno nema razredna kurikularna ograničenja."""
    for grade in (6, 7, 8):
        quick = rules.build_shared_math_rules(
            grade, "Površina trougla", "Mnogougao, kružnica i krug", mode="quick")
        assert OPERATION_MARKER not in quick
        assert DISPLAY_BAN_MARKER not in quick


# ---------------------------------------------------------------------------
# 6) FORMULE — recept za račun ide po OPERACIONOJ sposobnosti
# ---------------------------------------------------------------------------

def test_grade_seven_no_longer_receives_root_formulas():
    text = rules.build_shared_math_rules(
        7, "Dijagonala pravougaonika", "Četverougao, obim i površina", mode="explain")
    assert "Heronova formula" not in text
    assert "Pravilni šestougao" not in text


def test_grade_eight_keeps_root_formulas():
    text = rules.build_shared_math_rules(
        8, "Površina trougla", "Mnogougao, kružnica i krug", mode="explain")
    assert "Heronova formula" in text


# ---------------------------------------------------------------------------
# 7) REPRODUKCIJA IZVORNOG NALAZA G7-E4 (bez ijednog poziva modela)
# ---------------------------------------------------------------------------

def test_g7_e4_prompt_now_forbids_teaching_root_extraction():
    """Prije: 7. razred je na ovoj lekciji dobio $a=\\sqrt{20}=2\\sqrt{5}$."""
    text = prompts.build_explain_instructions(
        7, "Površina pravougaonika i kvadrata - obnova",
        "Četverougao, obim i površina")
    assert OPERATION_MARKER in text
    assert "ne svodi korijene" in text
    assert "uči kasnije" in text


def test_g7_e4_practice_equivalent_is_rejected_before_publication():
    policy = policy_for(7, lesson_id="7-05-019",
                        title="Površina pravougaonika i kvadrata - obnova",
                        oblast="Četverougao, obim i površina")
    assert pp.RADICAL_OPERATION_CODE in pp.answer_policy_failures(
        policy, "$2" + SQRT + "{5}\\,\\text{cm}$")


# ---------------------------------------------------------------------------
# 8) OŽIČENJE — provjera ne smije biti tiho neaktivna
# ---------------------------------------------------------------------------

def test_deterministic_candidate_field_name_is_wired_correctly():
    """Zaustavlja tihi no-op.

    Prvi pokušaj ožičenja čitao je `answer_display`, a deterministički paket
    polje zove `display_answer` — `getattr` je uredno vraćao prazan string i
    provjera se NIKAD nije izvršila. Test veže IME polja za stvarni paket."""
    import random

    from matbot.deterministic import equations
    from matbot.tutor import lesson_context as lesson_context_module
    from matbot.tutor import pipeline as tutor_pipeline

    context = lesson_context_module.build(6, "6-07-002")
    package = equations.generate_package(
        lesson_id="6-07-002", lesson_title=context.title,
        parameters=context.semantic_contract.parameters, level=1,
        rng=random.Random(0), policy=context.practice_policy)
    assert hasattr(package, "display_answer")
    assert not hasattr(package, "answer_display")
    source = (ROOT / "matbot" / "tutor" / "pipeline.py").read_text(encoding="utf-8")
    assert 'expected_answer=getattr(candidate, "display_answer"' in source


def test_publication_checks_the_expected_answer_surface():
    """Objava mora zvati strožu provjeru nad `expected`, ne samo po površinama."""
    source = (ROOT / "matbot" / "tutor" / "pipeline.py").read_text(encoding="utf-8")
    assert "practice_policy.answer_policy_failures(policy, expected)" in source


# ---------------------------------------------------------------------------
# 9) ZATVARANJE PRED IZDANJE — semantika odgovora i Pitagorina teorema
# ---------------------------------------------------------------------------

def test_no_model_declared_field_is_used_to_authorise_the_answer_role():
    """Kljucna arhitektonska zabrana.

    `task_type` i `task_signature.answer_type` popunjava MODEL, a nijedan
    validator ne provjerava njihovu vrijednost (vidi matbot/hint_policy.py).
    Da ih `answer_policy_failures` konsumira, model bi mogao sam sebi odobriti
    izlaz iz kapije — tacno ono sto `reviewer_authority` doktrina zabranjuje."""
    import inspect

    source = inspect.getsource(pp.answer_policy_failures)
    for forbidden in ("task_type", "answer_type", "task_signature"):
        assert forbidden not in source.split('"""')[2], forbidden


def test_the_recognition_gap_is_documented_where_the_check_lives():
    doc = pp.answer_policy_failures.__doc__
    assert "hint_policy" in doc and "session_task_class" in doc
    assert "family=None" in doc or "NEMA ugovor" in doc


# --- Pitagorina teorema: ista dvonivovska sposobnost ----------------------

PYT_MARKER = "PITAGORINA TEOREMA NIJE METODA"


@pytest.mark.parametrize("grade,allowed", [(6, False), (7, False), (8, True), (9, True)])
def test_pythagoras_capability_matrix(grade, allowed):
    assert pp.pythagoras_operation_allowed_for_grade(grade) is allowed
    assert policy_for(grade).pythagoras_operation_allowed is allowed


def test_pythagoras_boundary_is_derived_from_one_constant():
    assert pp.PYTHAGORAS_OPERATION_GRADE == 8
    assert pp._PYTHAGORAS_OPERATION_FORBIDDEN_GRADES == tuple(
        range(pp._LOWEST_SUPPORTED_GRADE, pp.PYTHAGORAS_OPERATION_GRADE))


@pytest.mark.parametrize("grade", [6, 7])
def test_lower_grades_are_told_pythagoras_is_not_their_method(grade):
    text = prompts.build_explain_instructions(
        grade, "Pravougli trougao i posebni uglovi", "Ugao i trougao")
    assert PYT_MARKER in text
    assert text.count(PYT_MARKER) == 1


@pytest.mark.parametrize("grade", [8, 9])
def test_introduction_grade_and_later_keep_pythagoras(grade):
    text = prompts.build_explain_instructions(
        grade, "Pravougli trougao i posebni uglovi", "Ugao i trougao")
    assert PYT_MARKER not in text
    assert "Pitagorina teorema:" in text          # formula ostaje


@pytest.mark.parametrize("grade,present", [(6, False), (7, False), (8, True), (9, True)])
def test_pythagoras_formula_row_is_filtered_below_the_introduction_grade(grade, present):
    text = rules.build_shared_math_rules(
        grade, "Pravougli trougao i posebni uglovi", "Ugao i trougao", mode="explain")
    assert ("Pitagorina teorema:" in text) is present


def test_the_concept_survives_even_when_the_theorem_is_filtered():
    """POJAM pravouglog trougla jeste gradivo 7. razreda — filtrira se RED."""
    text = rules.build_shared_math_rules(
        7, "Pravougli trougao i posebni uglovi", "Ugao i trougao", mode="explain")
    assert "PRAVOUGLI TROUGAO" in text.upper()
    assert "hipotenuza" in text.lower()
    assert "Pitagorina teorema:" not in text


def test_quick_is_untouched_by_the_pythagoras_capability():
    for grade in (6, 7):
        quick = rules.build_shared_math_rules(
            grade, "Pravougli trougao i posebni uglovi", "Ugao i trougao", mode="quick")
        assert PYT_MARKER not in quick
        assert "Pitagorina teorema:" in quick
