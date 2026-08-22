# -*- coding: utf-8 -*-
"""Zajednički blok zapisa NE smije protivrječiti razrednoj zabrani korijena.

ŽIVI NALAZ (produkcija 0a2f087, slučaj S11): šestaš je na „Koliko je
$\\sqrt{36}$?" dobio „Kvadratni korijen nije gradivo 6. razreda" — i odmah
zatim „$\\sqrt{36}=6$". Mjereno je da je isti prompt nosio zabranu I recept:
`Korijen: $\\sqrt{20}$`, „uvijek $\\sqrt{20}$", „Zadrži TAČAN oblik s
korijenom", te uzorno računanje `$24\\sqrt{3}$` i `$54\\sqrt{3}\\,\\text{cm}^3$`.

Ovi testovi zaključavaju podjelu po VEĆ POSTOJEĆIM sposobnostima
(`radical_notation_allowed` / `radical_operation_allowed`) — bez ijedne nove
tabele razreda i bez grananja po ID-ju lekcije.
"""
import re

import pytest

from matbot import practice_policy, prompts, rules

RADICAL_TOKEN = "\\sqrt"
KORIJEN_WORD = re.compile(r"korijen\w*|korjenov\w*", re.IGNORECASE)

# Reprezentativne lekcije: prva je lekcija iz živog kvara S11.
LESSONS = (
    (6, "Mnogougao/mnogokut", "Skupovi tačaka, kružnica i krug"),
    (6, "Kružnica i krug", "Skupovi tačaka, kružnica i krug"),
    (7, "Skup racionalnih brojeva Q", "Racionalni brojevi"),
    (8, "Korijen proizvoda i količnika", "Realni brojevi, korijeni i stepeni"),
    (9, "Mreža valjka", "Tijela"),
)


# ---------------------------------------------------------------------------
# 6. RAZRED — ZABRANA BEZ PROTIVRJEČNOSTI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ("explain", "practice"))
def test_grade_six_shared_notation_block_never_teaches_radicals(mode):
    block = rules.notation_rules_for_grade(6, mode=mode)
    assert RADICAL_TOKEN not in block
    assert not KORIJEN_WORD.search(block)


@pytest.mark.parametrize("mode", ("explain", "practice"))
def test_grade_six_keeps_the_radical_ban_itself(mode):
    full = rules.build_shared_math_rules(
        grade=6, mode=mode, oblast="Skupovi tačaka, kružnica i krug",
        lesson_title="Mnogougao/mnogokut")
    assert "NIJE gradivo ovog razreda" in full
    assert "ni kao međukorak" in full


@pytest.mark.parametrize("mode", ("explain", "practice"))
def test_grade_six_has_no_exact_radical_form_instruction(mode):
    block = rules.notation_rules_for_grade(6, mode=mode)
    assert "TAČAN oblik s korijenom" not in block
    # Uputa o tačnom obliku OSTAJE — samo za $\pi$, koji JESTE gradivo 6.
    assert "TAČAN oblik s $\\pi$" in block


@pytest.mark.parametrize("mode", ("explain", "practice"))
def test_grade_six_gets_no_worked_radical_example(mode):
    block = rules.notation_rules_for_grade(6, mode=mode)
    for banned in ("24\\sqrt{3}", "54\\sqrt{3}", "\\sqrt{20}", "\\sqrt{3}"):
        assert banned not in block, banned


def test_grade_six_final_explain_prompt_is_radical_free_outside_the_ban():
    """Jedini red koji 6. razredu smije spomenuti korijen je SAMA ZABRANA."""
    text = prompts.build_explain_instructions(
        6, "Mnogougao/mnogokut", "Skupovi tačaka, kružnica i krug")
    mentions = [l for l in text.splitlines()
                if RADICAL_TOKEN in l or KORIJEN_WORD.search(l)]
    assert len(mentions) == 1, mentions
    assert "NIJE gradivo ovog razreda" in mentions[0]


# ---------------------------------------------------------------------------
# PITAGORA (Faza prije) NE SMIJE UVESTI KORIJEN NA MALA VRATA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("grade", (6, 7))
def test_pythagoras_boundary_rule_never_demonstrates_a_root(grade):
    rule = practice_policy.pythagoras_operation_rule_text(
        practice_policy.resolve(grade=grade))
    assert rule, "razred bez teoreme mora dobiti pravilo"
    assert RADICAL_TOKEN not in rule
    assert not KORIJEN_WORD.search(rule)


def test_grade_six_right_triangle_prompt_stays_radical_free():
    text = prompts.build_explain_instructions(
        6, "Vrste uglova: nula, oštri, pravi, tupi, opruženi i puni ugao", "Uglovi")
    assert "PITAGORINA TEOREMA NIJE METODA" in text
    mentions = [l for l in text.splitlines()
                if RADICAL_TOKEN in l or KORIJEN_WORD.search(l)]
    assert len(mentions) == 1, mentions


# ---------------------------------------------------------------------------
# 7. RAZRED — PRIKAZ OSTAJE, RAČUN NE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ("explain", "practice"))
def test_grade_seven_keeps_radical_notation_guidance(mode):
    block = rules.notation_rules_for_grade(7, mode=mode)
    assert "Korijen: $\\sqrt{20}$" in block
    assert "\\frac, \\sqrt, ^ (stepen), \\cdot" in block


@pytest.mark.parametrize("mode", ("explain", "practice"))
def test_grade_seven_loses_operational_radical_guidance(mode):
    block = rules.notation_rules_for_grade(7, mode=mode)
    assert "TAČAN oblik s korijenom" not in block
    assert "24\\sqrt{3}" not in block, "uzorno RAČUNANJE s korijenom"


def test_grade_seven_recognition_rule_is_still_shipped():
    full = rules.build_shared_math_rules(
        grade=7, mode="explain", oblast="Racionalni brojevi",
        lesson_title="Skup racionalnih brojeva Q")
    assert "SMIJE SE SPOMENUTI, NE I RAČUNATI" in full


# ---------------------------------------------------------------------------
# 8-9. RAZRED I QUICK — BAJT ZA BAJT NEPROMIJENJENI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("grade", (8, 9))
@pytest.mark.parametrize("mode", ("explain", "practice"))
def test_operating_grades_receive_the_untouched_block(grade, mode):
    assert rules.notation_rules_for_grade(grade, mode=mode) is not None
    assert rules.notation_rules_for_grade(grade, mode=mode) == rules._MATH_NOTATION_RULES


@pytest.mark.parametrize("grade", (6, 7, 8, 9))
def test_quick_never_receives_curricular_notation_filtering(grade):
    """Quick nema razredna kurikularna ograničenja — vidi build_shared_math_rules."""
    assert rules.notation_rules_for_grade(grade, mode="quick") == rules._MATH_NOTATION_RULES


# ---------------------------------------------------------------------------
# JEDAN VLASNIK TABELE — filtriranje se izvodi iz POSTOJEĆIH sposobnosti
# ---------------------------------------------------------------------------

def test_filtering_is_driven_by_the_canonical_capabilities_only():
    source = (rules.__file__ and open(rules.__file__, encoding="utf-8").read())
    assert "radical_notation_allowed_for_grade" in source
    assert "radical_operation_allowed_for_grade" in source
    # Nikakva nova razredna lista ni ID lekcije u ovom sloju.
    assert "_RADICAL_FORBIDDEN_GRADES" not in source
    assert "_RADICAL_OPERATION_FORBIDDEN_GRADES" not in source
    assert not re.search(r"grade\s*==\s*6", source), "nema `if grade == 6`"


def test_replacements_fail_loudly_if_the_source_block_changes():
    """Tiho ne-djelovanje je gore od pada: `_apply` mora dići grešku."""
    with pytest.raises(AssertionError):
        rules._apply("tekst bez ijednog očekivanog reda",
                     rules._NOTATION_WITHOUT_RADICAL_DISPLAY)


@pytest.mark.parametrize("grade,title,oblast", LESSONS)
def test_every_probe_lesson_still_builds_a_non_empty_prompt(grade, title, oblast):
    text = rules.build_shared_math_rules(
        grade=grade, mode="explain", oblast=oblast, lesson_title=title)
    assert len(text) > 3000
    assert "PRAVILA MATEMATIČKOG ZAPISA" in text
