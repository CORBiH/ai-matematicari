"""Bug 2 fix: reply može biti prazan na PRVI pogrešan klik kad je 'hint'
prisutan (matbot/schema.py:validate_output + matbot/practice.py).

Živi nalaz: model je vratio reply="" i valjan hint na prvi pogrešan klik —
cio odgovor je bio odbačen jer je schema.validate_output tražio neprazan reply
bezuslovno, iako server sam sastavlja vidljiv tekst iz 'hint' za baš ovaj slučaj.
"""
from matbot import config
from matbot.schema import InvalidOutputError, PracticeTurnOutput, validate_output
from tests.conftest import FakeLLM, make_options, make_output, make_task_for_family
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore

FRACTION_TOPIC = "6-04-007"

LIVE_HINT = "Prvo nađi kojim brojem treba pomnožiti nazivnik $6$ da postane $18$."


def _payload(msg="Daj mi zadatak.", **kw):
    base = {
        "session_id": "sess-empty-reply", "grade": 6, "selected_topic": FRACTION_TOPIC,
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "", "selected_option_id": "", "client_turn_id": "",
    }
    base.update(kw)
    return base


def _start(store, fake):
    fake.queue(make_output(reply="Evo zadatka.",
                            new_task=make_task_for_family("expand_to_given_denominator")))
    run_practice_turn(store, fake, _payload())
    return store.peek("sess-empty-reply")


def _wrong_id(sess):
    return next(o["id"] for o in sess["current_options"] if o["id"] != sess["correct_option_id"])


def _click(store, fake, option_id, turn_id="t1"):
    return run_practice_turn(store, fake, _payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=option_id, client_turn_id=turn_id))


# ---------------------------------------------------------------------------
# Jedinični testovi schema.validate_output
# ---------------------------------------------------------------------------

def _output(reply="", hint=None):
    return PracticeTurnOutput(reply=reply, evaluation=None, gave_hint=False, new_task=None, hint=hint)


def test_empty_reply_with_valid_hint_passes_when_reply_not_required():
    validate_output(_output(reply="", hint=LIVE_HINT), require_reply=False)  # ne smije baciti


def test_empty_reply_and_empty_hint_still_fails_when_reply_not_required():
    try:
        validate_output(_output(reply="", hint=""), require_reply=False)
        assert False, "Očekivana greška: oba polja prazna"
    except InvalidOutputError as e:
        assert "prazan" in str(e)


def test_empty_reply_and_missing_hint_still_fails_when_reply_not_required():
    try:
        validate_output(_output(reply="", hint=None), require_reply=False)
        assert False, "Očekivana greška: nema ni reply ni hint"
    except InvalidOutputError:
        pass


def test_empty_reply_still_fails_by_default_require_reply_true():
    """Podrazumijevano ponašanje (require_reply=True) ostaje NEPROMIJENJENO —
    novi zadatak, tačan odgovor, drugi pogrešan/reveal i dalje traže reply."""
    try:
        validate_output(_output(reply="", hint=LIVE_HINT))  # default require_reply=True
        assert False, "Očekivana greška: reply je i dalje obavezan po defaultu"
    except InvalidOutputError as e:
        assert "prazan reply" == str(e)


def test_valid_reply_with_empty_hint_still_passes_regardless_of_require_reply():
    validate_output(_output(reply="Postojeći fallback tekst.", hint=""), require_reply=False)
    validate_output(_output(reply="Postojeći fallback tekst.", hint=""), require_reply=True)


def test_overlong_hint_is_still_rejected_by_length_check():
    huge_hint = "x" * (config.MAX_REPLY_CHARS + 1)
    try:
        validate_output(_output(reply="", hint=huge_hint), require_reply=False)
        assert False
    except InvalidOutputError as e:
        assert "predug hint" in str(e)


# ---------------------------------------------------------------------------
# 1. Prvi pogrešan klik: prazan reply, valjan hint → prihvaćen
# ---------------------------------------------------------------------------

def test_first_wrong_empty_reply_valid_hint_is_accepted():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="", hint="Podijeli brojnik i nazivnik istim brojem."))
    r = _click(store, fake, _wrong_id(sess))
    assert "status" in r
    assert r["answer_verdict"] == "incorrect"


def test_first_wrong_empty_reply_response_starts_with_netacno():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="", hint="Podijeli brojnik i nazivnik istim brojem."))
    r = _click(store, fake, _wrong_id(sess))
    assert r["answer"].startswith("Netačno.")


def test_first_wrong_empty_reply_includes_the_hint():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="", hint="Podijeli brojnik i nazivnik istim brojem."))
    r = _click(store, fake, _wrong_id(sess))
    assert "Podijeli brojnik i nazivnik istim brojem." in r["answer"]


def test_first_wrong_empty_reply_sets_retry_required():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="", hint="Podijeli brojnik i nazivnik istim brojem."))
    _click(store, fake, _wrong_id(sess))
    assert store.peek("sess-empty-reply")["retry_required"] is True


# ---------------------------------------------------------------------------
# 2. Prvi pogrešan klik: valjan reply, prazan hint → postojeći fallback
# ---------------------------------------------------------------------------

def test_first_wrong_valid_reply_empty_hint_falls_back_to_reply():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="Provjeri koji je zajednički nazivnik.", hint=None))
    r = _click(store, fake, _wrong_id(sess))
    assert "status" in r
    assert "Provjeri koji je zajednički nazivnik." in r["answer"]
    assert r["answer"].startswith("Netačno.")


# ---------------------------------------------------------------------------
# 3. Prvi pogrešan klik: oba prazna → odbijeno, bez mutacije, jedan poziv
# ---------------------------------------------------------------------------

def test_first_wrong_both_empty_is_rejected():
    store, fake = SessionStore(), FakeLLM()
    _start(store, fake)
    sess = store.peek("sess-empty-reply")
    before = dict(sess)
    fake.queue(make_output(reply="", hint=None))
    r = _click(store, fake, _wrong_id(sess))
    assert "status" not in r  # sigurni fallback


def test_first_wrong_both_empty_does_not_mutate_state():
    store, fake = SessionStore(), FakeLLM()
    _start(store, fake)
    before = store.peek("sess-empty-reply")
    fake.queue(make_output(reply="", hint=""))
    _click(store, fake, _wrong_id(before))
    after = store.peek("sess-empty-reply")
    assert after["retry_required"] == before["retry_required"]
    assert after["wrong_option_ids"] == before["wrong_option_ids"]
    assert after["last_result"] == before["last_result"]
    assert after["task_completed"] == before["task_completed"]


def test_first_wrong_both_empty_makes_exactly_one_llm_call():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="", hint=""))
    _click(store, fake, _wrong_id(sess))
    assert fake.call_count == 2  # bootstrap + ovaj klik


# ---------------------------------------------------------------------------
# 4. Prazan reply s hintom koji otkriva odgovor → generički hint, bez 2. poziva
# ---------------------------------------------------------------------------

def test_empty_reply_with_leaking_hint_falls_back_to_generic():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    correct_text = next(o["text"] for o in sess["current_options"]
                        if o["id"] == sess["correct_option_id"])
    fake.queue(make_output(reply="", hint=f"Odgovor je {correct_text}."))
    r = _click(store, fake, _wrong_id(sess))
    assert correct_text.strip("$") not in r["answer"]
    from matbot import feedback
    assert feedback.GENERIC_HINT in r["answer"]
    assert fake.call_count == 2


# ---------------------------------------------------------------------------
# 5. Prazan reply s predugim hintom → sigurno skraćen/zamijenjen, unutar granice
# ---------------------------------------------------------------------------

def test_empty_reply_with_overlong_hint_stays_within_hard_limit():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    long_hint = "Provjeri prvi korak pažljivo. " * 40
    fake.queue(make_output(reply="", hint=long_hint))
    r = _click(store, fake, _wrong_id(sess))
    assert len(r["answer"]) <= config.MAX_FIRST_WRONG_FEEDBACK_CHARS
    assert r["answer"].startswith("Netačno.")


# ---------------------------------------------------------------------------
# 6. Drugi pogrešan/reveal: prazan reply i dalje NIJE dozvoljen
# ---------------------------------------------------------------------------

def test_second_wrong_with_empty_reply_is_still_rejected():
    """Reveal mora imati stvaran sadržaj rješenja — prazan reply se ovdje NE
    smije tiho prihvatiti samo zato što postoji hint."""
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    wrong = _wrong_id(sess)

    fake.queue(make_output(reply="Prvi hint.", hint="Prvi hint."))
    _click(store, fake, wrong, turn_id="t1")

    sess2 = store.peek("sess-empty-reply")
    second_wrong = next(o["id"] for o in sess2["current_options"]
                        if o["id"] not in (sess2["correct_option_id"], wrong))
    fake.queue(make_output(reply="", hint="Ovo se ignoriše za reveal."))
    r = _click(store, fake, second_wrong, turn_id="t2")
    assert "status" not in r  # odbijeno kao prije — reply ostaje obavezan


def test_second_wrong_with_valid_reply_still_reveals_correctly():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    wrong = _wrong_id(sess)

    fake.queue(make_output(reply="Prvi hint.", hint="Prvi hint."))
    _click(store, fake, wrong, turn_id="t1")

    sess2 = store.peek("sess-empty-reply")
    second_wrong = next(o["id"] for o in sess2["current_options"]
                        if o["id"] not in (sess2["correct_option_id"], wrong))
    fake.queue(make_output(reply="Postupak: pomnoži brojnik i nazivnik istim brojem."))
    r = _click(store, fake, second_wrong, turn_id="t2")
    assert r["answer"].startswith("Netačno.")
    assert r["revealed_correct_option_id"] == sess2["correct_option_id"]
    assert "Postupak: pomnoži brojnik i nazivnik istim brojem." in r["answer"]


# ---------------------------------------------------------------------------
# 7. Tačan odgovor i normalni tekstualni odgovori: ugovor nepromijenjen
# ---------------------------------------------------------------------------

def test_correct_answer_with_empty_reply_is_still_rejected():
    """Tačan klik NIJE 'prvi pogrešan' — reply ostaje obavezan (server ne
    sastavlja odgovor umjesto modela u ovom slučaju)."""
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="", evaluation="correct"))
    r = _click(store, fake, sess["correct_option_id"])
    assert "status" not in r


def test_correct_answer_with_valid_reply_still_works():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="Tačno! Odličan posao.", evaluation="correct"))
    r = _click(store, fake, sess["correct_option_id"])
    assert r["answer_verdict"] == "correct"
    assert "Tačno! Odličan posao." in r["answer"]


def test_text_question_with_empty_reply_is_still_rejected():
    store, fake = SessionStore(), FakeLLM()
    _start(store, fake)
    fake.queue(make_output(reply=""))
    r = run_practice_turn(store, fake, _payload(msg="Šta znači brojnik?"))
    assert "status" not in r


def test_new_task_generation_with_empty_reply_is_still_rejected():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="", new_task=make_task_for_family("expand_to_given_denominator")))
    r = run_practice_turn(store, fake, _payload())
    assert "status" not in r


# ---------------------------------------------------------------------------
# 8. Tačan živi hint mora biti prihvaćen i ispravno oblikovan
# ---------------------------------------------------------------------------

def test_exact_live_hint_is_accepted_and_shaped_correctly():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="", hint=LIVE_HINT))
    r = _click(store, fake, _wrong_id(sess))
    assert r["answer"] == f"Netačno.\n\nHint: {LIVE_HINT}"
    assert fake.call_count == 2
