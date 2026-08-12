"""Pilot ograničene AI-eskalacije zbog RAZNOLIKOSTI (6-04-015).

ŽIVI QA NALAZ: u lekciji „Tekstualni zadaci s razlomcima“ traženje sve težih
zadataka vraća isti matematički arhetip s drugim imenom/predmetom/brojevima.
Izmjereno: 3600 determinističkih paketa → 2960 različitih tekstova, ali samo
12 rečeničnih kostura i 2 strukturna arhetipa, ista na sva tri nivoa.

Ugovor pilota:
  • obična progresija ostaje deterministička i NULA poziva;
  • model se uključuje tek kao eskalacija (teže na maksimumu ili izričit
    zahtjev za drugim tipom);
  • najviše dva poziva, bez trećeg i bez ponovnog generisanja;
  • nivo ostaje 3 — eskalacija je promjena RUTE, ne četvrti nivo.
"""
import json
import random
import re
from pathlib import Path

import pytest

from matbot.tutor import creative_escalation as esc
from matbot.tutor import lesson_context, pipeline as tutor_pipeline, prompts
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_difficulty_diagnostics,
                            make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

ROOT = Path(__file__).resolve().parent.parent

LESSON = "6-04-015"

def _contract_enum():
    """Enum se čita iz UGOVORA — test ne smije zaostati za podacima."""
    from matbot.semantics import contracts as _contracts
    return tuple(dict(_contracts.contract_for(LESSON).parameters)["problem_types"])

TITLE = "Tekstualni zadaci s razlomcima"
GRADE = 6

# Kontrolne lekcije koje pilot NE SMIJE dotaći.
DETERMINISTIC_CONTROL = ("6-04-003", 6)     # klasifikacija razlomaka
MODEL_CONTROL = ("9-02-006", 9)             # lekcija bez determinističkog puta


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def turn(session_id, message, topic=LESSON, grade=GRADE, **changes):
    payload = {
        "session_id": session_id, "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def archetypes_of(session, lesson_id=LESSON):
    out = []
    for record in session.get("recent_task_signatures") or []:
        if record.get("lesson_id") != lesson_id:
            continue
        out.append(json.loads(record["structured_signature"])
                   .get("operation_or_relation"))
    return out


# ---------------------------------------------------------------------------
# 1) SERVER JE VLASNIK ODLUKE I CILJA
# ---------------------------------------------------------------------------

def test_pilot_is_enabled_by_contract_data_on_exactly_one_lesson():
    """Uključivanje je PODATAK, ne ID lekcije u kodu — i važi za tačno jednu."""
    assert esc.is_pilot_lesson(lesson_context.build(GRADE, LESSON))
    assert not esc.is_pilot_lesson(
        lesson_context.build(DETERMINISTIC_CONTROL[1], DETERMINISTIC_CONTROL[0]))
    assert not esc.is_pilot_lesson(
        lesson_context.build(MODEL_CONTROL[1], MODEL_CONTROL[0]))

    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(
            encoding="utf-8"))["lessons"]
    enabled = sorted(lesson_id for lesson_id, entry in compiled.items()
                     if entry["parameters"].get("creative_escalation") == "enabled")
    assert enabled == [LESSON], enabled


def test_same_family_siblings_are_untouched():
    """Osam ostalih lekcija ISTE porodice ostaje van pilota."""
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(
            encoding="utf-8"))["lessons"]
    siblings = [lesson_id for lesson_id, entry in compiled.items()
                if entry["family_id"] == "structured_word_problem"
                and lesson_id != LESSON]
    assert len(siblings) >= 5
    for lesson_id in siblings:
        assert "creative_escalation" not in compiled[lesson_id]["parameters"]


def test_no_lesson_id_is_hard_coded_in_the_escalation_module():
    source = (ROOT / "matbot" / "tutor" / "creative_escalation.py").read_text(
        encoding="utf-8")
    assert not re.search(r"\d-\d{2}-\d{3}", source)


def test_supported_archetypes_come_from_the_lesson_contract():
    context = lesson_context.build(GRADE, LESSON)
    contract = context.semantic_contract
    # Enum raste s kurikularnim proširenjima — test čita UGOVOR, ne konstantu.
    assert tuple(dict(contract.parameters)["problem_types"]) == _contract_enum()
    assert len(_contract_enum()) >= 2


@pytest.mark.parametrize("recent,expected", [
    ((), "fraction_of_quantity"),
    (("fraction_of_quantity",), "fraction_remainder"),
    (("fraction_remainder",), "fraction_of_quantity"),
    (("fraction_of_quantity", "fraction_of_quantity"), "fraction_remainder"),
    # sve viđeno → najdavnije viđeni
    (("fraction_remainder", "fraction_of_quantity"), "fraction_remainder"),
    (("fraction_of_quantity", "fraction_remainder"), "fraction_of_quantity"),
])
def test_target_selection_avoids_recent_archetypes(recent, expected):
    supported = ("fraction_of_quantity", "fraction_remainder")
    assert esc.select_target(supported, recent) == expected


def test_selection_is_server_owned_arithmetic_not_a_model_judgement():
    """Planer ne smije nikad pozvati model — čista funkcija nad enumom."""
    assert esc.select_target((), ()) == ""
    assert esc.select_target(("only_one",), ("only_one",)) == "only_one"


# ---------------------------------------------------------------------------
# 2) OKIDAČI — kada da, kada ne
# ---------------------------------------------------------------------------

def _decision(session, intent, level, message=""):
    from matbot import difficulty_level
    context = lesson_context.build(GRADE, LESSON)
    session = {"difficulty_level": level, **session}
    transition = difficulty_level.transition(
        level, "harder" if intent == "harder_task" else "")
    return esc.decide(context, session, intent, transition,
                      explicit_variety=bool(message))


@pytest.mark.parametrize("intent,level", [
    ("generate_task", 1), ("next_task", 1),
    ("harder_task", 1), ("harder_task", 2),
    ("easier_task", 3), ("easier_task", 2),
])
def test_ordinary_progression_never_escalates(intent, level):
    assert _decision({"recent_task_signatures": []}, intent, level) is None


def test_harder_at_max_escalates_and_keeps_level_three():
    decision = _decision({"recent_task_signatures": []}, "harder_task", 3)
    assert decision is not None
    assert decision.reason == esc.REASON_MAX_LEVEL_HARDER
    assert decision.level == 3           # nikad 4


def test_explicit_variety_escalates_at_any_level():
    decision = _decision({"recent_task_signatures": []}, "", 1, message="x")
    assert decision is not None
    assert decision.reason == esc.REASON_EXPLICIT_VARIETY


@pytest.mark.parametrize("message,expected", [
    ("Daj mi drugačiji zadatak.", True),
    ("Daj neki drugi tip zadatka.", True),
    ("Daj mi nešto drugačije.", True),
    ("Nemoj opet isti fazon.", True),
    ("Daj mi zadatak.", False),
    ("Daj mi teži zadatak.", False),
    ("Daj mi lakši zadatak.", False),
])
def test_explicit_variety_vocabulary(message, expected):
    assert tutor_pipeline._explicit_variety_request(
        {"intent": "", "difficulty_request": "", "student_message": message}
    ) is expected


def test_ui_difficulty_button_is_never_a_variety_request():
    assert tutor_pipeline._explicit_variety_request(
        {"intent": "", "difficulty_request": "harder",
         "student_message": "Daj mi drugačiji zadatak."}) is False


# ---------------------------------------------------------------------------
# 3) OBIČNA PROGRESIJA OSTAJE NULA-POZIVA
# ---------------------------------------------------------------------------

def test_ordinary_progression_makes_zero_model_calls(universal):
    store, fake = SessionStore(), FakeLLM()
    levels = []
    for message in ["Daj mi zadatak.", "Daj mi teži zadatak.",
                    "Daj mi teži zadatak."]:
        response = run_practice_turn(store, fake, turn("zero", message))
        assert response["status"] == "ready"
        levels.append(store.peek("zero")["difficulty_level"])
    assert levels == [1, 2, 3]
    assert fake.call_count == 0


@pytest.mark.parametrize("lesson_id,grade", [DETERMINISTIC_CONTROL])
def test_control_lesson_never_escalates(universal, lesson_id, grade):
    """Kontrola: druga deterministička lekcija ostaje nula-poziva i na
    četvrtom „teže“ i na izričitom zahtjevu za drugim tipom."""
    store, fake = SessionStore(), FakeLLM()
    for message in ["Daj mi zadatak.", "Daj mi teži zadatak.",
                    "Daj mi teži zadatak.", "Daj mi teži zadatak."]:
        response = run_practice_turn(store, fake, turn(
            "ctl", message, topic=lesson_id, grade=grade))
        assert response["status"] == "ready"
    assert fake.call_count == 0


# ---------------------------------------------------------------------------
# 4) LAŽNI MODEL — kozmetički preslikan isti tip
# ---------------------------------------------------------------------------

_RESKIN_TEXT = ("Vedad ima $45$ bombona i pokloni $\\frac{1}{3}$ od toga. "
                "Koliko bombona je poklonjeno?")
_DIFFERENT_TEXT = ("Lejla je potrošila $\\frac{2}{5}$ od svojih $40$ olovaka. "
                   "Koliko olovaka joj je OSTALO?")


def _level3_evidence():
    """Iskren dokaz nivoa 3 za dvokoračni zadatak (pomnoži, pa oduzmi)."""
    from matbot.tutor.schema import DifficultyEvidence
    return DifficultyEvidence(
        reasoning_steps=3, condition_count=2, operation_count=3,
        representation_change_count=1, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


def _draft(text, expected, options, solution,
           archetype="fraction_remainder"):
    """`archetype` je OZNAKA koju Tutor upisuje u potpis.

    Od uvođenja serverske provjere ciljnog arhetipa oznaka mora biti baš enum
    koji je server izabrao — ranija fixture-vrijednost („fixture_operation“)
    danas s pravom pada prije objave."""
    payload = make_task_payload(text=text, options=options, expected=expected,
                                solution=solution, difficulty="hard")
    payload = payload.model_copy(update={
        "target_difficulty_level": 3,
        "difficulty_evidence": _level3_evidence(),
        "task_signature": payload.task_signature.model_copy(update={
            "operation_or_relation": archetype}),
    })
    return make_tutor_draft(
        intent="harder_task", reply="Evo zadatka.",
        lesson_focus="tekstualni zadaci s razlomcima",
        difficulty_diagnostics=make_difficulty_diagnostics(direction="higher"),
        new_task=payload)


SUPPORTED = _contract_enum()


def _warm_up(fake, store, session_id="esc"):
    """Deterministički do maksimuma; vrati arhetip koji će server ZATRAŽITI.

    Cilj se ne smije pretpostaviti — zavisi od stvarne historije sesije."""
    for message in ["Daj mi zadatak.", "Daj mi teži zadatak.",
                    "Daj mi teži zadatak."]:
        assert run_practice_turn(store, fake, turn(session_id, message)
                                 )["status"] == "ready"
    assert fake.call_count == 0
    recent = esc.recent_archetypes(store.peek(session_id), LESSON,
                                   supported=SUPPORTED)
    return esc.select_target(SUPPORTED, recent)


def _escalate(fake, store, session_id="esc"):
    return run_practice_turn(store, fake, turn(session_id, "Daj mi teži zadatak."))


def _run_escalation(fake, store, session_id="esc"):
    """Zatečeni oblik: zagrij pa eskaliraj (nacrt je već u redu čekanja)."""
    _warm_up(fake, store, session_id)
    return _escalate(fake, store, session_id)


def test_cosmetic_reskin_is_rejected_without_a_third_call(universal):
    store, fake = SessionStore(), FakeLLM()
    # Oznaka MORA biti serverski cilj — inače paket padne već na determinističkoj
    # kapiji arhetipa i nikad ne stigne do presude o RAZNOLIKOSTI, koju ovaj
    # test upravo ispituje.
    target = _warm_up(fake, store)
    draft = _draft(_RESKIN_TEXT, "$15$", ("$15$", "$30$", "$45$", "$12$"),
                   "$\\frac{1}{3} \\cdot 45 = 15$, pa je poklonjeno $15$ bombona.",
                   archetype=target)
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=draft,
        checks=make_reviewer_checks(
            independent_answer="$15$",
            matches_target_archetype=True,
            substantially_different_from_recent=False)))

    response = _escalate(fake, store)

    # `_error_response` po ugovoru NEMA 'status' — frontend tada čuva stanje.
    assert "status" not in response
    assert response["task_preserved"] is True
    session = store.peek("esc")
    # paket NIJE objavljen — aktivni zadatak je ostao onaj deterministički
    assert _RESKIN_TEXT not in (session["current_task"] or "")
    assert fake.call_count == 2          # Tutor + Recenzent, bez trećeg
    assert session["difficulty_level"] == 3


def test_genuinely_different_archetype_is_published_in_two_calls(universal):
    store, fake = SessionStore(), FakeLLM()
    target = _warm_up(fake, store)
    draft = _draft(_DIFFERENT_TEXT, "$24$", ("$24$", "$16$", "$40$", "$8$"),
                   "Potrošeno je $\\frac{2}{5} \\cdot 40 = 16$, pa je ostalo "
                   "$40 - 16 = 24$ olovaka.", archetype=target)
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=draft,
        checks=make_reviewer_checks(
            independent_answer="$24$",
            matches_target_archetype=True,
            substantially_different_from_recent=True)))

    response = _escalate(fake, store)
    session = store.peek("esc")

    assert response["status"] == "ready"
    assert _DIFFERENT_TEXT in session["current_task"]
    assert response["effective_topic"] == LESSON
    assert session["difficulty_level"] == 3       # ostaje maksimum, nikad 4
    assert fake.call_count == 2


# ---------------------------------------------------------------------------
# 5) PROMPT UGOVOR — Tutor i Recenzent vide ISTE serverske činjenice
# ---------------------------------------------------------------------------

def test_escalation_block_carries_the_server_owned_facts():
    decision = esc.CreativeEscalationDecision(
        reason=esc.REASON_MAX_LEVEL_HARDER,
        target_archetype="fraction_remainder",
        supported_archetypes=("fraction_of_quantity", "fraction_remainder"),
        recent_archetypes=("fraction_of_quantity", "fraction_of_quantity"),
        level=3)
    block = esc.prompt_block(decision)
    assert "fraction_remainder" in block            # ciljni arhetip
    assert "fraction_of_quantity" in block          # nedavni, izbjegni
    assert "OSTAJE 3" in block                      # nivo se ne diže
    assert "SUŠTINSKI drugačije" in block
    assert "STROGO unutar izabrane lekcije" in block


def test_both_prompts_receive_the_same_escalation_block():
    context = lesson_context.build(GRADE, LESSON)
    session = {"current_task": "", "recent_turns": [], "difficulty_level": 3,
               "hint_level": 0, "wrong_option_ids": [], "last_result": "",
               "recent_tasks": [], "correct_streak": 0, "retry_required": False,
               "current_options": [], "task_completed": False,
               "current_family": "", "correctly_completed_families": [],
               "difficulty": "hard", "current_task_had_hint": False}
    block = esc.prompt_block(esc.CreativeEscalationDecision(
        reason=esc.REASON_EXPLICIT_VARIETY, target_archetype="fraction_remainder",
        supported_archetypes=("fraction_of_quantity", "fraction_remainder"),
        recent_archetypes=("fraction_of_quantity",), level=3))
    tutor_input = prompts.build_tutor_input(
        context, session, "Daj mi drugačiji zadatak.", escalation_block=block)
    reviewer_input = prompts.build_reviewer_input(
        context, session, "Daj mi drugačiji zadatak.", "{}",
        escalation_block=block)
    assert block in tutor_input
    assert block in reviewer_input
    assert "substantially_different_from_recent" in reviewer_input


def test_ordinary_turn_prompt_is_unchanged_without_escalation():
    context = lesson_context.build(GRADE, LESSON)
    session = {"current_task": "", "recent_turns": [], "difficulty_level": 1,
               "hint_level": 0, "wrong_option_ids": [], "last_result": "",
               "recent_tasks": [], "correct_streak": 0, "retry_required": False,
               "current_options": [], "task_completed": False,
               "current_family": "", "correctly_completed_families": [],
               "difficulty": "easy", "current_task_had_hint": False}
    with_block = prompts.build_tutor_input(context, session, "Daj mi zadatak.",
                                           escalation_block="")
    legacy = prompts.build_tutor_input(context, session, "Daj mi zadatak.")
    assert with_block == legacy
    assert "ZAHTJEV ZA RAZNOLIKOŠĆU" not in legacy


# ---------------------------------------------------------------------------
# 6) TEŽINA I HISTORIJA NISU OŠTEĆENE
# ---------------------------------------------------------------------------

def test_easier_after_escalation_returns_to_deterministic_level_two(universal):
    store, fake = SessionStore(), FakeLLM()
    target = _warm_up(fake, store)
    draft = _draft(_DIFFERENT_TEXT, "$24$", ("$24$", "$16$", "$40$", "$8$"),
                   "Potrošeno je $\\frac{2}{5} \\cdot 40 = 16$, pa je ostalo "
                   "$40 - 16 = 24$ olovaka.", archetype=target)
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=draft,
        checks=make_reviewer_checks(independent_answer="$24$",
                                    matches_target_archetype=True,
            substantially_different_from_recent=True)))
    assert _escalate(fake, store)["status"] == "ready"
    assert store.peek("esc")["difficulty_level"] == 3
    calls_after_escalation = fake.call_count

    response = run_practice_turn(store, fake, turn("esc", "Daj mi lakši zadatak."))
    session = store.peek("esc")
    assert response["status"] == "ready"
    assert session["difficulty_level"] == 2
    assert session["deterministic_task"] is not None      # opet deterministički
    assert fake.call_count == calls_after_escalation      # bez novih poziva


def test_recent_archetype_history_is_read_from_existing_session_state():
    """Pilot ne uvodi novo stanje sesije — čita postojeće potpise."""
    session = {"recent_task_signatures": [
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": "fraction_of_quantity"})},
        {"lesson_id": "6-03-010", "structured_signature": json.dumps(
            {"operation_or_relation": "equal_sharing"})},
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": "fraction_remainder"})},
    ]}
    assert esc.recent_archetypes(session, LESSON) == (
        "fraction_of_quantity", "fraction_remainder")


# ---------------------------------------------------------------------------
# 7) OFFLINE EVALUACIJA RAZNOLIKOSTI — planer ne smije praviti izbježive ponove
# ---------------------------------------------------------------------------

def test_planner_never_produces_an_avoidable_immediate_repeat():
    supported = ("fraction_of_quantity", "fraction_remainder")
    recent, avoidable = [], 0
    for _ in range(50):
        target = esc.select_target(supported, tuple(recent[-esc.RECENT_WINDOW:]))
        if recent and target == recent[-1] and len(supported) > 1:
            avoidable += 1
        recent.append(target)
    assert avoidable == 0, recent[:10]


def test_decide_wires_session_history_into_target_selection():
    """Kraj-do-kraja: `decide` MORA čitati historiju sesije, ne samo je imati.

    Bez ovog testa uklanjanje historije iz `decide` prolazi neprimijećeno —
    `select_target` bi i dalje bio ispravan, a planer bi ipak vraćao isti
    arhetip svaki put."""
    from matbot import difficulty_level
    context = lesson_context.build(GRADE, LESSON)
    session = {"difficulty_level": 3, "recent_task_signatures": []}
    chosen, avoidable = [], 0
    for _ in range(12):
        decision = esc.decide(
            context, session, "harder_task",
            difficulty_level.transition(3, "harder"))
        assert decision is not None
        if chosen and decision.target_archetype == chosen[-1]:
            avoidable += 1
        chosen.append(decision.target_archetype)
        # objava bi upisala potpis — simuliraj postojeći serverski zapis
        session["recent_task_signatures"].append({
            "lesson_id": LESSON,
            "structured_signature": json.dumps(
                {"operation_or_relation": decision.target_archetype})})
    assert avoidable == 0, chosen
    assert set(chosen) == set(SUPPORTED), chosen


def test_single_archetype_lesson_does_not_claim_false_variety():
    decision = esc.CreativeEscalationDecision(
        reason=esc.REASON_EXPLICIT_VARIETY, target_archetype="only_one",
        supported_archetypes=("only_one",), recent_archetypes=("only_one",),
        level=3)
    assert decision.diversity_possible is False
    assert esc.reviewer_requires_variety(decision) is False
    assert "samo jedan dozvoljen tip" in esc.prompt_block(decision)
