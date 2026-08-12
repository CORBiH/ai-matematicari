"""RUTIRANJE NA MAKSIMUMU: „novi zadatak“ ostaje DETERMINISTIČKI, „teži“ ide modelu.

ODLUKA JE DONESENA NA ŽIVIM MJERENJIMA, ne na pretpostavci. Kratko je bilo
uključeno `NEW @ max → kreativna ruta`; živi retest nad 8 pokušaja pokazao je:

  • objavljeno 2/8 (25%). Tri pada su bila `substantially_different_from_recent`,
    i to STRUKTURNO: uz ČETIRI arhetipa i prozor od tri, planer na maksimumu
    skoro uvijek mora ponuditi tip koji je učenik nedavno vidio. Što učenik duže
    ostane na vrhu, to je gore — najangažovaniji učenik dobija najlošije;
  • medijan 35 s po pokušaju (23,5–48,3 s), uz jedan stvarni istek recenzenta;
  • do dva poziva po pokušaju, dakle ~8 poziva po jednom objavljenom zadatku.

U međuvremenu je deterministički nivo 3 dobio ČETIRI arhetipa i 10 rečeničnih
kostura (mjereno: 3838/4000 različitih tekstova), pa je razlog zbog kojeg je
pilot i nastao („samo dva arhetipa, ista na sva tri nivoa“) uglavnom otpao.

UGOVOR KOJI OVAJ FAJL ZAKLJUČAVA:
  podrazumijevana radnja (`novi zadatak`) je jeftina, trenutna i UVIJEK
  dostupna na svakom nivou; model se troši SAMO kad učenik izričito zatraži
  teže na maksimumu ili drugi tip zadatka.

Sigurnosne invarijante kreativne rute se ovom promjenom NE diraju — zato su
testovi H–K i dalje ovdje, samo na `teži` putu na kojem eskalacija sada živi.
"""
import json

import pytest

from matbot.difficulty_level import MAX_LEVEL
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import creative_escalation as esc
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor.schema import DifficultyEvidence, SignatureParameter
from tests.conftest import (FakeLLM, make_difficulty_diagnostics,
                            make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

PILOT_LESSON, GRADE = "6-04-015", 6
TITLE = "Tekstualni zadaci s razlomcima"

# Ispravan `fraction_of_fraction`: 48 · 3/4 = 36, 36 · 1/2 = 18.
CREATIVE_TASK = {
    "text": ("Amar ima $48$ sličica. Od toga je $\\frac{3}{4}$ novih. Od novih "
             "je $\\frac{1}{2}$ posebno označeno. Koliko je posebno označenih?"),
    "options": ("$18$", "$36$", "$24$", "$12$"),
    "correct_index": 0,
    "expected": "$18$",
    "solution": ("$\\frac{3}{4} \\cdot 48 = 36$, pa je "
                 "$\\frac{1}{2} \\cdot 36 = 18$."),
    "facts": {"type": "fraction_of_fraction", "total": "48",
              "first_fraction": "3/4", "second_fraction": "1/2"},
}
NEW_MESSAGE = "Daj mi novi zadatak."
HARDER_MESSAGE = "Daj mi teži zadatak."


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def turn(session_id, message, lesson=PILOT_LESSON, grade=GRADE):
    return {"session_id": session_id, "grade": grade, "selected_topic": lesson,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": "", "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}


def signature(session):
    raw = (session.get("current_task_signature") or {}).get("structured_signature")
    return json.loads(raw) if raw else {}


def published_history(session, lesson=PILOT_LESSON):
    out = []
    for record in session.get("recent_task_signatures") or []:
        if record.get("lesson_id") != lesson:
            continue
        out.append(json.loads(record["structured_signature"])
                   .get("operation_or_relation"))
    return out


def attempt_history(session, lesson=PILOT_LESSON):
    return [r.get("archetype")
            for r in (session.get("recent_creative_targets") or [])
            if isinstance(r, dict) and r.get("lesson_id") == lesson]


def creative_draft(target):
    payload = make_task_payload(
        text=CREATIVE_TASK["text"], options=CREATIVE_TASK["options"],
        correct_option_index=CREATIVE_TASK["correct_index"],
        expected=CREATIVE_TASK["expected"], solution=CREATIVE_TASK["solution"],
        difficulty="hard")
    payload = payload.model_copy(update={
        "selected_lesson_id": PILOT_LESSON, "selected_lesson_title": TITLE,
        "target_difficulty_level": 3,
        "difficulty_evidence": DifficultyEvidence(
            reasoning_steps=3, condition_count=2, operation_count=3,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        "task_signature": payload.task_signature.model_copy(update={
            "operation_or_relation": target,
            "normalized_parameters": [
                SignatureParameter(name=n, value=v)
                for n, v in CREATIVE_TASK["facts"].items()]}),
    })
    return make_tutor_draft(
        intent="harder_task", reply="Evo zadatka.",
        lesson_focus="tekstualni zadaci s razlomcima",
        difficulty_diagnostics=make_difficulty_diagnostics(direction="higher"),
        new_task=payload)


def climb_to_max(store, fake, session_id):
    """Deterministički 1→2→3; nula poziva, kao u produkciji."""
    for message in ("Daj mi zadatak.", HARDER_MESSAGE, HARDER_MESSAGE):
        assert run_practice_turn(store, fake, turn(session_id, message)
                                 )["status"] == "ready"
    assert fake.call_count == 0
    assert store.peek(session_id)["difficulty_level"] == MAX_LEVEL
    return store.peek(session_id)


def force_target(store, session_id, desired):
    """Postavi historiju objava tako da planer izabere baš `desired`."""
    from matbot.semantics import contracts as contracts_module
    supported = esc._contract_archetypes(
        type("C", (), {"semantic_contract":
                       contracts_module.contract_for(PILOT_LESSON)})())
    session = store.peek(session_id)
    session["recent_task_signatures"] = [
        {"lesson_id": PILOT_LESSON,
         "structured_signature": json.dumps({"operation_or_relation": name}),
         "structured_signature_hash": f"seed-{name}"}
        for name in supported if name != desired]
    store.save(session)


def _context():
    from matbot.semantics import contracts as contracts_module
    return type("C", (), {
        "topic_id": PILOT_LESSON,
        "semantic_contract": contracts_module.contract_for(PILOT_LESSON)})()


# ---------------------------------------------------------------------------
# A/B/C — „NOVI ZADATAK“ JE DETERMINISTIČKI NA SVAKOM NIVOU
# ---------------------------------------------------------------------------

def test_a_new_at_level_1_stays_deterministic(universal):
    store, fake = SessionStore(), FakeLLM()
    assert run_practice_turn(store, fake, turn("lvl1", "Daj mi zadatak.")
                             )["status"] == "ready"
    assert run_practice_turn(store, fake, turn("lvl1", NEW_MESSAGE)
                             )["status"] == "ready"
    assert store.peek("lvl1")["difficulty_level"] == 1
    assert fake.call_count == 0


def test_b_new_at_level_2_stays_deterministic(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn("lvl2", "Daj mi zadatak."))
    run_practice_turn(store, fake, turn("lvl2", HARDER_MESSAGE))
    assert store.peek("lvl2")["difficulty_level"] == 2
    assert run_practice_turn(store, fake, turn("lvl2", NEW_MESSAGE)
                             )["status"] == "ready"
    assert store.peek("lvl2")["difficulty_level"] == 2
    assert fake.call_count == 0


def test_c_new_at_max_stays_deterministic_and_zero_call(universal):
    """PRIMARNA REGRESIJA. Nivo ostaje 3, ruta je deterministička, 0 poziva.

    `FakeLLM` bez ijednog pripremljenog odgovora je ovdje dio dokaza: da ruta
    dotakne model, turn bi pao jer odgovor nije pripremljen."""
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "max-new")
    before = store.peek("max-new")
    assert before["difficulty_level"] == MAX_LEVEL

    response = run_practice_turn(store, fake, turn("max-new", NEW_MESSAGE))
    session = store.peek("max-new")

    assert response["status"] == "ready", response
    assert session["difficulty_level"] == MAX_LEVEL          # nivo se ne mijenja
    assert fake.call_count == 0                              # ni Tutor ni recenzent
    assert len(fake.tutor_calls) == 0 and len(fake.reviewer_calls) == 0
    assert session["current_task"] != before["current_task"]  # nov zadatak
    # Objavljen paket je STVARNO zadatak nivoa 3 iz determinističkog pogona.
    parameters = {p["name"]: p["value"]
                  for p in signature(session)["normalized_parameters"]}
    assert parameters["level"] == "3"
    assert parameters["type"] in dict(
        _context().semantic_contract.parameters)["problem_types_by_level"]["3"]


def test_c_decide_never_escalates_on_next_task(universal):
    """Okidač je uklonjen: `next_task` ne eskalira ni na maksimumu."""
    from matbot import difficulty_level

    for level in (1, 2, MAX_LEVEL):
        transition = difficulty_level.transition(level, "")
        assert transition.target_level == level              # nivo se čuva
        assert esc.decide(_context(), {"difficulty_level": level},
                          "next_task", transition) is None, level


def test_generate_task_never_escalates(universal):
    from matbot import difficulty_level

    transition = difficulty_level.transition(MAX_LEVEL, "")
    assert esc.decide(_context(), {"difficulty_level": MAX_LEVEL},
                      "generate_task", transition) is None


def test_no_reason_constant_survives_for_new_at_max():
    """Konstanta i njen tekst su uklonjeni, ne samo zaobiđeni."""
    assert not hasattr(esc, "REASON_MAX_LEVEL_NEW")
    assert set(esc._REASON_TEXT) == {esc.REASON_MAX_LEVEL_HARDER,
                                     esc.REASON_EXPLICIT_VARIETY}


# ---------------------------------------------------------------------------
# §7 — DETERMINISTIČKI NIVO 3 STVARNO NUDI RAZNOLIKOST
# ---------------------------------------------------------------------------

def test_consecutive_new_at_max_rotates_without_immediate_repeats(universal):
    """Šest uzastopnih „novi zadatak“ na maksimumu: 0 poziva, bez ponavljanja.

    Ne uvodi nikakvo novo stanje — mjeri se zatečeno ponašanje generatora i
    postojećeg čuvara duplikata."""
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "variety")
    texts, archetypes = [], []
    for _ in range(6):
        assert run_practice_turn(store, fake, turn("variety", NEW_MESSAGE)
                                 )["status"] == "ready"
        session = store.peek("variety")
        assert session["difficulty_level"] == MAX_LEVEL
        texts.append(session["current_task"])
        parameters = {p["name"]: p["value"]
                      for p in signature(session)["normalized_parameters"]}
        assert parameters["level"] == "3"
        archetypes.append(parameters["type"])
    assert fake.call_count == 0
    assert len(set(texts)) == len(texts)                  # nijedan doslovan duplikat
    assert all(a != b for a, b in zip(texts, texts[1:]))  # ni uzastopni
    assert len(set(archetypes)) >= 2                      # više od jedne strukture


# ---------------------------------------------------------------------------
# D/E — TEŽE I LAKŠE OSTAJU NEPROMIJENJENI
# ---------------------------------------------------------------------------

def test_d_harder_at_max_still_uses_the_creative_route(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "max-harder")
    force_target(store, "max-harder", "fraction_of_fraction")
    draft = creative_draft("fraction_of_fraction")
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=draft,
        checks=make_reviewer_checks(
            independent_answer="$18$", matches_target_archetype=True,
            substantially_different_from_recent=True)))
    response = run_practice_turn(store, fake, turn("max-harder", HARDER_MESSAGE))
    session = store.peek("max-harder")
    assert response["status"] == "ready"
    assert CREATIVE_TASK["text"] in (session.get("current_task") or "")
    assert session["difficulty_level"] == MAX_LEVEL
    assert published_history(session)[-1] == "fraction_of_fraction"
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1


def test_d_decide_still_escalates_on_harder_at_max(universal):
    from matbot import difficulty_level

    transition = difficulty_level.transition(MAX_LEVEL, "harder")
    assert transition.boundary_reason == "at_maximum"
    decision = esc.decide(_context(), {"difficulty_level": MAX_LEVEL},
                          "harder_task", transition)
    assert decision is not None
    assert decision.reason == esc.REASON_MAX_LEVEL_HARDER
    assert decision.level == MAX_LEVEL


def test_f_explicit_variety_request_still_escalates(universal):
    from matbot import difficulty_level

    decision = esc.decide(_context(), {"difficulty_level": 2}, "",
                          difficulty_level.transition(2, ""),
                          explicit_variety=True)
    assert decision is not None
    assert decision.reason == esc.REASON_EXPLICIT_VARIETY


def test_e_easier_at_max_drops_to_two_and_stays_deterministic(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "max-easier")
    response = run_practice_turn(store, fake,
                                 turn("max-easier", "Daj mi lakši zadatak."))
    assert response["status"] == "ready"
    assert store.peek("max-easier")["difficulty_level"] == 2
    assert fake.call_count == 0


# ---------------------------------------------------------------------------
# G — IZOLACIJA: nijedna lekcija van pilota ne postaje model-vođena
# ---------------------------------------------------------------------------

def test_g_unsupported_lesson_never_escalates_even_on_harder_at_max(universal):
    """Prve tri lekcije su IZ ISTE PORODICE i nose `problem_types` — njih
    zaustavlja ISKLJUČIVO pilot-kapija, pa test stvarno mjeri nju."""
    from matbot import difficulty_level
    from matbot.semantics import contracts as contracts_module

    transition = difficulty_level.transition(MAX_LEVEL, "harder")
    checked = 0
    for lesson in ("6-03-010", "6-05-011", "8-04-016",
                   "6-04-003", "6-04-009", "9-02-006"):
        context = type("C", (), {
            "topic_id": lesson,
            "semantic_contract": contracts_module.contract_for(lesson)})()
        assert esc.is_pilot_lesson(context) is False, lesson
        assert esc.decide(context, {"difficulty_level": MAX_LEVEL},
                          "harder_task", transition) is None, lesson
        checked += 1
    assert checked == 6
    assert esc.is_pilot_lesson(_context()) is True


# ---------------------------------------------------------------------------
# H–K — SIGURNOST KREATIVNE RUTE (sada na `teži` putu) OSTAJE NETAKNUTA
# ---------------------------------------------------------------------------

def _rejected_creative_harder(session_id, decision):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, session_id)
    force_target(store, session_id, "fraction_of_fraction")
    before_published = published_history(store.peek(session_id))
    draft = creative_draft("fraction_of_fraction")
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision=decision,
        final=draft if decision == "correct" else None,
        fail_reason_code="ambiguous_task" if decision == "fail_closed" else None,
        checks=make_reviewer_checks(
            independent_answer="$18$", matches_target_archetype=True,
            substantially_different_from_recent=True)))
    response = run_practice_turn(store, fake, turn(session_id, HARDER_MESSAGE))
    session = store.peek(session_id)
    return {"response": response, "session": session,
            "published_before": before_published,
            "published_after": published_history(session),
            "attempts_after": attempt_history(session),
            "tutor_calls": len(fake.tutor_calls),
            "reviewer_calls": len(fake.reviewer_calls)}


def test_h_creative_correct_fails_closed_with_the_quality_message(universal):
    result = _rejected_creative_harder("h-correct", "correct")
    assert "status" not in result["response"]
    assert CREATIVE_TASK["text"] not in (result["session"].get("current_task") or "")
    assert result["published_after"] == result["published_before"]
    assert result["session"]["difficulty_level"] == MAX_LEVEL
    assert result["attempts_after"] == ["fraction_of_fraction"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1
    assert result["response"]["answer"] == tutor_pipeline.QUALITY_REJECTION_MESSAGE
    assert result["response"]["answer"] != tutor_pipeline.SAFE_ERROR_MESSAGE


def test_i_creative_fail_closed_still_fails_closed_safely(universal):
    """`fail_closed` pada na ZAJEDNIČKOJ grani (dijeli je s običnom rutom), pa
    zadržava zatečenu poruku — mapiranje je namjerno usko."""
    result = _rejected_creative_harder("i-failclosed", "fail_closed")
    assert "status" not in result["response"]
    assert result["published_after"] == result["published_before"]
    assert result["session"]["difficulty_level"] == MAX_LEVEL
    assert result["response"]["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


def test_quality_message_never_leaks_internal_vocabulary():
    message = tutor_pipeline.QUALITY_REJECTION_MESSAGE.lower()
    for forbidden in ("recenzent", "tutor", "model", "ai", "arhetip",
                      "validac", "reviewer", "target", "creative"):
        assert forbidden not in message, forbidden


def test_j_rejected_harder_keeps_level_and_rotates_next_target(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "j-retry")
    force_target(store, "j-retry", "fraction_of_fraction")
    draft = creative_draft("fraction_of_fraction")
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="correct", final=draft,
        checks=make_reviewer_checks(
            independent_answer="$18$", matches_target_archetype=True,
            substantially_different_from_recent=True)))
    assert "status" not in run_practice_turn(
        store, fake, turn("j-retry", HARDER_MESSAGE))
    session = store.peek("j-retry")
    assert session["difficulty_level"] == MAX_LEVEL
    assert attempt_history(session) == ["fraction_of_fraction"]

    from matbot import difficulty_level
    decision = esc.decide(_context(), store.peek("j-retry"), "harder_task",
                          difficulty_level.transition(MAX_LEVEL, "harder"))
    assert decision is not None
    assert decision.reason == esc.REASON_MAX_LEVEL_HARDER
    assert decision.target_archetype != "fraction_of_fraction"
    assert "fraction_of_fraction" in decision.attempted_archetypes


def test_k_no_third_call_on_any_creative_outcome(universal):
    for name, decision in (("correct", "correct"), ("failclosed", "fail_closed")):
        result = _rejected_creative_harder(f"k-{name}", decision)
        assert result["tutor_calls"] == 1, name
        assert result["reviewer_calls"] == 1, name


def test_generic_failures_keep_the_original_safe_message(universal):
    """Nekreativno odbijanje NE smije dobiti poruku o kvalitetu."""
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "generic-fail")
    bad = make_tutor_draft(
        intent="harder_task", reply="Evo.",
        lesson_focus="tekstualni zadaci s razlomcima",
        difficulty_diagnostics=make_difficulty_diagnostics(direction="higher"),
        new_task=make_task_payload(
            text="Neupotrebljiv nacrt bez činjenica.",
            options=("$1$", "$2$", "$3$", "$4$"), correct_option_index=0,
            expected="$1$", solution="x", difficulty="hard"))
    fake.queue(bad)
    response = run_practice_turn(store, fake, turn("generic-fail", HARDER_MESSAGE))
    assert "status" not in response
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert len(fake.reviewer_calls) == 0
