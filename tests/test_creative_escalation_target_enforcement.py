"""Ciljni arhetip je SERVERSKA taksonomija — model je ne smije redefinisati.

ŽIVI NALAZ (ciljana živa kampanja, kandidat 895e912): server je izabrao
`fraction_remainder`, Tutor je vratio paket čiji je
`task_signature.operation_or_relation` glasio „successive subtraction of
fractions of an initial quantity“, recenzent ga je odobrio — i paket je
OBJAVLJEN. Ta slobodna vrijednost je zatim upisana u `recent_task_signatures`
i zauzela mjesto u prozoru historije, čime je degradirala buduće odluke
planera. Matematika je slučajno bila tačna; ugovor objave nije.

Ovi testovi zaključavaju tri odvojene tvrdnje:
  1. vrijednost mora pripadati enumu LEKCIJE (nikad slobodan tekst);
  2. i to baš arhetipu koji je SERVER izabrao (nikad drugi dozvoljeni);
  3. i recenzent mora potvrditi da mu STVARNA struktura zadatka odgovara —
     jer tačna oznaka ne dokazuje tačnu matematiku.
Prve dvije su determinističke i recenzent ih ne može nadjačati.
"""
import io
import json
import tokenize
from pathlib import Path

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import creative_escalation as esc
from matbot.tutor import lesson_context, prompts
from matbot.tutor.schema import DifficultyEvidence
from tests.conftest import (FakeLLM, make_difficulty_diagnostics,
                            make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

ROOT = Path(__file__).resolve().parent.parent

LESSON = "6-04-015"
GRADE = 6
SUPPORTED = ()   # popunjeno iz ugovora ispod
FREE_TEXT = "successive subtraction of fractions of an initial quantity"

def _contract_enum():
    """Enum se čita iz UGOVORA — test ne smije zaostati za podacima."""
    from matbot.semantics import contracts as _contracts
    return tuple(dict(_contracts.contract_for(LESSON).parameters)["problem_types"])

SUPPORTED = _contract_enum()

# Po jedan zadatak koji STVARNO jeste dati arhetip. Server bira cilj, pa test
# nudi zadatak koji tom cilju odgovara umjesto da arhetip pretpostavi.
TASKS = {
    "fraction_remainder": {
        "archetype": "fraction_remainder",
        "text": ("Lejla je potrošila $\\frac{2}{5}$ od svojih $40$ olovaka. "
                 "Koliko olovaka joj je OSTALO?"),
        "options": ("$24$", "$16$", "$40$", "$8$"),
        "correct_index": 0,
        "expected": "$24$",
        "solution": ("Potrošeno je $\\frac{2}{5} \\cdot 40 = 16$, pa je "
                     "ostalo $40 - 16 = 24$ olovaka."),
    },
    "fraction_of_quantity": {
        "archetype": "fraction_of_quantity",
        "text": ("Lejla ima $40$ olovaka i pokloni $\\frac{2}{5}$ od toga. "
                 "Koliko olovaka je poklonjeno?"),
        "options": ("$16$", "$24$", "$40$", "$8$"),
        "correct_index": 0,
        "expected": "$16$",
        "solution": ("$\\frac{2}{5} \\cdot 40 = 16$, pa je poklonjeno "
                     "$16$ olovaka."),
    },
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


def history(session):
    out = []
    for record in session.get("recent_task_signatures") or []:
        if record.get("lesson_id") != LESSON:
            continue
        out.append(json.loads(record["structured_signature"])
                   .get("operation_or_relation"))
    return out


# Od uvođenja egzaktne serverske provjere odgovora, kreativni paket mora nositi
# IR veličine u potpisu. Nacrt se veže na STVARNU lekciju jer `FakeLLM` svojim
# „__fixture__“ drafovima prepisuje `normalized_parameters`.
FACTS = {'fraction_remainder': {'type': 'fraction_remainder', 'total': '40', 'fraction': '2/5'}, 'fraction_of_quantity': {'type': 'fraction_of_quantity', 'total': '40', 'fraction': '2/5'}, 'fraction_of_fraction': {'type': 'fraction_of_fraction', 'total': '48', 'first_fraction': '2/3', 'second_fraction': '1/4'}, 'multi_fraction_remainder': {'type': 'multi_fraction_remainder', 'total': '24', 'fraction_1': '1/3', 'fraction_2': '1/4', 'fraction_3': '1/6'}}


_LEVEL_EVIDENCE = {
    1: DifficultyEvidence(
        reasoning_steps=1, condition_count=1, operation_count=1,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False),
    3: DifficultyEvidence(
        reasoning_steps=3, condition_count=2, operation_count=3,
        representation_change_count=1, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False),
}


def draft_with(operation, task, level=3, intent="harder_task"):
    payload = make_task_payload(
        text=task["text"], options=task["options"],
        correct_option_index=task["correct_index"], expected=task["expected"],
        solution=task["solution"],
        difficulty={1: "easy", 2: "standard", 3: "hard"}[level])
    from matbot.tutor.schema import SignatureParameter
    facts = FACTS[task["archetype"]]
    signature = payload.task_signature.model_copy(update={
        "operation_or_relation": operation,
        "normalized_parameters": [SignatureParameter(name=n, value=v)
                                  for n, v in facts.items()]})
    payload = payload.model_copy(update={
        "selected_lesson_id": LESSON,
        "selected_lesson_title": "Tekstualni zadaci s razlomcima",
        "target_difficulty_level": level,
        "task_signature": signature,
        "difficulty_evidence": _LEVEL_EVIDENCE[level],
    })
    return make_tutor_draft(
        intent=intent, reply="Evo zadatka.",
        lesson_focus="tekstualni zadaci s razlomcima",
        difficulty_diagnostics=(make_difficulty_diagnostics(direction="higher")
                                if intent == "harder_task" else None),
        new_task=payload)


def warm_up_to_max(store, fake, session_id):
    """Tri deterministička turna do nivoa 3 — nula poziva."""
    for message in ["Daj mi zadatak.", "Daj mi teži zadatak.",
                    "Daj mi teži zadatak."]:
        assert run_practice_turn(store, fake, turn(session_id, message)
                                 )["status"] == "ready"
    assert fake.call_count == 0
    assert store.peek(session_id)["difficulty_level"] == 3


def escalate(store, fake, session_id):
    return run_practice_turn(store, fake, turn(session_id, "Daj mi teži zadatak."))


def server_target(store, session_id):
    """Cilj koji će server STVARNO izabrati — nikad pretpostavka testa."""
    session = store.peek(session_id)
    recent = esc.recent_archetypes(session, LESSON, supported=SUPPORTED)
    return esc.select_target(SUPPORTED, recent)


def _seed_for_available_task(store, session_id):
    """Postavi historiju tako da cilj bude arhetip za koji suite ima zadatak."""
    desired = next(name for name in SUPPORTED if name in TASKS)
    session = store.peek(session_id)
    session["recent_task_signatures"] = [
        {"lesson_id": LESSON,
         "structured_signature": json.dumps({"operation_or_relation": name}),
         "structured_signature_hash": f"seed-{name}"}
        for name in SUPPORTED if name != desired]
    store.save(session)
    return desired


def other_than(target):
    return next(name for name in SUPPORTED if name != target)


def run_case(session_id, label=None, **checks_overrides):
    """Zagrij do maksimuma, pa pošalji JEDAN pripremljen kreativni odgovor.

    `label` je ono što Tutor upiše u potpis; None znači „upiši baš serverski
    cilj“. Sam ZADATAK uvijek odgovara serverskom cilju, pa se mjeri isključivo
    ponašanje oznake i recenzentovih presuda."""
    store, fake = SessionStore(), FakeLLM()
    warm_up_to_max(store, fake, session_id)
    # Ova matrica ispituje ponašanje OZNAKE i recenzentovih presuda, što je
    # nezavisno od toga koji je arhetip cilj. Historija se zato postavlja tako
    # da server izabere jedan od dva arhetipa za koje suite ima zadatak — cilj
    # i dalje računa `select_target`, ne test.
    desired = next(name for name in SUPPORTED if name in TASKS)
    session = store.peek(session_id)
    session["recent_task_signatures"] = [
        {"lesson_id": LESSON,
         "structured_signature": json.dumps({"operation_or_relation": name}),
         "structured_signature_hash": f"seed-{name}"}
        for name in SUPPORTED if name != desired]
    store.save(session)
    target = server_target(store, session_id)
    assert target == desired, (target, desired)
    before = history(store.peek(session_id))
    task = TASKS[target]
    if label is None:
        label = target
    elif label == "__other__":
        label = other_than(target)
    draft = draft_with(label, task)
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=draft,
        checks=make_reviewer_checks(independent_answer=task["expected"],
                                    **checks_overrides)))
    response = escalate(store, fake, session_id)
    session = store.peek(session_id)
    return {
        "response": response,
        "target": target,
        "label": label,
        "published": task["text"] in (session.get("current_task") or ""),
        "history_before": before,
        "history_after": history(session),
        "tutor_calls": len(fake.tutor_calls),
        "reviewer_calls": len(fake.reviewer_calls),
        "level": session.get("difficulty_level"),
    }


# ---------------------------------------------------------------------------
# CASE A — tačan enum, tačan cilj, tačna semantika → OBJAVLJUJE SE
# ---------------------------------------------------------------------------

def test_case_a_valid_target_publishes(universal):
    result = run_case("case-a", matches_target_archetype=True,
                      substantially_different_from_recent=True)
    assert result["published"] is True
    assert result["response"]["status"] == "ready"
    assert result["history_after"][-1] == result["target"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1
    assert result["level"] == 3


# ---------------------------------------------------------------------------
# CASE B — slobodan tekst (TAČAN ŽIVI DEFEKT) → pada PRIJE recenzenta
# ---------------------------------------------------------------------------

def test_case_b_free_text_archetype_is_rejected_before_the_reviewer(universal):
    result = run_case("case-b", label=FREE_TEXT,
                      matches_target_archetype=True,
                      substantially_different_from_recent=True)
    assert result["published"] is False
    assert "status" not in result["response"]          # sigurna poruka
    assert result["history_after"] == result["history_before"]
    assert FREE_TEXT not in result["history_after"]
    # Paket koji ionako ne može biti objavljen ne troši drugi poziv.
    assert result["tutor_calls"] == 1
    assert result["reviewer_calls"] == 0


@pytest.mark.parametrize("label", [
    "hard fraction task", "word problem", "razlomci", "",
])
def test_case_b_any_invented_label_is_rejected(universal, label):
    result = run_case(f"case-b-{label or 'empty'}", label=label,
                      matches_target_archetype=True,
                      substantially_different_from_recent=True)
    assert result["published"] is False
    assert result["history_after"] == result["history_before"]
    assert result["reviewer_calls"] == 0


# ---------------------------------------------------------------------------
# CASE C — dozvoljen enum, ali NIJE izabrani cilj → pada PRIJE recenzenta
# ---------------------------------------------------------------------------

def test_case_c_wrong_allowed_enum_is_rejected(universal):
    result = run_case("case-c", label="__other__",
                      matches_target_archetype=True,
                      substantially_different_from_recent=True)
    assert result["label"] != result["target"]
    assert result["label"] in SUPPORTED               # jeste dozvoljen enum…
    assert result["published"] is False               # …ali nije traženi
    assert result["history_after"] == result["history_before"]
    assert result["tutor_calls"] == 1
    assert result["reviewer_calls"] == 0


# ---------------------------------------------------------------------------
# CASE D — oznaka tačna, SEMANTIKA pogrešna → recenzent obara
# ---------------------------------------------------------------------------

def test_case_d_correct_label_wrong_semantics_is_rejected(universal):
    result = run_case("case-d", matches_target_archetype=False,
                      substantially_different_from_recent=True)
    assert result["published"] is False
    assert result["history_after"] == result["history_before"]
    # Drugi poziv se MORA potrošiti — semantiku vidi samo recenzent.
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


def test_case_d_missing_judgement_also_fails_closed(universal):
    """Izostavljena presuda nije tiho odobrenje."""
    result = run_case("case-d-none", substantially_different_from_recent=True)
    assert result["published"] is False
    assert result["reviewer_calls"] == 1


# ---------------------------------------------------------------------------
# CASE E — tačan cilj, ali kozmetički preslikan → recenzent obara
# ---------------------------------------------------------------------------

def test_case_e_cosmetic_reskin_is_rejected(universal):
    result = run_case("case-e", matches_target_archetype=True,
                      substantially_different_from_recent=False)
    assert result["published"] is False
    assert result["history_after"] == result["history_before"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


def test_no_case_ever_makes_a_third_call(universal):
    cases = [
        ("a", None, {"matches_target_archetype": True,
                     "substantially_different_from_recent": True}),
        ("b", FREE_TEXT, {"matches_target_archetype": True,
                          "substantially_different_from_recent": True}),
        ("c", "__other__", {"matches_target_archetype": True,
                            "substantially_different_from_recent": True}),
        ("d", None, {"matches_target_archetype": False,
                     "substantially_different_from_recent": True}),
        ("e", None, {"matches_target_archetype": True,
                     "substantially_different_from_recent": False}),
    ]
    for name, label, overrides in cases:
        result = run_case(f"third-{name}", label=label, **overrides)
        assert result["tutor_calls"] <= 1, name
        assert result["reviewer_calls"] <= 1, name
        assert result["tutor_calls"] + result["reviewer_calls"] <= 2, name


# ---------------------------------------------------------------------------
# CASE F — zatečena ZAGAĐENA historija se filtrira PRI ČITANJU
# ---------------------------------------------------------------------------

def test_case_f_polluted_history_is_filtered_at_read_time():
    session = {"recent_task_signatures": [
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": "fraction_remainder"})},
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": FREE_TEXT})},
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": "fraction_of_quantity"})},
        {"lesson_id": LESSON, "structured_signature": "{ nije json"},
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": "hard fraction task"})},
    ]}
    recent = esc.recent_archetypes(session, LESSON, supported=SUPPORTED)
    assert recent == ("fraction_remainder", "fraction_of_quantity")
    # Cilj je prvi PODRŽANI koji nije nedavno viđen — raste s enumom, pa se
    # računa iz ugovora umjesto da se upiše kao konstanta.
    assert esc.select_target(SUPPORTED, recent) == next(
        name for name in SUPPORTED if name not in recent)


def test_decide_reads_history_through_the_contract_filter():
    from matbot import difficulty_level
    context = lesson_context.build(GRADE, LESSON)
    session = {"difficulty_level": 3, "recent_task_signatures": [
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": FREE_TEXT})},
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": "fraction_of_quantity"})},
    ]}
    decision = esc.decide(context, session, "harder_task",
                          difficulty_level.transition(3, "harder"))
    assert decision is not None
    assert FREE_TEXT not in decision.recent_archetypes
    assert decision.recent_archetypes == ("fraction_of_quantity",)
    assert decision.target_archetype == next(
        name for name in SUPPORTED if name != "fraction_of_quantity")


# ---------------------------------------------------------------------------
# DETERMINISTIČKI VALIDATOR
# ---------------------------------------------------------------------------

def _decision(target="fraction_remainder", supported=SUPPORTED):
    return esc.CreativeEscalationDecision(
        reason=esc.REASON_MAX_LEVEL_HARDER, target_archetype=target,
        supported_archetypes=supported, recent_archetypes=(), level=3)


@pytest.mark.parametrize("value,expected", [
    ("fraction_remainder", ""),
    ("  fraction_remainder  ", ""),
    ("fraction_of_quantity", esc.ARCHETYPE_NOT_TARGET),
    (FREE_TEXT, esc.ARCHETYPE_NOT_IN_CONTRACT),
    ("hard fraction task", esc.ARCHETYPE_NOT_IN_CONTRACT),
    ("word problem", esc.ARCHETYPE_NOT_IN_CONTRACT),
    ("", esc.ARCHETYPE_NOT_IN_CONTRACT),
    (None, esc.ARCHETYPE_NOT_IN_CONTRACT),
])
def test_archetype_failure_codes(value, expected):
    assert esc.archetype_failure(_decision(), value) == expected


def test_archetype_validation_is_inert_without_escalation():
    """Van eskalacije validator ne smije reći ništa — obična ruta se ne dira."""
    assert esc.archetype_failure(None, FREE_TEXT) == ""
    assert esc.archetype_failure(None, "bilo šta") == ""


def test_allowed_enum_comes_from_the_lesson_contract_not_from_code():
    """Nijedna konkretna vrijednost arhetipa ne smije biti u IZVRŠNOM kodu."""
    for name in ("creative_escalation.py", "pipeline.py"):
        path = ROOT / "matbot" / "tutor" / name
        with tokenize.open(path) as handle:
            for token in tokenize.generate_tokens(handle.readline):
                if token.type == tokenize.COMMENT:
                    continue          # obrazloženje smije imenovati živi nalaz
                for value in SUPPORTED:
                    assert value not in token.string, f"{name}: {token.string}"
    context = lesson_context.build(GRADE, LESSON)
    assert tuple(dict(context.semantic_contract.parameters)["problem_types"]) \
        == SUPPORTED


def test_reviewer_target_match_is_required_on_every_escalation():
    """Traži se i kad lekcija ima jedan arhetip — oznaka nije dokaz strukture."""
    assert esc.reviewer_requires_target_match(_decision()) is True
    single = _decision(target="only_one", supported=("only_one",))
    assert esc.reviewer_requires_target_match(single) is True
    assert esc.reviewer_requires_variety(single) is False
    assert esc.reviewer_requires_target_match(None) is False


def test_reviewer_prompt_asks_for_the_target_semantics_judgement():
    context = lesson_context.build(GRADE, LESSON)
    session = {"current_task": "", "recent_turns": [], "difficulty_level": 3,
               "hint_level": 0, "wrong_option_ids": [], "last_result": "",
               "recent_tasks": [], "correct_streak": 0, "retry_required": False,
               "current_options": [], "task_completed": False,
               "current_family": "", "correctly_completed_families": [],
               "difficulty": "hard", "current_task_had_hint": False}
    block = esc.prompt_block(_decision())
    reviewer_input = prompts.build_reviewer_input(
        context, session, "Daj mi teži zadatak.", "{}", escalation_block=block)
    assert "matches_target_archetype" in reviewer_input
    assert "substantially_different_from_recent" in reviewer_input
    # Tutor ne dobija recenzentska polja — samo cilj i granice.
    tutor_input = prompts.build_tutor_input(
        context, session, "Daj mi teži zadatak.", escalation_block=block)
    assert "matches_target_archetype" not in tutor_input


# ---------------------------------------------------------------------------
# NEPROMIJENJENO PONAŠANJE VAN ESKALACIJE
# ---------------------------------------------------------------------------

def test_ordinary_progression_still_zero_call_and_unaffected(universal):
    store, fake = SessionStore(), FakeLLM()
    warm_up_to_max(store, fake, "ordinary")
    assert fake.call_count == 0
    values = history(store.peek("ordinary"))
    assert values
    for value in values:
        assert value in SUPPORTED           # deterministički potpisi su enum


def test_easier_after_valid_creative_returns_to_deterministic(universal):
    store, fake = SessionStore(), FakeLLM()
    warm_up_to_max(store, fake, "recover")
    _seed_for_available_task(store, "recover")
    target = server_target(store, "recover")
    task = TASKS[target]
    draft = draft_with(target, task)
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=draft,
        checks=make_reviewer_checks(
            independent_answer=task["expected"], matches_target_archetype=True,
            substantially_different_from_recent=True)))
    assert escalate(store, fake, "recover")["status"] == "ready"
    assert store.peek("recover")["difficulty_level"] == 3
    calls = fake.call_count

    response = run_practice_turn(store, fake, turn("recover", "Daj mi lakši zadatak."))
    session = store.peek("recover")
    assert response["status"] == "ready"
    assert session["difficulty_level"] == 2
    assert session["deterministic_task"] is not None
    assert fake.call_count == calls          # bez novih poziva


def test_explicit_variety_still_reaches_escalation(universal):
    """Postojeći prirodnojezički okidač i dalje vodi na kreativni put."""
    store, fake = SessionStore(), FakeLLM()
    assert run_practice_turn(store, fake, turn("variety", "Daj mi zadatak.")
                             )["status"] == "ready"
    assert fake.call_count == 0
    _seed_for_available_task(store, "variety")
    target = server_target(store, "variety")
    task = TASKS[target]
    # Izričita raznolikost radi NEZAVISNO od maksimuma — ovdje smo na nivou 1,
    # pa i nacrt nosi dokaz težine nivoa 1.
    draft = draft_with(target, task, level=1, intent="next_task")
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=draft,
        checks=make_reviewer_checks(
            independent_answer=task["expected"], matches_target_archetype=True,
            substantially_different_from_recent=True)))
    response = run_practice_turn(store, fake, turn(
        "variety", "Daj mi drugačiji zadatak."))
    assert response["status"] == "ready"
    assert task["text"] in store.peek("variety")["current_task"]
    assert fake.call_count == 2
