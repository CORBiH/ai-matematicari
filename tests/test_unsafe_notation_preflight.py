r"""Nesiguran zapis mora stići recenzentu s IMENOM polja koje treba prepisati.

ŽIVI NALAZ (run postStabilityFixes — A19, B04, B33, B42): turn je potrošio oba
poziva i tek u objavi pao s porukom

    tutor_rejected … stage=publication detail=nebezbjedan matematički zapis [solution]

Recenzent za to nikad nije saznao. `package_preflight` je u sekciji 5 radio
`text, safe = safe_visible_text(raw); if not safe: continue` — nesiguran
`text` i `solution` su se TIHO preskakali, dok su opcije i `expected_answer`
već imali svoje kodove. Bez imena polja recenzent ne zna šta da prepiše, iako
`correct` upravo to omogućava.

`mathsafe` pravila se NE mijenjaju — mijenja se samo dijagnostika prema
recenzentu. Objava ostaje fail-closed.
"""
import pytest

from matbot.tutor import package_preflight
from tests.conftest import make_task_payload

UNSAFE = r"$x = \ty{5}$"          # nepoznata komanda — mathsafe je odbija
SAFE = r"$x = 5$"


def _task(**updates):
    base = make_task_payload(text=r"Izračunaj: $2+3$", options=("$5$", "$6$", "$7$", "$8$"),
                             correct_option_index=0, expected="$5$")
    return base.model_copy(update=updates) if updates else base


def _codes(task):
    return [issue.code for issue in package_preflight.collect_package_issues(task)]


def test_unsafe_task_text_gets_its_own_code():
    assert "unsafe_task_text_notation" in _codes(_task(text=UNSAFE))


def test_unsafe_solution_gets_its_own_code():
    assert "unsafe_solution_notation" in _codes(_task(solution=UNSAFE))


def test_unsafe_option_keeps_its_existing_code():
    task = _task()
    broken = list(task.options)
    broken[1] = broken[1].model_copy(update={"text": UNSAFE})
    assert "unsafe_option_notation" in _codes(task.model_copy(update={"options": broken}))


def test_unsafe_expected_answer_keeps_its_existing_code():
    assert "unsafe_expected_answer_notation" in _codes(_task(expected_answer=UNSAFE))


def test_every_unsafe_field_is_named_exactly_once():
    codes = _codes(_task(text=UNSAFE, solution=UNSAFE))
    assert codes.count("unsafe_task_text_notation") == 1
    assert codes.count("unsafe_solution_notation") == 1


def test_valid_mathjax_is_never_reported():
    for field in ("text", "solution"):
        task = _task(**{field: r"Izračunaj $\frac{3}{4} + \frac{1}{4}$."})
        assert not [code for code in _codes(task) if code.startswith("unsafe_")]


def test_reviewer_block_names_the_field_to_rewrite():
    issues = package_preflight.collect_package_issues(_task(solution=UNSAFE))
    block = package_preflight.format_for_reviewer(issues)
    assert "unsafe_solution_notation" in block
    assert "rewrite" in block.lower()


def test_publication_stays_fail_closed_for_unsafe_notation(store, fake_llm, monkeypatch):
    """Prijava recenzentu ne smije oslabiti objavu."""
    from matbot.tutor import pipeline as tutor_pipeline
    from tests.conftest import make_tutor_draft, queue_two_call

    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    broken = _task(solution=UNSAFE)
    draft = make_tutor_draft(intent="generate_task", new_task=broken)
    queue_two_call(fake_llm, draft=draft)
    response = tutor_pipeline.run_turn(store, fake_llm, {
        "session_id": "uns-1", "grade": 9, "selected_topic": "9-04-003",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "uns-t1"})

    assert response.get("status") is None
    assert fake_llm.call_count == 2                 # bez trećeg poziva
    assert store.peek("uns-1") is None              # bez mutacije sesije
