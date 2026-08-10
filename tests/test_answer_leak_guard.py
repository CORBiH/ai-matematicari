"""Curenje tačnog odgovora prije izričitog zahtjeva za rješenjem.

ŽIVI NALAZ (scenario B53, dvije nezavisne kampanje, universal_two_call):

    aktivni zadatak : „Riješi jednačinu: $(x+2)=9$.“
    označena opcija : $x=7$        expected_answer: $x=7$
    učenik          : „Mislim da je rješenje x=3.“
    tutor           : „…dobijemo $x=9-2=7$, dakle tačno rješenje je $x=7$.“

Isto se ponovilo i na drugi pogrešan pokušaj. Zadatak je ostao otvoren, opcije
na ekranu — učeniku je preostalo samo da klikne ponuđeni $x=7$.

Legacy Practice put ovo hvata (`feedback.shape_first_wrong_feedback` →
`feedback.leaks_answer`). Univerzalni put je pri pivotu izgubio taj sloj i
nema NIJEDAN deterministički anti-leak gate.

Treći korak istog scenarija („Znam da trebam osloboditi zagrade, ali dalje ne
znam.“) NIJE procurio — model ume ispravno. Zato gate mora biti uzak: čuva
korisne hintove, a blokira samo dokazano otkrivanje committed odgovora.
"""
import pytest

from matbot import feedback
from matbot.tutor import pipeline as tutor_pipeline
from tests.conftest import (make_reviewer_final, make_task_payload, make_tutor_draft,
                            queue_two_call)

LESSON = "9-04-003"
TASK = "Riješi jednačinu: $(x+2)=9$."
OPTIONS = ("$x=7$", "$x=3$", "$x=11$", "$x=5$")
ANSWER = "$x=7$"

# Doslovan tekst koji je procurio u produkciji (B53, korak 1).
LEAKING_REPLY = ("Dobro što si pokušao. Provjerimo tvoj pokušaj: $(3+2)=5\\neq9$, pa $x=3$ "
                 "nije tačno. Prvi korak je prebacivanje člana uz promjenu znaka: iz "
                 "$x+2=9$ oduzmemo $2$ na obje strane. Tako dobijemo $x=9-2=7$, dakle "
                 "tačno rješenje je $x=7$.")

# Doslovan tekst koji NIJE procurio (B53, korak 3) — mora ostati netaknut.
SAFE_REPLY = ("Dobro — usmjerimo se na prvi korak. U jednačini $x+2=9$ prebaci član $+2$ "
              "na drugu stranu tako što ćeš ga oduzeti sa obje strane. Nemoj još računati "
              "konačan broj — samo napiši koju operaciju primjenjuješ.")


def _task():
    return make_task_payload(text=TASK, options=OPTIONS, correct_option_index=0,
                             expected=ANSWER)


def _turn(message, intent="", session_id="leak-1", client_turn_id="leak-t1",
          interaction_type="student_question", option_id=""):
    return {
        "session_id": session_id, "grade": 9, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": intent,
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": interaction_type, "selected_option_id": option_id,
        "client_turn_id": client_turn_id,
    }


@pytest.fixture(autouse=True)
def _levels_on(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _seed_task(store, fake_llm, session_id="leak-1"):
    """Objavi stvaran zadatak kroz pravi dvopozivni put."""
    queue_two_call(fake_llm, draft=make_tutor_draft(intent="generate_task", new_task=_task()))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn("Daj mi zadatak.",
                                                             session_id=session_id))
    assert response.get("status") == "ready", response
    return response


def _text_turn(store, fake_llm, message, reply, intent="answer_attempt", session_id="leak-1",
               **draft_kwargs):
    draft = make_tutor_draft(intent=intent, reply=reply, new_task=None, **draft_kwargs)
    queue_two_call(fake_llm, draft=draft)
    return tutor_pipeline.run_turn(store, fake_llm, _turn(message, session_id=session_id,
                                                          client_turn_id="leak-t2"))


# ---------------------------------------------------------------------------
# 1. POGREŠAN SLOBODAN POKUŠAJ NE SMIJE OTKRITI ODGOVOR
# ---------------------------------------------------------------------------

def test_wrong_free_text_attempt_never_reveals_the_expected_answer(store, fake_llm):
    _seed_task(store, fake_llm)
    calls_before = fake_llm.call_count
    response = _text_turn(store, fake_llm, "Mislim da je rješenje x=3.", LEAKING_REPLY,
                          grading="incorrect")

    assert response["status"] == "ready"
    answer = response["answer"]
    assert "x=7" not in answer.replace(" ", "")
    assert ANSWER not in answer
    assert not feedback.leaks_answer(answer, OPTIONS[0], ANSWER)
    # bez dodatnog poziva modela
    assert fake_llm.call_count - calls_before == 1


def test_second_consecutive_wrong_attempt_also_stays_closed(store, fake_llm):
    _seed_task(store, fake_llm)
    _text_turn(store, fake_llm, "Mislim da je rješenje x=3.", LEAKING_REPLY,
               grading="incorrect")
    second = ("Provjerimo: za $x=100$ imamo $(100+2)=102\\neq9$. Iz $x+2=9$ slijedi "
              "$x=9-2=7$, pa je $x=7$ tačno.")
    draft = make_tutor_draft(intent="answer_attempt", reply=second, new_task=None,
                             grading="incorrect")
    queue_two_call(fake_llm, draft=draft)
    response = tutor_pipeline.run_turn(
        store, fake_llm, _turn("Onda je vjerovatno x=100.", client_turn_id="leak-t3"))

    assert "x=7" not in response["answer"].replace(" ", "")
    assert not feedback.leaks_answer(response["answer"], OPTIONS[0], ANSWER)


def test_reply_never_carries_the_marked_option_text(store, fake_llm):
    _seed_task(store, fake_llm)
    response = _text_turn(store, fake_llm, "Je li x=3?",
                          "Tačna opcija je $x=7$.", grading="incorrect")
    assert OPTIONS[0] not in response["answer"]


def test_task_is_not_closed_by_a_wrong_free_text_attempt(store, fake_llm):
    _seed_task(store, fake_llm)
    _text_turn(store, fake_llm, "Mislim da je rješenje x=3.", LEAKING_REPLY,
               grading="incorrect")
    session = store.peek("leak-1")
    assert session["task_completed"] is False
    assert session["correct_option_id"]                     # tačna opcija netaknuta
    assert session["expected_answer_summary"] == ANSWER


# ---------------------------------------------------------------------------
# 2. ŠTA GATE NE SMIJE POKVARITI
# ---------------------------------------------------------------------------

def test_a_useful_next_step_hint_passes_through_unchanged(store, fake_llm):
    """Korak 3 iz B53 — model je bio ispravan i tekst mora ostati doslovan."""
    _seed_task(store, fake_llm)
    response = _text_turn(store, fake_llm, "Ne znam dalje.", SAFE_REPLY,
                          intent="hint_request", hint=SAFE_REPLY)
    assert SAFE_REPLY in response["answer"]


def test_explicit_full_solution_request_may_reveal_the_answer(store, fake_llm):
    _seed_task(store, fake_llm)
    worked = "Iz $x+2=9$ oduzmemo $2$: $x=9-2=7$. Dakle rješenje je $x=7$."
    response = _text_turn(store, fake_llm, "Uradi ga ti.", worked,
                          intent="full_solution_request", worked_solution=worked)
    assert "x=7" in response["answer"].replace(" ", "")
    assert response.get("revealed_correct_option_id")


def test_third_hint_shows_the_result_from_the_verified_artifact(store, fake_llm):
    """Nivo 3 DAJE rezultat — ali od Faze 2 ga sastavlja SERVER, ne model.

    ZATEČENO: vrh ljestvice je bio svježa proza modela i prolazio je
    nepromijenjen (anti-leak gate ga izričito izuzima). Baš tako je FW-X03
    nagovještaj 3 objavio tačan zaključak preko NETAČNE međutvrdnje.

    OD FAZE 2: tekst dolazi iz recenzentom odobrenog `solution` polja
    objavljenog paketa (`hint_policy.compose_top_hint`). Rezultat je i dalje tu
    — model kao autor izvoda nije."""
    from matbot import hint_policy

    _seed_task(store, fake_llm)
    for index in range(2):
        _text_turn(store, fake_llm, "Ne znam.", SAFE_REPLY, intent="hint_request",
                   hint=SAFE_REPLY)
    assert store.peek("leak-1")["hint_level"] == 2

    fresh_proof = "Cijeli postupak: iz $x+2=9$ slijedi $x=9-2=7$, dakle $x=7$."
    draft = make_tutor_draft(intent="hint_request", reply=fresh_proof, new_task=None,
                             hint=fresh_proof)
    queue_two_call(fake_llm, draft=draft)
    response = tutor_pipeline.run_turn(store, fake_llm,
                                       _turn("Ne znam.", client_turn_id="leak-t9"))

    answer = response["answer"]
    assert answer.startswith(hint_policy.TOP_HINT_INTRO)
    assert fresh_proof not in answer, "svježa proza modela se NE objavljuje"
    assert "x=7" in answer.replace(" ", "")             # rezultat je i dalje tu
    assert answer != tutor_pipeline.LEAK_BLOCKED_REPLY
    assert store.peek("leak-1")["hint_level"] == 3


def test_value_already_visible_in_the_task_is_not_treated_as_a_leak(store, fake_llm):
    """Ponavljanje broja iz samog zadatka nije curenje nego prepričavanje."""
    task = make_task_payload(text="Koji je veći: $\\frac{3}{5}$ ili $\\frac{2}{7}$?",
                             options=("$\\frac{3}{5}$", "$\\frac{2}{7}$", "$\\frac{1}{2}$",
                                      "$\\frac{4}{9}$"),
                             correct_option_index=0, expected="$\\frac{3}{5}$")
    queue_two_call(fake_llm, draft=make_tutor_draft(intent="generate_task", new_task=task))
    tutor_pipeline.run_turn(store, fake_llm, _turn("Daj mi zadatak.", session_id="leak-2"))

    reply = "Uporedi $\\frac{3}{5}$ i $\\frac{2}{7}$ svođenjem na zajednički nazivnik."
    draft = make_tutor_draft(intent="hint_request", reply=reply, new_task=None, hint=reply)
    queue_two_call(fake_llm, draft=draft)
    response = tutor_pipeline.run_turn(store, fake_llm,
                                       _turn("Ne znam.", session_id="leak-2",
                                             client_turn_id="leak-t4"))
    assert reply in response["answer"]


def test_correct_free_text_attempt_may_be_confirmed(store, fake_llm):
    _seed_task(store, fake_llm)
    reply = "Tačno! $x=7$ zadovoljava jednačinu."
    response = _text_turn(store, fake_llm, "Mislim da je x=7.", reply, grading="correct")
    assert "x=7" in response["answer"].replace(" ", "")


# ---------------------------------------------------------------------------
# 3. KLIK NA POGREŠNU OPCIJU — isti gate, isti razlog
# ---------------------------------------------------------------------------

def test_first_wrong_click_feedback_never_reveals_the_answer(store, fake_llm):
    _seed_task(store, fake_llm)
    session = store.peek("leak-1")
    wrong_id = next(option["id"] for option in session["current_options"]
                    if option["id"] != session["correct_option_id"])
    draft = make_tutor_draft(intent="clarification", reply=LEAKING_REPLY, new_task=None)
    queue_two_call(fake_llm, draft=draft)
    response = tutor_pipeline.run_turn(
        store, fake_llm,
        _turn("Izabrana opcija.", interaction_type="choice_answer", option_id=wrong_id,
              client_turn_id="leak-c1"))

    assert response["answer_verdict"] == "incorrect"          # serverski verdict očuvan
    assert "revealed_correct_option_id" not in response       # bez preranog otkrivanja
    assert not feedback.leaks_answer(response["answer"], OPTIONS[0], ANSWER)
    assert store.peek("leak-1")["task_completed"] is False


def test_second_wrong_click_still_reveals_by_design(store, fake_llm):
    _seed_task(store, fake_llm)
    session = store.peek("leak-1")
    wrong = [option["id"] for option in session["current_options"]
             if option["id"] != session["correct_option_id"]]
    for index, option_id in enumerate(wrong[:2]):
        queue_two_call(fake_llm, draft=make_tutor_draft(intent="clarification",
                                                        reply="Nije tačno, probaj ponovo.",
                                                        new_task=None))
        response = tutor_pipeline.run_turn(
            store, fake_llm,
            _turn("Izabrana opcija.", interaction_type="choice_answer", option_id=option_id,
                  client_turn_id=f"leak-c{index + 2}"))
    assert response["revealed_correct_option_id"] == session["correct_option_id"]


# ---------------------------------------------------------------------------
# Kapacitetna ekspanzija: ovi testovi ispituju MODEL-strategiju (Tutor +
# Recenzent) i na lekcijama koje produkcija sada rutira deterministički
# (blocking ugovor + potpun generator). Izričito isključenje je ISTI mehanizam
# koji služi i kao produkcijski rollback (MATBOT_DETERMINISTIC_PRACTICE=
# disabled) — model-put time ostaje trajno testiran, bajt za bajt kakav je bio.
# ---------------------------------------------------------------------------
import pytest as _pytest_capex


@_pytest_capex.fixture(autouse=True)
def _model_route_only_capex(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
