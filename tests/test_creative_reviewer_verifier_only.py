"""Na KREATIVNOJ ruti recenzent je ČISTI VERIFIKATOR — ne prepravlja paket.

ŽIVI NALAZ (finalna kampanja, turn 4). Server je tražio `fraction_of_fraction`;
Tutor je dao ispravan paket (30 · 2/5 = 12, 12 · 3/4 = 9, označeno 9) koji je
prošao egzaktnu provjeru činjenica. Recenzent je zatim vratio `correct` sa
ZAMJENSKIM paketom koji je pitanje promijenio u „koliko je OSTALO u tom manjem
dijelu“ (12 − 9 = 3). Objavljen je taj zamjenski paket — a kanonski potpis je i
dalje tvrdio `fraction_of_fraction` s činjenicama koje daju 9.

Uzrok: egzaktna provjera radi nad TUTOROVIM nacrtom, prije recenzenta, a
`reviewer.final` je postajao osnova objave bez ijedne nove provjere. Novu prozu
poslije toga niko ne sudi — trećeg poziva nema.

Zato na kreativnoj ruti postoje samo dva ishoda: `approve` (objavi kanonizovan
Tutorov nacrt) ili PAD. Obična ruta ostaje netaknuta.
"""
import json

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import creative_escalation as esc
from matbot.tutor.schema import DifficultyEvidence, SignatureParameter
from tests.conftest import (FakeLLM, make_difficulty_diagnostics,
                            make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

LESSON, GRADE, TITLE = "6-04-015", 6, "Tekstualni zadaci s razlomcima"


def _contract_enum():
    from matbot.semantics import contracts as contracts_module
    return tuple(dict(contracts_module.contract_for(LESSON).parameters)
                 ["creative_problem_types"])


SUPPORTED = _contract_enum()

# Doslovno živi turn 4: ispravan `fraction_of_fraction`, odgovor 9.
TUTOR_TASK = {
    "text": ("Mira ima $30$ knedli. Najprije proba $\\frac{2}{5}$ od njih. "
             "Zatim od tog dijela pojede $\\frac{3}{4}$. Koliko knedli je Mira "
             "pojela u drugom koraku?"),
    "options": ("$9$", "$12$", "$6$", "$18$"),
    "correct_index": 0,
    "expected": "$9$",
    "solution": ("$\\frac{2}{5} \\cdot 30 = 12$, pa je "
                 "$\\frac{3}{4} \\cdot 12 = 9$ knedli."),
    "facts": {"type": "fraction_of_fraction", "total": "30",
              "first_fraction": "2/5", "second_fraction": "3/4"},
}
# Doslovno recenzentova živa zamjena: isto pitanje pomjereno za jedno oduzimanje.
REVIEWER_REPLACEMENT = {
    "text": ("Mira ima $30$ knedli. Najprije proba $\\frac{2}{5}$ od njih. "
             "Zatim od tog dijela pojede $\\frac{3}{4}$. Koliko knedli je "
             "NEPOJEDENO u tom manjem dijelu nakon drugog koraka?"),
    "options": ("$12$", "$6$", "$9$", "$3$"),
    "correct_index": 3,
    "expected": "$3$",
    "solution": ("$30\\cdot\\frac{2}{5}=12$, pa $12\\cdot\\frac{3}{4}=9$, "
                 "pa je $12-9=3$."),
    "facts": TUTOR_TASK["facts"],
}


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def turn(session_id, message):
    return {"session_id": session_id, "grade": GRADE, "selected_topic": LESSON,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": "", "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}


def published_history(session):
    out = []
    for record in session.get("recent_task_signatures") or []:
        if record.get("lesson_id") != LESSON:
            continue
        out.append(json.loads(record["structured_signature"])
                   .get("operation_or_relation"))
    return out


def attempt_history(session):
    return [r.get("archetype")
            for r in (session.get("recent_creative_targets") or [])
            if isinstance(r, dict) and r.get("lesson_id") == LESSON]


def package(task, label="fraction_of_fraction"):
    payload = make_task_payload(
        text=task["text"], options=task["options"],
        correct_option_index=task["correct_index"], expected=task["expected"],
        solution=task["solution"], difficulty="hard")
    return payload.model_copy(update={
        "selected_lesson_id": LESSON, "selected_lesson_title": TITLE,
        "target_difficulty_level": 3,
        "difficulty_evidence": DifficultyEvidence(
            reasoning_steps=3, condition_count=2, operation_count=3,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        "task_signature": payload.task_signature.model_copy(update={
            "operation_or_relation": label,
            "normalized_parameters": [SignatureParameter(name=n, value=v)
                                      for n, v in task["facts"].items()]}),
    })


def as_draft(task, label="fraction_of_fraction"):
    return make_tutor_draft(
        intent="harder_task", reply="Evo zadatka.",
        lesson_focus="tekstualni zadaci s razlomcima",
        difficulty_diagnostics=make_difficulty_diagnostics(direction="higher"),
        new_task=package(task, label))


ALL_TRUE = {"matches_target_archetype": True,
            "substantially_different_from_recent": True}


def warm_up(store, fake, session_id, desired="fraction_of_fraction"):
    for message in ("Daj mi zadatak.", "Daj mi teži zadatak.",
                    "Daj mi teži zadatak."):
        assert run_practice_turn(store, fake, turn(session_id, message)
                                 )["status"] == "ready"
    assert fake.call_count == 0
    session = store.peek(session_id)
    session["recent_task_signatures"] = [
        {"lesson_id": LESSON,
         "structured_signature": json.dumps({"operation_or_relation": name}),
         "structured_signature_hash": f"seed-{name}"}
        for name in SUPPORTED if name != desired]
    store.save(session)
    recent = esc.recent_archetypes(store.peek(session_id), LESSON,
                                   supported=SUPPORTED)
    target = esc.select_target(SUPPORTED, recent)
    assert target == desired, (target, desired)
    return target


def run_creative(session_id, reviewer_decision, reviewer_final, **checks):
    store, fake = SessionStore(), FakeLLM()
    warm_up(store, fake, session_id)
    before_session = store.peek(session_id)
    before_published = published_history(before_session)
    before_task = before_session.get("current_task")
    before_answer = before_session.get("expected_answer_summary")
    tutor_draft = as_draft(TUTOR_TASK)
    fake.queue(tutor_draft)
    if reviewer_final is ...:
        reviewer_final = tutor_draft          # eho nacrta (server ga ionako ignoriše)
    fake.queue(make_reviewer_final(
        decision=reviewer_decision, final=reviewer_final,
        fail_reason_code=("ambiguous_task" if reviewer_decision == "fail_closed"
                          else None),
        checks=make_reviewer_checks(independent_answer="$9$",
                                    **{**ALL_TRUE, **checks})))
    response = run_practice_turn(store, fake,
                                 turn(session_id, "Daj mi teži zadatak."))
    session = store.peek(session_id)
    return {"response": response, "session": session,
            "task": session.get("current_task") or "",
            "task_before": before_task, "answer_before": before_answer,
            "published_before": before_published,
            "published_after": published_history(session),
            "attempts_after": attempt_history(session),
            "tutor_calls": len(fake.tutor_calls),
            "reviewer_calls": len(fake.reviewer_calls),
            "level": session.get("difficulty_level")}


# ---------------------------------------------------------------------------
# §4 — ŽIVI DEFEKT: zamjena NIKAD ne objavljuje, ni nacrt poslije `correct`
# ---------------------------------------------------------------------------

def test_reviewer_replacement_never_publishes(universal):
    result = run_creative("verif-4", "correct",
                          as_draft(REVIEWER_REPLACEMENT))
    assert "status" not in result["response"]              # sigurna poruka
    # Ni zamjena…
    assert REVIEWER_REPLACEMENT["text"] not in result["task"]
    # …ni nacrt koji recenzent NIJE bezuslovno odobrio.
    assert TUTOR_TASK["text"] not in result["task"]
    assert result["published_after"] == result["published_before"]
    assert result["attempts_after"] == ["fraction_of_fraction"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


def test_replacement_answer_never_reaches_the_session(universal):
    """Živi defekt: objavljen odgovor 3 uz potpis koji daje 9.

    Mjeri se NEPROMIJENJENOST sesije, a ne odsustvo konkretnog broja: nasumični
    deterministički zadatak iz zagrijavanja smije imati odgovor $3$, pa bi
    provjera po vrijednosti bila nestabilna po RNG-u."""
    result = run_creative("verif-4b", "correct",
                          as_draft(REVIEWER_REPLACEMENT))
    assert result["task"] == result["task_before"]
    assert result["session"].get("expected_answer_summary") == result["answer_before"]
    assert "NEPOJEDENO" not in (result["task"] or "")


# ---------------------------------------------------------------------------
# §5 — čista kontrola odobrenja
# ---------------------------------------------------------------------------

def test_approved_draft_publishes_with_the_server_archetype(universal):
    result = run_creative("verif-5", "approve", ...)
    assert result["response"]["status"] == "ready"
    assert TUTOR_TASK["text"] in result["task"]
    assert result["published_after"][-1] == "fraction_of_fraction"
    assert result["level"] == 3
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1
    # Objavljeni potpis mora i dalje egzaktno davati 9.
    from fractions import Fraction
    from matbot.mathkernel import wordfacts
    raw = json.loads(result["session"]["current_task_signature"]
                     ["structured_signature"])
    facts = {p["name"]: p["value"] for p in raw["normalized_parameters"]}
    facts.pop("type", None)
    facts.pop("level", None)
    assert wordfacts.solve_from_parameters("fraction_of_fraction", facts) == 9
    assert result["session"]["expected_answer_summary"] == "$9$"
    assert Fraction("9") == 9


# ---------------------------------------------------------------------------
# §6 — `correct` pada i kad je zamjena identična nacrtu
# ---------------------------------------------------------------------------

def test_correct_with_identical_final_still_fails_closed(universal):
    """Odluka `correct` sama po sebi znači: nije bezuslovno odobreno."""
    result = run_creative("verif-6", "correct", as_draft(TUTOR_TASK))
    assert "status" not in result["response"]
    assert TUTOR_TASK["text"] not in result["task"]
    assert result["published_after"] == result["published_before"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


def test_fail_closed_also_rejects(universal):
    result = run_creative("verif-6b", "fail_closed", None)
    assert "status" not in result["response"]
    assert result["published_after"] == result["published_before"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


@pytest.mark.parametrize("check", ("matches_target_archetype",
                                   "substantially_different_from_recent"))
def test_blocking_check_false_still_rejects_on_approve(universal, check):
    result = run_creative("verif-6c-" + check, "approve", ..., **{check: False})
    assert "status" not in result["response"]
    assert result["published_after"] == result["published_before"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


def test_no_creative_path_ever_makes_a_third_call(universal):
    for name, decision, final in (
            ("a", "approve", ...),
            ("b", "correct", as_draft(REVIEWER_REPLACEMENT)),
            ("c", "correct", as_draft(TUTOR_TASK)),
            ("d", "fail_closed", None)):
        result = run_creative(f"verif-third-{name}", decision, final)
        assert result["tutor_calls"] == 1, name
        assert result["reviewer_calls"] == 1, name


# ---------------------------------------------------------------------------
# §7 — OBIČNA RUTA SE NE MIJENJA
# ---------------------------------------------------------------------------

def test_ordinary_route_still_publishes_a_reviewer_correction(universal):
    """Bez eskalacije `reviewer.final` OSTAJE osnova objave."""
    from matbot.tutor import pipeline as tutor_pipeline

    store, fake = SessionStore(), FakeLLM()
    session_id = "ordinary-correct"
    corrected_text = "Ispravljen zadatak: koliko je $2+3$?"
    draft = make_tutor_draft(
        intent="generate_task", reply="Evo zadatka.",
        lesson_focus="sabiranje",
        new_task=make_task_payload(
            text="Loše sročen zadatak: koliko je $2+3$?",
            options=("$5$", "$4$", "$6$", "$7$"), correct_option_index=0,
            expected="$5$", solution="$2+3=5$", difficulty="standard"))
    corrected = make_tutor_draft(
        intent="generate_task", reply="Evo zadatka.",
        lesson_focus="sabiranje",
        new_task=make_task_payload(
            text=corrected_text,
            options=("$5$", "$4$", "$6$", "$7$"), correct_option_index=0,
            expected="$5$", solution="$2+3=5$", difficulty="standard"))
    fake.queue(draft)
    fake.queue(make_reviewer_final(decision="correct", final=corrected,
                                   checks=make_reviewer_checks(
                                       independent_answer="$5$")))
    # 9-02-006: model-ruta bez determinističkog generatora i bez eskalacije —
    # jedini pošten kontrolni uzorak za „obična ruta se ne mijenja“.
    ordinary_turn = {**turn(session_id, "Daj mi zadatak."),
                     "grade": 9, "selected_topic": "9-02-006"}
    response = run_practice_turn(store, fake, ordinary_turn)
    session = store.peek(session_id)
    assert response.get("status") == "ready", response
    assert corrected_text in (session.get("current_task") or "")
    assert len(fake.reviewer_calls) == 1


def test_verifier_only_rule_is_gated_on_escalation(universal):
    """Kapija postoji SAMO uz eskalaciju — čita se iz izvornog koda."""
    import inspect
    from matbot.tutor import pipeline as tutor_pipeline

    # Recenzentska faza je IZDVOJENA u `_reviewer_stage` da je obje
    # model-podržane rute dijele; kapija se čita odatle, plus iz `_two_call`
    # koji je poziva.
    source = (inspect.getsource(tutor_pipeline._two_call)
              + inspect.getsource(tutor_pipeline._reviewer_stage))
    assert "creative_reviewer_not_approved" in source
    marker = source.index("creative_reviewer_not_approved")
    guard = source[:marker]
    assert "escalation is not None and reviewer.decision != \"approve\"" in guard
