"""JEDAN nagovjestaj mora reci PRAVILO KOJE OVAJ ZADATAK TRAZI.

RUCNI NALAZ IZ PRODUKCIJE, potvrdjen offline revizijom svih 189 aktivnih
0-pozivnih lekcija: lekcija o pravilima djeljivosti davala je za SVAKI zadatak
isti nagovjestaj — nabrajala tri KATEGORIJE pravila i nikad nije rekla koje ide
uz djelioce iz zadatka. Mjereno: JEDAN nagovjestaj na 43 razlicita zadatka.

Ucenik koji klikne „Ne znam“ najcesce ne zna bas to koje pravilo ide uz koji
djelilac. Generator djelioce zna tacno — ta cinjenica mora doci do ucenika.
"""
import os
import random
import re

import pytest

from matbot import divisibility_rules as rules
from matbot import mcq_integrity
from matbot.deterministic import numbertheory
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore

LESSON, GRADE = "6-03-004", 6
PARAMS = {"divisors": [2, 3, 4, 5, 6, 9, 10, 15, 25]}


@pytest.fixture
def production_env(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_SCOPE", "model_backed")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")


class NoModel:
    """Ova lekcija je 0-pozivna; svaki modelski poziv je greska."""

    def __getattr__(self, name):
        def explode(*args, **kwargs):
            raise AssertionError(f"MODELSKI POZIV: {name}")
        return explode


def _turn(message, intent=""):
    return {"session_id": "hint", "grade": GRADE, "selected_topic": LESSON,
            "selected_oblast": "", "student_message": message, "intent": intent,
            "difficulty_request": "harder" if "teži" in message else "",
            "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "student_question", "selected_option_id": "",
            "client_turn_id": ""}


# ---------------------------------------------------------------------------
# TABELA PRAVILA JE PODATAK, I POKRIVA TACNO ONO STO ORAKL PODRZAVA
# ---------------------------------------------------------------------------

def test_rule_table_matches_the_supported_divisor_set():
    assert set(rules.RULES) == set(mcq_integrity.SUPPORTED_DIVISORS)


@pytest.mark.parametrize("divisor,must_contain", [
    (2, "parna"),
    (3, "zbir cifara"),
    (4, "posljednje dvije cifre"),
    (5, "$0$ ili $5$"),
    (6, "i sa $2$ i sa $3$"),
    (9, "zbir cifara"),
    (10, "posljednja cifra je $0$"),
    (15, "i sa $3$ i sa $5$"),
    (25, "$00$, $25$, $50$ ili $75$"),
])
def test_every_divisor_has_its_own_real_rule(divisor, must_contain):
    assert must_contain in rules.rule_for(divisor)


def test_unknown_divisor_gets_no_invented_rule():
    assert rules.rule_for(7) == ""
    assert rules.hint_for([7]) == ""            # nepotpuno -> pozivalac zadrzava svoje
    assert rules.hint_for([]) == ""


# ---------------------------------------------------------------------------
# NAGOVJESTAJ JE SKROJEN ZA ZADATAK (zahtjev iz rucnog QA: 4, 15, 25)
# ---------------------------------------------------------------------------

def test_hint_for_4_15_25_states_all_three_rules():
    hint = rules.hint_for([4, 15, 25])
    assert "posljednje dvije cifre čine broj djeljiv sa $4$" in hint
    assert "i sa $3$ i sa $5$" in hint
    assert "$00$, $25$, $50$ ili $75$" in hint
    assert "SVA navedena pravila istovremeno" in hint


def test_hint_does_not_dump_rules_the_task_never_asked_for():
    hint = rules.hint_for([4])
    assert "posljednje dvije cifre čine broj djeljiv sa $4$" in hint
    for absent in ("zbir cifara", "$00$, $25$, $50$ ili $75$", "parna"):
        assert absent not in hint


def test_single_divisor_hint_does_not_claim_several_rules():
    assert "SVA navedena pravila" not in rules.hint_for([9])


# ---------------------------------------------------------------------------
# NAGOVJESTAJ NE SMIJE ODATI ODGOVOR
# ---------------------------------------------------------------------------

def _packages(count=40):
    produced = []
    for seed in range(count):
        try:
            produced.append(numbertheory.generate_package(
                LESSON, "Pravila djeljivosti", PARAMS, 1 + seed % 3,
                rng=random.Random(seed)))
        except Exception:                                            # noqa: BLE001
            continue
    return produced


# Djelilac naveden u UVODNOJ listi nagovjestaja („…: sa $4$ — …; sa $15$ — …“).
# Namjerno se ne hvata `sa $5$` iz TIJELA pravila za $15$.
_STATED_RE = re.compile(r"(?:pravila: |; )sa \$(\d+)\$ —")


def test_hint_cannot_depend_on_the_answer_at_all():
    """Najjaca garancija protiv curenja: nagovjestaj je CISTA funkcija djelilaca.

    Ne racuna se ni nad tacnom vrijednoscu ni nad opcijama, pa ih ne moze odati
    bez obzira koji brojevi ispadnu."""
    for package in _packages():
        asked = [int(value) for value in _STATED_RE.findall(package.hints[0])]
        assert package.hints[0] == rules.hint_for(asked)


def test_hint_never_points_at_a_candidate():
    for package in _packages():
        hint = package.hints[0]
        for phrase in ("tačan odgovor", "tačan je", "odgovor je", "izaberi",
                       "zaokruži", "opcija"):
            assert phrase not in hint.lower(), package.question


def test_hint_states_exactly_the_divisors_the_task_asks_about():
    for package in _packages():
        asked = [int(value) for value in re.findall(r"sa \$(\d+)\$", package.question)]
        stated = [int(value) for value in _STATED_RE.findall(package.hints[0])]
        assert stated == asked, (package.question, package.hints[0])


def test_hint_is_not_a_disguised_full_solution():
    for package in _packages():
        hint = package.hints[0]
        assert ":" not in hint.replace("Podsjetnik na pravila:", "")
        assert "bez ostatka" not in hint


# ---------------------------------------------------------------------------
# JEDAN NAGOVJESTAJ, 0 POZIVA, STABILAN NA PONAVLJANJE
# ---------------------------------------------------------------------------

def test_repeated_hint_returns_the_same_stored_text_with_zero_calls(production_env):
    store, llm = SessionStore(), NoModel()
    run_practice_turn(store, llm, _turn("Daj mi zadatak."))
    first = run_practice_turn(store, llm, _turn("Ne znam.", "hint_request"))["answer"]
    second = run_practice_turn(store, llm, _turn("Ne znam.", "hint_request"))["answer"]
    third = run_practice_turn(store, llm, _turn("Ne znam.", "hint_request"))["answer"]
    assert first == second == third
    assert "Podsjetnik na pravila:" in first


def test_the_served_hint_is_task_aware_end_to_end(production_env):
    store, llm = SessionStore(), NoModel()
    run_practice_turn(store, llm, _turn("Daj mi zadatak."))
    for _ in range(2):
        task = store.peek("hint")["current_task"]
        hint = run_practice_turn(store, llm, _turn("Ne znam.", "hint_request"))["answer"]
        asked = [int(value) for value in re.findall(r"sa \$(\d+)\$", task)]
        stated = [int(value) for value in _STATED_RE.findall(hint)]
        assert stated == asked, (task, hint)
        run_practice_turn(store, llm, _turn("Daj mi teži zadatak."))


def test_full_solution_stays_separate_from_the_hint(production_env):
    store, llm = SessionStore(), NoModel()
    run_practice_turn(store, llm, _turn("Daj mi zadatak."))
    hint = run_practice_turn(store, llm, _turn("Ne znam.", "hint_request"))["answer"]
    solution = run_practice_turn(
        store, llm, _turn("Uradi ga ti.", "solution_request"))["answer"]
    assert solution != hint
    assert "bez ostatka" in solution              # rjesenje stvarno racuna
    assert "bez ostatka" not in hint              # nagovjestaj ne racuna


def test_new_task_clears_the_stored_hint(production_env):
    store, llm = SessionStore(), NoModel()
    run_practice_turn(store, llm, _turn("Daj mi zadatak."))
    run_practice_turn(store, llm, _turn("Ne znam.", "hint_request"))
    run_practice_turn(store, llm, _turn("Daj mi novi zadatak."))
    assert not store.peek("hint").get("current_task_had_hint")


def test_no_lesson_id_in_the_rule_metadata():
    from pathlib import Path
    source = Path(rules.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", source)
