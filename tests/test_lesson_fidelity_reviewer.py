"""Recenzent vjernosti lekciji — pet prijavljenih živih slučajeva.

Aktivan put je STABILAN `legacy_single_call`; recenzent je JEDAN dodatni poziv
i to SAMO na turnu koji pravi ili mijenja zadatak.

Konkretne lekcije iz nalaza žive OVDJE (fixture), nikad u kodu motora — vidi
`test_no_lesson_id_branching_was_introduced` na dnu.
"""
import copy
import json
import re
from pathlib import Path

import pytest

from matbot import lesson_fidelity, task_families
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.topics import lesson_info
from tests.conftest import (FakeLLM, make_fidelity_review, make_options,
                            make_output, make_task, make_task_for_family,
                            queue_generation)

ROOT = Path(__file__).resolve().parent.parent

# --- pet prijavljenih slučajeva (fixture, ne kod) ---------------------------
DECIMAL_COMPARE = ("6-05-006", 6, "Upoređivanje decimalnih brojeva")
DIVISIBILITY_RULES = ("6-03-004", 6, "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25")
ANGLE_COMPARE = ("6-09-007", 6, "Upoređivanje uglova")
INTEGER_ADD = ("7-02-008", 7, "Sabiranje cijelih brojeva različitih znakova")
SYSTEM_WORD = ("9-05-013", 9, "Tekstualni zadatak sa sistemom")


def _turn(topic, grade, **changes):
    payload = {
        "session_id": f"fid-{topic}", "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def _task(text, options, expected="", correct_index=0):
    return make_task(text=text, options=make_options(*options),
                     correct_option_index=correct_index,
                     expected=expected or options[correct_index])


# ---------------------------------------------------------------------------
# ROUTING: ispravka mapiranja (defekt se NE skriva iza recenzenta)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("topic,grade,expected_first", [
    # DECIMALNI brojevi → reprezentacijski neutralan `compare_or_order`;
    # `compare_fractions` bi svojim validatorom odbio decimalni zapis.
    (DECIMAL_COMPARE[0], DECIMAL_COMPARE[1], "compare_or_order"),
    (ANGLE_COMPARE[0], ANGLE_COMPARE[1], "compare_or_order"),
    (SYSTEM_WORD[0], SYSTEM_WORD[1], "system_word_problem"),
    ("6-04-008", 6, "compare_fractions"),   # lekcija BAŠ o razlomcima
])
def test_routing_now_matches_the_task_form_named_in_the_lesson_title(
        topic, grade, expected_first):
    """Naslov lekcije doslovno imenuje oblik; routing mu sada daje prednost."""
    info = lesson_info(grade, topic)
    families = task_families.applicable_families(
        grade, info["oblast"], info["title"], lesson_id=topic)
    assert families[0] == expected_first, families


def test_routing_fix_did_not_introduce_any_new_family():
    """Ispravka SAMO podiže porodicu koja je već bila u listi te oblasti."""
    catalog = set(task_families.ALL_FAMILIES) if hasattr(task_families, "ALL_FAMILIES") \
        else None
    data = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    for grade, payload in data["grades"].items():
        for lesson in payload["lessons"]:
            families = task_families.applicable_families(
                int(grade), lesson["oblast"], lesson["title"], lesson_id=lesson["id"])
            assert len(families) == len(set(families)), lesson["id"]
            if catalog:
                assert set(families) <= catalog, lesson["id"]


def test_integer_addition_routing_is_unchanged():
    """Slučaj 4 je bio ISPRAVAN i mora ostati netaknut."""
    topic, grade, _ = INTEGER_ADD
    info = lesson_info(grade, topic)
    families = task_families.applicable_families(
        grade, info["oblast"], info["title"], lesson_id=topic)
    assert families[0] == "direct_computation"


# ---------------------------------------------------------------------------
# PET SLUČAJEVA KROZ RECENZENTA
# ---------------------------------------------------------------------------

def _reject_case(topic, grade, task, reason):
    """Recenzent odbija zadatak koji ne ispituje izabranu lekciju."""
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, task, review=make_fidelity_review(
        decision="fail_closed", fail_reason_code=reason,
        tests_exact_lesson=False))
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert fake.call_count == 2                    # nikad treći poziv
    assert store.peek(f"fid-{topic}") is None      # bez mutacije stanja
    return response


def test_case1_fraction_subtraction_is_rejected_for_decimal_comparison():
    topic, grade, _ = DECIMAL_COMPARE
    _reject_case(topic, grade, _task(
        r"Izračunaj $\frac{3}{4} - \frac{1}{8}$.",
        (r"$\frac{5}{8}$", r"$\frac{1}{2}$", r"$\frac{3}{8}$", r"$\frac{7}{8}$")),
        "wrong_lesson")


def test_case1_decimal_comparison_task_is_accepted():
    topic, grade, _ = DECIMAL_COMPARE
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task(
        "Koji je broj veći: $0,7$ ili $0,68$?",
        ("$0,7$", "$0,68$", "Jednaki su.", "Nije moguće odrediti.")))
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["status"] == "ready"
    assert "0,7" in store.peek(f"fid-{topic}")["current_task"]


def test_case2_plain_division_is_rejected_for_divisibility_rules():
    topic, grade, _ = DIVISIBILITY_RULES
    _reject_case(topic, grade, _task(
        "Izračunaj $375 : 25$.", ("$15$", "$14$", "$16$", "$25$")),
        "wrong_task_form")


def test_case2_divisibility_rule_task_is_accepted():
    topic, grade, _ = DIVISIBILITY_RULES
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task(
        "Koji od brojeva je djeljiv sa $9$ po pravilu o zbiru cifara?",
        ("$153$", "$142$", "$167$", "$121$")))
    assert run_practice_turn(store, fake, _turn(topic, grade))["status"] == "ready"


def test_case3_angle_subtraction_is_rejected_for_angle_comparison():
    topic, grade, _ = ANGLE_COMPARE
    _reject_case(topic, grade, _task(
        r"Izračunaj razliku uglova $\alpha=70^\circ$ i $\beta=45^\circ$.",
        (r"$25^\circ$", r"$115^\circ$", r"$35^\circ$", r"$20^\circ$")),
        "wrong_lesson")


def test_case3_angle_comparison_task_is_accepted():
    topic, grade, _ = ANGLE_COMPARE
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task(
        r"Koji ugao je veći: $\alpha=70^\circ$ ili $\beta=45^\circ$?",
        (r"$\alpha$", r"$\beta$", "Jednaki su.", "Nije moguće odrediti.")))
    assert run_practice_turn(store, fake, _turn(topic, grade))["status"] == "ready"


def test_case4_integer_addition_of_different_signs_is_accepted():
    """Slučaj 4 je bio ISPRAVAN — recenzent ga NE SMIJE odbiti."""
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task(
        "Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")))
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["status"] == "ready"
    assert "-7 + 12" in store.peek(f"fid-{topic}")["current_task"]
    assert fake.call_count == 2


def test_case5_bare_system_is_rejected_for_a_textual_system_lesson():
    topic, grade, _ = SYSTEM_WORD
    _reject_case(topic, grade, _task(
        "Riješi sistem: $x+y=10$, $x-y=2$.",
        ("$(6,4)$", "$(4,6)$", "$(5,5)$", "$(8,2)$")),
        "wrong_task_form")


def test_case5_word_problem_with_a_system_is_accepted():
    topic, grade, _ = SYSTEM_WORD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task(
        "Amar i Lejla zajedno imaju $10$ KM. Amar ima $2$ KM više od Lejle. "
        "Koliko KM ima svako?",
        ("$(6,4)$", "$(4,6)$", "$(5,5)$", "$(8,2)$")))
    assert run_practice_turn(store, fake, _turn(topic, grade))["status"] == "ready"


# ---------------------------------------------------------------------------
# ISPRAVKA (`correct`) — recenzentov zadatak se objavljuje
# ---------------------------------------------------------------------------

def test_corrected_task_is_the_one_published():
    topic, grade, _ = DECIMAL_COMPARE
    store, fake = SessionStore(), FakeLLM()
    corrected = _task("Koji je broj manji: $0,45$ ili $0,5$?",
                      ("$0,45$", "$0,5$", "Jednaki su.", "Ne može se odrediti."))
    queue_generation(fake, _task(
        r"Izračunaj $\frac{3}{4} - \frac{1}{8}$.",
        (r"$\frac{5}{8}$", r"$\frac{1}{2}$", r"$\frac{3}{8}$", r"$\frac{7}{8}$")),
        review=make_fidelity_review(decision="correct", corrected_task=corrected))
    response = run_practice_turn(store, fake, _turn(topic, grade))

    assert response["status"] == "ready"
    session = store.peek(f"fid-{topic}")
    assert "0,45" in session["current_task"]
    assert "frac{3}{4}" not in session["current_task"]
    assert fake.call_count == 2


def test_corrected_task_keeps_answer_and_marked_option_consistent():
    topic, grade, _ = DECIMAL_COMPARE
    store, fake = SessionStore(), FakeLLM()
    corrected = _task("Koji je broj veći: $0,9$ ili $0,89$?",
                      ("$0,9$", "$0,89$", "Jednaki su.", "Ne može se odrediti."),
                      correct_index=0)
    queue_generation(fake, _task("Izračunaj $2+2$.", ("$4$", "$3$", "$5$", "$6$")),
                     review=make_fidelity_review(decision="correct",
                                                 corrected_task=corrected))
    run_practice_turn(store, fake, _turn(topic, grade))
    session = store.peek(f"fid-{topic}")
    correct_text = next(o["text"] for o in session["current_options"]
                        if o["id"] == session["correct_option_id"])
    assert correct_text == "$0,9$"
    assert session["expected_answer_summary"] == "$0,9$"


def test_correct_decision_without_a_task_fails_closed():
    topic, grade, _ = DECIMAL_COMPARE
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $2+2$.", ("$4$", "$3$", "$5$", "$6$")),
                     review=make_fidelity_review(decision="correct", corrected_task=None))
    assert run_practice_turn(store, fake, _turn(topic, grade))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(f"fid-{topic}") is None


def test_reviewer_cannot_change_the_selected_lesson():
    """Ispravljen zadatak se objavljuje pod ISTOM lekcijom; recenzent nema
    nijedan kanal kojim bi promijenio temu, razred ili oblast."""
    topic, grade, _ = DECIMAL_COMPARE
    store, fake = SessionStore(), FakeLLM()
    corrected = _task("Koji je broj veći: $1,2$ ili $1,15$?",
                      ("$1,2$", "$1,15$", "Jednaki su.", "Ne zna se."))
    queue_generation(fake, _task("Izračunaj $2+2$.", ("$4$", "$3$", "$5$", "$6$")),
                     review=make_fidelity_review(decision="correct",
                                                 corrected_task=corrected))
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["effective_topic"] == topic
    session = store.peek(f"fid-{topic}")
    assert session["lesson_id"] == topic
    assert session["grade"] == grade
    assert session["oblast"] == lesson_info(grade, topic)["oblast"]
    # Šema recenzenta uopšte nema polje za lekciju/razred/oblast.
    fields = set(lesson_fidelity.LessonFidelityReview.model_fields)
    assert fields == {"decision", "checks", "fail_reason_code", "corrected_task"}


def test_approval_contradicting_its_own_checks_fails_closed():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")),
                     review=make_fidelity_review(decision="approve", math_correct=False))
    assert run_practice_turn(store, fake, _turn(topic, grade))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(f"fid-{topic}") is None


# ---------------------------------------------------------------------------
# GRANICA POZIVA I STANJA
# ---------------------------------------------------------------------------

def test_task_generation_uses_exactly_two_calls():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")))
    assert run_practice_turn(store, fake, _turn(topic, grade))["status"] == "ready"
    assert fake.call_count == 2
    assert len(fake.fidelity_calls) == 1


def test_invalid_tutor_payload_never_reaches_the_reviewer():
    """Payload koji padne na ŠEMI (prazan tekst zadatka) staje na jednom pozivu
    — nema šta recenzirati."""
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=_task(
        "   ", ("$5$", "$-5$", "$19$", "$-19$"))))
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1
    assert len(fake.fidelity_calls) == 0
    assert store.peek(f"fid-{topic}") is None


def test_duplicate_options_still_fail_closed_after_review():
    """Duple opcije su SADRŽAJNI defekt: recenzent ih smije vidjeti (ima
    `options_unique`), ali i kad ih propusti, serverska provjera obara zadatak."""
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$5$", "$19$", "$-19$")))
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 2                 # i dalje nikad tri
    assert store.peek(f"fid-{topic}") is None


@pytest.mark.parametrize("message,queued", [
    ("ne znam", "hint"),
    ("Koliko je 5+5?", "reply"),
    ("Objasni mi ovu lekciju.", "reply"),
])
def test_non_generation_turns_keep_the_existing_single_call(message, queued):
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")))
    run_practice_turn(store, fake, _turn(topic, grade))
    calls_before, fidelity_before = fake.call_count, len(fake.fidelity_calls)

    if queued == "hint":
        fake.queue(make_output(reply="Pogledaj znakove.", gave_hint=True))
    else:
        fake.queue(make_output(reply="Evo objašnjenja."))
    response = run_practice_turn(store, fake, _turn(topic, grade, student_message=message))

    assert response["status"] == "ready"
    assert fake.call_count == calls_before + 1          # tačno jedan poziv
    assert len(fake.fidelity_calls) == fidelity_before  # recenzent NIJE zvan


def test_answer_click_keeps_the_existing_single_call():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")))
    run_practice_turn(store, fake, _turn(topic, grade))
    session = store.peek(f"fid-{topic}")
    wrong = next(o["id"] for o in session["current_options"]
                 if o["id"] != session["correct_option_id"])
    calls_before, fidelity_before = fake.call_count, len(fake.fidelity_calls)

    fake.queue(make_output(reply="", hint="Pazi na znakove."))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, interaction_type="choice_answer", selected_option_id=wrong,
        student_message="[klik]", client_turn_id="c1"))

    assert response["answer_verdict"] == "incorrect"
    assert fake.call_count == calls_before + 1
    assert len(fake.fidelity_calls) == fidelity_before


def test_rejection_after_an_existing_task_leaves_state_untouched():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")))
    run_practice_turn(store, fake, _turn(topic, grade))
    before = copy.deepcopy(store.peek(f"fid-{topic}"))

    queue_generation(fake, _task("Izračunaj $2+2$.", ("$4$", "$3$", "$5$", "$6$")),
                     review=make_fidelity_review(decision="fail_closed",
                                                 fail_reason_code="wrong_lesson",
                                                 tests_exact_lesson=False))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi drugi zadatak."))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(f"fid-{topic}") == before


def test_easier_request_sends_the_prior_task_for_comparison():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $-27 + 45$.", ("$18$", "$-18$", "$72$", "$-72$")))
    run_practice_turn(store, fake, _turn(topic, grade))
    prior = store.peek(f"fid-{topic}")["current_task"]

    queue_generation(fake, _task("Izračunaj $-3 + 5$.", ("$2$", "$-2$", "$8$", "$-8$")))
    run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj lakši zadatak.", difficulty_request="easier"))

    reviewer_input = fake.fidelity_calls[-1][1]
    assert prior in reviewer_input
    assert "easier" in reviewer_input


def test_wrong_difficulty_direction_fails_closed_when_a_change_was_requested():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $-3 + 5$.", ("$2$", "$-2$", "$8$", "$-8$")))
    run_practice_turn(store, fake, _turn(topic, grade))
    before = copy.deepcopy(store.peek(f"fid-{topic}"))

    queue_generation(fake, _task("Izračunaj $-4 + 9$.", ("$5$", "$-5$", "$13$", "$-13$")),
                     review=make_fidelity_review(difficulty_direction_correct=False))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj teži zadatak.", difficulty_request="harder"))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(f"fid-{topic}") == before


# ---------------------------------------------------------------------------
# PROMPT I ARHITEKTURA
# ---------------------------------------------------------------------------

def test_reviewer_receives_the_full_lesson_identity_and_the_draft():
    topic, grade, title = DECIMAL_COMPARE
    store, fake = SessionStore(), FakeLLM()
    draft_text = "Koji je broj veći: $0,7$ ili $0,68$?"
    queue_generation(fake, _task(draft_text, ("$0,7$", "$0,68$", "Jednaki su.", "Ne zna se.")))
    run_practice_turn(store, fake, _turn(topic, grade))

    _instructions, reviewer_input = fake.fidelity_calls[0]
    info = lesson_info(grade, topic)
    assert title in reviewer_input                 # tačan naslov
    assert topic in reviewer_input                 # kanonski ID
    assert info["oblast"] in reviewer_input        # oblast
    assert f"razred: {grade}" in reviewer_input
    assert draft_text in reviewer_input            # nacrt
    assert "OZNAČENA KAO TAČNA" in reviewer_input  # označena opcija
    assert "Daj mi zadatak." in reviewer_input     # zahtjev učenika


def test_reviewer_prompt_states_that_area_similarity_is_not_enough():
    instructions = lesson_fidelity.build_instructions(6)
    assert "TAČNO IZABRANU LEKCIJU" in instructions
    assert "„Ista oblast“ NIJE dovoljno" in instructions
    for principle in ("UPOREĐIVANJU", "PRAVILIMA DJELJIVOSTI", "TEKSTUALNOM"):
        assert principle in instructions


def test_no_lesson_id_branching_was_introduced():
    topic_re = re.compile(r"\b\d-\d{2}-\d{3}\b")
    for name in ("matbot/lesson_fidelity.py", "matbot/task_families.py",
                 "matbot/practice.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        offenders = [line.strip() for line in source.splitlines()
                     if topic_re.search(line)]
        assert not offenders, f"{name}: {offenders}"


def test_contract_lessons_do_not_pay_for_the_reviewer():
    """Šest lekcija s ugovorom gradi zadatak SERVERSKI i već ga determinističi
    provjerava — dodatni model poziv tu ne bi ništa dokazao."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    response = run_practice_turn(store, fake, _turn("6-04-009", 6))
    assert response["status"] == "ready"
    assert fake.call_count == 1
    assert len(fake.fidelity_calls) == 0


# ---------------------------------------------------------------------------
# DEKLARATIVNI IZUZECI ROUTINGA (data/routing_overrides.json)
# ---------------------------------------------------------------------------

DECIMAL_EXPR_WORD = ("6-05-011", 6, "Brojevni izrazi i tekstualni zadaci s decimalnim brojevima")
DEPENDENCY_COMPARE = ("9-03-021", 9, "Poređenje direktne, obrnute i linearne zavisnosti")


@pytest.mark.parametrize("topic,grade,expected_first", [
    (DECIMAL_EXPR_WORD[0], DECIMAL_EXPR_WORD[1], "word_problem"),
    (DEPENDENCY_COMPARE[0], DEPENDENCY_COMPARE[1], "recognize_correct_statement"),
])
def test_routing_override_wins_over_the_generic_title_rule(topic, grade, expected_first):
    """Generičko pravilo iz naslova je dobra pretpostavka; za ove dvije lekcije
    podaci imaju zadnju riječ."""
    info = lesson_info(grade, topic)
    families = task_families.applicable_families(
        grade, info["oblast"], info["title"], lesson_id=topic)
    assert families[0] == expected_first, families
    assert len(families) == len(set(families))


def test_decimal_word_problem_lesson_no_longer_uses_a_fraction_family():
    """Lekcija je o DECIMALNIM brojevima — porodica s razlomcima bi nametnula
    pogrešan zapis."""
    topic, grade, _ = DECIMAL_EXPR_WORD
    info = lesson_info(grade, topic)
    families = task_families.applicable_families(
        grade, info["oblast"], info["title"], lesson_id=topic)
    assert families[0] == "word_problem"
    assert "fraction" not in families[0]


def test_decimal_word_problem_task_publishes_end_to_end():
    topic, grade, _ = DECIMAL_EXPR_WORD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task(
        "Amar je kupio svesku za $2,50$ KM i olovku za $1,20$ KM. "
        "Koliko je ukupno platio?",
        ("$3,70$ KM", "$3,30$ KM", "$1,30$ KM", "$2,70$ KM")))
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["status"] == "ready"
    assert "2,50" in store.peek(f"fid-{topic}")["current_task"]


def test_dependency_comparison_lesson_uses_the_conceptual_family():
    """Naslov traži pojmovno razlikovanje VRSTA zavisnosti, ne uređivanje
    brojeva po veličini."""
    topic, grade, _ = DEPENDENCY_COMPARE
    info = lesson_info(grade, topic)
    families = task_families.applicable_families(
        grade, info["oblast"], info["title"], lesson_id=topic)
    assert families[0] == "recognize_correct_statement"
    assert families[0] != "compare_or_order"


def test_dependency_comparison_task_publishes_end_to_end():
    topic, grade, _ = DEPENDENCY_COMPARE
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task(
        "Koja tvrdnja o direktnoj i obrnutoj proporcionalnosti je tačna?",
        ("Kod direktne zavisnosti količnik je stalan, a kod obrnute proizvod.",
         "Kod obje zavisnosti proizvod je stalan.",
         "Kod direktne zavisnosti proizvod je stalan.",
         "Linearna zavisnost nikad nije direktna proporcionalnost.")))
    assert run_practice_turn(store, fake, _turn(topic, grade))["status"] == "ready"


def test_override_table_only_names_known_families():
    """Red koji imenuje nepostojeću porodicu bi tiho proizveo porodicu koju
    katalog ne poznaje — zato se ignoriše, a ovaj test to i drži čistim."""
    payload = json.loads((ROOT / "data" / "routing_overrides.json").read_text(
        encoding="utf-8"))
    rows = payload["overrides"]
    assert rows, "tabela izuzetaka ne smije biti prazna dok je referencirana"
    for row in rows:
        assert row["primary_family"] in task_families.FAMILY_DESCRIPTIONS, row
        assert row["canonical_topic_id"]
        assert row["lesson_title"]
        assert row["reason"]


def test_override_table_stays_small():
    """Izuzetak je izuzetak: rast ove tabele znači da generičko pravilo treba
    ispraviti, a ne zaobići."""
    payload = json.loads((ROOT / "data" / "routing_overrides.json").read_text(
        encoding="utf-8"))
    assert len(payload["overrides"]) <= 5


def test_unknown_family_in_an_override_is_ignored(monkeypatch):
    monkeypatch.setattr(task_families, "_OVERRIDES_CACHE",
                        {"9-03-021": "izmisljena_porodica"})
    info = lesson_info(9, "9-03-021")
    families = task_families.applicable_families(
        9, info["oblast"], info["title"], lesson_id="9-03-021")
    assert families[0] != "izmisljena_porodica"
    monkeypatch.setattr(task_families, "_OVERRIDES_CACHE", None)


def test_no_lesson_ids_leaked_into_python_branching():
    """ID-jevi lekcija smiju živjeti u PODACIMA i fixture-ima, nikad u grani."""
    topic_re = re.compile(r"['\"]\d-\d{2}-\d{3}['\"]")
    source = (ROOT / "matbot" / "task_families.py").read_text(encoding="utf-8")
    assert not topic_re.search(source)
