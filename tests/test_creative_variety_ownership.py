"""RAZNOLIKOST: server bira TIP, recenzent sudi o SVJEŽINI PRIMJERA.

ŽIVI NALAZ (dvije ciljane kampanje, 5 odbijanja na
`substantially_different_from_recent = false`):

  NEW04  cilj fraction_remainder      nedavni [fr, fof, mfr]   cilj U listi
  NEW06  cilj fraction_of_quantity    nedavni [foq, fr, fof]   cilj U listi
  NEW08  cilj fraction_of_fraction    nedavni [foq, fr, fof]   cilj U listi
  CTL-V  cilj multi_fraction_remainder nedavni [mfr, fr, fr]   cilj U listi
  NEW05  cilj multi_fraction_remainder nedavni [foq, fr, fof]  cilj NIJE u listi

Četiri od pet su bila STRUKTURNA: eskalacijski blok je istovremeno govorio
„CILJNI tip (obavezan): X“ i „nedavno viđeni tipovi (IZBJEGNI IH): …, X, …“.
Kad planer uđe u drugi/treći nivo izbora (svi tipovi potrošeni), ta dva reda se
DOSLOVNO protivrječe, pa je recenzent pošteno odgovarao „nije drugačije“ — na
cilj koji je server sam i namjerno odredio.

VLASNIŠTVO KOJE OVAJ FAJL ZAKLJUČAVA:
  SERVER  → koji TIP se traži (objave, pokušaji, trotarifna svježina).
  MODEL   → je li OVAJ PRIMJER stvarno nov ili samo presvučen stari.

Provjera OSTAJE BLOKIRAJUĆA. Mijenja se samo pitanje na koje odgovara.
"""
import json

import pytest

from matbot.difficulty_level import MAX_LEVEL
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import creative_escalation as esc
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.schema import DifficultyEvidence, SignatureParameter
from tests.conftest import (FakeLLM, make_difficulty_diagnostics,
                            make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

LESSON, GRADE = "6-04-015", 6
TITLE = "Tekstualni zadaci s razlomcima"
POOL = ("fraction_of_fraction", "fraction_of_quantity",
        "fraction_remainder", "multi_fraction_remainder")

TASKS = {
    "fraction_of_fraction": {
        "text": ("Amar ima $48$ sličica. Od toga je $\\frac{3}{4}$ novih. Od "
                 "novih je $\\frac{1}{2}$ posebno označeno. Koliko je posebno "
                 "označenih?"),
        "options": ("$18$", "$36$", "$24$", "$12$"), "correct_index": 0,
        "expected": "$18$", "solution": "$48\\cdot\\frac{3}{4}=36$, pa $36\\cdot\\frac{1}{2}=18$.",
        "facts": {"type": "fraction_of_fraction", "total": "48",
                  "first_fraction": "3/4", "second_fraction": "1/2"}},
    "fraction_remainder": {
        "text": ("Lejla je potrošila $\\frac{2}{5}$ od svojih $40$ olovaka. "
                 "Koliko olovaka joj je OSTALO?"),
        "options": ("$24$", "$16$", "$40$", "$8$"), "correct_index": 0,
        "expected": "$24$", "solution": "$40\\cdot\\frac{2}{5}=16$, pa $40-16=24$.",
        "facts": {"type": "fraction_remainder", "total": "40",
                  "fraction": "2/5"}},
}


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def turn(sid, message):
    return {"session_id": sid, "grade": GRADE, "selected_topic": LESSON,
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


def decision(target, recent):
    from matbot.semantics import contracts as contracts_module
    defs = dict(contracts_module.contract_for(LESSON).archetype_definitions)
    return esc.CreativeEscalationDecision(
        reason=esc.REASON_MAX_LEVEL_HARDER, target_archetype=target,
        supported_archetypes=POOL, recent_archetypes=tuple(recent),
        level=MAX_LEVEL,
        definitions=tuple((k, v) for k, v in defs.items() if k == target))


def _flat(text):
    return " ".join((text or "").split())


def draft_for(target):
    task = TASKS[target]
    payload = make_task_payload(
        text=task["text"], options=task["options"],
        correct_option_index=task["correct_index"], expected=task["expected"],
        solution=task["solution"], difficulty="hard")
    payload = payload.model_copy(update={
        "selected_lesson_id": LESSON, "selected_lesson_title": TITLE,
        "target_difficulty_level": 3,
        "difficulty_evidence": DifficultyEvidence(
            reasoning_steps=3, condition_count=2, operation_count=3,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        "task_signature": payload.task_signature.model_copy(update={
            "operation_or_relation": target,
            "normalized_parameters": [SignatureParameter(name=n, value=v)
                                      for n, v in task["facts"].items()]})})
    return make_tutor_draft(
        intent="harder_task", reply="Evo zadatka.",
        lesson_focus="tekstualni zadaci s razlomcima",
        difficulty_diagnostics=make_difficulty_diagnostics(direction="higher"),
        new_task=payload)


def climb_to_max(store, fake, sid):
    for message in ("Daj mi zadatak.", "Daj mi teži zadatak.",
                    "Daj mi teži zadatak."):
        assert run_practice_turn(store, fake, turn(sid, message))["status"] == "ready"
    assert fake.call_count == 0
    assert store.peek(sid)["difficulty_level"] == MAX_LEVEL


def force_target(store, sid, desired):
    session = store.peek(sid)
    session["recent_task_signatures"] = [
        {"lesson_id": LESSON,
         "structured_signature": json.dumps({"operation_or_relation": name}),
         "structured_signature_hash": f"seed-{name}"}
        for name in POOL if name != desired]
    store.save(session)


def run_creative_harder(sid, target, freshness):
    """Jedan kreativni turn na maksimumu s zadatom presudom o svježini."""
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, sid)
    force_target(store, sid, target)
    before = published_history(store.peek(sid))
    draft = draft_for(target)
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=draft,
        checks=make_reviewer_checks(
            independent_answer=TASKS[target]["expected"],
            matches_target_archetype=True,
            substantially_different_from_recent=freshness)))
    response = run_practice_turn(store, fake, turn(sid, "Daj mi teži zadatak."))
    session = store.peek(sid)
    return {"response": response, "session": session,
            "published": TASKS[target]["text"] in (session.get("current_task") or ""),
            "published_before": before, "published_after": published_history(session),
            "tutor_calls": len(fake.tutor_calls),
            "reviewer_calls": len(fake.reviewer_calls)}


# ===========================================================================
# A/E/F/H — SVJEŽINA OSTAJE BLOKIRAJUĆA
# ===========================================================================

def test_a_cosmetic_reskin_still_fails_closed(universal):
    """Presuda `false` i dalje obara objavu — provjera NIJE savjetodavna."""
    result = run_creative_harder("reskin", "fraction_of_fraction", False)
    assert "status" not in result["response"]
    assert result["published"] is False
    assert result["published_after"] == result["published_before"]
    assert result["session"]["difficulty_level"] == MAX_LEVEL
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


def test_h_missing_freshness_judgement_also_fails_closed(universal):
    """Izostavljena presuda nije tiho odobrenje."""
    result = run_creative_harder("reskin-none", "fraction_remainder", None)
    assert "status" not in result["response"]
    assert result["published_after"] == result["published_before"]


def test_freshness_gate_is_still_wired_as_blocking():
    import inspect
    source = inspect.getsource(tutor_pipeline._two_call)
    assert "substantially_different_from_recent is not True" in source
    assert "creative_escalation_not_varied" in source


# ===========================================================================
# C/G — SERVER OSTAJE VLASNIK IZBORA TIPA
# ===========================================================================

def test_c_target_inside_recent_window_no_longer_contradicts_itself():
    """ŽIVI NEW04: cilj je bio i u „izbjegni ih“ listi. Više nije."""
    block = _flat(esc.prompt_block(decision(
        "fraction_remainder",
        ("fraction_remainder", "fraction_of_fraction", "multi_fraction_remainder"))))
    assert "CILJNI tip zadatka (obavezan): fraction_remainder" in block
    assert "izbjegni ih" not in block                    # nema protivrječja
    assert "NAMJERNA serverska odluka" in block
    assert "DRUGAČIJI PRIMJER" in block


def test_c_fresh_target_keeps_the_avoid_guidance():
    """Kad cilj JESTE svjež, zatečena uputa Tutoru se ne gubi."""
    block = _flat(esc.prompt_block(decision(
        "multi_fraction_remainder",
        ("fraction_of_quantity", "fraction_remainder"))))
    assert "nedavno već viđeni tipovi (izbjegni ih)" in block
    assert "NAMJERNA serverska odluka" not in block


def test_g_planner_still_owns_target_selection():
    """Recenzent ne bira cilj — planer ga bira iz obje historije."""
    published = ("fraction_of_quantity", "fraction_remainder",
                 "fraction_of_fraction")
    assert esc.select_target(POOL, published, ()) == "multi_fraction_remainder"
    # Kad su svi potrošeni, bira se najdavnije objavljen — a NE se odustaje.
    assert esc.select_target(POOL, POOL, ()) in POOL


def test_g_server_may_legally_target_a_recent_archetype():
    """Trotarifni planer smije vratiti nedavno viđen tip kad svježeg nema."""
    published = ("fraction_remainder", "fraction_of_fraction",
                 "multi_fraction_remainder")
    attempted = ("fraction_of_quantity",)
    target = esc.select_target(POOL, published, attempted)
    assert target in published            # cilj JESTE nedavno viđen — legalno
    assert target == "fraction_remainder"  # i to najdavnije objavljen


# ===========================================================================
# §17 — UGOVOR RECENZENTOVOG PROMPTA
# ===========================================================================

def _reviewer_input(target, recent):
    context = lesson_context_module.build(GRADE, LESSON)
    session = {"recent_tasks": [TASKS["fraction_remainder"]["text"]],
               "recent_turns": [], "difficulty": "hard", "difficulty_level": 3,
               "current_task": "", "current_options": [], "lesson_title": TITLE}
    return _flat(tutor_prompts.build_reviewer_input(
        context, session, "Daj mi teži zadatak.", "{}",
        escalation_block=esc.prompt_block(decision(target, recent))))


def test_reviewer_is_told_target_selection_is_server_owned():
    text = _reviewer_input("fraction_remainder",
                           ("fraction_remainder", "fraction_of_fraction"))
    assert "IZBOR CILJNOG TIPA JE SERVERSKI, NE TVOJ" in text


def test_reviewer_is_told_recent_archetype_alone_is_not_enough():
    text = _reviewer_input("fraction_remainder", ("fraction_remainder",))
    assert "nedavno viđen NIJE, sam po sebi, razlog za `false`" in text


def test_reviewer_is_told_to_compare_this_task_with_specific_recent_tasks():
    text = _reviewer_input("fraction_remainder", ("fraction_remainder",))
    assert "sudi SAMO o OVOM zadatku naspram KONKRETNIH nedavnih zadataka" in text
    # Konkretni nedavni zadaci mu STVARNO stižu u ulazu.
    assert "NEDAVNI ZADACI" in text


def test_reviewer_contract_defines_false_as_cosmetic_reskin():
    text = _reviewer_input("fraction_remainder", ("fraction_remainder",))
    assert "isti zadatak u novom ruhu" in text
    assert "isti niz koraka rješavanja" in text
    assert "promijenjeni su samo ime, predmet, priča ili brojevi" in text


def test_reviewer_contract_defines_true_as_materially_different_instance():
    text = _reviewer_input("fraction_remainder", ("fraction_remainder",))
    assert "matematički drugačiji primjer" in text
    assert "pa i kad je tip zadatka isti kao nedavno" in text


def test_reviewer_still_receives_the_target_and_its_meaning():
    text = _reviewer_input("fraction_of_fraction", ("fraction_of_quantity",))
    assert "CILJNI tip zadatka (obavezan): fraction_of_fraction" in text
    assert "matches_target_archetype" in text


# ===========================================================================
# B/D — ŠIROKA TEMA NIJE ISTI ZADATAK
# ===========================================================================

def test_b_different_archetype_is_never_described_as_the_same_task():
    """Dva različita tipa se ne smiju odbiti zato što su „oba razlomci“."""
    text = _reviewer_input("fraction_of_fraction",
                           ("fraction_of_quantity", "fraction_remainder"))
    # Kriterij je PUT DO RJEŠENJA, ne tema.
    assert "isti niz koraka rješavanja" in text
    assert "druge veličine i odnosi" in text


def test_d_same_archetype_can_still_be_judged_fresh(universal):
    """Isti tip + `true` → objavljuje se. Tip sam po sebi ne obara paket."""
    result = run_creative_harder("same-arch", "fraction_remainder", True)
    assert result["response"]["status"] == "ready"
    assert result["published"] is True
    assert result["published_after"][-1] == "fraction_remainder"
    assert result["session"]["difficulty_level"] == MAX_LEVEL
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


# ===========================================================================
# I/J — SIGURNOST KREATIVNE RUTE NEPROMIJENJENA
# ===========================================================================

def test_i_creative_approve_publishes_canonical_tutor_draft(universal):
    result = run_creative_harder("approve", "fraction_of_fraction", True)
    assert result["response"]["status"] == "ready"
    assert result["published_after"][-1] == "fraction_of_fraction"


def test_j_creative_correct_still_fails_closed(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "correct")
    force_target(store, "correct", "fraction_of_fraction")
    before = published_history(store.peek("correct"))
    draft = draft_for("fraction_of_fraction")
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="correct", final=draft,
        checks=make_reviewer_checks(
            independent_answer="$18$", matches_target_archetype=True,
            substantially_different_from_recent=True)))
    response = run_practice_turn(store, fake, turn("correct", "Daj mi teži zadatak."))
    session = store.peek("correct")
    assert "status" not in response
    assert TASKS["fraction_of_fraction"]["text"] not in (session.get("current_task") or "")
    assert published_history(session) == before
    assert response["answer"] == tutor_pipeline.QUALITY_REJECTION_MESSAGE
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1


# ===========================================================================
# K/L/M — RUTIRANJE SE NE DIRA
# ===========================================================================

def test_k_new_at_max_is_still_deterministic_and_zero_call(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "routing-new")
    before = store.peek("routing-new")["current_task"]
    assert run_practice_turn(store, fake, turn("routing-new", "Daj mi novi zadatak.")
                             )["status"] == "ready"
    session = store.peek("routing-new")
    assert session["difficulty_level"] == MAX_LEVEL
    assert session["current_task"] != before
    assert fake.call_count == 0


def test_l_harder_at_max_is_still_creative():
    from matbot import difficulty_level
    from matbot.semantics import contracts as contracts_module

    context = type("C", (), {
        "topic_id": LESSON,
        "semantic_contract": contracts_module.contract_for(LESSON)})()
    result = esc.decide(context, {"difficulty_level": MAX_LEVEL}, "harder_task",
                        difficulty_level.transition(MAX_LEVEL, "harder"))
    assert result is not None and result.reason == esc.REASON_MAX_LEVEL_HARDER


def test_m_explicit_variety_is_still_creative():
    from matbot import difficulty_level
    from matbot.semantics import contracts as contracts_module

    context = type("C", (), {
        "topic_id": LESSON,
        "semantic_contract": contracts_module.contract_for(LESSON)})()
    result = esc.decide(context, {"difficulty_level": 2}, "",
                        difficulty_level.transition(2, ""), explicit_variety=True)
    assert result is not None and result.reason == esc.REASON_EXPLICIT_VARIETY
