# -*- coding: utf-8 -*-
"""„Sutra imam kontrolni“ v1 — serverski test od 5 MCQ pitanja.

Pokriva ugovore moda: kanonsko razrješenje oblasti, tačno 5 pitanja, serverski
izbor ciljnih lekcija, profile težine, MCQ integritet (4 opcije / tačno jedna
tačna / bez ekvivalentnih), ključ odgovora NIKAD u klijentskom payloadu,
serversko ocjenjivanje, ignorisanje krivotvorenog klijentskog skora, preporuku
lekcija, tranzicije lakši/isti/teži, zabranu doslovnog ponavljanja, zatvoren
pad nepotpunog paketa i granicu od NAJVIŠE dva poziva modela bez trećeg.
"""
import json
import pathlib
import re

import pytest

from matbot import config, exactly_one, kontrolni, option_equivalence
from matbot.llm import LLMResult, LLMUnavailable
from matbot.prompts import kontrolni_repair_hint
from matbot.schema import KontrolniQuestionOutput, KontrolniTestOutput
from matbot.tutor.package_preflight import safe_visible_text
from tests.conftest import FakeLLM, make_kontrolni_question, make_kontrolni_test

_SLOT_RE = re.compile(r"SLOT (\d+): lesson_id=(\S+) \| LEKCIJA: (.+?) \| difficulty=(\w+)")


def _parse_slots(input_text):
    return [
        {"slot": int(m.group(1)), "lesson_id": m.group(2),
         "lesson_title": m.group(3), "difficulty": m.group(4)}
        for m in _SLOT_RE.finditer(input_text)
    ]


class EchoKontrolniLLM(FakeLLM):
    """Vrati validno pitanje za SVAKI traženi slot (čita slotove iz ulaza) —
    ekvivalent modela koji savršeno poštuje batch ugovor. `mutate` dozvoljava
    testu da pokvari TAČNO određene slotove prvog poziva."""

    def __init__(self, mutate=None, vary_from=7):
        super().__init__()
        self.mutate = mutate or {}
        self.batch_calls = 0
        self.vary_from = vary_from

    def kontrolni_turn(self, instructions, input_text, timeout_s=None):
        self.calls.append((instructions, input_text))
        self.kontrolni_calls = getattr(self, "kontrolni_calls", [])
        self.kontrolni_calls.append((instructions, input_text))
        self.batch_calls += 1
        questions = []
        base = self.vary_from + 10 * (self.batch_calls - 1)
        for slot in _parse_slots(input_text):
            n = slot["slot"]
            options = [f"$\\frac{{{n}}}{{{base + 2 * k}}}$" for k in range(1, 5)]
            question = KontrolniQuestionOutput(
                slot=n, lesson_id=slot["lesson_id"],
                text=(f"U razredu je {base + 2} učenika, a njih {n} nosi naočale. "
                      f"Koji dio učenika nosi naočale?"),
                options=options, correct_option_index=0,
                expected_answer=options[0],
                # Rješenje IZVODI označenu vrijednost (živi validator
                # solution_marked_value_divergence to sada zahtijeva).
                solution=(f"Od {base + 2} učenika naočale nosi {n}, pa je "
                          f"traženi dio {options[0]}."),
                difficulty=slot["difficulty"])
            if self.batch_calls == 1 and n in self.mutate:
                question = self.mutate[n](question, slot)
            questions.append(question)
        return LLMResult(output=KontrolniTestOutput(questions=questions),
                         latency_ms=7, usage={})


@pytest.fixture
def exam_store():
    return kontrolni.KontrolniStore()


def start_payload(**kw):
    base = {"session_id": "kontrolni-sess", "grade": 6, "oblast_id": "6-04"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Kanonsko razrješenje oblasti + serverski izbor ciljeva
# ---------------------------------------------------------------------------

def test_unknown_oblast_is_rejected_before_any_model_call(exam_store):
    llm = EchoKontrolniLLM()
    with pytest.raises(kontrolni.ExamValidationError) as err:
        kontrolni.run_start(exam_store, llm, start_payload(oblast_id="6-99"))
    assert err.value.code == "UNKNOWN_OBLAST"
    assert llm.call_count == 0


def test_oblast_from_wrong_grade_is_rejected(exam_store):
    llm = EchoKontrolniLLM()
    with pytest.raises(kontrolni.ExamValidationError):
        kontrolni.run_start(exam_store, llm, start_payload(grade=7, oblast_id="6-04"))
    assert llm.call_count == 0


def test_arbitrary_oblast_name_is_not_accepted(exam_store):
    with pytest.raises(kontrolni.ExamValidationError) as err:
        kontrolni.run_start(exam_store, EchoKontrolniLLM(),
                            start_payload(oblast_id="Razlomci"))
    assert err.value.code == "INVALID_OBLAST"


def test_server_selects_five_distinct_lessons_inside_oblast(exam_store):
    llm = EchoKontrolniLLM()
    status, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert status == 200 and resp["status"] == "ready"
    state = exam_store.get("kontrolni-sess")
    lesson_ids = [q["lesson_id"] for q in state["questions"]]
    assert len(lesson_ids) == 5
    assert len(set(lesson_ids)) == 5          # oblast 6-04 ima 15 lekcija
    assert all(lid.startswith("6-04-") for lid in lesson_ids)


def test_small_oblast_reuses_lessons_only_as_necessary(exam_store):
    # 6-11 Vektori ima samo 2 lekcije — test i dalje ima TAČNO 5 pitanja, a
    # lekcije se smjenjuju ciklično (nikad 5x ista).
    llm = EchoKontrolniLLM()
    status, resp = kontrolni.run_start(
        exam_store, llm, start_payload(oblast_id="6-11"))
    assert status == 200 and resp["status"] == "ready"
    state = exam_store.get("kontrolni-sess")
    lesson_ids = [q["lesson_id"] for q in state["questions"]]
    assert len(lesson_ids) == 5
    assert len(set(lesson_ids)) == 2


# ---------------------------------------------------------------------------
# Tačno 5 pitanja, 4 opcije, klijentski payload bez ključa
# ---------------------------------------------------------------------------

def test_ready_test_has_exactly_five_mcq_questions(exam_store):
    _status, resp = kontrolni.run_start(exam_store, EchoKontrolniLLM(), start_payload())
    assert resp["question_count"] == 5
    assert len(resp["questions"]) == 5
    for question in resp["questions"]:
        assert len(question["options"]) == 4
        assert sorted(o["id"] for o in question["options"]) == ["a", "b", "c", "d"]


def test_answer_key_never_reaches_the_client_payload(exam_store):
    _status, resp = kontrolni.run_start(exam_store, EchoKontrolniLLM(), start_payload())
    for question in resp["questions"]:
        assert set(question) == {"id", "ordinal", "text", "options"}
        for option in question["options"]:
            assert set(option) == {"id", "text"}
    serialized = json.dumps(resp, ensure_ascii=False)
    for forbidden in ("correct", "solution", "expected", "lesson"):
        assert forbidden not in serialized, forbidden
    # Ključ postoji ISKLJUČIVO na serveru.
    state = exam_store.get("kontrolni-sess")
    assert all(q["correct_option_id"] in "abcd" for q in state["questions"])
    assert all(q["solution"] for q in state["questions"])


# ---------------------------------------------------------------------------
# Profili težine i tranzicije
# ---------------------------------------------------------------------------

def test_profile_distributions_match_specification():
    assert kontrolni.PROFILE_SLOTS["easier"] == ("easy", "easy", "easy", "medium", "medium")
    assert kontrolni.PROFILE_SLOTS["standard"] == ("easy", "medium", "medium", "medium", "hard")
    assert kontrolni.PROFILE_SLOTS["harder"] == ("medium", "hard", "hard", "hard", "demanding")


def test_profile_transitions_step_and_clamp():
    assert kontrolni.next_profile("standard", "easier") == "easier"
    assert kontrolni.next_profile("standard", "harder") == "harder"
    assert kontrolni.next_profile("standard", "same") == "standard"
    assert kontrolni.next_profile("easier", "easier") == "easier"   # klema
    assert kontrolni.next_profile("harder", "harder") == "harder"   # klema
    assert kontrolni.next_profile("easier", "harder") == "standard"


def test_first_test_is_standard_and_buttons_step_the_profile(exam_store):
    llm = EchoKontrolniLLM()
    _s, first = kontrolni.run_start(exam_store, llm, start_payload())
    assert first["difficulty"] == "standard"
    _s, harder = kontrolni.run_start(exam_store, llm, start_payload(relative="harder"))
    assert harder["difficulty"] == "harder"
    _s, same = kontrolni.run_start(exam_store, llm, start_payload(relative="same"))
    assert same["difficulty"] == "harder"
    _s, easier = kontrolni.run_start(exam_store, llm, start_payload(relative="easier"))
    assert easier["difficulty"] == "standard"


def test_relative_without_active_exam_falls_back_to_standard(exam_store):
    _s, resp = kontrolni.run_start(exam_store, EchoKontrolniLLM(),
                                   start_payload(relative="harder"))
    assert resp["difficulty"] == "standard"


def test_slot_difficulties_follow_the_profile(exam_store):
    llm = EchoKontrolniLLM()
    kontrolni.run_start(exam_store, llm, start_payload())
    state = exam_store.get("kontrolni-sess")
    assert tuple(q["difficulty"] for q in state["questions"]) == \
        kontrolni.PROFILE_SLOTS["standard"]


# ---------------------------------------------------------------------------
# MCQ integritet — validatori odbijaju, popravka je USLOVNA, treći poziv ne postoji
# ---------------------------------------------------------------------------

def _clean_question_for(slot_info, **overrides):
    question = make_kontrolni_question(
        slot=slot_info["slot"], lesson_id=slot_info["lesson_id"],
        difficulty=slot_info["difficulty"])
    return question.model_copy(update=overrides)


def test_happy_path_costs_exactly_one_model_call(exam_store):
    llm = EchoKontrolniLLM()
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "ready"
    assert llm.call_count == 1


def test_invalid_slot_triggers_exactly_one_repair_call(exam_store):
    def break_options(question, _slot):
        # Dvije SEMANTIČKI ekvivalentne opcije — potencijalna dva tačna odgovora.
        return question.model_copy(update={
            "options": ["$\\frac{1}{2}$", "$0,5$", "$\\frac{1}{3}$", "$\\frac{1}{4}$"],
            "expected_answer": "$\\frac{1}{2}$",
        })

    llm = EchoKontrolniLLM(mutate={2: break_options})
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "ready"
    assert llm.call_count == 2
    # Popravka je tražila SAMO pali slot.
    repair_slots = _parse_slots(llm.kontrolni_calls[1][1])
    assert [s["slot"] for s in repair_slots] == [2]


def test_lesson_replacement_is_rejected_and_repaired(exam_store):
    def replace_lesson(question, _slot):
        return question.model_copy(update={"lesson_id": "6-01-001"})

    llm = EchoKontrolniLLM(mutate={4: replace_lesson})
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "ready"
    assert llm.call_count == 2
    state = exam_store.get("kontrolni-sess")
    assert state["questions"][3]["lesson_id"].startswith("6-04-")


def test_wrong_marked_answer_is_rejected(exam_store):
    def wrong_mark(question, slot):
        # Direktan račun s POGREŠNO označenom opcijom — mcq_integrity orakl
        # rješava zadatak i dokazuje da označena nije rezultat.
        return question.model_copy(update={
            "text": "Koliko je $12 \\cdot 9$?",
            "options": ["$96$", "$108$", "$118$", "$121$"],
            "correct_option_index": 0,
            "expected_answer": "$96$",
            "solution": "Množenjem se dobija rezultat.",
        })

    llm = EchoKontrolniLLM(mutate={1: wrong_mark})
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "ready"
    assert llm.call_count == 2                      # slot 1 je morao u popravku


def test_duplicate_options_are_rejected(exam_store):
    def duplicate(question, _slot):
        return question.model_copy(update={
            "options": ["$\\frac{1}{2}$", "$\\frac{1}{2}$", "$\\frac{1}{3}$", "$\\frac{1}{4}$"],
            "expected_answer": "$\\frac{1}{2}$",
        })

    llm = EchoKontrolniLLM(mutate={3: duplicate})
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "ready" and llm.call_count == 2


def test_option_letter_claim_in_solution_is_rejected(exam_store):
    def letter_claim(question, _slot):
        return question.model_copy(update={
            "solution": "Rezultat je pod opcijom a jer se brojnik ne mijenja."})

    llm = EchoKontrolniLLM(mutate={5: letter_claim})
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "ready" and llm.call_count == 2


def test_failed_repair_fails_the_whole_test_closed_without_third_call(exam_store):
    class AlwaysBrokenLLM(EchoKontrolniLLM):
        def kontrolni_turn(self, instructions, input_text, timeout_s=None):
            result = super().kontrolni_turn(instructions, input_text)
            broken = [q.model_copy(update={"lesson_id": "6-01-001"})
                      for q in result.output.questions]
            return LLMResult(output=KontrolniTestOutput(questions=broken),
                             latency_ms=7, usage={})

    llm = AlwaysBrokenLLM()
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "failed"
    assert resp["message"] == kontrolni.GENERATION_FAILED_MESSAGE
    assert llm.call_count == 2                      # NIKAD treći poziv
    assert exam_store.get("kontrolni-sess") is None  # ništa nije objavljeno


def test_llm_error_fails_closed_without_any_retry(exam_store):
    llm = FakeLLM()
    llm.queue(LLMUnavailable("mreža"))
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "failed"
    assert llm.call_count == 1                      # bez skrivenog retryja


def test_validator_rejects_answer_leaked_in_question_text():
    context = kontrolni._slot_contexts(6, [
        {"slot": 1, "lesson_id": "6-04-001", "lesson_title": "L", "difficulty": "easy"}])[1]
    slot = {"slot": 1, "lesson_id": "6-04-001", "lesson_title": "L", "difficulty": "easy"}
    question = make_kontrolni_question(
        text="Rezultat je $\\frac{2}{5}$. Koji dio kruga je osjenčen?")
    clean, code = kontrolni.validate_generated_question(question, slot, context, set())
    assert clean is None and code == "stem_reveals_marked_option"


def test_stem_enumerating_multiple_candidates_is_legitimate():
    # Zadatak smije nabrojati kandidate (i tačan među njima) — pada SAMO kad
    # tekst izdvoji isključivo označenu opciju.
    slot = {"slot": 1, "lesson_id": "6-04-001", "lesson_title": "L", "difficulty": "easy"}
    context = kontrolni._slot_contexts(6, [slot])[1]
    question = make_kontrolni_question(
        text=("Koji od razlomaka $\\frac{2}{5}$, $\\frac{3}{5}$ i $\\frac{1}{5}$ "
              "je najveći?"),
        options=["$\\frac{3}{5}$", "$\\frac{2}{5}$", "$\\frac{1}{5}$", "$\\frac{4}{15}$"],
        correct_option_index=0)
    clean, code = kontrolni.validate_generated_question(question, slot, context, set())
    assert clean is not None, code


def test_validator_rejects_unsafe_mathjax():
    slot = {"slot": 1, "lesson_id": "6-04-001", "lesson_title": "L", "difficulty": "easy"}
    context = kontrolni._slot_contexts(6, [slot])[1]
    question = make_kontrolni_question(text="Izračunaj $\\ty{2}{5}$ dijela.")
    clean, code = kontrolni.validate_generated_question(question, slot, context, set())
    assert clean is None and code == "unsafe_or_long_text"


def test_validator_rejects_expected_answer_that_is_not_the_marked_option():
    slot = {"slot": 1, "lesson_id": "6-04-001", "lesson_title": "L", "difficulty": "easy"}
    context = kontrolni._slot_contexts(6, [slot])[1]
    question = make_kontrolni_question(expected_answer="$\\frac{9}{5}$")
    clean, code = kontrolni.validate_generated_question(question, slot, context, set())
    assert clean is None and code == "expected_option_mismatch"


# ---------------------------------------------------------------------------
# Ponavljanje između testova iste sesije
# ---------------------------------------------------------------------------

def test_new_test_never_repeats_previous_questions_signatures(exam_store):
    llm = EchoKontrolniLLM()
    kontrolni.run_start(exam_store, llm, start_payload())
    first = exam_store.get("kontrolni-sess")
    kontrolni.run_start(exam_store, llm, start_payload(relative="same"))
    second = exam_store.get("kontrolni-sess")
    first_signatures = {q["signature"] for q in first["questions"]}
    second_signatures = {q["signature"] for q in second["questions"]}
    assert not first_signatures & second_signatures


def test_literal_repeat_from_model_is_rejected(exam_store):
    class RepeatingLLM(EchoKontrolniLLM):
        """Uvijek vraća IDENTIČNA pitanja (vary_from fiksan po pozivu)."""
        def kontrolni_turn(self, instructions, input_text, timeout_s=None):
            self.batch_calls -= self.batch_calls  # resetuj varijaciju: uvijek isti test
            return super().kontrolni_turn(instructions, input_text)

    llm = RepeatingLLM()
    _s, first = kontrolni.run_start(exam_store, llm, start_payload())
    assert first["status"] == "ready"
    _s, second = kontrolni.run_start(exam_store, llm, start_payload(relative="same"))
    # Doslovno ponovljen test pada zatvoreno (poslije neuspjele popravke).
    assert second["status"] == "failed"


# ---------------------------------------------------------------------------
# Serversko ocjenjivanje
# ---------------------------------------------------------------------------

def _make_graded_exam(exam_store, wrong_count=0, session="kontrolni-sess"):
    llm = EchoKontrolniLLM()
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload(session_id=session))
    state = exam_store.get(session)
    answers = {}
    for i, stored in enumerate(state["questions"]):
        if i < wrong_count:
            answers[stored["id"]] = next(
                o["id"] for o in stored["options"]
                if o["id"] != stored["correct_option_id"])
        else:
            answers[stored["id"]] = stored["correct_option_id"]
    return resp, state, answers


@pytest.mark.parametrize("wrong,expected_pct", [(0, 100), (1, 80), (2, 60), (3, 40), (4, 20), (5, 0)])
def test_server_side_grading_and_percentages(exam_store, wrong, expected_pct):
    resp, _state, answers = _make_graded_exam(exam_store, wrong_count=wrong)
    _s, graded = kontrolni.run_submit(exam_store, {
        "session_id": "kontrolni-sess", "exam_id": resp["exam_id"], "answers": answers})
    assert graded["status"] == "graded"
    assert graded["score"] == 5 - wrong
    assert graded["percentage"] == expected_pct


def test_incomplete_submission_reports_remaining_and_does_not_grade(exam_store):
    resp, _state, answers = _make_graded_exam(exam_store)
    partial = {k: answers[k] for k in list(answers)[:3]}
    _s, out = kontrolni.run_submit(exam_store, {
        "session_id": "kontrolni-sess", "exam_id": resp["exam_id"], "answers": partial})
    assert out["status"] == "incomplete" and out["remaining"] == 2
    assert exam_store.get("kontrolni-sess")["graded"] is False


def test_wrong_questions_map_to_canonical_lesson_recommendation(exam_store):
    resp, state, answers = _make_graded_exam(exam_store, wrong_count=2)
    _s, graded = kontrolni.run_submit(exam_store, {
        "session_id": "kontrolni-sess", "exam_id": resp["exam_id"], "answers": answers})
    wrong_lessons = [q["lesson_title"] for q in state["questions"][:2]]
    assert graded["recommendation"]["lessons"] == wrong_lessons
    assert "ponoviš" in graded["recommendation"]["message"]


def test_score_messages_follow_the_rubric(exam_store):
    for wrong, fragment in ((0, "Odlično"), (1, "Vrlo dobro"),
                            (3, "ponoviš"), (5, "lakši test")):
        store = kontrolni.KontrolniStore()
        resp, _state, answers = _make_graded_exam(store, wrong_count=wrong)
        _s, graded = kontrolni.run_submit(store, {
            "session_id": "kontrolni-sess", "exam_id": resp["exam_id"],
            "answers": answers})
        assert fragment in graded["recommendation"]["message"]


def test_forged_resubmission_cannot_change_the_stored_score(exam_store):
    resp, _state, answers = _make_graded_exam(exam_store, wrong_count=2)
    _s, graded = kontrolni.run_submit(exam_store, {
        "session_id": "kontrolni-sess", "exam_id": resp["exam_id"], "answers": answers})
    assert graded["score"] == 3
    # „Popravljeni“ odgovori + krivotvoren score u payloadu — sve se ignoriše.
    all_correct = {q["id"]: q["correct_option_id"]
                   for q in exam_store.get("kontrolni-sess")["questions"]}
    _s, again = kontrolni.run_submit(exam_store, {
        "session_id": "kontrolni-sess", "exam_id": resp["exam_id"],
        "answers": all_correct, "score": 5, "percentage": 100})
    assert again["score"] == 3                      # server ostaje autoritativan


def test_unknown_or_replayed_exam_id_is_rejected(exam_store):
    resp, _state, answers = _make_graded_exam(exam_store)
    with pytest.raises(kontrolni.ExamValidationError) as err:
        kontrolni.run_submit(exam_store, {
            "session_id": "kontrolni-sess", "exam_id": "AAAAAAAAAAAA", "answers": answers})
    assert err.value.code == "UNKNOWN_EXAM"
    # Poslije NOVOG testa stari exam_id više ne važi (replay zaštita).
    llm = EchoKontrolniLLM()
    kontrolni.run_start(exam_store, llm, start_payload(relative="same"))
    with pytest.raises(kontrolni.ExamValidationError):
        kontrolni.run_submit(exam_store, {
            "session_id": "kontrolni-sess", "exam_id": resp["exam_id"], "answers": answers})


def test_foreign_question_ids_are_rejected(exam_store):
    resp, _state, answers = _make_graded_exam(exam_store)
    answers["q9"] = "a"
    with pytest.raises(kontrolni.ExamValidationError) as err:
        kontrolni.run_submit(exam_store, {
            "session_id": "kontrolni-sess", "exam_id": resp["exam_id"], "answers": answers})
    assert err.value.code == "INVALID_ANSWERS"


def test_oversized_or_malformed_answers_are_rejected(exam_store):
    with pytest.raises(kontrolni.ExamValidationError):
        kontrolni.validate_submit_payload({
            "session_id": "s", "exam_id": "ABCDEFGH1234",
            "answers": {f"q{i}": "a" for i in range(1, 8)}})
    with pytest.raises(kontrolni.ExamValidationError):
        kontrolni.validate_submit_payload({
            "session_id": "s", "exam_id": "ABCDEFGH1234", "answers": {"q1": "z"}})
    with pytest.raises(kontrolni.ExamValidationError):
        kontrolni.validate_submit_payload({
            "session_id": "s", "exam_id": "ABCDEFGH1234", "answers": "a,b,c"})


# ---------------------------------------------------------------------------
# ŽIVA KAMPANJA v1 — regresije dvije objavljene greške i kozmetičkih klasa
# ---------------------------------------------------------------------------

def _validate(question):
    slot = {"slot": 1, "lesson_id": "6-04-001", "lesson_title": "L", "difficulty": "easy"}
    context = kontrolni._slot_contexts(6, [slot])[1]
    return kontrolni.validate_generated_question(question, slot, context, set())


def test_live16_provably_false_marked_bracketing_is_rejected():
    """Živi nalaz: označeno $7<\\sqrt{70}<8$, a $\\sqrt{70}\\approx8{,}37$;
    tačan interval je stajao neoznačen kao distraktor."""
    question = make_kontrolni_question(
        text="Između kojih se uzastopnih cijelih brojeva nalazi broj $\\sqrt{70}$?",
        options=["$7<\\sqrt{70}<8$", "$6<\\sqrt{70}<7$",
                 "$8<\\sqrt{70}<9$", "$9<\\sqrt{70}<10$"],
        correct_option_index=0,
        solution="Pošto je $8^2=64$ i $9^2=81$, važi $8<\\sqrt{70}<9$.")
    clean, code = _validate(question)
    assert clean is None and code == "marked_statement_provably_false"


def test_live16_true_distractor_is_rejected_even_with_true_marked():
    question = make_kontrolni_question(
        text="Između kojih se uzastopnih cijelih brojeva nalazi broj $\\sqrt{70}$?",
        options=["$8<\\sqrt{70}<9$", "$6<\\sqrt{70}<7$",
                 "$0,5<1$", "$9<\\sqrt{70}<10$"],
        correct_option_index=0,
        solution="Pošto je $8^2=64$ i $9^2=81$, važi $8<\\sqrt{70}<9$.")
    clean, code = _validate(question)
    assert clean is None and code == "distractor_statement_provably_true"


def test_live25_solution_marked_value_divergence_is_rejected():
    """Živi nalaz: rješenje izvodi $113,04$, a označena opcija nosi $56,52$."""
    question = make_kontrolni_question(
        text="Sfera ima poluprečnik $r=3\\,\\text{cm}$. Kolika je njena površina? Uzmi $\\pi\\approx3,14$.",
        options=["$56,52\\,\\text{cm}^2$", "$28,26\\,\\text{cm}^2$",
                 "$37,68\\,\\text{cm}^2$", "$120\\,\\text{cm}^2$"],
        correct_option_index=0,
        solution=("Površina sfere računa se formulom $P=4\\pi r^2$. Zato je "
                  "$P=4\\cdot3,14\\cdot9=113,04\\,\\text{cm}^2$."))
    clean, code = _validate(question)
    assert clean is None and code == "solution_marked_value_divergence"


def test_solution_that_derives_the_marked_value_is_accepted():
    question = make_kontrolni_question(
        text="Koliko iznosi površina sfere poluprečnika $r=3\\,\\text{cm}$ uz $\\pi\\approx3,14$?",
        options=["$113,04\\,\\text{cm}^2$", "$28,26\\,\\text{cm}^2$",
                 "$37,68\\,\\text{cm}^2$", "$56,52\\,\\text{cm}^2$"],
        correct_option_index=0,
        solution=("Površina sfere je $P=4\\pi r^2$, pa je "
                  "$P=4\\cdot3,14\\cdot9=113,04\\,\\text{cm}^2$."))
    clean, code = _validate(question)
    assert clean is not None, code


def test_prose_conclusion_corroborates_marked_value():
    # Zaključak u PROZI („…je 42.“) mora biti dovoljan — bez toga bi validan
    # NZD zadatak lažno pao.
    question = make_kontrolni_question(
        text="Odredi najveći zajednički djelilac brojeva $84$ i $126$.",
        options=["$42$", "$21$", "$14$", "$63$"],
        correct_option_index=0,
        solution="Rastavljanjem na proste faktore dobija se da je najveći zajednički djelilac 42.")
    clean, code = _validate(question)
    assert clean is not None, code


def test_live15_damaged_latex_word_in_math_is_rejected():
    question = make_kontrolni_question(
        text="Odredi $x$ ako je $2^xcdot2^3=2^7$.",
        options=["$4$", "$5$", "$10$", "$3$"],
        correct_option_index=0,
        solution="Izložioci se sabiraju, pa je $x=4$.")
    clean, code = _validate(question)
    # Paket i dalje PADA; od uvođenja opšte zabrane gole proze u matematici
    # (N-5) „xcdot“ obori već `mathsafe` unutar `_safe_field`, prije uže
    # kontrolni-provjere. Oba koda su ispravna odbijanja — test čuva ZAŠTITU,
    # ne redoslijed detektora.
    assert clean is None
    assert code in ("damaged_latex_word_in_math", "unsafe_or_long_text")


def test_live11_caret_outside_math_is_rejected():
    question = make_kontrolni_question(
        options=["42^$\\circ$", "48^$\\circ$", "90^$\\circ$", "138^$\\circ$"],
        expected_answer="42^$\\circ$",
        solution="Uglovi s normalnim kracima koji su oba oštra su jednaki, dakle 42 stepena.")
    clean, code = _validate(question)
    assert clean is None and code == "caret_outside_math"


def test_prose_option_with_embedded_formula_is_not_judged_as_chain():
    # „Ne, jer je $1=-2\\cdot2+5$“ — matematika u opciji je tačna, ali opcija
    # tvrdi „Ne“; lanac se zato sudi SAMO kod čisto matematičkih opcija.
    question = make_kontrolni_question(
        text="Da li tačka $A(2,1)$ pripada grafiku funkcije $y=-2x+5$?",
        options=["Da, jer je $1=-2\\cdot2+5$", "Ne, jer je $1=-2\\cdot2+5$",
                 "Da, jer je $2=-2\\cdot1+5$", "Ne, jer je $2=-2\\cdot1+5$"],
        correct_option_index=0,
        solution="Uvrštavanjem $x=2$ dobija se $y=1$, pa tačka pripada grafiku.")
    clean, code = _validate(question)
    assert clean is not None, code


def test_ordering_chain_options_with_decimal_commas_are_judged():
    question = make_kontrolni_question(
        text="Poredaj brojeve od najmanjeg do najvećeg.",
        options=["$0,67<0,7<0,76$", "$0,7<0,67<0,76$",
                 "$0,76<0,7<0,67$", "$0,76<0,67<0,7$"],
        correct_option_index=1,          # dokazivo NETAČAN označen
        solution="Poređenjem decimala slijedi $0,67<0,7<0,76$.")
    clean, code = _validate(question)
    # Prvi dokazani nalaz je tačan distraktor (opcija 0) — obje su ista klasa
    # „pogrešno označen odgovor“ i obje padaju zatvoreno.
    assert clean is None and code == "distractor_statement_provably_true"


def test_live_r2_divisibility_without_any_true_option_is_rejected():
    """Drugi krug kampanje: „Koji broj je djeljiv sa $100$?“ — nijedna opcija
    nije djeljiva sa 100 (hiljadni `\\,` je slijepio orakl)."""
    question = make_kontrolni_question(
        text="Koji broj je djeljiv sa $100$?",
        options=["$3\\,120$", "$5\\,018$", "$4\\,305$", "$2\\,450$"],
        correct_option_index=0,
        solution="Broj djeljiv sa 100 završava se sa dvije nule, dakle 3 120... provjeri.")
    clean, code = _validate(question)
    assert clean is None and code == "divisibility_marked_option_false"


def test_divisibility_with_true_distractor_is_rejected():
    question = make_kontrolni_question(
        text="Koji broj je djeljiv sa $9$?",
        options=["$135$", "$270$", "$142$", "$124$"],
        correct_option_index=0,
        solution="Zbir cifara broja 135 je 9, pa je 135 djeljiv sa 9.")
    clean, code = _validate(question)
    # Bez hiljadnog `\,` ovu klasu obara već mcq_integrity orakl; novi
    # validator je mreža za slijepu tačku. Bitno je DA pada, ne KO je prvi.
    assert clean is None
    assert code == "divisibility_distractor_also_true" or code.startswith("mcq_integrity")


def test_valid_divisibility_question_passes():
    question = make_kontrolni_question(
        text="Koji broj je djeljiv sa $9$?",
        options=["$135$", "$158$", "$142$", "$124$"],
        correct_option_index=0,
        solution="Zbir cifara broja 135 je 9, pa je 135 djeljiv sa 9.")
    clean, code = _validate(question)
    assert clean is not None, code


def test_negated_divisibility_marked_must_not_be_divisible():
    question = make_kontrolni_question(
        text="Koji broj nije djeljiv sa $5$?",
        options=["$40$", "$35$", "$25$", "$15$"],
        correct_option_index=0,
        solution="Broj 40 jeste djeljiv sa 5, pa tvrdnja ne stoji... 40 se završava nulom.")
    clean, code = _validate(question)
    assert clean is None
    assert code == "divisibility_marked_option_false" or code.startswith("mcq_integrity")


def test_live_r2_angle_side_correspondence_violation_is_rejected():
    """Drugi krug: „U trouglu je $\\alpha=\\beta$“ s označenim $b=c$ —
    naspramni par za α=β je a=b, i to je čista konvencija."""
    question = make_kontrolni_question(
        text="U trouglu je $\\alpha=\\beta$. Koji odnos između naspramnih stranica mora važiti?",
        options=["$b=c$", "$a=b$", "$a+b=c$", "$a=c$"],
        correct_option_index=0,
        solution="Naspram jednakih uglova leže jednake stranice, pa je a=b... odnosno b=c.")
    clean, code = _validate(question)
    assert clean is None and code == "angle_side_correspondence_violated"


def test_triple_form_angle_equality_is_also_judged():
    """Živi nalaz (finalna runda): „$\\beta=\\gamma=50^\\circ$“ s označenim
    $a=c$ — trojni zapis je promakao užem obrascu."""
    question = make_kontrolni_question(
        text="Trougao $ABC$ ima jednake uglove $\\beta=\\gamma=50^\\circ$. Koje stranice su jednake?",
        options=["$a=c$", "$a=b$", "Sve tri stranice su jednake.", "$b=c$"],
        correct_option_index=0,
        solution="Naspram jednakih uglova leže jednake stranice, pa su te dvije stranice jednake.")
    clean, code = _validate(question)
    assert clean is None and code == "angle_side_correspondence_violated"


def test_correct_correspondence_is_still_unprovable_and_therefore_rejected():
    """DOKTRINA JE JAČA OD RANIJEG PONAŠANJA: i kad je korespondencija ispravna,
    server ne može DOKAZATI da je tačna tačno jedna opcija (u jednakostraničnom
    trouglu vrijedi i $a=b$), pa oblik pada i slot ide u popravku. Ranije je
    isti paket prolazio samo zato što nijedan čuvar nije imao nalaz."""
    question = make_kontrolni_question(
        text="U trouglu je $\\beta=\\gamma$. Koji odnos između naspramnih stranica mora važiti?",
        options=["$b=c$", "$a=b$", "$a+b=c$", "$a=c$"],
        correct_option_index=0,
        solution="Naspram jednakih uglova leže jednake stranice: naspram $\\beta$ je $b$, "
                 "naspram $\\gamma$ je $c$, pa je $b=c$.")
    clean, code = _validate(question)
    assert clean is None and code == "unprovable_claim_selection"


def test_live_r3_wrong_decimal_place_is_rejected():
    """Treći krug: „mjesto stotinki“ u $12,305$ je cifra $0$, označeno $3$."""
    question = make_kontrolni_question(
        text="Koja cifra u decimalnom broju $12,305$ zauzima mjesto stotinki?",
        options=["$3$", "$5$", "$0$", "$2$"],
        correct_option_index=0,
        solution="Iza zareza redom stoje desetinke, stotinke i hiljaditke, dakle 3.")
    clean, code = _validate(question)
    assert clean is None and code == "decimal_place_marked_wrong"


def test_correct_decimal_place_passes():
    question = make_kontrolni_question(
        text="Koja cifra u decimalnom broju $12,305$ zauzima mjesto stotinki?",
        options=["$0$", "$5$", "$3$", "$2$"],
        correct_option_index=0,
        solution="Iza zareza redom stoje desetinke (3), stotinke (0) i hiljaditke (5), dakle 0.")
    clean, code = _validate(question)
    assert clean is not None, code


def test_live_r3_construction_distractor_reaching_target_is_rejected():
    """Treći krug (7-04): SVA četiri recepta davala su $75^\\circ$."""
    question = make_kontrolni_question(
        text="Kako se konstrukcijom može dobiti ugao od $75^\\circ$?",
        options=["Sastaviti ugao od $60^\\circ$ i ugao od $15^\\circ$.",
                 "Prepoloviti ugao od $90^\\circ$ i dodati ugao od $30^\\circ$.",
                 "Sastaviti ugao od $60^\\circ$ i ugao od $30^\\circ$.",
                 "Sastaviti ugao od $45^\\circ$ i ugao od $20^\\circ$."],
        correct_option_index=0,
        solution="Ugao od 75 stepeni nastaje sastavljanjem uglova od 60 i 15 stepeni.")
    clean, code = _validate(question)
    assert clean is None and code == "construction_distractor_reaches_target"


def test_marked_recipe_that_misses_target_is_rejected():
    question = make_kontrolni_question(
        text="Kako se konstrukcijom može dobiti ugao od $75^\\circ$?",
        options=["Sastaviti ugao od $60^\\circ$ i ugao od $30^\\circ$.",
                 "Sastaviti ugao od $45^\\circ$ i ugao od $20^\\circ$.",
                 "Sastaviti ugao od $90^\\circ$ i ugao od $30^\\circ$.",
                 "Sastaviti ugao od $30^\\circ$ i ugao od $30^\\circ$."],
        correct_option_index=0,
        solution="Sastavljanjem se dobija traženi ugao od 75 stepeni.")
    clean, code = _validate(question)
    assert clean is None and code == "construction_marked_recipe_misses_target"


def test_recipe_question_is_rejected_even_when_this_wording_is_decidable():
    """Isti razlog: recept je oblik „izaberi tvrdnju". Šablonski čuvar poznaje
    samo dvije doslovne formulacije — živi nalaz je promakao TREĆOM — pa se
    objava više ne smije oslanjati na to da baš taj šablon razumije rečenicu."""
    question = make_kontrolni_question(
        text="Kako se konstrukcijom može dobiti ugao od $75^\\circ$?",
        options=["Sastaviti ugao od $60^\\circ$ i ugao od $15^\\circ$.",
                 "Prepoloviti ugao od $90^\\circ$ i dodati ugao od $60^\\circ$.",
                 "Sastaviti ugao od $60^\\circ$ i ugao od $30^\\circ$.",
                 "Sastaviti ugao od $45^\\circ$ i ugao od $20^\\circ$."],
        correct_option_index=0,
        solution="Sastavljanjem uglova od $60^\\circ$ i $15^\\circ$ dobija se ugao od 75 stepeni.")
    clean, code = _validate(question)
    assert clean is None and code == "unprovable_claim_selection"


# ---------------------------------------------------------------------------
# ŽIVI NALAZ A (2026-08-16): STRANI DELIMITERI U OPCIJAMA
# Produkcija je učeniku prikazala „\( 8/5 \)“ — razlomak iscrtan, delimiteri
# vidljivi. Uzrok: model povremeno vrati `\(…\)`, a umotavanje je pravilo
# MIJEŠAN zapis. Kanonizacija je sada prvi korak sanitizacije.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (r"\(\frac{5}{8}\) čokolade", "$\\frac{5}{8}$ čokolade"),
    (r"\(\frac{5}{8}\)", "$\\frac{5}{8}$"),
    (r"\(x^2\)", "$x^2$"),
    (r"\(\sqrt{3}\)", "$\\sqrt{3}$"),
    (r"\(x \le 4\)", "$x \\le 4$"),
    (r"\(2,5\,\text{cm}^2\)", "$2,5\\,\\text{cm}^2$"),
    (r"\(A = \{1,2,3\}\)", "$A = \\{1,2,3\\}$"),
    (r"\(\mathbb{Z}\)", "$\\mathbb{Z}$"),
    (r"\[\frac{5}{8}\]", "$$\\frac{5}{8}$$"),
])
def test_alien_delimiters_become_the_canonical_form(raw, expected):
    cleaned, safe = safe_visible_text(raw, allow_wrap=True)
    assert safe and cleaned == expected
    assert "\\(" not in cleaned and "\\)" not in cleaned


def test_canonical_and_plain_text_are_untouched():
    for text in ("$\\frac{5}{8}$", "obična proza bez matematike",
                 "$P = \\left(a+b\\right) \\cdot h$"):
        cleaned, safe = safe_visible_text(text, allow_wrap=True)
        assert safe and cleaned == text, text


@pytest.mark.parametrize("raw", [r"\(nezatvoreno", r"nezatvoreno\)", r"\[pola"])
def test_unpaired_alien_delimiter_fails_closed(raw):
    """Neuparen delimiter bi na ekranu bio doslovno smeće — zatvaranje se ne
    izmišlja, paket pada zatvoreno (ista doktrina kao nepoznata komanda)."""
    _cleaned, safe = safe_visible_text(raw, allow_wrap=True)
    assert not safe


def test_generated_question_with_alien_delimiters_publishes_clean(exam_store):
    """Cio put: model vrati `\\(…\\)`, objavljena opcija je kanonska."""
    def alien(question, _slot):
        return question.model_copy(update={
            "options": [r"\(\frac{2}{5}\)", r"\(\frac{3}{5}\)",
                        r"\(\frac{1}{5}\)", r"\(\frac{4}{5}\)"],
            "expected_answer": r"\(\frac{2}{5}\)",
            "solution": r"Osjenčeni dio je \(\frac{2}{5}\) kruga.",
        })

    llm = EchoKontrolniLLM(mutate={1: alien})
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "ready"
    published = json.dumps(resp, ensure_ascii=False)
    assert "\\\\(" not in published and "\\\\)" not in published
    first = exam_store.get("kontrolni-sess")["questions"][0]
    assert all(o["text"].startswith("$") for o in first["options"])


# ---------------------------------------------------------------------------
# ŽIVI NALAZ B (2026-08-16): UKUPAN ROK GENERISANJA
# Jedno generisanje je isteklo, ponovljeni pokušaj prošao. Kontrolni nije imao
# ukupan rok, pa je legitiman dvopozivni zahtjev mogao trajati do ~90 s i
# probiti podrazumijevani proxy rok (60 s) — učenik bi dobio 504 umjesto
# kontrolisane poruke.
# ---------------------------------------------------------------------------

def test_total_deadline_is_below_the_default_proxy_timeout():
    from matbot import config
    assert config.kontrolni_deadline_s() <= 55.0
    # Klijent nikad ne odustaje prije servera.
    index = (pathlib.Path(__file__).resolve().parent.parent
             / "templates" / "index.html").read_text(encoding="utf-8")
    abort_ms = int(re.search(r"const EXAM_ABORT_MS = (\d+);", index).group(1))
    assert abort_ms / 1000.0 > config.kontrolni_deadline_s()


def test_repair_call_gets_only_the_remaining_budget(exam_store, monkeypatch):
    """Drugi poziv smije trajati samo do ukupnog roka — ne punih 45 s."""
    seen = []

    class TimingLLM(EchoKontrolniLLM):
        def kontrolni_turn(self, instructions, input_text, timeout_s=None):
            seen.append(timeout_s)
            return super().kontrolni_turn(instructions, input_text)

    llm = TimingLLM(mutate={2: lambda q, _s: q.model_copy(
        update={"lesson_id": "6-01-001"})})
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "ready" and llm.call_count == 2
    assert seen[0] is None                      # prvi poziv: puni rok adaptera
    assert seen[1] is not None and 0 < seen[1] <= config.kontrolni_deadline_s()


def test_exhausted_deadline_skips_repair_and_fails_closed(exam_store, monkeypatch):
    """Kad prvi poziv pojede budžet, popravka se NE pokušava (nema trećeg
    poziva, nema 504) — paket pada zatvoreno s kontrolisanom porukom."""
    monkeypatch.setattr(config, "kontrolni_deadline_s", lambda: 0.001)
    llm = EchoKontrolniLLM(mutate={2: lambda q, _s: q.model_copy(
        update={"lesson_id": "6-01-001"})})
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "failed"
    assert resp["message"] == kontrolni.GENERATION_FAILED_MESSAGE
    assert llm.call_count == 1                  # popravka preskočena


# ---------------------------------------------------------------------------
# DOKTRINA „TAČNO JEDAN TAČAN" (matbot/exactly_one.py) — dva ISTORIJSKA nalaza
# iz živih kampanja (2/300 objavljenih pitanja) i njihova klasa.
# ---------------------------------------------------------------------------

# Nalaz 1: 7. razred, „Konstrukcije izvedenih uglova 15°, 75°, 105°, 120°".
# Označen recept daje 45°, a traženih 75° daje NEOZNAČENA opcija b.
HISTORICAL_RECIPE_STEM = (
    "Koji slijed konstrukcija pomoću šestara i linijara (lenjira) daje ugao "
    "od $75^\\circ$?")
HISTORICAL_RECIPE_OPTIONS = [
    "Konstruisati ugao od $90^\\circ$, zatim njegovu simetralu i dobiti ugao od $45^\\circ$",
    "Konstruisati ugao od $90^\\circ$, zatim njegovu simetralu i dodati ugao od $30^\\circ$",
    "Konstruisati ugao od $60^\\circ$, zatim ugao od $30^\\circ$ i uzeti njihov zbir",
    "Konstruisati ugao od $60^\\circ$, zatim njegovu simetralu i sabrati ga sa uglom od $15^\\circ$",
]

# Nalaz 2: 7. razred, „Primjena podudarnosti u dokazivanju jednakosti elemenata".
# Ista podudarnost $ABD\\cong ACD$ daje I $BD=DC$ I $\\angle ABD=\\angle ACD$.
HISTORICAL_PROOF_STEM = (
    "U trouglu $ABC$ važi $AB=AC$. Duž $AD$ je simetrala ugla kod tjemena $A$ "
    "i siječe stranicu $BC$ u tački $D$. Koja jednakost se može dokazati "
    "primjenom podudarnosti trouglova $ABD$ i $ACD$?")
HISTORICAL_PROOF_OPTIONS = [
    "$BD=DC$", "$AB=AD$", "$BC=AD$", "$\\angle ABD=\\angle ACD$"]


@pytest.mark.parametrize("stem,options,marked", [
    (HISTORICAL_RECIPE_STEM, HISTORICAL_RECIPE_OPTIONS, 3),
    (HISTORICAL_PROOF_STEM, HISTORICAL_PROOF_OPTIONS, 0),
])
def test_historical_prose_defects_are_now_rejected(stem, options, marked):
    """Oba živa nalaza padaju — ne po rečenici, nego zato što server ne može
    DOKAZATI da je tačna tačno jedna opcija."""
    assert exactly_one.publication_failure(stem, options, marked) == \
        "unprovable_claim_selection"
    clean, code = _validate(make_kontrolni_question(
        text=stem, options=options, correct_option_index=marked,
        expected_answer=options[marked],
        solution="Postupak vodi do traženog rezultata."))
    assert clean is None and code == "unprovable_claim_selection"


def test_provable_claim_with_exactly_one_true_is_accepted():
    stem = "Uporedi razlomke $\\frac{5}{8}$ i $\\frac{2}{3}$. Koja tvrdnja je tačna?"
    options = ["$\\frac{5}{8}<\\frac{2}{3}$", "$\\frac{5}{8}>\\frac{2}{3}$",
               "$\\frac{5}{8}=\\frac{2}{3}$", "$\\frac{5}{8}=\\frac{35}{72}$"]
    verdict, _code = exactly_one.evaluate(stem, options, 0)
    assert verdict == exactly_one.PROVEN_ONE_CORRECT
    clean, code = _validate(make_kontrolni_question(
        text=stem, options=options, correct_option_index=0,
        expected_answer=options[0],
        solution="Svođenjem na zajednički nazivnik slijedi $\\frac{5}{8}<\\frac{2}{3}$."))
    assert clean is not None, code


@pytest.mark.parametrize("options,marked,verdict", [
    (["$1>2$", "$2>3$", "$3>4$", "$4>5$"], 0, exactly_one.PROVEN_ZERO_CORRECT),
    (["$1<2$", "$2<3$", "$3>4$", "$4>5$"], 0, exactly_one.PROVEN_MULTI_CORRECT),
    (["$1<2$", "$2>3$", "$3>4$", "$4>5$"], 1, exactly_one.PROVEN_ZERO_CORRECT),
    (["$1<2$", "$2>3$", "$3>4$", "$4>5$"], 0, exactly_one.PROVEN_ONE_CORRECT),
])
def test_four_verdicts_of_the_doctrine(options, marked, verdict):
    assert exactly_one.evaluate("Koja tvrdnja je tačna?", options, marked)[0] == verdict


def test_value_questions_never_enter_the_claim_gate():
    """Pitanja koja traže REZULTAT ostaju netaknuta — inače bi doktrina
    oborila i sasvim ispravna računska pitanja."""
    for stem, options in (
        ("Izračunaj proizvod $2\\cdot\\frac{3}{5}\\cdot\\frac{4}{7}$.",
         ["$\\frac{24}{35}$", "$\\frac{12}{35}$", "$\\frac{8}{35}$", "$\\frac{24}{17}$"]),
        ("Odredi najveći zajednički djelilac brojeva $84$ i $126$.",
         ["$42$", "$21$", "$14$", "$63$"]),
        ("Zapiši nepravi razlomak $\\frac{17}{5}$ u obliku mješovitog broja.",
         ["$3\\frac{2}{5}$", "$2\\frac{3}{5}$", "$3\\frac{5}{2}$", "$4\\frac{2}{5}$"]),
    ):
        assert not exactly_one.is_claim_selection(stem, options), stem
        assert exactly_one.publication_failure(stem, options, 0) == ""


@pytest.mark.parametrize("options", [
    ["$\\frac{1}{2}$", "$0,5$", "$\\frac{1}{3}$", "$\\frac{1}{4}$"],        # decimalni ≡ razlomak
    ["$8\\sqrt{2}$", "$11,3$", "$\\frac{1}{3}$", "$\\frac{1}{4}$"],          # radikal ≡ decimalni
    ["$\\frac{2}{4}$", "$\\frac{1}{2}$", "$\\frac{1}{3}$", "$\\frac{1}{5}$"],  # neskraćen ≡ skraćen
])
def test_equivalent_options_are_always_rejected(options):
    clean, code = _validate(make_kontrolni_question(
        text="Izračunaj vrijednost izraza.", options=options,
        correct_option_index=0, expected_answer=options[0],
        solution=f"Rezultat je {options[0]}."))
    assert clean is None and code in ("equivalent_options", "duplicate_options")


# ---------------------------------------------------------------------------
# POPRAVKA ZNA RAZLOG (živi baseline 6-04: isti `equivalent_options` i prije i
# poslije popravke, jer popravka nije znala šta je bilo pogrešno)
# ---------------------------------------------------------------------------

def test_repair_receives_the_exact_failure_reason(exam_store):
    def equivalent(question, _slot):
        return question.model_copy(update={
            "options": ["$\\frac{1}{2}$", "$0,5$", "$\\frac{1}{3}$", "$\\frac{1}{4}$"],
            "expected_answer": "$\\frac{1}{2}$",
            "solution": "Rezultat je $\\frac{1}{2}$.",
        })

    llm = EchoKontrolniLLM(mutate={3: equivalent})
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "ready" and llm.call_count == 2
    repair_input = llm.kontrolni_calls[1][1]
    assert "PRETHODNI POKUŠAJ ZA OVAJ SLOT JE ODBIJEN" in repair_input
    assert "ISTU brojnu vrijednost" in repair_input
    assert "Zadrži ISTU lekciju i ISTU težinu" in repair_input
    # Popravka je SLOT-SKOPIRANA: traži se samo pali slot.
    assert _parse_slots(repair_input) and [s["slot"] for s in _parse_slots(repair_input)] == [3]


def test_repair_keeps_lesson_and_difficulty_of_the_failed_slot(exam_store):
    llm = EchoKontrolniLLM(mutate={2: lambda q, _s: q.model_copy(
        update={"options": ["$\\frac{1}{2}$", "$0,5$", "$\\frac{1}{3}$", "$\\frac{1}{4}$"],
                "expected_answer": "$\\frac{1}{2}$"})})
    _s, resp = kontrolni.run_start(exam_store, llm, start_payload())
    assert resp["status"] == "ready"
    first_slots = {s["slot"]: (s["lesson_id"], s["difficulty"])
                   for s in _parse_slots(llm.kontrolni_calls[0][1])}
    repair_slots = {s["slot"]: (s["lesson_id"], s["difficulty"])
                    for s in _parse_slots(llm.kontrolni_calls[1][1])}
    assert repair_slots == {2: first_slots[2]}
    state = exam_store.get("kontrolni-sess")
    assert state["questions"][1]["lesson_id"] == first_slots[2][0]
    assert state["questions"][1]["difficulty"] == first_slots[2][1]


def test_published_options_are_always_pairwise_non_equivalent(exam_store):
    llm = EchoKontrolniLLM()
    kontrolni.run_start(exam_store, llm, start_payload())
    for question in exam_store.get("kontrolni-sess")["questions"]:
        texts = [o["text"] for o in question["options"]]
        assert not option_equivalence.find_equivalent_option_pairs(texts)
        assert not option_equivalence.find_textual_duplicate_pairs(texts)


@pytest.mark.parametrize("code,fragment", [
    ("equivalent_options", "ISTU brojnu vrijednost"),
    ("unprovable_claim_selection", "KONKRETAN rezultat"),
    ("mcq_integrity_marked_option_math_mismatch", "nije rezultat zadatka"),
    ("lesson_target_replaced", "NEPROMIJENJEN `lesson_id`"),
    ("stem_reveals_marked_option", "ne smije otkrivati"),
    ("nepoznat_kod_koji_ne_postoji", "Provjeri račun"),
])
def test_repair_hints_are_specific_and_closed(code, fragment):
    assert fragment in kontrolni_repair_hint(code)


def test_prompt_actually_carries_rules_and_slots(exam_store):
    llm = EchoKontrolniLLM()
    kontrolni.run_start(exam_store, llm, start_payload())
    instructions, input_text = llm.kontrolni_calls[0]
    for fragment in ("ZNAČENJE TEŽINE", "TAČNO 4 opcije", "TAČNO JEDNA",
                     "bosanskom jeziku", "NIKAD ne zamjenjuj ciljanu lekciju"):
        assert fragment in instructions, fragment
    assert input_text.count("SLOT ") == 5
    assert "OBLAST: Razlomci" in input_text
    # Popravka nosi tekstove PRIHVAĆENIH pitanja kao „već iskorišteno“.
    llm2 = EchoKontrolniLLM(mutate={2: lambda q, _s: q.model_copy(
        update={"lesson_id": "6-01-001"})})
    store2 = kontrolni.KontrolniStore()
    kontrolni.run_start(store2, llm2, start_payload())
    repair_input = llm2.kontrolni_calls[1][1]
    assert "VEĆ ISKORIŠTENA PITANJA" in repair_input


# ---------------------------------------------------------------------------
# API sloj: guard lanac + ugovor odgovora
# ---------------------------------------------------------------------------

def _client_exam_start(client, fake, **kw):
    payload = start_payload(**kw)
    return client.post("/api/ai-tutor/exam/start", json=payload)


def test_api_exam_start_requires_token(flask_app):
    c = flask_app.test_client()                      # BEZ tokena
    r = c.post("/api/ai-tutor/exam/start", json=start_payload())
    assert r.status_code == 401


def test_api_exam_submit_requires_token(flask_app):
    c = flask_app.test_client()
    r = c.post("/api/ai-tutor/exam/submit", json={})
    assert r.status_code == 401


def test_api_exam_start_end_to_end(client, flask_app):
    flask_app.config["MATBOT_LLM"] = EchoKontrolniLLM()
    r = client.post("/api/ai-tutor/exam/start", json=start_payload())
    assert r.status_code == 200
    j = r.get_json()
    assert j["status"] == "ready" and len(j["questions"]) == 5
    body = r.get_data(as_text=True)
    for forbidden in ("correct", "solution", "expected", "lesson"):
        assert forbidden not in body
    # Predaja kroz API — sve tačno.
    store = flask_app.config["MATBOT_EXAM_STORE"]
    answers = {q["id"]: q["correct_option_id"]
               for q in store.get("kontrolni-sess")["questions"]}
    r2 = client.post("/api/ai-tutor/exam/submit", json={
        "session_id": "kontrolni-sess", "exam_id": j["exam_id"], "answers": answers})
    assert r2.status_code == 200
    graded = r2.get_json()
    assert graded["score"] == 5 and graded["percentage"] == 100
    assert "Odlično" in graded["recommendation"]["message"]


def test_api_invalid_payload_is_400_with_zero_model_calls(client, fake_llm):
    r = client.post("/api/ai-tutor/exam/start", json={"session_id": "s", "grade": 5,
                                                      "oblast_id": "6-04"})
    assert r.status_code == 400
    assert fake_llm.call_count == 0


def test_api_generation_failure_returns_controlled_message(client, flask_app):
    class DeadLLM(FakeLLM):
        def kontrolni_turn(self, instructions, input_text, timeout_s=None):
            self.calls.append((instructions, input_text))
            raise LLMUnavailable("mreža")

    flask_app.config["MATBOT_LLM"] = DeadLLM()
    r = client.post("/api/ai-tutor/exam/start", json=start_payload())
    assert r.status_code == 200
    j = r.get_json()
    assert j["status"] == "failed"
    assert j["message"] == kontrolni.GENERATION_FAILED_MESSAGE
    # Interni kodovi nikad u browser.
    assert "llm" not in json.dumps(j)


def test_api_chat_exam_mode_still_returns_canned_message(client, fake_llm):
    # Postojeći /chat ugovor za mode=exam ostaje NEPROMIJENJEN (regresija):
    # novi mod živi na /exam/* endpointima.
    r = client.post("/api/ai-tutor/chat", json={
        "session_id": "s", "grade": 6, "mode": "exam", "selected_topic": "",
        "selected_oblast": "", "student_message": "Pripremi me.",
        "conversation_history": []})
    assert r.status_code == 200
    assert "Vježbaj sa mnom" in r.get_json()["answer"]
    assert fake_llm.call_count == 0
