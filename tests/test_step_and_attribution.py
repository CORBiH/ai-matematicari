# -*- coding: utf-8 -*-
"""2026-07-14: tačan odgovor/međukorak više NIKAD ne smije dobiti "Netačno".

Dvije sistemske popravke (live nalazi sa screenshotova):
1. ATRIBUCIJA — jedan nenumerisan odgovor ("x=4 1/4") na višestavkovni zadatak
   se deterministički pripiše pravoj stavci umjesto da model pogađa.
2. MEĐUKORAK (correct_step) — tvrdnja ekvivalentna zadatku, ali nedovršena
   ("2x<12" za "2x-5<7"), potvrđuje se kao TAČAN KORAK, bez ocjenskih labela
   i bez "došlo je do male greške".
"""
import pytest

from matbot.answer_checker import (
    check_practice_answer,
    format_check_block,
)
from matbot.grading_guard import (
    authoritative_verdict,
    enforce_grading_consistency,
)
from matbot.grading_guard import neutralize_non_answer_grade
from matbot.ai_tutor_service import (
    _flag_non_answer_reflection,
    _run_answer_check,
    _soften_post_hint_reply,
    _task_items_for_response,
)
from matbot.answer_checker import check_practice_answer as _cpa

KONTROLNI = (
    "1. Riješi jednačinu: x + \\frac{3}{4} = 5.\n"
    "2. Riješi nejednačinu: 2x - 5 < 7.\n"
    "3. Riješi jednačinu s razlomkom: \\frac{5}{2}x = 10."
)


def _by_n(result):
    return {i.n: i.verdict for i in result.items}


# --- 1) Atribucija (screenshot 2: "x=4 1/4" → "Netačno, ali blizu si") ---------------

def test_mixed_number_equation_answer_attributed_correct():
    r = check_practice_answer(KONTROLNI, "x=4 1/4")
    assert r.checkable
    assert _by_n(r) == {1: "correct", 2: "not_attempted", 3: "not_attempted"}


def test_attribution_respects_pending_items():
    # stavka 1 već ocijenjena → "x=4" pripada stavci 3 (5/2·x = 10 → x = 4)
    r = check_practice_answer(KONTROLNI, "x=4", pending_items=[2, 3])
    assert _by_n(r) == {2: "not_attempted", 3: "correct"}


def test_attribution_bails_out_when_no_item_matches_and_multiple_pending():
    r = check_practice_answer(KONTROLNI, "x=100", pending_items=[1, 3])
    assert not r.checkable


def test_attributed_result_grades_task_items_state():
    payload = {
        "last_tutor_task": KONTROLNI,
        "student_message": "x=4 1/4",
        "previous_next_state": {
            "task_items": {"labels": [1, 2, 3], "graded": []},
        },
    }
    _run_answer_check(payload)
    assert payload["_current_task_item"] == 1
    state = _task_items_for_response(payload, "")
    assert state == {"labels": [1, 2, 3], "graded": [1]}


def test_guard_rewrites_false_netacno_into_subset_summary():
    r = check_practice_answer(KONTROLNI, "x=4 1/4")
    answer = (
        "Netačno, ali blizu si. Imamo x + 3/4 = 5, pa je x = 17/4, "
        "što je kao mješoviti broj 4 1/4."
    )
    out = enforce_grading_consistency(answer, r)
    assert "Netačno" not in out
    assert out.startswith("Zadatak 1 je tačan.")
    assert "Zadaci 2 i 3 još čekaju tvoj odgovor." in out


# --- 2) Međukorak (screenshot 3: "2x<12" → "došlo je do male greške") ----------------

def test_equivalent_inequality_midstep_is_correct_step():
    r = check_practice_answer(KONTROLNI, "2x<12", pending_items=[2, 3])
    assert _by_n(r) == {2: "correct_step", 3: "not_attempted"}
    assert authoritative_verdict(r) == "step"


def test_final_inequality_forms_are_fully_correct():
    for ans in ("x<6", "x < 6", "6 > x"):
        r = check_practice_answer(KONTROLNI, ans, pending_items=[2, 3])
        assert _by_n(r)[2] == "correct", ans


def test_wrong_inequality_transform_is_incorrect():
    # 2x < 11 ⇒ x < 5,5 ≠ x < 6 — transformacija je stvarno pogrešna
    r = check_practice_answer("Riješi nejednačinu: 2x - 5 < 7", "2x<11")
    assert _by_n(r) == {1: "incorrect"}


def test_equation_midstep_and_final_forms():
    task = "Riješi jednačinu: x + \\frac{3}{4} = 5."
    assert _by_n(check_practice_answer(task, "x = 5 - 3/4")) == {1: "correct_step"}
    assert _by_n(check_practice_answer(task, "x = 17/4")) == {1: "correct"}
    assert _by_n(check_practice_answer(task, "x = 5 + 3/4")) == {1: "incorrect"}


def test_equation_midstep_with_coefficient():
    assert _by_n(check_practice_answer("Riješi jednačinu: 2x - 5 = 7", "2x = 12")) \
        == {1: "correct_step"}


def test_step_never_marks_task_item_graded():
    payload = {
        "last_tutor_task": KONTROLNI,
        "student_message": "2x<12",
        "previous_next_state": {
            "task_items": {"labels": [1, 2, 3], "graded": [1]},
        },
    }
    _run_answer_check(payload)
    assert payload["_current_task_item"] == 2
    state = _task_items_for_response(payload, "")
    assert state == {"labels": [1, 2, 3], "graded": [1]}   # step NE zatvara stavku


def test_soften_post_hint_keeps_confirmed_step_verdict():
    payload = {
        "last_tutor_task": KONTROLNI,
        "student_message": "2x<12",
        "previous_next_state": {
            "task_items": {"labels": [1, 2, 3], "graded": [1]},
        },
    }
    _run_answer_check(payload)
    _soften_post_hint_reply(payload)
    assert payload.get("_post_hint_reply") is True
    assert payload.get("answer_check") is not None          # presuda OSTAJE
    assert not payload.get("_skip_answer_check")            # guard se primjenjuje


def test_soften_post_hint_keeps_arithmetic_midstep():
    # "12/12" poslije hinta = tačan prefiks-međukorak (5/12+7/12) → presuda ostaje
    payload = {
        "last_tutor_task": "Izračunaj \\frac{5}{12} + \\frac{7}{12} - \\frac{3}{12}",
        "student_message": "12/12",
        "previous_next_state": {},
    }
    _run_answer_check(payload)
    _soften_post_hint_reply(payload)
    check = payload.get("answer_check")
    assert check is not None and check.items[0].verdict == "correct_step"
    assert payload.get("_post_hint_reply") is True


def test_soften_post_hint_still_drops_unverified_check():
    # stvarno neprovjerljiv odgovor poslije hinta → kao do sada: model procjenjuje
    payload = {
        "last_tutor_task": "Izračunaj \\frac{5}{12} + \\frac{7}{12} - \\frac{3}{12}",
        "student_message": "saberem brojnike pa oduzmem?",
        "previous_next_state": {},
    }
    _run_answer_check(payload)
    _soften_post_hint_reply(payload)
    assert payload.get("answer_check") is None
    assert payload.get("_post_hint_reply") is True


def test_guard_step_removes_error_claim_and_confirms():
    r = check_practice_answer(KONTROLNI, "2x<12", pending_items=[2, 3])
    answer = (
        "Izgleda da je tu došlo do male greške. Kada si dodao 5 na obje strane "
        "nejednačine 2x - 5 < 7, trebao si dobiti:\n2x < 12, to je super!\n"
        "Sada podijeli s 2. Koliko je to?"
    )
    out = enforce_grading_consistency(answer, r)
    assert "greške" not in out and "greska" not in out.lower()
    assert out.startswith("Tako je")
    assert "2x < 12" in out                                  # račun ostaje


def test_guard_step_strips_hard_labels_too():
    r = check_practice_answer("Riješi jednačinu: 2x - 5 = 7", "2x = 12")
    out = enforce_grading_consistency("Netačno. Trebalo bi x = 6.", r)
    assert not out.startswith("Netačno")
    assert out.startswith("Tako je")


def test_format_block_announces_step_and_forbids_labels():
    r = check_practice_answer(KONTROLNI, "2x<12", pending_items=[2, 3])
    block = format_check_block(r)
    assert "TAČAN MEĐUKORAK" in block
    assert "NIKAD ne reci da je pogriješio" in block
    assert "STIL (TAČAN MEĐUKORAK)" in block


# --- ijekavsko "pogriješio" i meke fraze u guard leksikonu ---------------------------

def test_guard_removes_ijekavian_pogrijesio_on_correct_answer():
    r = check_practice_answer("Izračunaj \\frac{1}{2} + \\frac{1}{4}", "3/4")
    out = enforce_grading_consistency(
        "Pogriješio si u sabiranju, ali rezultat 3/4 je dobar.", r
    )
    assert "pogriješio" not in out.lower()
    assert out.startswith("Tačno.")


def test_guard_removes_soft_error_claim_on_correct_answer():
    r = check_practice_answer("Izračunaj \\frac{1}{2} + \\frac{1}{4}", "3/4")
    out = enforce_grading_consistency(
        "Došlo je do male greške, rezultat je 3/4.", r
    )
    assert "grešk" not in out.lower()
    assert out.startswith("Tačno.")


def test_empathy_phrase_greska_je_dio_ucenja_survives():
    # golo "greška je dio učenja" NIJE tvrdnja o učenikovoj grešci — ne dira se
    r = check_practice_answer("Izračunaj \\frac{1}{2} + \\frac{1}{4}", "3/4")
    out = enforce_grading_consistency(
        "Tačno. Greška je dio učenja, a ti si ovo lijepo riješio: 3/4.", r
    )
    assert "Greška je dio učenja" in out


# --- mixed-broj u linearnim stranama (latentni bug otkriven ovim radom) --------------

def test_mixed_number_in_inequality_side_parses_correctly():
    r = check_practice_answer("Riješi nejednačinu: 2x < 9", "x < 4 1/2")
    assert _by_n(r) == {1: "correct"}


def test_mixed_number_wrong_value_still_incorrect():
    r = check_practice_answer("Riješi jednačinu: x + \\frac{3}{4} = 5", "x = 4 3/4")
    assert _by_n(r) == {1: "incorrect"}


# --- refleksija/ne-odgovor ostaje neocijenjen (konzervativnost) ----------------------

def test_reflection_message_stays_uncheckable():
    r = check_practice_answer(KONTROLNI, "nisam znao da li se sabira ili oduzima")
    assert not r.checkable


def test_ne_znam_stays_uncheckable():
    r = check_practice_answer(KONTROLNI, "Ne znam.")
    assert not r.checkable


# --- Fix 3: refleksija/ne-odgovor ne dobija ocjensku labelu --------------------------

def _reflection_payload(msg, prev=""):
    p = {
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": KONTROLNI,
        "student_message": msg,
        "last_tutor_message": prev,
    }
    p["answer_check"] = _cpa(KONTROLNI, msg)
    return p


def test_flag_set_for_reflection_message():
    p = _reflection_payload("nisam znao da li se sabira ili oduzima")
    _flag_non_answer_reflection(p)
    assert p.get("_non_answer_reflection") is True


def test_flag_set_when_answering_reflective_question():
    p = _reflection_payload("hmm pa ne znam baš", prev="Gdje misliš da je zapelo?")
    _flag_non_answer_reflection(p)
    assert p.get("_non_answer_reflection") is True


def test_flag_not_set_for_real_numeric_answer():
    p = _reflection_payload("x=4 1/4")
    _flag_non_answer_reflection(p)
    assert not p.get("_non_answer_reflection")


def test_flag_not_set_for_midstep_answer():
    p = _reflection_payload("2x<12")
    _flag_non_answer_reflection(p)
    assert not p.get("_non_answer_reflection")


def test_flag_not_set_for_attempt_with_number_in_sentence():
    p = _reflection_payload("mislim da je 5/8")
    _flag_non_answer_reflection(p)
    assert not p.get("_non_answer_reflection")


def test_flag_not_set_when_skip_answer_check():
    p = _reflection_payload("nisam znao")
    p["_skip_answer_check"] = True
    _flag_non_answer_reflection(p)
    assert not p.get("_non_answer_reflection")


def test_guard_strips_hard_label_from_reflection():
    out = neutralize_non_answer_grade(
        "Netačno. Izgleda da si bio neodlučan oko redoslijeda — prvo sabiramo."
    )
    assert not out.lower().startswith("netačno")
    assert out.startswith("Izgleda")


def test_guard_strips_negative_phrase_from_reflection():
    out = neutralize_non_answer_grade(
        "Netačno, ali blizu si. Prvo sabiramo, pa oduzimamo."
    )
    assert "netačno" not in out.lower()
    assert out.startswith("Ali blizu si")


def test_guard_strips_false_positive_label_from_reflection():
    out = neutralize_non_answer_grade("Tačno. To je dobro pitanje o redoslijedu.")
    assert not out.lower().startswith("tačno")
    assert "dobro pitanje" in out


def test_guard_leaves_unlabeled_reflection_untouched():
    text = "Odlično pitanje! U ovom zadatku prvo sabiramo, pa oduzimamo."
    assert neutralize_non_answer_grade(text) == text


def test_flag_set_for_where_did_i_go_wrong_questions():
    # sim500 nalaz: "gdje sam pogriješio?" poslije Netačno je META pitanje —
    # bot je ponovo otvarao sa "Netačno." (6/15 wrong-sesija)
    for msg in (
        "gdje sam pogriješio?",
        "šta sam pogriješio",
        "u čemu sam pogriješila?",
        "gdje mi je greška?",
        "zašto je netačno?",
    ):
        p = _reflection_payload(msg)
        _flag_non_answer_reflection(p)
        assert p.get("_non_answer_reflection") is True, msg


def test_flag_not_set_for_wrong_answer_mentioning_greska():
    # odgovor s brojem koji spominje grešku i dalje JE pokušaj
    p = _reflection_payload("x=5, valjda nije greška")
    _flag_non_answer_reflection(p)
    assert not p.get("_non_answer_reflection")


# --- aritmetički međukorak preko prefiks-vrijednosti izraza --------------------------

def test_arithmetic_prefix_midstep_is_correct_step():
    # hint: "prvo saberi 5/12 + 7/12" → učenik javi 12/12 (= 1) — tačan korak
    task = "Izračunaj \\frac{5}{12} + \\frac{7}{12} - \\frac{3}{12}."
    assert _by_n(check_practice_answer(task, "12/12")) == {1: "correct_step"}
    assert _by_n(check_practice_answer(task, "1")) == {1: "correct_step"}


def test_arithmetic_final_and_wrong_values_unchanged():
    task = "Izračunaj \\frac{5}{12} + \\frac{7}{12} - \\frac{3}{12}."
    assert _by_n(check_practice_answer(task, "9/12")) == {1: "correct_equivalent_form"}
    assert _by_n(check_practice_answer(task, "3/4")) == {1: "correct"}
    assert _by_n(check_practice_answer(task, "5/12")) == {1: "incorrect"}  # nije međukorak


def test_arithmetic_two_term_expression_has_no_midsteps():
    # jedan operator → nema pravog međukoraka; pogrešan zbir ostaje netačan
    r = check_practice_answer("Izračunaj \\frac{1}{2} + \\frac{1}{4}", "1/2")
    assert _by_n(r) == {1: "incorrect"}


def test_mixed_precedence_expression_has_no_prefix_midsteps():
    # "1/2 + 1/2 * 4" — prefiks slijeva (1/2+1/2) NIJE validan korak (prvo množenje)
    r = check_practice_answer("Izračunaj \\frac{1}{2} + \\frac{1}{2} \\cdot 4", "1")
    assert _by_n(r) == {1: "incorrect"}


def test_soften_post_hint_keeps_attributed_correct_with_siblings():
    # atribucija: [correct, not_attempted] — deterministička potvrda OSTAJE
    payload = {
        "last_tutor_task": KONTROLNI,
        "student_message": "x<6",
        "previous_next_state": {
            "task_items": {"labels": [1, 2, 3], "graded": [1]},
            "just_hinted": True,
        },
    }
    _run_answer_check(payload)
    _soften_post_hint_reply(payload)
    assert payload.get("answer_check") is not None
    assert not payload.get("_post_hint_reply")   # pun tačan odgovor → normalno "Tačno."


def test_guard_no_duplicate_summary_when_body_already_covers_waiting_items():
    r = check_practice_answer(KONTROLNI, "x=4 1/4")
    answer = (
        "Zadatak 1 je tačan: x = 17/4 = 4 1/4. "
        "Zadaci 2 i 3 još čekaju tvoja rješenja."
    )
    out = enforce_grading_consistency(answer, r)
    assert out.count("još čekaju") == 1


def test_guard_no_duplicate_answered_sentence_comma_variant():
    # live nalaz: model kaže "Zadatak 1 je tačan, a zadaci 2 i 3 još čekaju
    # odgovor." — guard NE smije prepend-ati svoju verziju iste rečenice
    r = check_practice_answer(KONTROLNI, "x=4 1/4")
    answer = "Zadatak 1 je tačan, a zadaci 2 i 3 još čekaju odgovor. Bravo!"
    out = enforce_grading_consistency(answer, r)
    assert out.lower().count("zadatak 1 je tačan") == 1


def test_forbidden_new_task_removed_from_visible_text():
    from matbot.ai_tutor_service import _remove_marked_task_paragraph
    answer = (
        "2. Tačno. Dodao si 5 na obje strane i dobio x < 6.\n\n"
        "Zadatak: Riješi nejednačinu 3x - 4 < 8."
    )
    out = _remove_marked_task_paragraph(answer)
    assert "Zadatak:" not in out
    assert "Tačno. Dodao si 5" in out


def test_remove_marked_task_keeps_original_when_task_is_whole_answer():
    from matbot.ai_tutor_service import _remove_marked_task_paragraph
    assert _remove_marked_task_paragraph("Zadatak: Izračunaj 2 + 2.") == ""
