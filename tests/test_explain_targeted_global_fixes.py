"""Ciljane globalne popravke iz cross-curriculum audita moda „Objasni mi“.

Tri nalaza — DVA popravljena, jedan izmjeren pa svjesno odbijen:

  • GRANICA ZAPISA KORIJENA (nalaz G7-E4): defekt je stvaran, ali paušalno
    proširenje zabrane na 7. razred obara legitimno prepoznavanje iracionalnih
    brojeva u lekciji o skupu Q. Granica OSTAJE nepromijenjena, a razlog je
    ovdje zaključan da se popravka ne pokuša ponovo naslijepo;
  • SIMBOL KOJI JE CIJELI PREDMET PORUKE (nalaz G8-A7) smije demovirati
    izabranu lekciju, a isti simbol UNUTAR šire formule ne smije — popravljeno;
  • KONVENCIJA $\\mathbb{N}$ / $\\mathbb{N}_0$ (nalaz G8-A1) stoji na jednom
    mjestu i stiže svim modovima — popravljeno.

Uz njih i četvrta, evaluacijska: svako mjesto na kojem Explain odbije odgovor
mora u log upisati KOJU kapiju je palo — pet od 120 slučajeva audita ostalo je
neobjašnjeno upravo zato što se to iz loga nije moglo pročitati.
"""
import re
from pathlib import Path

import pytest

from matbot import lesson_relevance as lr
from matbot import practice_policy as pp
from matbot import prompts, rules

ROOT = Path(__file__).resolve().parent.parent
SQRT = "\\sqrt"


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) GRANICA ZAPISA KORIJENA — NALAZ POTVRĐEN, PAUŠALNA POPRAVKA ODBIJENA
# ---------------------------------------------------------------------------
# Audit (G7-E4) je dokazao da 7. razred dobija $\sqrt{20}$ kao redovan postupak,
# i to JESTE defekt. Ali proširenje zabrane na 7. razred je izmjereno i ODBIJENO:
# lekcija 7-03-001 „Skup racionalnih brojeva Q“ nosi kanonski ishod „shvatiti
# potrebu proširivanja skupa racionalnih brojeva“, a to se predaje tako što se
# $\sqrt{2}$ ili $\pi$ POKAŽE kao neprimjer. Paušalna zabrana obara i taj,
# ispravan zadatak (mjereno: 18 padova u zatečenoj sviti).
#
# Ovi testovi zato zaključavaju ZATEČENU granicu i njen razlog, da se popravka
# ne pokuša ponovo naslijepo. Pravi lijek traži razliku „zapis smije biti
# PRIKAZAN“ naspram „odgovor ne smije TRAŽITI korijen“ — zaseban zahvat.


@pytest.mark.parametrize("grade,allowed",
                         [(6, False), (7, True), (8, True), (9, True)])
def test_radical_boundary_is_unchanged_by_this_patch(grade, allowed):
    assert pp.radical_notation_allowed_for_grade(grade) is allowed


def test_the_recognition_use_of_irrationals_at_grade_seven_still_passes():
    """Baš onaj slučaj koji je paušalnu zabranu oborio — mora ostati legalan."""
    policy = pp.resolve(grade=7, lesson_id="7-03-001",
                        lesson_title="Skup racionalnih brojeva Q",
                        oblast="Racionalni brojevi")
    codes = pp.text_policy_failures(
        policy, "Koji broj NIJE racionalan? $" + SQRT + "{2}$")
    assert pp.GRADE_CAPABILITY_CODE not in codes


def test_grade_six_radical_protection_is_intact():
    policy = pp.resolve(grade=6, lesson_id="6-08-004",
                        lesson_title="Mnogougao", oblast="Skupovi tačaka")
    codes = pp.text_policy_failures(policy, "Rezultat je $" + SQRT + "{20}$.")
    assert pp.GRADE_CAPABILITY_CODE in codes
    text = prompts.build_explain_instructions(
        6, "Površina pravougaonika i kvadrata", "Četverougao, obim i površina")
    assert "NIJE gradivo ovog razreda" in text


def test_the_declined_extension_is_documented_where_the_boundary_lives():
    """Odbijena popravka mora ostaviti trag, inače se ponovi."""
    source = _source("matbot/practice_policy.py")
    assert "G7-E4" in source
    assert "SKUPU RACIONALNIH" in source.upper()
    # Arhitektonska kapija zabranjuje ID lekcije bilo gdje u matbot/ —
    # dokaz se zato imenuje opisno (tests/test_legacy_routing_parity.py).
    import re as _re
    assert not _re.search(r"\d-\d{2}-\d{3}", source)


# ---------------------------------------------------------------------------
# 2) SIMBOLIČKO RUTIRANJE
# ---------------------------------------------------------------------------

BAR_CHART = ("Stupčasti dijagram", "Podaci i vjerovatnoća")
PYTHAGORAS = ("Određivanje nepoznate katete", "Pitagorina teorema i primjene u ravni")
ROOT_LESSON = ("Kvadratni korijen nenegativnog racionalnog broja",
               "Realni brojevi, korijeni i stepeni")


@pytest.mark.parametrize("message", [
    "Koliko je $" + SQRT + "{144}$?",
    "Izračunaj $" + SQRT + "{81}$.",
    "Šta znači $" + SQRT + "{25}$?",
    "Koliko je $" + SQRT + "{0,49}$?",
])
def test_sole_radical_question_demotes_an_unrelated_lesson(message):
    """Nalaz G8-A7: pitanje o korijenu ne smije progutati lekcija o dijagramima."""
    assert lr.lesson_context_is_strong(message, *BAR_CHART) is False


@pytest.mark.parametrize("message", [
    "Zašto je $c=" + SQRT + "{a^2+b^2}$?",
    "Izračunaj $" + SQRT + "{13^2-5^2}$.",
    "Kolika je dijagonala $d=" + SQRT + "{a^2+b^2}$?",
])
def test_radical_inside_a_larger_formula_keeps_the_lesson(message):
    """KONZERVATIVNOST: Pitagorina formula ne smije postati „druga tema“."""
    assert lr.lesson_context_is_strong(message, *PYTHAGORAS) is True


def test_radical_in_a_larger_expression_is_not_a_topic_signal():
    assert lr.lesson_context_is_strong(
        "Koliko je $2+" + SQRT + "{9}$?", *BAR_CHART) is True


def test_sole_radical_stays_strong_when_the_lesson_is_about_roots():
    assert lr.lesson_context_is_strong(
        "Koliko je $" + SQRT + "{144}$?", *ROOT_LESSON) is True


def test_two_math_segments_are_never_a_sole_symbol_signal():
    assert lr.sole_segment_topics(
        "Uporedi $" + SQRT + "{16}$ i $" + SQRT + "{25}$.") == set()


@pytest.mark.parametrize("word", ["korijen", "korijena", "koren", "korjen"])
def test_every_spelling_of_the_word_root_is_recognised(word):
    """`kor[ij]en` je hvatao SAMO „korjen“ — ni ijekavsko „korijen“ (dva znaka)
    ni ekavsko „koren“ (nijedan) nisu pogađali."""
    assert "stepen" in lr.named_topics("Objasni mi šta je {} broja.".format(word))


def test_prose_root_question_demotes_an_unrelated_lesson():
    assert lr.lesson_context_is_strong(
        "Objasni mi šta je korijen broja.", *BAR_CHART) is False


@pytest.mark.parametrize("word", ["korištenje", "koristi", "korisno", "korak", "korice"])
def test_the_broadened_word_pattern_has_no_false_positives(word):
    assert "stepen" not in lr.named_topics("Ovo je {}.".format(word))


def test_percent_symbolic_routing_is_unchanged():
    assert lr.symbolic_topics("Koliko je 15% od 300 KM?") == {"procenat"}
    assert lr.lesson_context_is_strong(
        "Koliko je 15% od 300 KM?", "Osna simetrija u ravni",
        "Izometrijske transformacije i konstrukcije") is False


def test_deictic_messages_still_inherit_the_lesson():
    for message in ("Objasni mi ovo.", "Ne razumijem.", "Daj mi primjer."):
        assert lr.lesson_context_is_strong(message, *BAR_CHART) is True


def test_the_symbolic_rule_is_concept_based_not_symbol_specific():
    """Nov simbol se mora moći dodati kao PODATAK, bez nove politike."""
    assert isinstance(lr._SOLE_SEGMENT_CONCEPTS, tuple)
    for pattern, concept in lr._SOLE_SEGMENT_CONCEPTS:
        assert isinstance(pattern, re.Pattern)
        assert concept in lr._TOPIC_PATTERNS


# ---------------------------------------------------------------------------
# 3) KONVENCIJA N / N_0
# ---------------------------------------------------------------------------

MARKER = "OZNAKA SKUPA PRIRODNIH BROJEVA"


@pytest.mark.parametrize("grade", [6, 7, 8, 9])
def test_the_natural_number_convention_reaches_explain_in_every_grade(grade):
    text = prompts.build_explain_instructions(
        grade, "Skupovi N, Z, Q, I i R i odnosi među njima",
        "Realni brojevi, korijeni i stepeni")
    assert MARKER in text


def test_the_convention_states_both_sets_explicitly():
    line = [row for row in rules.build_shared_math_rules(
        8, "x", "y", mode="explain").splitlines() if MARKER in row][0]
    assert "NE sadrži nulu" in line
    assert "_0" in line


@pytest.mark.parametrize("mode", ["explain", "practice", "quick", "kontrolni"])
def test_the_convention_is_shared_by_every_mode(mode):
    assert MARKER in rules.build_shared_math_rules(8, "x", "y", mode=mode)


def test_the_convention_ships_exactly_once_per_prompt():
    text = prompts.build_explain_instructions(
        8, "Skupovi N, Z, Q, I i R i odnosi među njima",
        "Realni brojevi, korijeni i stepeni")
    assert text.count(MARKER) == 1


# ---------------------------------------------------------------------------
# 4) DIJAGNOSTIKA ODBIJANJA (evaluacija, ne API ugovor)
# ---------------------------------------------------------------------------

EXPECTED_STAGES = ("stage=model_call", "stage=schema", "stage=unexpected",
                   "stage=mathsafe", "stage=numeric_consistency",
                   "stage=geometry_notation")


@pytest.mark.parametrize("stage", EXPECTED_STAGES)
def test_every_explain_rejection_site_records_its_stage(stage):
    assert stage in _source("matbot/explain.py")


def test_no_rejection_site_is_left_without_a_stage():
    source = _source("matbot/explain.py")
    sites = source.count("logger.warning") + source.count("logger.exception")
    assert source.count("stage=") >= sites


def test_internal_stage_names_never_reach_the_student():
    """Kodovi i imena kapija su SAMO za log (CLAUDE.md pravilo 7)."""
    from matbot.practice import SAFE_ERROR_MESSAGE
    for stage in EXPECTED_STAGES:
        assert stage.split("=")[1] not in SAFE_ERROR_MESSAGE
    assert "stage=" not in SAFE_ERROR_MESSAGE
