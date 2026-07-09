# -*- coding: utf-8 -*-
"""Audit — deterministička provjera odgovora (matbot.answer_checker).

Primjeri A–D iz prijave bugova su OVDJE regresioni slučajevi opštih klasa
zadataka (komplement razlomka, više numerisanih stavki, pretvaranje mješovitog
u nepravi razlomak) — logika NIJE hardkodirana na njih.
"""
from fractions import Fraction

import pytest

from matbot.answer_checker import (
    check_practice_answer,
    derive_expected,
    format_check_block,
    parse_student_answers,
    split_numbered_items,
    summarize_result,
)

TASK_C = (
    "1. Ako je obojano 2/5 kruga, koji dio nije obojen?\n"
    "2. Uči se da je prepelica pojela 3/4 zrna riže. Koliko zrna ostaje?\n"
    "3. Na torti je pojedeno 1/6. Koliki dio torte je ostao?"
)
TASK_D = (
    "1. Ako je obojeno 5/12 pizze, koji dio nije obojen?\n"
    "2. Dječak je potrošio 3/10 novca. Koji dio novca mu je ostao?\n"
    "3. Pretvori 2 1/4 u nepravi razlomak."
)


def _verdicts(result):
    return [i.verdict for i in result.items]


# --- jedan zadatak, jedan odgovor ---------------------------------------------------

def test_correct_single_fraction_answer_example_a():
    r = check_practice_answer("Ako su obojane 3/8 kruga, koji dio nije obojen?", "5/8")
    assert r.checkable and _verdicts(r) == ["correct"]
    assert r.items[0].expected.value == Fraction(5, 8)


def test_correct_single_fraction_answer_example_b():
    r = check_practice_answer("Ako je pojedeno 7/12 pizze, koji dio je ostao?", "5/12")
    assert r.checkable and _verdicts(r) == ["correct"]


def test_incorrect_single_fraction_answer():
    r = check_practice_answer("Ako su obojane 3/8 kruga, koji dio nije obojen?", "3/8")
    assert r.checkable and _verdicts(r) == ["incorrect"]
    assert r.items[0].expected.value == Fraction(5, 8)


def test_equivalent_fraction_accepted_as_correct():
    r = check_practice_answer("Ako je pojedeno 7/12 pizze, koji dio je ostao?", "10/24")
    assert _verdicts(r) == ["correct"]


def test_answer_inside_short_sentence_still_parsed():
    r = check_practice_answer(
        "Ako su obojane 3/8 kruga, koji dio nije obojen?", "mislim da je 5/8"
    )
    assert _verdicts(r) == ["correct"]


# --- mješoviti i nepravi razlomci ---------------------------------------------------

def test_mixed_number_equals_improper_fraction():
    r = check_practice_answer("Izračunaj: 1/2 + 3/4", "1 1/4")
    assert _verdicts(r) == ["correct"]
    assert r.items[0].expected.value == Fraction(5, 4)


def test_convert_mixed_to_improper_correct():
    r = check_practice_answer("Pretvori 2 1/4 u nepravi razlomak.", "9/4")
    assert _verdicts(r) == ["correct"]


def test_convert_mixed_to_improper_wrong_form_is_not_incorrect():
    # vrijednost ista, ali oblik nije traženi — ne smije biti "incorrect"
    r = check_practice_answer("Pretvori 2 1/4 u nepravi razlomak.", "2 1/4")
    assert _verdicts(r) == ["correct_value_wrong_form"]


def test_latex_task_notation_parsed():
    r = check_practice_answer(
        r"Pretvori \(2\frac{1}{4}\) u nepravi razlomak.", "9/4"
    )
    assert _verdicts(r) == ["correct"]


# --- direktan račun -----------------------------------------------------------------

def test_arithmetic_with_unicode_dot_and_latex():
    assert _verdicts(check_practice_answer("Izračunaj: 1/2 · 5/9.", "5/18")) == ["correct"]
    assert _verdicts(check_practice_answer(
        "Izračunaj: $$\\frac{3}{4} \\cdot \\frac{2}{5}$$", "3/10")) == ["correct"]


def test_bare_expression_task_is_checkable():
    r = check_practice_answer("3/4 * 2/5", "3/10")
    assert _verdicts(r) == ["correct"]
    assert _verdicts(check_practice_answer("3/4 * 2/5", "5/18")) == ["incorrect"]
    assert _verdicts(check_practice_answer("Izračunaj 3/4 · 2.", "3/2")) == ["correct"]


def test_decimal_comma_answer():
    r = check_practice_answer("Izračunaj: 0,5 + 0,25", "0,75")
    assert _verdicts(r) == ["correct"]


def test_negative_numbers():
    r = check_practice_answer("Izračunaj: -7 + 12.", "5")
    assert _verdicts(r) == ["correct"]
    assert _verdicts(check_practice_answer("Izračunaj: -7 + 12.", "-5")) == ["incorrect"]


def test_simplify_requires_reduced_form():
    assert _verdicts(check_practice_answer("Skrati razlomak 6/10.", "3/5")) == ["correct"]
    assert _verdicts(check_practice_answer("Skrati razlomak 6/10.", "6/10")) == [
        "correct_value_wrong_form"
    ]
    assert _verdicts(check_practice_answer("Skrati razlomak 18/24.", "9/12")) == [
        "correct_value_wrong_form"
    ]
    assert _verdicts(check_practice_answer("Skrati razlomak 18/24.", "3/4")) == ["correct"]


def test_expand_fraction_to_target_denominator():
    task = "Proširi razlomak 3/5 na desni nazivnik 15."
    correct = check_practice_answer(task, "9/15")
    assert _verdicts(correct) == ["correct"]
    assert summarize_result(correct)["items"][0]["expected"] == "9/15"

    wrong_form = check_practice_answer(task, "6/10")
    assert _verdicts(wrong_form) == ["correct_value_wrong_form"]
    assert summarize_result(wrong_form)["items"][0]["expected"] == "9/15"

    assert _verdicts(check_practice_answer("Prosiri razlomak 3/5 do nazivnika 15.", "9/15")) == [
        "correct"
    ]


def test_rate_problem_time_distance_speed():
    task = "Automobil prijeđe 65 km za 1 sat. Koliko sati mu treba za 260 km?"
    e = derive_expected(task)
    assert e is not None
    assert e.value == Fraction(4)
    assert e.unit == "sata"
    assert "260 : 65 = 4 sata" in e.basis
    assert _verdicts(check_practice_answer(task, "4 sata")) == ["correct"]
    assert _verdicts(check_practice_answer(task, "2 sata")) == ["incorrect"]


def test_rate_problem_distance_and_speed_forms():
    assert derive_expected(
        "Koliki put pređe automobil brzinom 65 km/h za 4 sata?"
    ).value == Fraction(260)
    assert derive_expected(
        "Kolika brzina ako pređe 260 km za 4 sata?"
    ).value == Fraction(65)


def test_time_ratio_40_minutes_to_2_hours():
    e = derive_expected("Odredi omjer 40 minuta prema 2 sata.")
    assert e is not None
    assert e.value == Fraction(1, 3)
    assert e.required_form == "fraction"


# --- više numerisanih stavki --------------------------------------------------------

def test_multi_question_all_correct_example_c():
    r = check_practice_answer(TASK_C, "1) 3/5 2) 1/4 3) 5/6")
    assert r.checkable and _verdicts(r) == ["correct", "correct", "correct"]


def test_multi_question_ordered_answers_without_markers():
    r = check_practice_answer(TASK_C, "3/5, 1/4 i 5/6")
    assert _verdicts(r) == ["correct", "correct", "correct"]


def test_multi_question_some_wrong():
    r = check_practice_answer(TASK_C, "1) 2/5 2) 1/4 3) 5/6")
    assert _verdicts(r) == ["incorrect", "correct", "correct"]


def test_multi_question_missing_item_example_d():
    r = check_practice_answer(TASK_D, "2) 7/10 3) 9/4")
    assert _verdicts(r) == ["missing", "correct", "correct"]
    block = format_check_block(r)
    assert "Stavka 1: BEZ ODGOVORA" in block
    assert "NE ocjenjuj je kao netačnu" in block
    assert "Stavka 2: TAČNO" in block
    assert "Stavka 3: TAČNO" in block


def test_multi_question_only_third_referenced_conceptual_answer():
    task = (
        "1. Šta je djelilac?\n"
        "2. Kada je broj djeljiv sa 2?\n"
        "3. Zašto se broj ne može dijeliti nulom?"
    )
    r = check_practice_answer(
        task,
        "Odgovor na treće pitanje je da se broj ne može dijeliti sa nulom.",
    )
    assert r.checkable
    assert _verdicts(r) == ["not_attempted", "not_attempted", "unverified"]
    block = format_check_block(r)
    assert "Stavka 1: NIJE POKUŠANA" in block
    assert "Stavka 2: NIJE POKUŠANA" in block
    assert "Stavka 3: nije automatski provjerena" in block
    assert "NE izmišljaj njegov odgovor" in block
    assert "netačnu" in block


def test_unparseable_numbered_answer_is_unverified_not_missing():
    r = check_practice_answer(TASK_C, "1) 3/5 ili 2/5 2) 1/4 3) 5/6")
    assert _verdicts(r)[0] == "unverified"      # odgovorio je, samo nejasno


def test_single_unnumbered_answer_to_multi_item_task_not_guessed():
    r = check_practice_answer(TASK_C, "3/5")
    assert not r.checkable


# --- konzervativnost (nikad izmišljeno "netačno") -----------------------------------

def test_complement_without_dio_word_confirms_but_never_rejects():
    # "koliko minuta ostaje" — jedinica odgovora nije nužno razlomak
    task = "Od 3/4 sata koliko minuta ostaje do punog sata?"
    r = check_practice_answer(task, "15")
    assert _verdicts(r) == ["unverified"]       # 15 minuta NIJE proglašeno netačnim


def test_extra_numbers_disable_complement_shortcut():
    # "3/10 od 50 KM" — tačan odgovor je 35 KM, ne 7/10; kod ne smije presuditi
    r = check_practice_answer(
        "Dječak je potrošio 3/10 od 50 KM. Koliko mu je ostalo?", "35"
    )
    assert not r.has_verdicts


def test_ne_znam_is_not_graded():
    r = check_practice_answer("Izračunaj: 1/2 + 1/3", "ne znam")
    assert not r.checkable


def test_unsolvable_task_not_checkable():
    r = check_practice_answer("Objasni zašto je 1/2 veće od 1/3.", "zato")
    assert not r.checkable


def test_never_raises_on_garbage():
    assert check_practice_answer(None, None).checkable is False
    assert check_practice_answer("", "5/8").checkable is False
    assert check_practice_answer("x" * 5000, "??!!").checkable is False


# --- pomoćne funkcije ---------------------------------------------------------------

def test_split_numbered_items_rejects_numbers_inside_sentence():
    assert split_numbered_items("Zadatak 12. januara: izračunaj 5. stepen broja 2") == []


def test_split_numbered_items_requires_sequence_from_one():
    assert split_numbered_items("2. prvi 3. drugi") == []
    items = split_numbered_items("1. prvi zadatak? 2. drugi zadatak?")
    assert [n for n, _t in items] == [1, 2]


def test_parse_student_answers_marker_needs_punctuation():
    # "2 1/4" je mješoviti broj, NE "stavka 2 → 1/4"
    mode, answers = parse_student_answers("2 1/4")
    assert mode == "single"
    assert answers[1].value == Fraction(9, 4)


def test_format_block_forbids_contradiction():
    r = check_practice_answer("Ako su obojane 3/8 kruga, koji dio nije obojen?", "5/8")
    block = format_check_block(r)
    assert "POUZDANA" in block
    assert "nikad ne proglašavaj netačnom" in block


def test_summarize_result_shape():
    r = check_practice_answer(TASK_D, "2) 7/10 3) 9/4")
    s = summarize_result(r)
    assert [i["verdict"] for i in s["items"]] == ["missing", "correct", "correct"]
    assert s["items"][1]["expected"] == "7/10"
    assert summarize_result(check_practice_answer("Objasni pojam.", "ok")) is None
