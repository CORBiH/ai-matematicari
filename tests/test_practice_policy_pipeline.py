"""PP-1 kroz CIO turn: ruta, objava, preflight, promptovi — 0 poziva modela.

Ove provjere zaključavaju da politika veže OBA autora paketa (deterministički
generator i model) na ISTIM tačkama: kandidat prije objave, objava, preflight
za recenzenta — i da Tutor i Recenzent dobiju IDENTIČAN render politike.
"""
import dataclasses
import logging

import pytest

from matbot import practice_policy as pp
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import package_preflight, pipeline
from matbot.tutor import prompts as tutor_prompts
from tests.conftest import (FakeLLM, make_reviewer_final, make_task_payload,
                            make_tutor_draft)

GRADE6_LESSONS = ("6-07-002", "6-07-003", "6-07-004", "6-07-005", "6-07-006")
SESSION = "policy-pipe"


def turn(lesson, message="Daj mi jedan zadatak za vježbu iz ove teme.",
         grade=6, **changes):
    payload = {
        "session_id": SESSION, "grade": grade, "selected_topic": lesson,
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


# ---------------------------------------------------------------------------
# 1) SVJEŽ DETERMINISTIČKI ZADATAK 6. RAZREDA — METODA NEPOZNATOG ČLANA, 0 SDK
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson", GRADE6_LESSONS)
def test_grade6_fresh_turn_publishes_unknown_member_with_zero_calls(
        universal, lesson):
    store, fake = SessionStore(), FakeLLM()

    response = run_practice_turn(store, fake, turn(lesson))

    assert fake.call_count == 0
    assert response["status"] == "ready"
    session = store.peek(SESSION)
    annex = session["deterministic_task"]
    surfaces = [response["answer"], session["solution_summary"],
                *annex["hints"], annex["solution"]]
    for surface in surfaces:
        assert not pp.find_forbidden_method_prose(surface), (lesson, surface)
        assert not pp.find_visible_negative_literals(surface), (lesson, surface)
    for option in session["current_options"]:
        assert not pp.find_visible_negative_literals(option["text"]), (
            lesson, option)


def test_deterministic_lifecycle_stays_zero_calls_under_policy(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn("6-07-003"))
    run_practice_turn(store, fake, turn(
        "6-07-003", message="Ne znam.", intent="hint_request",
        interaction_phase="practice_help", client_turn_id="h1"))
    run_practice_turn(store, fake, turn(
        "6-07-003", message="Uradi ga ti.", intent="solution_request",
        interaction_phase="practice_help", client_turn_id="s1"))
    run_practice_turn(store, fake, turn("6-07-003", message="Daj mi novi zadatak."))
    assert fake.call_count == 0


# ---------------------------------------------------------------------------
# 2) STRUKTURNI PREKRŠAJ PADA PRIJE MUTACIJE STANJA (STEP 17.M)
# ---------------------------------------------------------------------------

class _ForcedTranspositionGenerator:
    """Pravi generator porodice, ali s NAMETNUTOM transpozicionom prozom —
    simulacija regresije u kojoj bi motor opet počeo učiti prebacivanje."""

    def __init__(self, real):
        self._real = real

    def supports(self, parameters):
        return self._real.supports(parameters)

    def generate_package(self, **kwargs):
        kwargs.pop("policy", None)          # regresija: ignoriše politiku
        package = self._real.generate_package(**kwargs)
        return dataclasses.replace(
            package,
            hints=("Prebaci poznati član na drugu stranu sa suprotnim "
                   "predznakom.",) + package.hints[1:],
            method_id="transposition")


def test_forbidden_method_package_is_blocked_before_any_state_mutation(
        universal, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="matbot.tutor")
    family = lesson_context_module.build(6, "6-07-002").semantic_contract.family_id
    real = pipeline._DETERMINISTIC_GENERATORS[family]
    monkeypatch.setitem(pipeline._DETERMINISTIC_GENERATORS, family,
                        _ForcedTranspositionGenerator(real))
    store, fake = SessionStore(), FakeLLM()

    response = run_practice_turn(store, fake, turn("6-07-002"))

    assert fake.call_count == 0
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None          # nijedna mutacija nije commitovana
    assert "deterministic_policy" in caplog.text
    assert pp.METHOD_PROVENANCE_CODE in caplog.text \
        or pp.FORBIDDEN_METHOD_CODE in caplog.text


# ---------------------------------------------------------------------------
# 3) DOSLOVNI PRODUKCIJSKI SLUČAJ — x - 1/2 > -3/14 NE MOŽE U OBJAVU
# ---------------------------------------------------------------------------

def _screenshot_task(context):
    """Doslovni produkcijski zadatak (2026-08-08): $x - 1/2 > -3/14$ s
    transpozicionim rješenjem — sada nemoguć pod Q+ politikom lekcije."""
    payload = make_task_payload(
        text=r"Riješi nejednačinu: $x - \frac{1}{2} > -\frac{3}{14}$",
        options=[r"$x > -\frac{3}{14}$", r"$x > \frac{2}{7}$",
                 r"$x < \frac{2}{7}$", r"$x > \frac{4}{7}$"],
        correct_option_index=1, expected=r"$x > \frac{2}{7}$")
    return payload.model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title,
        "solution": (r"Prebaci $\frac{1}{2}$ na drugu stranu: "
                     r"$x > \frac{2}{7}$."),
    })


def test_screenshot_package_fails_publication_under_q_plus_policy(universal):
    context = lesson_context_module.build(6, "6-07-003")
    task = _screenshot_task(context)
    with pytest.raises(Exception) as excinfo:
        pipeline._validate_task_server_side(task, context)
    message = str(excinfo.value)
    assert (pp.VISIBLE_DOMAIN_CODE in message
            or pp.FORBIDDEN_METHOD_CODE in message)


def test_screenshot_package_is_rejected_on_the_model_path_end_to_end(
        universal):
    """Model-put: Tutor predloži doslovno produkcijski zadatak, recenzent ga
    (pogrešno) odobri — objava SVEJEDNO pada zatvoreno, stanje netaknuto."""
    context = lesson_context_module.build(6, "6-07-003")
    draft = make_tutor_draft(intent="generate_task",
                             new_task=_screenshot_task(context))
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(store, fake, turn(
        "6-07-003", message="Hoću zadatak s razlomcima gdje se oduzima."))

    assert fake.call_count == 2                  # tačno dva poziva, bez trećeg
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None


def test_advanced_scope_task_is_rejected_at_publication(universal):
    """Odluka I: Vježbajmo ne smije UVESTI sin/cos/tg/log — na bilo kojoj
    lekciji čija ih politika ne dozvoljava (trenutno: nijedna)."""
    context = lesson_context_module.build(9, "9-01-001")
    if context is None:
        pytest.skip("lekcija ne postoji")
    payload = make_task_payload(
        text=r"Izračunaj $\sin(30^\circ) + 1$.",
        options=[r"$\frac{3}{2}$", "$1$", "$2$", r"$\frac{1}{2}$"],
        correct_option_index=0, expected=r"$\frac{3}{2}$")
    payload = payload.model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title,
        "solution": r"Pošto je $\sin(30^\circ) = \frac{1}{2}$, zbir je $\frac{3}{2}$."})
    with pytest.raises(Exception) as excinfo:
        pipeline._validate_task_server_side(payload, context)
    assert pp.ADVANCED_SCOPE_CODE in str(excinfo.value)


def test_grade7_negative_rhs_still_publishes(universal):
    """KONTROLA: ista nejednačina s negativnom desnom stranom je legitimna u
    7. razredu (skup Z; ugovor lekcije deklariše integer/rational domen)."""
    context = lesson_context_module.build(7, "7-02-019")
    policy = context.practice_policy
    assert policy.visible_number_domain == pp.VISIBLE_DOMAIN_ANY
    assert pp.package_policy_failures(
        policy, r"Riješi nejednačinu: $x + 5 > -2$", [r"$x > -7$"],
        ["Prebaci poznati član sa suprotnim predznakom."], "",
        "transposition") == ()


# ---------------------------------------------------------------------------
# 4) PREFLIGHT — recenzent dobija nalaz politike
# ---------------------------------------------------------------------------

def test_preflight_reports_policy_codes_for_a_model_draft(universal):
    context = lesson_context_module.build(6, "6-07-003")
    task = _screenshot_task(context)

    issues = package_preflight.collect_package_issues(
        task, contract=context.semantic_contract,
        practice_policy=context.practice_policy)

    codes = {issue.code for issue in issues}
    assert pp.VISIBLE_DOMAIN_CODE in codes
    assert pp.FORBIDDEN_METHOD_CODE in codes
    block = package_preflight.format_for_reviewer(issues)
    assert pp.VISIBLE_DOMAIN_CODE in block
    assert "unknown-member relations" in block


# ---------------------------------------------------------------------------
# 5) PARITET PROMPTOVA + IDENTITET POLITIKE (STEP 17.K/L)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("grade,lesson", [
    (6, "6-07-003"), (6, "6-04-009"), (8, "8-03-004"), (9, "9-07-001"),
])
def test_tutor_and_reviewer_render_the_identical_policy_block(grade, lesson):
    context = lesson_context_module.build(grade, lesson)
    if context is None:
        pytest.skip("lekcija ne postoji u ovom kurikulumu")
    from matbot.rules import build_shared_math_rules
    shared = build_shared_math_rules(context.grade, context.title,
                                     context.oblast, mode="practice")
    tutor = tutor_prompts.build_tutor_instructions(context)
    reviewer = tutor_prompts.build_reviewer_instructions(context)
    assert shared in tutor and shared in reviewer
    assert "Trigonometrijske funkcije" in shared     # scope granica u OBA


def test_arrow_method_reaches_both_prompts_for_proportionality(universal):
    context = lesson_context_module.build(8, "8-03-004")
    tutor = tutor_prompts.build_tutor_instructions(context)
    reviewer = tutor_prompts.build_reviewer_instructions(context)
    for text in (tutor, reviewer):
        assert "METODU STRELICA" in text
        assert "\\uparrow\\uparrow" in text and "\\uparrow\\downarrow" in text


def test_pipeline_passes_the_same_resolved_policy_to_the_generator(
        universal, monkeypatch):
    context = lesson_context_module.build(6, "6-07-002")
    family = context.semantic_contract.family_id
    real = pipeline._DETERMINISTIC_GENERATORS[family]
    seen = {}

    class Spy:
        def supports(self, parameters):
            return real.supports(parameters)

        def generate_package(self, policy=None, **kwargs):
            seen["policy"] = policy
            return real.generate_package(policy=policy, **kwargs)

    monkeypatch.setitem(pipeline._DETERMINISTIC_GENERATORS, family, Spy())
    store, fake = SessionStore(), FakeLLM()
    response = run_practice_turn(store, fake, turn("6-07-002"))

    assert response["status"] == "ready" and fake.call_count == 0
    policy = seen["policy"]
    assert policy is not None
    assert policy.policy_version == "PP-1"
    assert policy.lesson_id == "6-07-002"
    assert policy.equation_method == pp.METHOD_UNKNOWN_MEMBER
