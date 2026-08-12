"""Kurikularni opseg klasifikacijskog MCQ-a o vrstama zapisa (živi QA nalaz).

ŽIVI NALAZ: lekcija „Jednakost, jednačina, nejednakost i nejednačina“ (6.
razred) je na nivoima 2 i 3 nudila KVADRATNU jednačinu ($x^{2} + b = c$) kao
pogrešnu opciju, u oko 50% paketa. Učenik je nije trebao rješavati, ali je
nije mogao ni OBRAZLOŽITI: stepenovanje se u 6. razredu ne pojavljuje ni u
jednoj NPP stavki i uvodi se tek u 8. razredu.

Ista porodica opslužuje i 9-04-001 i 9-04-012, gdje je kvadratna jednačina
obrađeno gradivo i legitiman ne-primjer. Granica zato živi u UGOVORU LEKCIJE
(`max_variable_degree`), nikad u razredu ni ID-ju — isti mehanizam koji već
čuva lekciju o izrazima s promjenljivim.

Zamjena za izgubljeni distraktor nije nasumična: nivo 2/3 sada nosi
NEJEDNAKOST bez nepoznate — četvrti pojam koji ishod lekcije (KS_2018-0060)
izričito imenuje uz jednačinu, jednakost i nejednačinu.
"""
import json
import random
import re
import tokenize
from pathlib import Path

import pytest

from matbot import deterministic as det
from matbot import practice_policy
from matbot.deterministic import core, equations
from matbot.practice import run_practice_turn
from matbot.semantics import contracts as semantic_contracts
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM

ROOT = Path(__file__).resolve().parent.parent

GRADE6_LESSON = "6-07-001"
GRADE6_TITLE = "Jednakost, jednačina, nejednakost i nejednačina"
LATER_LESSONS = ("9-04-001", "9-04-012")

VARIABLE_POWER = re.compile(r"(?<![\\A-Za-z])[A-Za-z]\s*\^")
LETTER = re.compile(r"(?<![\\A-Za-z])[a-z](?![A-Za-z])")
REL_EQ = re.compile(r"(?<![<>])=")
REL_INEQ = re.compile(r"[<>]")


def contract_of(lesson_id):
    return semantic_contracts.contract_for(lesson_id)


def generate(lesson_id, title, level, seed, grade):
    contract = contract_of(lesson_id)
    module = det.GENERATORS[contract.family_id]
    policy = practice_policy.resolve(grade=grade, lesson_id=lesson_id)
    return module.generate_package(lesson_id, title, dict(contract.parameters),
                                   level, rng=random.Random(seed), policy=policy)


def surfaces(package):
    yield package.question
    yield from package.option_texts
    yield package.solution
    yield from package.hints


def is_requested_class(option_text, asks_equation):
    """Porodični strukturni predikat — nikad čitanje proze zadatka."""
    body = option_text.strip().strip("$")
    if VARIABLE_POWER.search(body):
        return False                       # nije LINEARNA
    if len(set(LETTER.findall(body))) != 1:
        return False                       # nema tačno jednu nepoznatu
    has_eq, has_ineq = bool(REL_EQ.search(body)), bool(REL_INEQ.search(body))
    return (has_eq and not has_ineq) if asks_equation else has_ineq


def asks_equation(question):
    return "je linearna jednačina" in question


# ---------------------------------------------------------------------------
# 1) UGOVOR NOSI GRANICU
# ---------------------------------------------------------------------------

def test_grade6_contract_bounds_the_unknown_degree():
    contract = contract_of(GRADE6_LESSON)
    assert contract.parameters.get("max_variable_degree") == "1"
    assert contract.evidence_ids  # granica nikad bez zapisanog dokaza
    assert core.max_variable_degree(dict(contract.parameters)) == 1


def test_bound_reaches_tutor_and_reviewer_prompt():
    joined = " ".join(contract_of(GRADE6_LESSON).prompt_lines)
    assert "prvi stepen" in joined and "kvadratnih jednačina" in joined
    for lesson_id in LATER_LESSONS:
        assert "stepen" not in " ".join(contract_of(lesson_id).prompt_lines)


def test_bound_is_opt_in_so_later_lessons_are_untouched():
    assert core.max_variable_degree({}) == 2
    assert core.max_variable_degree(None) == 2
    for lesson_id in LATER_LESSONS:
        assert "max_variable_degree" not in dict(contract_of(lesson_id).parameters)


def test_no_grade_or_lesson_branch_in_the_generator():
    path = ROOT / "matbot" / "deterministic" / "equations.py"
    identifiers, literals = [], []
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.NAME:
                identifiers.append(token.string)
            elif token.type == tokenize.STRING:
                literals.append(token.string)
    # Nijedan IDENTIFIKATOR ne smije nositi razred — proza smije („oblici se
    # grade iz uloge“ je bosanski glagol graditi, ne engleska imenica).
    for name in identifiers:
        assert not re.search(r"^grade$|razred", name), name
    # Nijedan ID lekcije nigdje — ni u kodu ni u tekstu.
    for literal in literals + identifiers:
        assert not re.search(r"\d-\d{2}-\d{3}", literal), literal


# ---------------------------------------------------------------------------
# 2) GRADE 6 — nijedan stepen nepoznate ni u jednoj vidljivoj površini
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", (1, 2, 3))
def test_grade6_classification_never_shows_a_power(level):
    for seed in range(150):
        package = generate(GRADE6_LESSON, GRADE6_TITLE, level, seed, 6)
        for text in surfaces(package):
            assert not VARIABLE_POWER.search(text or ""), (level, seed, text)


@pytest.mark.parametrize("level", (1, 2, 3))
def test_grade6_classification_has_exactly_one_correct_option(level):
    for seed in range(150):
        package = generate(GRADE6_LESSON, GRADE6_TITLE, level, seed, 6)
        wants = asks_equation(package.question)
        flags = [is_requested_class(text, wants)
                 for text in package.option_texts]
        assert sum(flags) == 1, (package.question, package.option_texts)
        assert flags[package.correct_index] is True
        assert not any(flag for index, flag in enumerate(flags)
                       if index != package.correct_index)


def test_grade6_level_two_and_three_gain_a_curriculum_supported_distractor():
    """Izgubljeni kvadratni ne-primjer zamjenjuju pojmovi iz ishoda lekcije."""
    kinds_by_level = {}
    for level in (1, 2, 3):
        shapes = set()
        for seed in range(150):
            package = generate(GRADE6_LESSON, GRADE6_TITLE, level, seed, 6)
            if not asks_equation(package.question):
                continue
            for text in package.option_texts:
                body = text.strip("$")
                letters = set(LETTER.findall(body))
                if len(letters) == 2:
                    shapes.add("two_unknowns")
                elif not letters and REL_INEQ.search(body):
                    shapes.add("numeric_inequality")
                elif not letters:
                    shapes.add("equality")
        kinds_by_level[level] = shapes
    # nivo 1 nema nijedan od dva finija ne-primjera
    assert "two_unknowns" not in kinds_by_level[1]
    assert "numeric_inequality" not in kinds_by_level[1]
    # nivoi 2 i 3 nose OBA
    for level in (2, 3):
        assert {"two_unknowns", "numeric_inequality"} <= kinds_by_level[level], \
            (level, kinds_by_level[level])


def test_grade6_solution_prose_matches_the_options_actually_offered():
    """Rečenica rješenja se sastavlja od izabranih vrsta, pa ne smije tvrditi
    ni kvadrat ni dvije nepoznate kad ih u zadatku nema."""
    for level in (1, 2, 3):
        for seed in range(120):
            package = generate(GRADE6_LESSON, GRADE6_TITLE, level, seed, 6)
            assert "kvadrat" not in package.solution.lower(), package.solution
            has_two = any(len(set(LETTER.findall(text.strip("$")))) == 2
                          for text in package.option_texts)
            assert ("dvije nepoznate" in package.solution) is has_two, \
                (package.option_texts, package.solution)


# ---------------------------------------------------------------------------
# 3) KONTROLA — kasnija lekcija i dalje smije kvadratni ne-primjer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson_id", LATER_LESSONS)
def test_later_lessons_still_offer_the_quadratic_nonexample(lesson_id):
    seen = 0
    for level in (2, 3):
        for seed in range(150):
            package = generate(lesson_id, "kontrola", level, seed, 9)
            if any(VARIABLE_POWER.search(text) for text in package.option_texts):
                seen += 1
    assert seen >= 100, (lesson_id, seen)


@pytest.mark.parametrize("lesson_id", LATER_LESSONS)
def test_later_lessons_keep_exactly_one_correct_option(lesson_id):
    for level in (1, 2, 3):
        for seed in range(80):
            package = generate(lesson_id, "kontrola", level, seed, 9)
            wants = asks_equation(package.question)
            flags = [is_requested_class(text, wants)
                     for text in package.option_texts]
            assert sum(flags) == 1, (package.question, package.option_texts)
            assert flags[package.correct_index] is True


def test_only_the_grade6_lesson_carries_the_bound():
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(
            encoding="utf-8"))["lessons"]
    bounded = sorted(lesson_id for lesson_id, entry in compiled.items()
                     if "max_variable_degree" in entry["parameters"]
                     and entry["family_id"] == "linear_equation_direct")
    assert bounded == [GRADE6_LESSON], bounded


# ---------------------------------------------------------------------------
# 4) STVARNI PRACTICE PUT — nula poziva, ocjena klika
# ---------------------------------------------------------------------------

def _turn(session_id, message, **changes):
    payload = {
        "session_id": session_id, "grade": 6,
        "selected_topic": GRADE6_LESSON, "selected_oblast": "",
        "student_message": message, "intent": "", "difficulty_request": "",
        "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def test_real_practice_progression_stays_in_scope(universal):
    for index in range(6):
        session_id = f"g6-cls-scope-{index}"
        store, fake = SessionStore(), FakeLLM()
        identities = []
        for message in ["Daj mi zadatak.", "Daj mi teži zadatak.",
                        "Daj mi teži zadatak."]:
            response = run_practice_turn(store, fake, _turn(session_id, message))
            assert response["status"] == "ready" and fake.call_count == 0
            session = store.peek(session_id)
            annex = session["deterministic_task"]
            assert annex is not None
            assert annex["family_id"] == "linear_equation_direct"
            assert annex["operation"] == "classification"
            assert response["effective_topic"] == GRADE6_LESSON

            for text in [session["current_task"], session["solution_summary"],
                         *annex["hints"],
                         *(o["text"] for o in session["current_options"])]:
                assert not VARIABLE_POWER.search(text or ""), text

            wants = asks_equation(session["current_task"])
            truth = {o["id"]: is_requested_class(o["text"], wants)
                     for o in session["current_options"]}
            assert sum(truth.values()) == 1, session["current_options"]
            assert truth[session["correct_option_id"]] is True

            identity = session["current_task_identity"]
            assert identity not in identities
            identities.append(identity)


def test_real_answer_handling_accepts_only_the_correct_option(universal):
    accepted = rejected = 0
    for index in range(6):
        for option_slot in ("a", "b", "c", "d"):
            session_id = f"g6-cls-click-{index}-{option_slot}"
            store, fake = SessionStore(), FakeLLM()
            assert run_practice_turn(store, fake, _turn(
                session_id, "Daj mi zadatak."))["status"] == "ready"
            session = store.peek(session_id)
            wants = asks_equation(session["current_task"])
            text = next(o["text"] for o in session["current_options"]
                        if o["id"] == option_slot)
            math_true = is_requested_class(text, wants)
            verdict = run_practice_turn(store, fake, _turn(
                session_id, "[odgovor]", interaction_type="choice_answer",
                selected_option_id=option_slot, client_turn_id="c1"))
            assert fake.call_count == 0
            if verdict["answer_verdict"] == "correct":
                accepted += 1
                assert math_true and option_slot == session["correct_option_id"]
            else:
                rejected += 1
                # nijedna matematički tačna opcija ne smije biti odbijena
                assert not math_true, (session["current_task"], text)
    assert accepted >= 1 and rejected >= 1
