"""Regresija za produkcijski nalaz (VPS, lekcija „Tekstualni zadaci s
razlomcima“, dvaput zaredom):

    practice_turn category=invalid_output
    detail=family_contract_mismatch: fraction_word_problem:
    nedostaje obavezan oblik 'ima_zivotni_kontekst'

Tutor je vratio golu računsku operaciju bez životnog konteksta, a nacrt je
pao na determinističkom ugovoru porodice PRIJE nego što je recenzent
vjernosti lekciji (matbot/lesson_fidelity.py) dobio priliku da ga popravi na
temelju TAČNOG razloga kršenja. Ovaj fajl dokazuje da:

  1. bare fraction operacija i dalje STIŽE do recenzenta (isti broj poziva
     kao i do sada — recenzent se poziva bezuslovno na svaki new_task, vidi
     tests/test_lesson_fidelity_reviewer.py::test_task_generation_uses_exactly_two_calls);
  2. recenzent DOBIJA tačan razlog kršenja (family_contract_mismatch) u
     promptu, pa može ciljano ispraviti baš taj nedostatak;
  3. ispravljen zadatak prolazi punu (post-recenzent) provjeru ugovora;
  4. vidljiv izlaz nosi CIJELU priču i pitanje;
  5. degenerisan recenzentov odgovor („Evo zadatka.“ + gole opcije) i dalje
     pada zatvoreno — recenzent SMIJE odobriti pogrešnu stvar, deterministička
     provjera poslije njega je zadnja linija odbrane;
  6. nikad treći poziv, nikad mutacija stanja na odbijanje.
"""
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.task_family_validation import FamilyContractError, validate_task_family
from tests.conftest import (FakeLLM, make_fidelity_review, make_options,
                            make_output, make_task)

FRACTION_WORD_PROBLEM_LESSON = ("6-04-015", 6, "Tekstualni zadaci s razlomcima")

BARE_FRACTION_OP_TASK = make_task(
    text=r"Izračunaj $\frac{2}{8} + \frac{3}{8}$.",
    options=make_options(r"$\frac{5}{8}$", r"$\frac{5}{16}$", r"$\frac{6}{8}$", r"$\frac{1}{8}$"),
    expected=r"$\frac{5}{8}$",
)

REAL_WORD_PROBLEM_TASK = make_task(
    text=(
        "Amar je pojeo $\\frac{2}{8}$ torte na rođendanskoj proslavi, a Lejla "
        "$\\frac{3}{8}$ iste torte. Koliko su torte ukupno pojeli zajedno?"
    ),
    options=make_options(r"$\frac{5}{8}$", r"$\frac{5}{16}$", r"$\frac{6}{8}$", r"$\frac{1}{8}$"),
    expected=r"$\frac{5}{8}$",
)


def _turn(**changes):
    payload = {
        "session_id": "fwp-6-04-015", "grade": FRACTION_WORD_PROBLEM_LESSON[1],
        "selected_topic": FRACTION_WORD_PROBLEM_LESSON[0], "selected_oblast": "",
        "student_message": "Daj mi zadatak.", "intent": "", "difficulty_request": "",
        "interaction_phase": "", "last_tutor_task": "", "interaction_type": "student_question",
        "selected_option_id": "", "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def test_bare_fraction_operation_fails_the_deterministic_contract_directly():
    """Sanity: ista nedostatak koji je izazvao produkcijski pad, dokazan
    direktno na validatoru — dokumentuje TAČNU poruku iz VPS logova."""
    try:
        validate_task_family(
            "fraction_word_problem",
            question=BARE_FRACTION_OP_TASK.text,
            option_texts=[o.text for o in BARE_FRACTION_OP_TASK.options],
            correct_option_index=BARE_FRACTION_OP_TASK.correct_option_index,
            expected_answer=BARE_FRACTION_OP_TASK.expected_answer,
        )
        raise AssertionError("očekivan FamilyContractError")
    except FamilyContractError as e:
        assert "fraction_word_problem" in str(e)
        assert "ima_zivotni_kontekst" in str(e)


def test_bare_fraction_operation_reaches_the_reviewer_instead_of_failing_before_it():
    """Nacrt bez životnog konteksta MORA stići do recenzenta (2 poziva), ne
    smije pasti na 1 poziv prije njega — to je bio produkcijski defekt."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=BARE_FRACTION_OP_TASK))
    fake.queue(make_fidelity_review(decision="correct", corrected_task=REAL_WORD_PROBLEM_TASK))

    response = run_practice_turn(store, fake, _turn())

    assert len(fake.fidelity_calls) == 1, "recenzent MORA biti pozvan"
    assert fake.call_count == 2, "tačno dva poziva — nikad treći"
    assert response["status"] == "ready"


def test_reviewer_receives_the_exact_family_contract_mismatch_reason():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=BARE_FRACTION_OP_TASK))
    fake.queue(make_fidelity_review(decision="correct", corrected_task=REAL_WORD_PROBLEM_TASK))

    run_practice_turn(store, fake, _turn())

    _instructions, reviewer_input = fake.fidelity_calls[0]
    assert "DETERMINISTIČKA PROVJERA UGOVORA JE ODBILA NACRT" in reviewer_input
    assert "fraction_word_problem" in reviewer_input
    assert "ima_zivotni_kontekst" in reviewer_input
    assert "Tekstualni zadaci s razlomcima" in reviewer_input


def test_reviewer_correction_into_a_real_word_problem_publishes_the_full_story():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=BARE_FRACTION_OP_TASK))
    fake.queue(make_fidelity_review(decision="correct", corrected_task=REAL_WORD_PROBLEM_TASK))

    response = run_practice_turn(store, fake, _turn())

    assert response["status"] == "ready"
    session = store.peek("fwp-6-04-015")
    assert "rođendanskoj proslavi" in session["current_task"]
    assert "Koliko su torte ukupno pojeli zajedno?" in session["current_task"]
    # Server-owned uvod ("Evo zadatka.") + puna priča + pitanje, ne samo uvod.
    assert "rođendanskoj proslavi" in response["answer"]


def test_reviewer_response_of_only_the_intro_plus_options_is_rejected():
    """Degenerisan popravak — recenzent SMIJE ostati na golom „Evo zadatka.“ —
    i dalje mora pasti na determinističkoj provjeri poslije njega."""
    degenerate = make_task(
        text="Evo zadatka.",
        options=make_options("$1$", "$2$", "$3$", "$4$"),
        expected="$1$",
    )
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=BARE_FRACTION_OP_TASK))
    fake.queue(make_fidelity_review(decision="correct", corrected_task=degenerate))

    response = run_practice_turn(store, fake, _turn())

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 2, "nikad treći poziv čak ni na odbijanje"
    assert store.peek("fwp-6-04-015") is None, "odbijanje ne smije mutirati stanje"


def test_approved_bare_operation_without_correction_still_fails_closed():
    """Ako recenzent bezrazložno odobri (`approve`) nacrt koji krši ugovor,
    deterministička provjera poslije njega i dalje odbija — recenzent nikad
    nije jedina linija odbrane."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=BARE_FRACTION_OP_TASK))
    fake.queue(make_fidelity_review(decision="approve"))

    response = run_practice_turn(store, fake, _turn())

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 2
    assert store.peek("fwp-6-04-015") is None
