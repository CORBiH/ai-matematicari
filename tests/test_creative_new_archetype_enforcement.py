"""§17 matrica: kreativna eskalacija nad NOVIM arhetipima, kroz stvarni put.

Server sada iz potpisa SAM preračunava odgovor egzaktnim rješavačem, pa
tačnost kreativnog paketa ne ovisi ni o jednoj modelovoj tvrdnji. Recenzentove
presude ostaju DODATNI sloj, nikad zamjena.
"""
import json
from fractions import Fraction

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import creative_escalation as esc
from matbot.tutor.schema import DifficultyEvidence, SignatureParameter
from tests.conftest import (FakeLLM, make_difficulty_diagnostics,
                            make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

LESSON = "6-04-015"
GRADE = 6
SUPPORTED = ("fraction_of_fraction", "fraction_of_quantity",
             "fraction_remainder", "multi_fraction_remainder")

# Zadatak koji STVARNO jeste fraction_of_fraction: 48 · 2/3 · 1/4 = 8.
OF_FRACTION = {
    "text": ("U kutiji ima $48$ olovaka. Od njih je $\\frac{2}{3}$ plavih, a "
             "od plavih je $\\frac{1}{4}$ tamnoplavih. Koliko je tamnoplavih "
             "olovaka?"),
    "options": ("$8$", "$32$", "$12$", "$16$"),
    "correct_index": 0,
    "expected": "$8$",
    "solution": ("$\\frac{2}{3} \\cdot 48 = 32$ plavih, pa "
                 "$\\frac{1}{4} \\cdot 32 = 8$ tamnoplavih."),
    "facts": {"type": "fraction_of_fraction", "total": "48",
              "first_fraction": "2/3", "second_fraction": "1/4"},
}
# Zadatak koji STVARNO jeste multi_fraction_remainder: 24 · (1−1/3−1/4−1/6) = 6.
MULTI = {
    "text": ("Amina ima $24$ naljepnice. Pokloni $\\frac{1}{3}$, "
             "$\\frac{1}{4}$ i $\\frac{1}{6}$ od SVOJIH naljepnica. Koliko "
             "naljepnica je OSTALO?"),
    "options": ("$6$", "$18$", "$8$", "$12$"),
    "correct_index": 0,
    "expected": "$6$",
    "solution": ("Poklonjeno je $8 + 6 + 4 = 18$, pa je ostalo "
                 "$24 - 18 = 6$ naljepnica."),
    "facts": {"type": "multi_fraction_remainder", "total": "24",
              "fraction_1": "1/3", "fraction_2": "1/4", "fraction_3": "1/6"},
}
TASKS = {"fraction_of_fraction": OF_FRACTION,
         "multi_fraction_remainder": MULTI}


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


def draft(task, label, facts=None, options=None, correct_index=None):
    payload = make_task_payload(
        text=task["text"],
        options=options if options is not None else task["options"],
        correct_option_index=(task["correct_index"] if correct_index is None
                              else correct_index),
        expected=task["expected"], solution=task["solution"],
        difficulty="hard")
    parameters = task["facts"] if facts is None else facts
    payload = payload.model_copy(update={
        # Nacrt se veže na STVARNU lekciju: `FakeLLM` svojim „__fixture__“
        # drafovima prepisuje `normalized_parameters` na jedan `text` unos, pa
        # bi progutao upravo činjenice koje ovaj test mjeri. Vezivanjem na
        # pravu lekciju test-dvojnik nacrt ne dira (vidi conftest
        # `_bind_universal_fixture_metadata`).
        "selected_lesson_id": LESSON,
        "selected_lesson_title": "Tekstualni zadaci s razlomcima",
        "target_difficulty_level": 3,
        "difficulty_evidence": DifficultyEvidence(
            reasoning_steps=3, condition_count=2, operation_count=3,
            representation_change_count=1, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        "task_signature": payload.task_signature.model_copy(update={
            "operation_or_relation": label,
            "normalized_parameters": [
                SignatureParameter(name=name, value=value)
                for name, value in parameters.items()],
        }),
    })
    return make_tutor_draft(
        intent="harder_task", reply="Evo zadatka.",
        lesson_focus="tekstualni zadaci s razlomcima",
        difficulty_diagnostics=make_difficulty_diagnostics(direction="higher"),
        new_task=payload)


def _signature_record(archetype):
    return {"lesson_id": LESSON,
            "structured_signature": json.dumps(
                {"operation_or_relation": archetype}),
            "structured_signature_hash": f"seed-{archetype}"}


def warm_up(store, fake, session_id, desired_target):
    """Zagrij do maksimuma, pa POSTAVI historiju tako da server izabere
    baš `desired_target`.

    Historija je serverska projekcija prethodnih objava — postavljanje je
    vjerno simuliranje ranijih turnova, a ne zaobilaženje planera: cilj se i
    dalje računa `select_target`-om iz te historije."""
    for message in ["Daj mi zadatak.", "Daj mi teži zadatak.",
                    "Daj mi teži zadatak."]:
        assert run_practice_turn(store, fake, turn(session_id, message)
                                 )["status"] == "ready"
    assert fake.call_count == 0
    session = store.peek(session_id)
    seen = [name for name in SUPPORTED if name != desired_target]
    session["recent_task_signatures"] = [_signature_record(n) for n in seen]
    store.save(session)
    recent = esc.recent_archetypes(store.peek(session_id), LESSON,
                                   supported=SUPPORTED)
    target = esc.select_target(SUPPORTED, recent)
    assert target == desired_target, (target, desired_target)
    return target


def run(session_id, make_draft, desired_target, **checks):
    """Zagrij, pa pošalji jedan kreativni odgovor izgrađen oko SERVERSKOG cilja."""
    store, fake = SessionStore(), FakeLLM()
    target = warm_up(store, fake, session_id, desired_target)
    before = history(store.peek(session_id))
    tutor_draft = make_draft(target)
    fake.queue(tutor_draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=tutor_draft,
        checks=make_reviewer_checks(
            independent_answer=TASKS[target]["expected"], **checks)))
    response = run_practice_turn(store, fake, turn(session_id, "Daj mi teži zadatak."))
    session = store.peek(session_id)
    return {"target": target, "response": response,
            "published": TASKS[target]["text"] in (session.get("current_task") or ""),
            "history_before": before, "history_after": history(session),
            "tutor_calls": len(fake.tutor_calls),
            "reviewer_calls": len(fake.reviewer_calls),
            "level": session.get("difficulty_level")}


APPROVE = {"matches_target_archetype": True,
           "substantially_different_from_recent": True}


# ---------------------------------------------------------------------------
# CASE 1 — sve tačno → OBJAVLJUJE SE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", tuple(TASKS))
def test_case_1_valid_new_archetype_publishes(universal, target):
    result = run(f"new-1-{target}", lambda t: draft(TASKS[t], t), target, **APPROVE)
    assert result["published"] is True
    assert result["response"]["status"] == "ready"
    assert result["history_after"][-1] == result["target"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1
    assert result["level"] == 3


# ---------------------------------------------------------------------------
# CASE 2 — oznaka je cilj, ali ČINJENICE opisuju drugi arhetip → REJECT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", tuple(TASKS))
def test_case_2_facts_describe_another_archetype(universal, target):
    other = {"type": "fraction_of_quantity", "total": "48", "fraction": "2/3"}
    result = run(f"new-2-{target}", lambda t: draft(TASKS[t], t, facts=other),
                 target, **APPROVE)
    assert result["published"] is False
    assert result["history_after"] == result["history_before"]
    assert result["reviewer_calls"] == 0      # aritmetika pada prije recenzenta


# ---------------------------------------------------------------------------
# CASE 3 — činjenice tačne, ODGOVOR pogrešan → deterministička odbrana
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", tuple(TASKS))
def test_case_3_wrong_answer_is_rejected_by_the_exact_solver(universal, target):
    def build(target):
        task = TASKS[target]
        wrong = list(task["options"])
        wrong[task["correct_index"]] = "$999$"
        return draft(task, target, options=tuple(wrong))
    result = run(f"new-3-{target}", build, target, **APPROVE)
    assert result["published"] is False
    assert result["history_after"] == result["history_before"]
    assert result["reviewer_calls"] == 0


# ---------------------------------------------------------------------------
# CASE 4 — DVIJE opcije jednake tačnom odgovoru → REJECT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", tuple(TASKS))
def test_case_4_duplicate_true_answer_is_rejected(universal, target):
    def build(target):
        task = TASKS[target]
        truth = task["expected"]
        options = list(task["options"])
        options[1] = truth.replace("$", "$ ")       # ista vrijednost, drugi zapis
        return draft(task, target, options=tuple(options))
    result = run(f"new-4-{target}", build, target, **APPROVE)
    assert result["published"] is False
    assert result["history_after"] == result["history_before"]


# ---------------------------------------------------------------------------
# CASE 5 — Tutor izmišlja PETI tip u oznaci → oznaka se NEUTRALIŠE, ne nasljeđuje
# ---------------------------------------------------------------------------
# Ranije je ovo obaralo paket. Otkad je taksonomija serverska, izmišljena
# vrijednost ne može ući u sistem NI DA JE PROPUŠTENA: objavljuje se serverski
# cilj, pa `successive_fraction_remainder` nikad ne postaje arhetip lekcije.
# Zaštita od petog arhetipa je time JAČA (konstrukcija umjesto detekcije), a
# ispravan zadatak zbog pogrešne naljepnice više ne propada.

@pytest.mark.parametrize("target", tuple(TASKS))
def test_case_5_invented_label_never_becomes_an_archetype(universal, target):
    result = run(f"new-5-{target}",
                 lambda t: draft(TASKS[t], "successive_fraction_remainder"),
                 target, **APPROVE)
    assert result["published"] is True
    assert result["history_after"][-1] == target
    assert "successive_fraction_remainder" not in result["history_after"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


# ---------------------------------------------------------------------------
# EGZAKTNA PROVJERA — jedinica, bez rute
# ---------------------------------------------------------------------------

def _decision(target):
    return esc.CreativeEscalationDecision(
        reason=esc.REASON_MAX_LEVEL_HARDER, target_archetype=target,
        supported_archetypes=SUPPORTED, recent_archetypes=(), level=3)


def test_facts_failure_is_silent_without_escalation():
    assert esc.facts_failure(None, None) == ""


@pytest.mark.parametrize("target", ("fraction_of_fraction",
                                    "multi_fraction_remainder"))
def test_facts_failure_accepts_a_correct_package(target):
    package = draft(TASKS[target], target).new_task
    assert esc.facts_failure(_decision(target), package) == ""


@pytest.mark.parametrize("target", ("fraction_of_fraction",
                                    "multi_fraction_remainder"))
def test_facts_failure_rejects_a_package_without_the_target_facts(target):
    """OD SADA OBAVEZNO: bez činjenica cilja nema čime dokazati arhetip.

    Ranije je ovaj sloj tu ĆUTAO, jer je tvrdnju „ovo je taj arhetip“ nosila
    modelova OZNAKA. Otkad oznaka nije autoritet, struktura je jedini
    deterministički dokaz — pa paket bez nje pada zatvoreno."""
    package = draft(TASKS[target], target, facts={"type": target}).new_task
    assert esc.facts_failure(_decision(target), package) == esc.FACTS_MISSING


@pytest.mark.parametrize("target", ("fraction_of_fraction",
                                    "multi_fraction_remainder"))
def test_facts_failure_rejects_partial_or_foreign_facts(target):
    """Činjenice drugog arhetipa NE smiju proći kao „nema šta da se provjeri“."""
    foreign = {"type": target, "total": "48", "fraction": "2/3"}
    package = draft(TASKS[target], target, facts=foreign).new_task
    assert esc.facts_failure(_decision(target), package) == esc.FACTS_MISSING
