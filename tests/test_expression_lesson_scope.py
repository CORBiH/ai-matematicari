"""Kurikularni opseg lekcije o izrazima s promjenljivim (živi QA nalaz).

ŽIVI NALAZ (direktor škole, Practice): u lekciji „Izrazi s promjenljivim i
brojna vrijednost izraza“ (6. razred) traženje sve težih zadataka dovelo je do
izraza sa $x^2$. Uzrok nije bio bug u računu: porodica `polynomial_basic`
opslužuje i lekcije 8. i 9. razreda u kojima je stepenovanje obrađeno gradivo,
pa je nivo 3 NAMJERNO dizao stepen — a generator o lekciji ne zna ništa osim
parametara ugovora.

Granica zato živi u UGOVORU LEKCIJE (`max_variable_degree`), nikad u razredu,
naslovu ni ID-ju: ista porodica u jednoj lekciji smije $x^2$, u drugoj ne
smije, i to je razlika PODATAKA. Testovi ispod dokazuju oba smjera.
"""
import json
import random
import re
import tokenize
from fractions import Fraction
from pathlib import Path

import pytest

from matbot import deterministic as det
from matbot.deterministic import polynomials
from matbot.practice import run_practice_turn
from matbot.semantics import contracts as semantic_contracts
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM

ROOT = Path(__file__).resolve().parent.parent

EXPRESSION_LESSON = "6-02-008"
EXPRESSION_TITLE = "Izrazi s promjenljivim i brojna vrijednost izraza"
POWER_LESSONS = ("8-07-001", "9-01-001")

# Stepen PROMJENLJIVE: slovo neposredno prije `^` (nikad LaTeX komanda, pa
# `90^{\circ}` i `cm^2` ne mogu biti pogodak — ispred njih nije gola
# promjenljiva ovog generatora).
VARIABLE_POWER = re.compile(r"(?<![\\A-Za-z])[A-Za-z]\s*\^")
# Strogi linearni član: znak, opcioni koeficijent, JEDNO slovo — bez izlagača.
LINEAR_TERM = re.compile(r"^[+-]?\s*\d*[A-Za-z]$")
CONSTANT_TERM = re.compile(r"^[+-]?\s*\d+$")
GIVEN = re.compile(r"\$([A-Za-z])\s*=\s*(-?\d+)\$")


def contract_of(lesson_id):
    return semantic_contracts.contract_for(lesson_id)


def generate(lesson_id, title, level, seed):
    contract = contract_of(lesson_id)
    module = det.GENERATORS[contract.family_id]
    return module.generate_package(lesson_id, title, dict(contract.parameters),
                                   level, rng=random.Random(seed))


def expression_terms(display):
    normalized = display.replace(" - ", " + -").replace("- ", "-")
    return [part.strip() for part in normalized.split(" + ") if part.strip()]


def student_surfaces(package):
    yield package.question
    yield from package.option_texts
    yield package.solution
    yield from package.hints


# ---------------------------------------------------------------------------
# 1) UGOVOR NOSI GRANICU, I TO S DOKAZOM
# ---------------------------------------------------------------------------

def test_expression_lesson_contract_bounds_variable_degree():
    contract = contract_of(EXPRESSION_LESSON)
    assert contract.parameters.get("max_variable_degree") == "1"
    assert contract.evidence_ids  # granica nikad bez zapisanog dokaza
    assert polynomials.max_variable_degree(dict(contract.parameters)) == 1


def test_contract_carries_the_bound_into_tutor_and_reviewer_prompt():
    """Model-put (rollback) mora dobiti ISTU granicu — ista prompt_lines."""
    contract = contract_of(EXPRESSION_LESSON)
    joined = " ".join(contract.prompt_lines)
    assert "prvi stepen" in joined and "bez stepenovanja" in joined
    for lesson_id in POWER_LESSONS:
        assert "stepen" not in " ".join(contract_of(lesson_id).prompt_lines)


def test_bound_is_absent_by_default_so_no_lesson_changes_silently():
    assert polynomials.max_variable_degree({}) == 2
    assert polynomials.max_variable_degree(None) == 2
    for lesson_id in POWER_LESSONS:
        parameters = dict(contract_of(lesson_id).parameters)
        assert "max_variable_degree" not in parameters
        assert polynomials.max_variable_degree(parameters) == 2


def test_no_grade_based_power_rule_exists_in_the_generator():
    """Zabrana NIJE po razredu: modul ne smije znati ni za razred ni za lekciju."""
    path = ROOT / "matbot" / "deterministic" / "polynomials.py"
    # Gleda se KOD, ne komentari: obrazloženje smije reći „nikad iz razreda“,
    # ali izvršni kod ne smije nigdje pomenuti razred ni ID lekcije.
    code = []
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type in (tokenize.COMMENT, tokenize.NL):
                continue
            code.append(token.string)
    blob = "\n".join(code)
    # Riječi, ne podnizovi: „zagrade“ legitimno sadrži „grade“.
    for forbidden in (r"\bgrade\b", r"\brazred", r"\b\d-\d{2}-\d{3}\b"):
        assert not re.search(forbidden, blob), forbidden


# ---------------------------------------------------------------------------
# 2) POGOĐENA LEKCIJA — nijedan stepen promjenljive, svi nivoi
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", (1, 2, 3))
def test_expression_lesson_never_emits_a_variable_power(level):
    for seed in range(120):
        package = generate(EXPRESSION_LESSON, EXPRESSION_TITLE, level, seed)
        for text in student_surfaces(package):
            assert not VARIABLE_POWER.search(text or ""), (level, seed, text)


@pytest.mark.parametrize("level", (1, 2, 3))
def test_expression_stays_linear_by_structure(level):
    """Strukturna provjera: svaki član je linearan ili slobodan, bez izlagača."""
    for seed in range(120):
        package = generate(EXPRESSION_LESSON, EXPRESSION_TITLE, level, seed)
        display = dict(package.signature_parameters)["expression"]
        for term in expression_terms(display):
            assert LINEAR_TERM.match(term) or CONSTANT_TERM.match(term), \
                (level, seed, display, term)


def test_level_three_is_harder_without_raising_the_degree():
    """Nivo 3 raste po dimenziji koju dokaz nosi: DVIJE promjenljive."""
    variables_by_level = {}
    for level in (1, 2, 3):
        counts = set()
        for seed in range(120):
            package = generate(EXPRESSION_LESSON, EXPRESSION_TITLE, level, seed)
            counts.add(len(GIVEN.findall(package.question)))
        variables_by_level[level] = counts
    assert variables_by_level[1] == {1}
    assert variables_by_level[2] == {1}
    assert variables_by_level[3] == {2}


@pytest.mark.parametrize("level", (1, 2, 3))
def test_expression_lesson_math_is_correct(level):
    """Vrijednost se preračunava NEZAVISNO iz izraza i datih vrijednosti."""
    for seed in range(120):
        package = generate(EXPRESSION_LESSON, EXPRESSION_TITLE, level, seed)
        display = dict(package.signature_parameters)["expression"]
        given = {name: int(value)
                 for name, value in GIVEN.findall(package.question)}
        total = Fraction(0)
        for term in expression_terms(display):
            term = term.replace(" ", "")
            sign = -1 if term.startswith("-") else 1
            body = term.lstrip("+-")
            if body.isdigit():
                total += sign * int(body)
                continue
            coefficient = body[:-1]
            name = body[-1]
            assert name in given, (display, package.question)
            total += sign * (int(coefficient) if coefficient else 1) * given[name]
        marked = package.option_texts[package.correct_index]
        assert Fraction(marked.strip("$")) == total, (package.question, marked)
        equal = [text for text in package.option_texts
                 if Fraction(text.strip("$")) == total]
        assert equal == [marked], (package.question, package.option_texts)


# ---------------------------------------------------------------------------
# 3) KONTROLA — lekcije u kojima je stepenovanje GRADIVO ostaju netaknute
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson_id", POWER_LESSONS)
def test_power_lessons_still_produce_powers_at_level_three(lesson_id):
    seen = 0
    for seed in range(60):
        package = generate(lesson_id, "kontrola", 3, seed)
        if package.operation != "expression_evaluation":
            continue
        if VARIABLE_POWER.search(package.question):
            seen += 1
    assert seen >= 30, (lesson_id, seen)


def test_other_polynomial_lessons_are_byte_for_byte_unchanged():
    """Granica je OPT-IN: bez parametra generator radi tačno kao ranije."""
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(
            encoding="utf-8"))["lessons"]
    others = [lesson_id for lesson_id, entry in compiled.items()
              if entry["family_id"] == "polynomial_basic"
              and lesson_id != EXPRESSION_LESSON]
    assert len(others) >= 20
    for lesson_id in others:
        parameters = compiled[lesson_id]["parameters"]
        assert "max_variable_degree" not in parameters, lesson_id


# ---------------------------------------------------------------------------
# 4) STVARNA PROGRESIJA TEŽINE — nula poziva modela
# ---------------------------------------------------------------------------

def _turn(session_id, message, **changes):
    payload = {
        "session_id": session_id, "grade": 6,
        "selected_topic": EXPRESSION_LESSON, "selected_oblast": "",
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


def test_asking_for_harder_never_leaves_the_lesson_scope(universal):
    for index in range(6):
        session_id = f"expr-scope-{index}"
        store, fake = SessionStore(), FakeLLM()
        seen_tasks, levels = [], []
        messages = ["Daj mi zadatak."] + ["Daj mi teži zadatak."] * 4
        for message in messages:
            response = run_practice_turn(store, fake, _turn(session_id, message))
            assert response["status"] == "ready"
            session = store.peek(session_id)
            task = session["current_task"]
            annex = session["deterministic_task"]

            # 1) nema nepodržanog stepena
            assert not VARIABLE_POWER.search(task), task
            for text in [task, session["solution_summary"],
                         *(annex or {}).get("hints", [])]:
                assert not VARIABLE_POWER.search(text or ""), text
            # 2) zadatak je iz IZABRANE lekcije i porodice
            assert annex is not None and fake.call_count == 0
            assert annex["family_id"] == "polynomial_basic"
            assert annex["operation"] == "expression_evaluation"
            assert response["effective_topic"] == EXPRESSION_LESSON
            # 3) nema ponavljanja istog zadatka
            assert task not in seen_tasks, task
            seen_tasks.append(task)
            levels.append(session["difficulty_level"])

        # 4) težina stvarno raste i staje na maksimumu
        assert levels == [1, 2, 3, 3, 3], levels
        # 5) najteži nivo nosi DVIJE promjenljive, ne viši stepen
        assert len(GIVEN.findall(seen_tasks[-1])) == 2, seen_tasks[-1]
