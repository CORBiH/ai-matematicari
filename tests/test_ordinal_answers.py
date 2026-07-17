# -*- coding: utf-8 -*-
"""AUD-02 — parsing ordinalno imenovanih odgovora ('prvi je 6/9, drugi 4/8').

Deterministički unit testovi (bez mreže). Štite od regresije parsera i
detekcije referenci, uključujući guardove protiv lažnih pozitiva.
"""
import pytest

from matbot.answer_checker import (
    parse_student_answers, detect_referenced_items, _numbered_nonanswer_items,
    check_practice_answer,
)


def _ans(msg):
    mode, m = parse_student_answers(msg)
    return mode, {k: (v.raw if v else None) for k, v in m.items()}


def test_ordinal_named_answers_parsed():
    mode, ans = _ans("prvi je 6/9, drugi 4/8, treci ne znam")
    assert mode == "numbered"
    assert ans[1] == "6/9" and ans[2] == "4/8" and ans[3] is None


def test_ordinal_named_answers_without_diacritics():
    mode, ans = _ans("prvi = 6/9 drugi je 4/8 treci 5/6")
    assert mode == "numbered"
    assert ans == {1: "6/9", 2: "4/8", 3: "5/6"}


def test_ordinal_named_answers_with_diacritics():
    mode, ans = _ans("prvi je 6/9, drugi 4/8, treći je 5/6, četvrti 7")
    assert mode == "numbered"
    assert ans == {1: "6/9", 2: "4/8", 3: "5/6", 4: "7"}


def test_ordinal_named_answers_with_newlines():
    mode, ans = _ans("prvi = 6/9\ndrugi je 4/8\ntreći je 5/6")
    assert mode == "numbered"
    assert ans == {1: "6/9", 2: "4/8", 3: "5/6"}


def test_ordinal_gender_variants():
    # prva/prvo, druga/drugo, treca/trece — svi rodovi
    assert _ans("prva 6/9 druga 4/8")[1] == {1: "6/9", 2: "4/8"}
    assert _ans("prvo je 6/9 i drugo je 4/8")[1] == {1: "6/9", 2: "4/8"}


def test_ordinal_nonanswer_item_remains_unattempted():
    # 'ne znam' po stavci = nepokušano; ne ulazi u matematičku provjeru
    assert _numbered_nonanswer_items("prvi je 6/9, drugi 4/8, treci ne znam") == {3}
    task = "1. Izračunaj: 2/9 + 4/9\n2. Izračunaj: 5/8 - 1/8\n3. Izračunaj: 1/2 + 1/3"
    res = check_practice_answer(task, "prvi je 6/9, drugi 4/8, treci ne znam")
    by_n = {i.n: i.verdict for i in res.items}
    assert by_n[1] == "correct_equivalent_form" and by_n[2] == "correct_equivalent_form"
    assert by_n[3] in ("missing", "not_attempted")


@pytest.mark.parametrize("msg", [
    "prvi korak je da nadjes zajednicki nazivnik",
    "prvo treba sabrati brojnike",
    "drugi nacin je laksi",
    "treći zadatak mi nije jasan",
    "objasni prvi korak",
    "prvi primjer mi je bio laksi",
    "drugi razlomak treba prosiriti",
])
def test_ordinal_answer_false_positive_guards(msg):
    mode, _ = parse_student_answers(msg)
    assert mode == "none"                       # NIJE predani odgovor


@pytest.mark.parametrize("msg,expected", [
    ("treci je 5/6", {3}),
    ("drugi = 4/8", {2}),
    ("peti 7", {5}),
    ("treći je x=4", {3}),
])
def test_ordinal_single_reference_detected(msg, expected):
    assert detect_referenced_items(msg, [1, 2, 3, 4, 5]) == expected


def test_single_ordinal_answer_attributed_via_refs():
    # 'treci je 5/6' + aktivni 3-stavkovni zadatak → ocjenjuje SAMO stavku 3
    task = "1. Izračunaj: 2/9 + 4/9\n2. Izračunaj: 5/8 - 1/8\n3. Izračunaj: 1/2 + 1/3"
    res = check_practice_answer(task, "treci je 5/6")
    by_n = {i.n: i.verdict for i in res.items}
    assert by_n[3] == "correct"
    assert by_n[1] in ("not_attempted", "missing") and by_n[2] in ("not_attempted", "missing")


@pytest.mark.parametrize("msg,mode,pair", [
    ("1. je 6/9", "numbered", (1, "6/9")),
    ("2) 4/8", "numbered", (2, "4/8")),
    ("3. je 5/6", "numbered", (3, "5/6")),
    ("5", "single", (1, "5")),
])
def test_existing_numeric_formats_preserved(msg, mode, pair):
    m, ans = _ans(msg)
    assert m == mode and ans[pair[0]] == pair[1]


def test_za_2_je_reference_preserved():
    assert 2 in detect_referenced_items("za 2. je 4/8", [1, 2, 3])
