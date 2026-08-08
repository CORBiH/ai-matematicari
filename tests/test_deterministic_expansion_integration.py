"""Kapacitetna ekspanzija — integracija kroz STVARNI Practice orkestrator.

Po jedna reprezentativna lekcija SVAKE nove determinističke porodice prolazi
cijeli životni ciklus strukturisanih akcija sa NULA poziva modela: svjež
zadatak → teži → lakši → hint → klik na opciju → potpuno rješenje → nov
zadatak. Slobodna poruka na istoj lekciji i dalje ide model-putem (dva
poziva), a rollback zastavica vraća SVE na model-put.

Frontend ugovor se ne mijenja: odgovor je isti generički oblik
(answer/next_state/current_options) koji postojeći DOM testovi već pokrivaju.
"""
import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_reviewer_final, make_task_payload, make_tutor_draft

# Po jedna lekcija svake NOVE porodice (porodica → (razred, lekcija)).
REPRESENTATIVES = [
    ("natural_arithmetic_direct", 6, "6-02-003"),
    ("decimal_arithmetic_direct", 6, "6-05-009"),
    ("divisibility_predicate_application", 6, "6-03-004"),
    ("common_divisors_multiples", 6, "6-03-008"),
    ("prime_structure", 6, "6-03-005"),
    ("number_comparison_order", 6, "6-04-008"),
    ("percent_basic", 6, "6-06-002"),
    ("arithmetic_mean_direct", 6, "6-06-004"),
    ("integer_arithmetic_direct", 7, "7-02-011"),
    ("rational_arithmetic_direct", 7, "7-03-009"),
    ("absolute_value_opposite", 7, "7-02-005"),
    ("linear_equation_direct", 7, "7-02-016"),
    ("power_arithmetic_direct", 8, "8-01-015"),
    ("square_root_direct", 8, "8-01-008"),
    ("classical_probability_basic", 8, "8-06-012"),
    ("linear_equation_direct", 9, "9-04-003"),
    ("arithmetic_mean_direct", 9, "9-08-010"),
    # Batch #2 — po jedna lekcija svake nove porodice / velikog proširenja.
    ("number_comparison_order", 6, "6-02-002"),
    ("natural_arithmetic_direct", 6, "6-02-005"),
    ("polynomial_basic", 6, "6-02-008"),
    ("divisibility_value_properties", 6, "6-03-002"),
    ("fraction_decimal_conversion", 6, "6-05-003"),
    ("decimal_rounding", 6, "6-05-007"),
    ("ratio_proportion_direct", 6, "6-06-003"),
    ("linear_equation_direct", 6, "6-07-002"),
    ("unit_conversion_direct", 6, "6-13-005"),
    ("linear_equation_direct", 7, "7-02-020"),
    ("rational_arithmetic_direct", 7, "7-03-015"),
    ("simple_quadratic_equation", 8, "8-01-009"),
    ("power_arithmetic_direct", 8, "8-01-017"),
    ("linear_function_direct", 8, "8-02-005"),
    ("ratio_proportion_direct", 8, "8-03-003"),
    ("frequency_basic", 8, "8-06-002"),
    ("polynomial_basic", 8, "8-07-008"),
    ("linear_function_direct", 9, "9-03-014"),
    ("linear_equation_direct", 9, "9-04-016"),
    ("simple_quadratic_equation", 9, "9-06-013"),
    ("unit_conversion_direct", 9, "9-08-013"),
    # Batch #3 — po jedna lekcija svake nove porodice / velikog proširenja.
    ("angle_relationships_direct", 6, "6-09-014"),
    ("angle_relationships_direct", 7, "7-04-008"),
    ("geometry_formula_2d", 7, "7-05-021"),
    ("geometry_formula_2d", 8, "8-08-011"),
    ("pythagoras_direct", 8, "8-04-004"),
    ("solid_geometry_direct", 8, "8-05-006"),
    ("solid_geometry_direct", 9, "9-07-023"),
    ("linear_system_direct", 9, "9-05-007"),
    ("polynomial_basic", 8, "8-07-009"),
    ("polynomial_basic", 9, "9-06-006"),
    # Batch #4 — po jedna lekcija svake nove porodice (i profilirane lekcije:
    # praktična Pitagora, sistemska priča i udaljenost tačaka nose
    # lekcijski-relativni profil težine, pa lifecycle dokazuje i tu granicu).
    ("rational_expression_direct", 9, "9-01-005"),
    ("rational_equation_direct", 9, "9-01-014"),
    ("structured_word_problem", 6, "6-03-010"),
    ("structured_word_problem", 8, "8-04-016"),
    ("structured_word_problem", 9, "9-05-013"),
    ("finite_set_direct", 6, "6-01-006"),
    ("number_set_membership", 8, "8-01-002"),
    ("event_probability_facts", 8, "8-06-011"),
    ("financial_arithmetic_direct", 9, "9-08-005"),
    ("parametric_linear_discussion", 9, "9-04-022"),
    ("linear_inequality_direct", 9, "9-04-013"),
    ("operation_property_recognition", 7, "7-02-014"),
    ("fraction_concept_direct", 6, "6-04-003"),
    ("similarity_direct", 8, "8-03-016"),
    ("polygon_angle_direct", 8, "8-08-003"),
    ("unit_conversion_direct", 7, "7-05-018"),
    ("coordinate_line_direct", 8, "8-02-004"),
]


def turn(grade, lesson, message="Daj mi jedan zadatak za vježbu iz ove teme.",
         session_id="exp-int", **changes):
    payload = {
        "session_id": session_id, "grade": grade, "selected_topic": lesson,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


@pytest.mark.parametrize("family,grade,lesson",
                         REPRESENTATIVES,
                         ids=[f"{family}-{lesson}"
                              for family, _g, lesson in REPRESENTATIVES])
def test_full_zero_call_lifecycle_for_each_new_family(universal, family,
                                                      grade, lesson):
    store, fake = SessionStore(), FakeLLM()
    session_id = f"exp-{lesson}"

    # 1) Svjež zadatak.
    fresh = run_practice_turn(store, fake, turn(grade, lesson,
                                                session_id=session_id))
    assert fresh["status"] == "ready", (lesson, fresh)
    session = store.peek(session_id)
    assert session["deterministic_task"], lesson
    assert session["deterministic_task"]["family_id"] == family
    assert len(session["current_options"]) == 4
    assert session["difficulty_level"] == 1

    # 2) Teži pa lakši — serverska tranzicija nivoa.
    harder = run_practice_turn(store, fake, turn(
        grade, lesson, "Daj mi teži zadatak.", session_id=session_id,
        difficulty_request="harder"))
    assert harder["status"] == "ready"
    assert store.peek(session_id)["difficulty_level"] == 2
    easier = run_practice_turn(store, fake, turn(
        grade, lesson, "Daj mi lakši zadatak.", session_id=session_id,
        difficulty_request="easier"))
    assert easier["status"] == "ready"
    assert store.peek(session_id)["difficulty_level"] == 1

    # 3) Hint nad aktivnim determinističkim zadatkom.
    hint = run_practice_turn(store, fake, turn(
        grade, lesson, "Ne znam.", session_id=session_id,
        intent="hint_request",
        last_tutor_task=store.peek(session_id)["current_task"]))
    assert hint["answer"], lesson
    assert store.peek(session_id)["hint_level"] == 1

    # 4) Klik na TAČNU opciju — server presuđuje bez modela.
    session = store.peek(session_id)
    correct_id = session["correct_option_id"]
    click = run_practice_turn(store, fake, turn(
        grade, lesson, "", session_id=session_id,
        interaction_type="choice_answer", selected_option_id=correct_id,
        client_turn_id="click-1"))
    assert click["answer_verdict"] == "correct", (lesson, click)
    assert click["answer"].startswith("Tačno")

    # 5) Nov zadatak pa potpuno rješenje.
    fresh2 = run_practice_turn(store, fake, turn(
        grade, lesson, "Daj mi novi zadatak.", session_id=session_id))
    assert fresh2["status"] == "ready"
    solution = run_practice_turn(store, fake, turn(
        grade, lesson, "Uradi ga ti.", session_id=session_id,
        intent="solution_request",
        last_tutor_task=store.peek(session_id)["current_task"]))
    assert solution["answer"], lesson
    assert solution.get("revealed_correct_option_id")

    # CIJELI životni ciklus: nijedan poziv modela.
    assert fake.call_count == 0, (lesson, fake.call_count)


@pytest.mark.parametrize("family,grade,lesson", REPRESENTATIVES[:3],
                         ids=[lesson for _f, _g, lesson in REPRESENTATIVES[:3]])
def test_free_form_message_still_uses_the_model(universal, family, grade, lesson):
    store, fake = SessionStore(), FakeLLM()
    session_id = f"exp-ff-{lesson}"
    run_practice_turn(store, fake, turn(grade, lesson, session_id=session_id))
    assert fake.call_count == 0

    draft = make_tutor_draft(intent="explanation_request",
                             reply="Objasnimo pravilo korak po korak.")
    fake.queue(draft)
    response = run_practice_turn(store, fake, turn(
        grade, lesson, "Zašto ovo pravilo uopšte radi?",
        session_id=session_id,
        last_tutor_task=store.peek(session_id)["current_task"]))
    assert response["status"] == "ready"
    # Objašnjenje bez novog zadatka staje na JEDNOM pozivu (recenzent
    # pregleda samo pakete zadataka) — isto kao i prije ekspanzije.
    assert fake.call_count == 1


def test_rollback_flag_returns_every_lesson_to_the_model_route(universal,
                                                               monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    store, fake = SessionStore(), FakeLLM()
    task = make_task_payload(
        text="Izračunaj: $12 + 35$",
        options=("$47$", "$46$", "$48$", "$23$"), correct_option_index=0,
        expected="$47$")
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(store, fake, turn(6, "6-02-003",
                                                   session_id="exp-rollback"))
    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert store.peek("exp-rollback")["deterministic_task"] is None
