"""NOV ZADATAK NA MAKSIMUMU IDE KREATIVNOM RUTOM (proizvodna odluka).

RUČNI TEST U PRODUKCIJI (6. razred, `Tekstualni zadaci s razlomcima`): učenik
se popeo na maksimum, tražio „Daj mi novi zadatak.“ i dobio DETERMINISTIČKI
zadatak. Nivo je pri tome bio ISPRAVAN — ostajao je 3, i dobijeni zadatak je
bio zadatak nivoa 3. Problem nije bio TEŽINA nego RUTA: na iscrpljenoj
ljestvici „još jedan“ znači isto što i „nešto drugo“, a to je upravo ono što
kreativna eskalacija postoji da ponudi.

ŠTA SE MIJENJA: samo okidač eskalacije (`decide`) dobija treći razlog —
`next_task` kad je ciljni nivo maksimalan. Prelazi nivoa se NE diraju,
verifikatorska politika recenzenta se NE dira, planer se NE dira.

ŠTA SE NE SMIJE DESITI: da lekcija koja nije u pilotu postane model-vođena
samo zato što je nivo 3. Zato je posljednji test u fajlu izolacija.
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
        intent="next_task", reply="Evo zadatka.",
        lesson_focus="tekstualni zadaci s razlomcima",
        difficulty_diagnostics=make_difficulty_diagnostics(direction="higher"),
        new_task=payload)


def climb_to_max(store, fake, session_id):
    """Deterministički 1→2→3; nula poziva, kao u produkciji."""
    for message in ("Daj mi zadatak.", "Daj mi teži zadatak.",
                    "Daj mi teži zadatak."):
        assert run_practice_turn(store, fake, turn(session_id, message)
                                 )["status"] == "ready"
    assert fake.call_count == 0
    assert store.peek(session_id)["difficulty_level"] == MAX_LEVEL
    return store.peek(session_id)


def force_target(store, session_id, desired):
    """Postavi historiju objava tako da planer izabere baš `desired`."""
    supported = esc._contract_archetypes(
        type("C", (), {"semantic_contract": __import__(
            "matbot.semantics.contracts", fromlist=["x"]
        ).contract_for(PILOT_LESSON)})())
    session = store.peek(session_id)
    session["recent_task_signatures"] = [
        {"lesson_id": PILOT_LESSON,
         "structured_signature": json.dumps({"operation_or_relation": name}),
         "structured_signature_hash": f"seed-{name}"}
        for name in supported if name != desired]
    store.save(session)


# ---------------------------------------------------------------------------
# A/B — niži nivoi ostaju DETERMINISTIČKI i nula-pozivni
# ---------------------------------------------------------------------------

def test_a_new_at_level_1_stays_deterministic(universal):
    store, fake = SessionStore(), FakeLLM()
    assert run_practice_turn(store, fake, turn("lvl1", "Daj mi zadatak.")
                             )["status"] == "ready"
    assert run_practice_turn(store, fake, turn("lvl1", "Daj mi novi zadatak.")
                             )["status"] == "ready"
    assert store.peek("lvl1")["difficulty_level"] == 1
    assert fake.call_count == 0


def test_b_new_at_level_2_stays_deterministic(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn("lvl2", "Daj mi zadatak."))
    run_practice_turn(store, fake, turn("lvl2", "Daj mi teži zadatak."))
    assert store.peek("lvl2")["difficulty_level"] == 2
    assert run_practice_turn(store, fake, turn("lvl2", "Daj mi novi zadatak.")
                             )["status"] == "ready"
    assert store.peek("lvl2")["difficulty_level"] == 2
    assert fake.call_count == 0


# ---------------------------------------------------------------------------
# C/G — PRIMARNA REGRESIJA: NOV ZADATAK NA MAKSIMUMU → KREATIVNA RUTA
# ---------------------------------------------------------------------------

def test_c_new_at_max_uses_the_creative_route_and_publishes(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "max-new")
    force_target(store, "max-new", "fraction_of_fraction")
    before_calls = fake.call_count

    draft = creative_draft("fraction_of_fraction")
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=draft,
        checks=make_reviewer_checks(
            independent_answer="$18$", matches_target_archetype=True,
            substantially_different_from_recent=True)))
    response = run_practice_turn(store, fake, turn("max-new", "Daj mi novi zadatak."))

    session = store.peek("max-new")
    assert response["status"] == "ready", response
    assert CREATIVE_TASK["text"] in (session.get("current_task") or "")
    assert session["difficulty_level"] == MAX_LEVEL          # nivo ostaje 3
    assert published_history(session)[-1] == "fraction_of_fraction"
    assert fake.call_count - before_calls == 2               # Tutor + Recenzent
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1


def test_c_decide_reports_the_new_reason_at_max(universal):
    from matbot import difficulty_level
    from matbot.semantics import contracts as contracts_module

    context = type("C", (), {
        "topic_id": PILOT_LESSON,
        "semantic_contract": contracts_module.contract_for(PILOT_LESSON)})()
    transition = difficulty_level.transition(MAX_LEVEL, "")
    decision = esc.decide(context, {"difficulty_level": MAX_LEVEL},
                          "next_task", transition)
    assert decision is not None
    assert decision.reason == esc.REASON_MAX_LEVEL_NEW
    assert decision.level == MAX_LEVEL
    # Nivo se NE mijenja — prelaz je i dalje `same`.
    assert transition.target_level == MAX_LEVEL and not transition.level_changed


def test_new_below_max_never_escalates(universal):
    from matbot import difficulty_level
    from matbot.semantics import contracts as contracts_module

    context = type("C", (), {
        "topic_id": PILOT_LESSON,
        "semantic_contract": contracts_module.contract_for(PILOT_LESSON)})()
    for level in (1, 2):
        transition = difficulty_level.transition(level, "")
        assert esc.decide(context, {"difficulty_level": level},
                          "next_task", transition) is None


def test_generate_task_at_max_does_not_escalate(universal):
    """`generate_task` znači da aktivnog zadatka NEMA — nema ni od čega
    praviti „nešto drugo“, pa okidač na to namjerno ne reaguje."""
    from matbot import difficulty_level
    from matbot.semantics import contracts as contracts_module

    context = type("C", (), {
        "topic_id": PILOT_LESSON,
        "semantic_contract": contracts_module.contract_for(PILOT_LESSON)})()
    transition = difficulty_level.transition(MAX_LEVEL, "")
    assert esc.decide(context, {"difficulty_level": MAX_LEVEL},
                      "generate_task", transition) is None


# ---------------------------------------------------------------------------
# D/E — teže i lakše ostaju nepromijenjeni
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
    response = run_practice_turn(store, fake,
                                 turn("max-harder", "Daj mi teži zadatak."))
    assert response["status"] == "ready"
    assert store.peek("max-harder")["difficulty_level"] == MAX_LEVEL
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1


def test_e_easier_at_max_drops_to_two_and_stays_deterministic(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "max-easier")
    response = run_practice_turn(store, fake,
                                 turn("max-easier", "Daj mi lakši zadatak."))
    assert response["status"] == "ready"
    assert store.peek("max-easier")["difficulty_level"] == 2
    assert fake.call_count == 0                      # nikakva kreativna ruta


# ---------------------------------------------------------------------------
# F/K — IZOLACIJA: nivo 3 sam po sebi ne pravi model-rutu
# ---------------------------------------------------------------------------

def test_f_unsupported_lesson_never_escalates_on_new_at_max(universal):
    """Lekcija bez pilota mora ostati na svojoj ruti i na maksimumu."""
    from matbot import difficulty_level
    from matbot.semantics import contracts as contracts_module
    from matbot.tutor import lesson_context

    transition = difficulty_level.transition(MAX_LEVEL, "")
    checked = 0
    # KLJUČNE KONTROLE: prve tri lekcije su IZ ISTE PORODICE i NOSE
    # `problem_types` — njih zaustavlja ISKLJUČIVO pilot-kapija. Bez njih bi
    # test prolazio i kad se kapija ukloni, jer lekcije bez enuma ionako padnu
    # na drugoj provjeri (mjereno: falsifikacija F2 je to i pokazala).
    for lesson in ("6-03-010", "6-05-011", "8-04-016",
                   "6-04-003", "6-04-009", "9-02-006"):
        contract = contracts_module.contract_for(lesson)
        context = type("C", (), {"topic_id": lesson,
                                 "semantic_contract": contract})()
        assert esc.is_pilot_lesson(context) is False, lesson
        assert esc.decide(context, {"difficulty_level": MAX_LEVEL},
                          "next_task", transition) is None, lesson
        checked += 1
    assert checked == 6
    # I pozitivna kontrola: pilot lekcija JESTE u pilotu.
    pilot = type("C", (), {
        "topic_id": PILOT_LESSON,
        "semantic_contract": contracts_module.contract_for(PILOT_LESSON)})()
    assert esc.is_pilot_lesson(pilot) is True


# ---------------------------------------------------------------------------
# H/I — sigurnost recenzenta NEPROMIJENJENA + iskrena poruka
# ---------------------------------------------------------------------------

def _rejected_creative_new(session_id, decision, universal_env=None):
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
    response = run_practice_turn(store, fake, turn(session_id, "Daj mi novi zadatak."))
    session = store.peek(session_id)
    return {"response": response, "session": session,
            "published_before": before_published,
            "published_after": published_history(session),
            "attempts_after": attempt_history(session),
            "tutor_calls": len(fake.tutor_calls),
            "reviewer_calls": len(fake.reviewer_calls)}


def test_h_creative_correct_fails_closed_with_the_quality_message(universal):
    result = _rejected_creative_new("max-correct", "correct")
    assert "status" not in result["response"]
    assert CREATIVE_TASK["text"] not in (result["session"].get("current_task") or "")
    assert result["published_after"] == result["published_before"]
    assert result["session"]["difficulty_level"] == MAX_LEVEL
    assert result["attempts_after"] == ["fraction_of_fraction"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1
    # ISKRENA poruka umjesto generičke.
    assert result["response"]["answer"] == tutor_pipeline.QUALITY_REJECTION_MESSAGE
    assert result["response"]["answer"] != tutor_pipeline.SAFE_ERROR_MESSAGE


def test_i_creative_fail_closed_still_fails_closed_safely(universal):
    """`fail_closed` pada na ZAJEDNIČKOJ grani (dijeli je s običnom rutom).

    Mapiranje poruke je NAMJERNO usko: samo dokazana kategorija
    `creative_reviewer_not_approved` (živi log: `decision=correct`) dobija
    iskreniju poruku. Širenje na `fail_closed` diralo bi i običnu rutu, pa se
    ovdje zaključava zatečeno ponašanje umjesto da se tiho proširi."""
    result = _rejected_creative_new("max-failclosed", "fail_closed")
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


def test_generic_failures_keep_the_original_safe_message(universal):
    """Nekreativno odbijanje NE smije dobiti poruku o kvalitetu."""
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "generic-fail")
    # Neupotrebljiv nacrt pada PRIJE recenzenta (kapija činjenica), dakle to
    # nije kategorija `creative_reviewer_not_approved`.
    bad = make_tutor_draft(
        intent="next_task", reply="Evo.",
        lesson_focus="tekstualni zadaci s razlomcima",
        difficulty_diagnostics=make_difficulty_diagnostics(direction="higher"),
        new_task=make_task_payload(
            text="Neupotrebljiv nacrt bez činjenica.",
            options=("$1$", "$2$", "$3$", "$4$"), correct_option_index=0,
            expected="$1$", solution="x", difficulty="hard"))
    fake.queue(bad)
    response = run_practice_turn(store, fake,
                                 turn("generic-fail", "Daj mi novi zadatak."))
    assert "status" not in response
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert len(fake.reviewer_calls) == 0                 # pao prije recenzenta


# ---------------------------------------------------------------------------
# J — RETRY: ponovni zahtjev ostaje na maksimumu i ROTIRA cilj
# ---------------------------------------------------------------------------

def test_j_retry_stays_creative_at_max_and_rotates_the_target(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "retry")
    force_target(store, "retry", "fraction_of_fraction")

    draft = creative_draft("fraction_of_fraction")
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="correct", final=draft,
        checks=make_reviewer_checks(
            independent_answer="$18$", matches_target_archetype=True,
            substantially_different_from_recent=True)))
    first = run_practice_turn(store, fake, turn("retry", "Daj mi novi zadatak."))
    assert "status" not in first
    session = store.peek("retry")
    assert session["difficulty_level"] == MAX_LEVEL
    assert attempt_history(session) == ["fraction_of_fraction"]

    # Isti korisnički zahtjev ponovo — bez ijednog automatskog pokušaja servera.
    from matbot import difficulty_level
    from matbot.semantics import contracts as contracts_module
    context = type("C", (), {
        "topic_id": PILOT_LESSON,
        "semantic_contract": contracts_module.contract_for(PILOT_LESSON)})()
    decision = esc.decide(context, store.peek("retry"), "next_task",
                          difficulty_level.transition(MAX_LEVEL, ""))
    assert decision is not None
    assert decision.reason == esc.REASON_MAX_LEVEL_NEW
    assert decision.target_archetype != "fraction_of_fraction"   # rotirano
    assert "fraction_of_fraction" in decision.attempted_archetypes


def test_k_no_third_call_on_any_creative_new_outcome(universal):
    for name, decision in (("approve", "approve"), ("correct", "correct"),
                           ("failclosed", "fail_closed")):
        result = _rejected_creative_new(f"calls-{name}", decision) \
            if decision != "approve" else None
        if result is None:
            continue
        assert result["tutor_calls"] == 1, name
        assert result["reviewer_calls"] == 1, name
