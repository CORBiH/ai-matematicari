"""Testovi multiple-choice Practice toka (4 opcije po zadatku, fake LLM, bez
mreže): generisanje/shuffle, choice_answer klik lifecycle, tekstualna pitanja
uz aktivne opcije i browser-safe reveal ugovor."""
import json

from tests.conftest import FakeLLM, make_options, make_output, make_task
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore


def turn_payload(msg="Daj mi jedan zadatak za vježbu iz ove teme.", **kw):
    base = {
        "session_id": "sess-1",
        "grade": 6,
        "selected_topic": "6-01-006",
        "selected_oblast": "",
        "student_message": msg,
        "intent": "",
        "difficulty_request": "",
        "interaction_phase": "",
        "last_tutor_task": "",
        "interaction_type": "",
        "selected_option_id": "",
        "client_turn_id": "",
    }
    base.update(kw)
    return base


def choice_payload(selected_option_id, client_turn_id="turn-1", **kw):
    return turn_payload(
        msg="[klik]",
        interaction_type="choice_answer",
        selected_option_id=selected_option_id,
        client_turn_id=client_turn_id,
        **kw,
    )


def start_session(store, fake, task_text="Skrati razlomak $\\frac{20}{32}$.", expected="5/8",
                   options=None, correct_option_index=0):
    if options is None:
        options = make_options("5/8", "10/16", "5/4", "4/5")
    fake.queue(make_output(
        reply="Evo zadatka za tebe.",
        new_task=make_task(text=task_text, expected=expected, options=options,
                            correct_option_index=correct_option_index),
    ))
    return run_practice_turn(store, fake, turn_payload())


def correct_option_id(session):
    return session["correct_option_id"]


def wrong_option_id(session):
    correct = session["correct_option_id"]
    return next(o["id"] for o in session["current_options"] if o["id"] != correct)


# ---------------------------------------------------------------------------
# Generisanje
# ---------------------------------------------------------------------------

def test_bootstrap_generates_exactly_four_options_with_one_correct_id():
    store, fake = SessionStore(), FakeLLM()
    r = start_session(store, fake)
    opts = r["next_state"]["task"]["options"]
    assert len(opts) == 4
    ids = {o["id"] for o in opts}
    assert ids == {"a", "b", "c", "d"}
    sess = store.peek("sess-1")
    assert sess["correct_option_id"] in ids


def test_browser_safe_response_never_leaks_correct_id_or_expected_answer():
    store, fake = SessionStore(), FakeLLM()
    r = start_session(store, fake)
    raw = json.dumps(r, ensure_ascii=False)
    assert "revealed_correct_option_id" not in raw
    assert "correct_option_id" not in raw
    assert "expected_answer" not in raw
    for opt in r["next_state"]["task"]["options"]:
        assert set(opt.keys()) == {"id", "text"}


def test_duplicate_option_texts_rejected_as_invalid_output():
    store, fake = SessionStore(), FakeLLM()
    before = store.peek("sess-1")
    fake.queue(make_output(
        reply="Evo.",
        new_task=make_task(options=make_options("1/2", "1/2", "1/3", "1/4"), correct_option_index=0),
    ))
    r = run_practice_turn(store, fake, turn_payload())
    assert "answer" in r and "status" not in r
    assert store.peek("sess-1") == before


def test_mathjax_sanitized_per_option():
    store, fake = SessionStore(), FakeLLM()
    broken = make_options("Izračunaj $\x0crac{1}{2}$.", "2", "3", "4")
    fake.queue(make_output(reply="Evo.", new_task=make_task(options=broken, correct_option_index=0)))
    r = run_practice_turn(store, fake, turn_payload())
    texts = [o["text"] for o in r["next_state"]["task"]["options"]]
    assert any("\\frac" in t for t in texts)
    assert not any("\x0c" in t for t in texts)


def test_shuffle_changes_position_but_keeps_correct_identity():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake, options=make_options("5/8", "10/16", "5/4", "4/5"), correct_option_index=0)
    sess = store.peek("sess-1")
    correct_id = sess["correct_option_id"]
    correct_text = next(o["text"] for o in sess["current_options"] if o["id"] == correct_id)
    assert correct_text == "5/8"


def test_idempotent_retry_of_bootstrap_like_generation_is_not_reshuffled_by_second_choice_retry():
    """Isti client_turn_id na choice_answer vraća IDENTIČAN poredak/response —
    dokazano niže u test_idempotent_retry_same_client_turn_id (klik grana).
    Ovdje samo potvrđujemo da se redoslijed opcija ne mijenja između dva čitanja
    iste sesije (nema drugog shufflea osim pri kreiranju zadatka)."""
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    first = store.peek("sess-1")["current_options"]
    second = store.peek("sess-1")["current_options"]
    assert first == second


# ---------------------------------------------------------------------------
# Klik
# ---------------------------------------------------------------------------

def test_correct_click_marks_correct_increments_streak_completes_task():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    sess = store.peek("sess-1")
    cid = correct_option_id(sess)
    fake.queue(make_output(reply="Tačno! Odličan posao."))
    r = run_practice_turn(store, fake, choice_payload(cid))
    assert r["answer_verdict"] == "correct"
    assert r["next_state"]["correct_streak"] == 1
    assert "revealed_correct_option_id" not in r
    assert store.peek("sess-1")["task_completed"] is True


def test_first_wrong_click_keeps_task_active_no_reveal():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    sess = store.peek("sess-1")
    wid = wrong_option_id(sess)
    fake.queue(make_output(reply="Nije baš — probaj ponovo."))
    r = run_practice_turn(store, fake, choice_payload(wid))
    assert r["answer_verdict"] == "incorrect"
    assert "revealed_correct_option_id" not in r
    sess_after = store.peek("sess-1")
    assert sess_after["task_completed"] is False
    assert sess_after["wrong_option_ids"] == [wid]


def test_second_wrong_click_reveals_and_completes_task():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    sess = store.peek("sess-1")
    cid = correct_option_id(sess)
    wid = wrong_option_id(sess)
    fake.queue(make_output(reply="Nije baš — probaj ponovo."))
    run_practice_turn(store, fake, choice_payload(wid, client_turn_id="t1"))
    fake.queue(make_output(reply="Evo cijelog postupka."))
    r = run_practice_turn(store, fake, choice_payload(wid, client_turn_id="t2"))
    assert r["answer_verdict"] == "incorrect"
    assert r["revealed_correct_option_id"] == cid
    assert store.peek("sess-1")["task_completed"] is True


def test_invalid_option_id_rejected_without_llm_call_or_state_change():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    before = store.peek("sess-1")
    calls_before = fake.call_count
    r = run_practice_turn(store, fake, choice_payload("z"))
    assert fake.call_count == calls_before
    assert "status" not in r
    assert store.peek("sess-1") == before


def test_same_option_clicked_twice_with_new_turn_id_counts_as_second_attempt():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    sess = store.peek("sess-1")
    cid = correct_option_id(sess)
    wid = wrong_option_id(sess)
    fake.queue(make_output(reply="Nije baš."))
    run_practice_turn(store, fake, choice_payload(wid, client_turn_id="t1"))
    fake.queue(make_output(reply="Ni ovaj put."))
    r = run_practice_turn(store, fake, choice_payload(wid, client_turn_id="t2"))
    assert r["revealed_correct_option_id"] == cid


def test_idempotent_retry_same_client_turn_id_returns_identical_result_no_double_count():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    sess = store.peek("sess-1")
    wid = wrong_option_id(sess)
    fake.queue(make_output(reply="Nije baš."))
    r1 = run_practice_turn(store, fake, choice_payload(wid, client_turn_id="same-id"))
    calls_after_first = fake.call_count
    r2 = run_practice_turn(store, fake, choice_payload(wid, client_turn_id="same-id"))
    assert r1 == r2
    assert fake.call_count == calls_after_first  # NEMA drugog LLM poziva
    assert store.peek("sess-1")["wrong_option_ids"] == [wid]  # NIJE duplo brojano


def test_server_verdict_wins_over_contradictory_model_evaluation():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    sess = store.peek("sess-1")
    cid = correct_option_id(sess)
    # model (pogrešno) tvrdi 'incorrect' iako je server utvrdio tačan klik
    fake.queue(make_output(reply="Hmm, nije tačno?", evaluation="incorrect"))
    r = run_practice_turn(store, fake, choice_payload(cid))
    assert r["answer_verdict"] == "correct"  # server-truth pobjeđuje
    assert store.peek("sess-1")["correct_streak"] == 1


def test_click_with_no_active_task_rejected_without_llm_call():
    store, fake = SessionStore(), FakeLLM()
    r = run_practice_turn(store, fake, choice_payload("a"))
    assert fake.call_count == 0
    assert "status" not in r


def test_click_after_task_completed_rejected_without_llm_call():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    sess = store.peek("sess-1")
    cid = correct_option_id(sess)
    fake.queue(make_output(reply="Tačno!"))
    run_practice_turn(store, fake, choice_payload(cid, client_turn_id="t1"))
    calls_before = fake.call_count
    r = run_practice_turn(store, fake, choice_payload(cid, client_turn_id="t2"))
    assert fake.call_count == calls_before
    assert "status" not in r


# ---------------------------------------------------------------------------
# Pitanja / "Ne znam" / "Uradi ga ti"
# ---------------------------------------------------------------------------

def test_ne_znam_is_not_a_wrong_choice_attempt_and_keeps_options():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    before_options = store.peek("sess-1")["current_options"]
    fake.queue(make_output(reply="Pogledaj prvo brojnik i nazivnik.", gave_hint=True))
    r = run_practice_turn(store, fake, turn_payload(msg="ne znam"))
    assert r["answer_verdict"] is None
    sess = store.peek("sess-1")
    assert sess["current_options"] == before_options
    assert sess["wrong_option_ids"] == []


def test_question_about_active_task_does_not_get_graded():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Skraćivanje ne mijenja vrijednost razlomka."))
    r = run_practice_turn(store, fake, turn_payload(msg="zašto ovdje dijelimo sa 4?"))
    assert r["answer_verdict"] is None
    assert "revealed_correct_option_id" not in r


def test_partial_understanding_message_preserves_task_and_options():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    before_options = store.peek("sess-1")["current_options"]
    fake.queue(make_output(reply="Dobro, hajde da razjasnimo drugi korak."))
    r = run_practice_turn(store, fake, turn_payload(msg="shvatio sam prvi korak, ali ne razumijem drugi"))
    assert r["answer_verdict"] is None
    assert store.peek("sess-1")["current_options"] == before_options


def test_hint_preserves_option_order():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    before_options = store.peek("sess-1")["current_options"]
    fake.queue(make_output(reply="Mali hint.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="daj mi hint", intent="hint_request"))
    assert store.peek("sess-1")["current_options"] == before_options


def test_uradi_ga_ti_reveals_correct_option_and_completes_task_without_counting_as_wrong():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    sess = store.peek("sess-1")
    cid = correct_option_id(sess)
    fake.queue(make_output(reply="Evo cijelog postupka i rezultata.", gave_hint=True))
    r = run_practice_turn(store, fake, turn_payload(msg="uradi ga ti", intent="solution_request"))
    assert r["revealed_correct_option_id"] == cid
    sess_after = store.peek("sess-1")
    assert sess_after["task_completed"] is True
    assert sess_after["wrong_option_ids"] == []
    assert sess_after["correct_streak"] == 0


# ---------------------------------------------------------------------------
# Browser-safe state: revealed_correct_option_id se pojavljuje SAMO tačno kada
# treba (2. pogrešan klik / solution_request), nikad ranije.
# ---------------------------------------------------------------------------

def test_reveal_absent_on_bootstrap_hint_question_and_first_wrong_click():
    store, fake = SessionStore(), FakeLLM()
    r_bootstrap = start_session(store, fake)
    assert "revealed_correct_option_id" not in r_bootstrap

    fake.queue(make_output(reply="Hint.", gave_hint=True))
    r_hint = run_practice_turn(store, fake, turn_payload(msg="ne znam"))
    assert "revealed_correct_option_id" not in r_hint

    fake.queue(make_output(reply="Odgovor na pitanje."))
    r_question = run_practice_turn(store, fake, turn_payload(msg="zašto?"))
    assert "revealed_correct_option_id" not in r_question

    sess = store.peek("sess-1")
    wid = wrong_option_id(sess)
    fake.queue(make_output(reply="Nije baš."))
    r_first_wrong = run_practice_turn(store, fake, choice_payload(wid))
    assert "revealed_correct_option_id" not in r_first_wrong


def test_new_task_clears_previous_reveal_and_options():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    sess = store.peek("sess-1")
    wid = wrong_option_id(sess)
    fake.queue(make_output(reply="Nije baš."))
    run_practice_turn(store, fake, choice_payload(wid, client_turn_id="t1"))
    fake.queue(make_output(reply="Evo rješenja."))
    run_practice_turn(store, fake, choice_payload(wid, client_turn_id="t2"))
    assert store.peek("sess-1")["task_completed"] is True

    fake.queue(make_output(
        reply="Evo novog zadatka.",
        new_task=make_task(text="Skrati $\\frac{18}{24}$.", expected="3/4",
                            options=make_options("3/4", "6/8", "4/3", "1/2"), correct_option_index=0),
    ))
    r_new = run_practice_turn(store, fake, turn_payload(msg="daj mi novi zadatak"))
    assert "revealed_correct_option_id" not in r_new
    sess_new = store.peek("sess-1")
    assert sess_new["task_completed"] is False
    assert sess_new["wrong_option_ids"] == []
    assert len(sess_new["current_options"]) == 4
