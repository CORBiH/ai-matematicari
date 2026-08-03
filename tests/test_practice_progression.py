"""Integracijski testovi rotacije porodica zadataka kroz run_practice_turn
(fake LLM, bez mreže).

Ovi testovi dokazuju ponašanje koje jedinični testovi task_families.py ne mogu:
da server STVARNO bira porodicu prije jedinog AI poziva, da je šalje u prompt i
da napredovanje mutira samo na dozvoljenim mjestima.
"""
import re

from tests.conftest import FakeLLM, make_options, make_output, make_task, make_task_for_family
from matbot import task_families as tf
from matbot.llm import LLMTimeout
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.topics import lesson_info

FRACTION_TOPIC = "6-04-007"   # Proširivanje razlomaka
OTHER_TOPIC = "6-04-008"      # Sabiranje/oduzimanje s jednakim imeniocima

_FAMILY_RE = re.compile(r"PORODICA ZADATKA \(obavezna za novi zadatak, ne mijenjaj je\): ([a-z_]+)")


def turn_payload(msg="Daj mi jedan zadatak za vježbu.", **kw):
    base = {
        "session_id": "sess-prog",
        "grade": 6,
        "selected_topic": FRACTION_TOPIC,
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


def choice_payload(option_id, client_turn_id, **kw):
    return turn_payload(msg="[klik]", interaction_type="choice_answer",
                        selected_option_id=option_id, client_turn_id=client_turn_id, **kw)


def family_sent_on_call(fake, index):
    """Porodica koju je server poslao modelu u datom pozivu."""
    _, input_text = fake.practice_calls[index]
    match = _FAMILY_RE.search(input_text)
    return match.group(1) if match else ""


def predicted_family(store, session_id, grade, topic_id):
    """Porodicu koju će server dodijeliti u SLJEDEĆEM turnu — ista determinis-
    tička logika kao practice._handle_text_turn. Test je koristi da unaprijed
    pripremi zadatak koji ZADOVOLJAVA ugovor te porodice (model koji poštuje
    dodjelu)."""
    info = lesson_info(grade, topic_id)
    oblast = info["oblast"] if info else ""
    title = info["title"] if info else ""
    sess = store.peek(session_id)
    # Promjena lekcije resetuje sesiju na serveru (context_key sadrži lesson_id),
    # pa i predviđanje mora krenuti od nule — inače bi test predvidio porodicu
    # na osnovu napretka koji je server upravo odbacio.
    if sess is not None and sess.get("lesson_id") != topic_id:
        sess = None
    applicable = tf.applicable_families(grade, oblast, title, lesson_id=topic_id)
    return tf.select_family(
        applicable,
        recently_used=sess["recently_used_families"] if sess else [],
        completed_families=sess["correctly_completed_families"] if sess else [],
        retry_required=sess["retry_required"] if sess else False,
        current_family=sess["current_family"] if sess else "",
    )


def give_task(store, fake, suffix="", session_id="sess-prog", grade=6,
              topic_id=FRACTION_TOPIC, **payload_kw):
    """Simuliraj model koji POŠTUJE dodijeljenu porodicu: pripremi zadatak koji
    zadovoljava ugovor porodice koju će server upravo dodijeliti.

    `suffix` čini tekst jedinstvenim kad se ista porodica ponovi (retry), da
    ne padne na doslovnu duplicate-signature zaštitu."""
    topic = payload_kw.get("selected_topic", topic_id)
    family = predicted_family(store, session_id, grade, topic)
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task_for_family(family, suffix=suffix),
    ))
    return run_practice_turn(store, fake, turn_payload(**payload_kw))


def answer(store, fake, session_id, correct, turn_id, **payload_kw):
    sess = store.peek(session_id)
    correct_id = sess["correct_option_id"]
    option_id = correct_id if correct else next(
        o["id"] for o in sess["current_options"] if o["id"] != correct_id
    )
    fake.queue(make_output(reply="Komentar.", hint="Provjeri prvi korak."))
    return run_practice_turn(store, fake, choice_payload(option_id, turn_id, **payload_kw))


# ---------------------------------------------------------------------------
# Osnovni ugovor: server bira porodicu i šalje je modelu
# ---------------------------------------------------------------------------

def test_server_sends_a_task_family_to_the_model():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    assert family_sent_on_call(fake, 0), "Server nije poslao porodicu u prompt"


def test_generated_task_records_its_family_in_session():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    sess = store.peek("sess-prog")
    assert sess["current_family"] == family_sent_on_call(fake, 0)
    assert sess["recently_used_families"] == [sess["current_family"]]


def test_one_llm_call_per_turn_including_progression_logic():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    assert fake.practice_call_count == 1
    answer(store, fake, "sess-prog", correct=True, turn_id="t1")
    assert fake.practice_call_count == 2
    give_task(store, fake, " (B)")
    assert fake.practice_call_count == 3


# ---------------------------------------------------------------------------
# Tačan odgovor → druga porodica
# ---------------------------------------------------------------------------

def test_correct_answer_marks_family_completed():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    first_family = store.peek("sess-prog")["current_family"]
    answer(store, fake, "sess-prog", correct=True, turn_id="t1")
    sess = store.peek("sess-prog")
    assert sess["correctly_completed_families"] == [first_family]
    assert sess["last_result"] == "correct"
    assert sess["retry_required"] is False


def test_next_task_after_correct_answer_uses_a_different_family():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    first_family = family_sent_on_call(fake, 0)
    answer(store, fake, "sess-prog", correct=True, turn_id="t1")
    give_task(store, fake, " (B)")
    assert family_sent_on_call(fake, 2) != first_family


# ---------------------------------------------------------------------------
# Netačan odgovor → ista porodica, ista težina
# ---------------------------------------------------------------------------

def test_incorrect_answer_sets_retry_and_does_not_complete_family():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    answer(store, fake, "sess-prog", correct=False, turn_id="t1")
    sess = store.peek("sess-prog")
    assert sess["retry_required"] is True
    assert sess["last_result"] == "incorrect"
    assert sess["correctly_completed_families"] == []


def test_retry_task_keeps_the_same_family():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    first_family = family_sent_on_call(fake, 0)
    answer(store, fake, "sess-prog", correct=False, turn_id="t1")
    give_task(store, fake, " (A2)")
    assert family_sent_on_call(fake, 2) == first_family


def test_retry_prompt_forbids_raising_difficulty():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    answer(store, fake, "sess-prog", correct=False, turn_id="t1")
    give_task(store, fake, " (A2)")
    _, input_text = fake.practice_calls[2]
    assert "PONOVNI POKUŠAJ" in input_text
    assert "NE povećavaj je" in input_text


def test_retry_with_identical_text_is_rejected_without_second_llm_call():
    """Provjera iste vještine mora imati DRUGE vrijednosti — doslovno isti
    tekst zadatka se odbija, bez popravnog AI poziva i bez mutacije stanja."""
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake)
    before = store.peek("sess-prog")

    # Model vraća DOSLOVNO isti zadatak kao prethodni (ista porodica, isti tekst).
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task_for_family(before["current_family"]),
    ))
    r = run_practice_turn(store, fake, turn_payload(msg="Daj još jedan."))

    assert fake.practice_call_count == 2, "Odbijanje ne smije izazvati drugi AI poziv"
    assert "status" not in r  # sigurni fallback
    after = store.peek("sess-prog")
    assert after["current_task"] == before["current_task"]
    assert after["recently_used_families"] == before["recently_used_families"]


def test_correct_retry_clears_flag_and_moves_to_a_different_family():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    first_family = family_sent_on_call(fake, 0)
    answer(store, fake, "sess-prog", correct=False, turn_id="t1")
    give_task(store, fake, " (A2)")
    answer(store, fake, "sess-prog", correct=True, turn_id="t2")

    sess = store.peek("sess-prog")
    assert sess["retry_required"] is False
    assert first_family in sess["correctly_completed_families"]

    give_task(store, fake, " (B)")
    assert family_sent_on_call(fake, 4) != first_family


# ---------------------------------------------------------------------------
# Izolacija po temi
# ---------------------------------------------------------------------------

def test_topic_change_isolates_progression():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    answer(store, fake, "sess-prog", correct=True, turn_id="t1")
    assert store.peek("sess-prog")["correctly_completed_families"]

    # Ista sesija, DRUGA lekcija → svježe napredovanje.
    give_task(store, fake, " (druga lekcija)", topic_id=OTHER_TOPIC, selected_topic=OTHER_TOPIC)
    sess = store.peek("sess-prog")
    assert sess["correctly_completed_families"] == []
    assert sess["lesson_id"] == OTHER_TOPIC


# ---------------------------------------------------------------------------
# Šta NE smije mijenjati napredovanje
# ---------------------------------------------------------------------------

def test_hint_request_does_not_complete_a_family():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    fake.queue(make_output(reply="Evo hinta.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="Daj mi hint.", intent="hint_request"))
    sess = store.peek("sess-prog")
    assert sess["correctly_completed_families"] == []
    assert sess["last_result"] == ""


def test_student_question_does_not_complete_a_family():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    fake.queue(make_output(reply="Brojnik je gornji broj."))
    run_practice_turn(store, fake, turn_payload(msg="Šta znači brojnik?"))
    assert store.peek("sess-prog")["correctly_completed_families"] == []


def test_ne_znam_does_not_complete_a_family():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    fake.queue(make_output(reply="U redu, idemo korak po korak.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="Ne znam."))
    assert store.peek("sess-prog")["correctly_completed_families"] == []


def test_solution_request_does_not_complete_a_family():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    fake.queue(make_output(reply="Evo cijelog postupka.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="Uradi ga ti.", intent="solution_request"))
    assert store.peek("sess-prog")["correctly_completed_families"] == []


def test_invalid_model_output_does_not_mutate_family_state():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    before = store.peek("sess-prog")

    # Nebezbjedan matematički zapis u tekstu zadatka → InvalidOutputError.
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Izracunaj \\frac{3 i nastavi", expected="x",
                            options=make_options("1", "2", "3", "4")),
    ))
    run_practice_turn(store, fake, turn_payload(msg="Daj novi zadatak."))

    after = store.peek("sess-prog")
    assert after["current_family"] == before["current_family"]
    assert after["recently_used_families"] == before["recently_used_families"]
    assert after["recent_task_signatures"] == before["recent_task_signatures"]


def test_llm_error_does_not_mutate_family_state():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    before = store.peek("sess-prog")
    fake.queue(LLMTimeout("timeout"))
    run_practice_turn(store, fake, turn_payload(msg="Daj novi zadatak."))
    after = store.peek("sess-prog")
    assert after["recently_used_families"] == before["recently_used_families"]
    assert after["current_family"] == before["current_family"]


# ---------------------------------------------------------------------------
# Drugi ciklus kad su sve porodice savladane
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# B.1/B.2: "Novi zadatak" traženo prije odgovora, i više puta zaredom
# ---------------------------------------------------------------------------

def test_new_task_request_before_answering_uses_a_different_family():
    store, fake = SessionStore(), FakeLLM()
    give_task(store, fake, " (A)")
    first_family = family_sent_on_call(fake, 0)

    # Učenik NIJE odgovorio — samo traži novi zadatak.
    give_task(store, fake, " (B)", msg="Daj mi drugi zadatak.")
    second_family = family_sent_on_call(fake, 1)

    assert second_family != first_family
    sess = store.peek("sess-prog")
    assert sess["correctly_completed_families"] == []  # ništa nije "savladano"


def test_several_new_task_requests_without_answering_never_repeat_immediately():
    store, fake = SessionStore(), FakeLLM()
    seen = []
    for i in range(5):
        give_task(store, fake, f" (broj {i})", msg="Daj mi novi zadatak.")
        family = family_sent_on_call(fake, len(fake.practice_calls) - 1)
        if seen:
            assert family != seen[-1], f"Ponovljena porodica na krugu {i}"
        seen.append(family)
    sess = store.peek("sess-prog")
    assert sess["correctly_completed_families"] == []


def test_second_cycle_uses_least_recently_used_family():
    """Nakon što su SVE primjenjive porodice savladane, sljedeći izbor je
    najdulje neupotrijebljena — ne odmah ponovo prethodna."""
    store, fake = SessionStore(), FakeLLM()
    from matbot import task_families as tf

    applicable = tf.applicable_families(
        6, "Razlomci", "Proširivanje razlomaka", lesson_id=FRACTION_TOPIC
    )
    used = []
    for i, _ in enumerate(applicable):
        give_task(store, fake, f" (broj {i})")
        used.append(family_sent_on_call(fake, len(fake.practice_calls) - 1))
        answer(store, fake, "sess-prog", correct=True, turn_id=f"t{i}")

    sess = store.peek("sess-prog")
    assert set(sess["correctly_completed_families"]) == set(applicable)

    give_task(store, fake, " (novi ciklus)")
    next_family = family_sent_on_call(fake, len(fake.practice_calls) - 1)
    assert next_family != used[-1], "Drugi ciklus ne smije ponoviti zadnju porodicu"
    assert next_family in applicable
