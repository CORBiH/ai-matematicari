"""Lekcijski-relativna kalibracija težine na modelskoj ruti (Faza F5G).

ŽIVI NALAZI (četiri ponovljena, međusobno nezavisna sudara — po dva na
9-05-013, po jedan na 8-04-016 i 8-05-007, uključujući završnu kapiju na
6e91db8):

    8-05-007  „Pravilna uspravna trostrana piramida“, svježi nivo 1:
              zadatak = direktno izračunavanje zapremine iz $a=6$, $H=9$;
              recenzentov iskren dokaz steps=1 cond=1 ops=3 repr=0 →
              server: level_1_is_not_direct_introductory_application.
    8-04-016  „Praktični problemski zadaci“ (Pitagora), svježi nivo 1:
              isti oblik dokaza (1,1,3,0) → isto odbijanje.
    9-05-013  „Tekstualni zadatak sa sistemom“, svježi nivo 1 (dvaput):
              recenzentov iskren dokaz steps=1 cond=2 ops=1 repr=1 →
              isto odbijanje: dva uslova su SAMA VJEŠTINA lekcije.

KLASIFIKACIJA: B — globalna rubrika nivoa 1 (cond<=1, ops<=2) je za ove
lekcije STRUKTURNO nedostižna: najjednostavniji legitiman zadatak lekcije već
prelazi globalne pragove. Model je odgovarao iskreno; dokaz nije lagao.

RJEŠENJE: lekcijski-relativan profil težine kao PODACI
(data/difficulty_profiles.json), ključan po ZAMRZNUTOJ primarnoj porodici
lekcije (matbot/task_families.py — server-vlasništvo, nikad modelova proza) i
aktivan ISKLJUČIVO za lekcije bez semantičkog ugovora (modelska ruta).
Globalna rubrika bez profila ostaje bajt-za-bajt ista — laka lekcija se NE
popušta.
"""
import json
import re
from pathlib import Path

import pytest

from matbot import difficulty_profiles, task_families
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import package_preflight
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE
from matbot.tutor.schema import (REVIEWER_EVIDENCE_OUTSIDE_TARGET, DifficultyEvidence,
                                 ReviewerChecks, ReviewerFinal, SignatureParameter,
                                 TaskPayload, TaskSignature, TutorDraft, TutorOption,
                                 UnifiedOutputError, difficulty_evidence_errors,
                                 validate_reviewer)
from tests.conftest import FakeLLM

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "difficulty_profiles.json"

# Lekcije iz živih nalaza — SAMO kao test podaci, nikad u produkcijskom kodu.
PYRAMID = (8, "8-05-007")
PYTHAGORAS = (8, "8-04-016")
SYSTEM_WORDS = (9, "9-05-013")
# PETI živi sudar (svježa kapija na 8a68ffe): „Udaljenost između dvije tačke“
# — formula udaljenosti (iskreno ops=3) u oblasti koju routing NE vidi kao
# geometriju, pa je primarna porodica bila opšta i profil nije važio. Ispravka
# je red u data/routing_overrides.json (podaci, ne Python).
DISTANCE_PLANE = (8, "8-02-004")
EASY_CONTROL = (6, "6-03-010")   # „Tekstualni zadaci iz djeljivosti“ — ostaje globalna rubrika

SESSION = "lesson-relative-difficulty"


def ev(steps=1, cond=1, ops=1, repr_changes=0, explanation=False, comparison=False,
       construction=False, proof=False, combines=False):
    return DifficultyEvidence(
        reasoning_steps=steps, condition_count=cond, operation_count=ops,
        representation_change_count=repr_changes, requires_explanation=explanation,
        requires_comparison=comparison, requires_construction=construction,
        requires_proof_or_justification=proof, combines_concepts=combines)


# Iskreni dokazi IZ ŽIVIH ARTEFAKATA — doslovno.
LIVE_PYRAMID_L1 = ev(1, 1, 3, 0)
LIVE_SYSTEM_L1 = ev(1, 2, 1, 1)


def profile_for(grade, topic):
    return difficulty_profiles.resolve_for_context(build(grade, topic))


def turn(grade, topic, message="Daj mi zadatak."):
    return {
        "session_id": SESSION, "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


def checks(**changes):
    base = dict(math_correct=True, marked_option_correct=True, inside_lesson=True,
                intent_handled=True, difficulty_direction_correct=True,
                response_addresses_student=True, task_solvable_and_unambiguous=True,
                mathjax_valid=True, language_age_appropriate=True,
                independently_solved=True, independent_answer="provjereno",
                task_package_consistent=True, difficulty_evidence_valid=True,
                task_signature_consistent=True,
                stem_requires_student_reasoning=True,
                exactly_one_option_correct=True)
    base.update(changes)
    return ReviewerChecks(**base)


def task(context, text, options, *, level=1, correct=0, signature="one",
         task_evidence=None):
    return TaskPayload(
        selected_lesson_id=context.topic_id, selected_lesson_title=context.title,
        target_difficulty_level=level, text=text, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(options)],
        correct_option_index=correct, correct_option_id="abcd"[correct],
        expected_answer=options[correct],
        solution=f"Tačan odgovor je: {options[correct]}",
        difficulty=("easy", "standard", "hard")[level - 1],
        difficulty_evidence=task_evidence if task_evidence is not None else ev(),
        task_signature=TaskSignature(
            task_family="generic", operation_or_relation="application",
            normalized_parameters=[SignatureParameter(name="case", value=signature)],
            required_conditions=["valid"], relevant_objects=["object"],
            answer_type="multiple_choice",
        ),
    )


def queue(fake, context, draft_task, *, decision="approve", final_task=..., reviewed=...):
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="izabrana lekcija", new_task=draft_task)
    final_task = draft_task if final_task is ... else final_task
    reviewed = final_task.difficulty_evidence if reviewed is ... else reviewed
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision=decision, checks=checks(),
        final=draft.model_copy(update={"new_task": final_task}),
        reviewed_difficulty_evidence=reviewed,
    ))


PYRAMID_TEXT = ("Pravilna uspravna trostrana piramida ima ivicu baze "
                "$a=6\\,\\text{cm}$ i visinu $H=9\\,\\text{cm}$. Izračunaj "
                "zapreminu piramide. Koja vrijednost je tačna?")
PYRAMID_OPTIONS = ("$46{,}77\\,\\text{cm}^3$", "$140{,}3\\,\\text{cm}^3$",
                   "$15{,}59\\,\\text{cm}^3$", "$93{,}53\\,\\text{cm}^3$")
SYSTEM_TEXT = ("Zbir dva broja je $12$, a njihova razlika je $4$. Postavi "
               "sistem jednačina i odredi ta dva broja. Koji par je tačan?")
SYSTEM_OPTIONS = ("$x=8$, $y=4$", "$x=7$, $y=5$", "$x=9$, $y=3$", "$x=6$, $y=6$")


# ---------------------------------------------------------------------------
# 1) RAZRJEŠENJE PROFILA — isključivo serverske činjenice, nikad model
# ---------------------------------------------------------------------------

def test_collision_lessons_resolve_a_lesson_relative_profile():
    for grade, topic in (PYRAMID, PYTHAGORAS, DISTANCE_PLANE):
        profile = profile_for(grade, topic)
        assert profile is not None, topic
        assert profile.profile_id == "direct_formula_application", topic
    profile = profile_for(*SYSTEM_WORDS)
    assert profile is not None
    assert profile.profile_id == "system_word_translation"


def test_live_distance_gate_evidence_is_level_1_under_its_profile():
    """Kapija na 8a68ffe: „Koliko iznosi rastojanje između tačaka A(1,2) i
    B(5,5)?“ — recenzentov iskren dokaz (1,1,3,0), odobreno, server odbio.
    S override-om porodice profil sada važi i dokaz je validan nivo 1."""
    profile = profile_for(*DISTANCE_PLANE)
    assert difficulty_evidence_errors(ev(1, 1, 3, 0), 1, profile=profile) == ()


def test_easy_control_lesson_keeps_the_global_rubric():
    assert profile_for(*EASY_CONTROL) is None


def test_profile_resolution_is_strategy_independent():
    """Batch #4: profil se razrješava po primarnoj porodici za SVAKU lekciju
    — i determinističku i model-only — pa lekcija ima tačno JEDAN autoritet
    težine. (U F5G je važila konzervativna ograda „samo bez semantičkog
    ugovora“; aktivacija profiliranih lekcija ju je učinila štetnom: vratila
    bi globalni sudar na slobodne model-turnove istih lekcija.) Da SVAKI
    deterministički paket zadovoljava razriješeni profil na svakom nivou
    dokazuje tests/test_batch4_deterministic.py."""
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(encoding="utf-8"))
    assignments = difficulty_profiles.family_assignments()
    for lesson_id in sorted(compiled["lessons"].keys()):
        context = build(int(lesson_id.split("-")[0]), lesson_id)
        assert context is not None, lesson_id
        profile = difficulty_profiles.resolve_for_context(context)
        if context.primary_family in assignments:
            assert profile is not None, lesson_id
        else:
            assert profile is None, lesson_id


def test_profile_resolution_uses_only_server_context():
    """Model ne može sam izabrati blažu rubriku: razrješenje ne prima nijedan
    dio modelovog payloada — potpis funkcije je (context) i ništa drugo."""
    import inspect

    parameters = list(inspect.signature(
        difficulty_profiles.resolve_for_context).parameters)
    assert parameters == ["context"]


def test_profile_coverage_is_family_driven():
    """Profil nose TAČNO lekcije čija je primarna porodica dodijeljena u
    data/difficulty_profiles.json — ništa po ID-ju, ništa po naslovu. Skup
    dodjela ostaje minimalan (dvije porodice iz živih F5G/F5H dokaza)."""
    assignments = difficulty_profiles.family_assignments()
    assert assignments == {
        "direct_formula_application": "direct_formula_application",
        "system_word_problem": "system_word_translation",
    }
    topics = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    profiled = {}
    for grade_key, grade_data in topics["grades"].items():
        for lesson in grade_data["lessons"]:
            context = build(int(grade_key), lesson["id"])
            profile = difficulty_profiles.resolve_for_context(context)
            expected = assignments.get(context.primary_family)
            assert (profile.profile_id if profile else None) == expected, \
                lesson["id"]
            if profile is not None:
                profiled.setdefault(profile.profile_id, []).append(lesson["id"])
    assert "8-02-004" in profiled["direct_formula_application"]
    assert "9-05-013" in profiled["system_word_translation"]


# ---------------------------------------------------------------------------
# 2) PODACI PROFILA — integritet i disciplina (lekcija = podaci)
# ---------------------------------------------------------------------------

def test_profile_data_contains_no_lesson_identity():
    raw = DATA_PATH.read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d{2}-\d{3}\b", raw), (
        "Profili se ključaju po porodici, nikad po ID-ju lekcije.")


def test_every_assignment_references_known_family_and_profile():
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    profiles = payload["profiles"]
    for row in payload["assignments"]:
        assert row["primary_family"] in task_families.FAMILY_DESCRIPTIONS, row
        assert row["profile"] in profiles, row
        assert row.get("evidence"), f"dodjela bez zapisanog dokaza: {row}"


def test_profile_levels_are_disjoint_and_progressive():
    """Nivo 2 mora POČINJATI iznad kapaciteta nivoa 1, a nivo 3 iznad
    kapaciteta nivoa 2 — teže ostaje mjerljivo teže na svakom profilu."""
    for profile in difficulty_profiles.all_profiles().values():
        level1, level2, level3 = (profile.levels[level] for level in (1, 2, 3))
        assert level2.require_any and level3.require_any, profile.profile_id
        for atom in level2.require_any:
            kind, name, minimum = atom
            if kind == "field":
                cap = level1.max_counts.get(name)
                assert cap is not None and minimum > cap, (profile.profile_id, atom)
            else:
                assert name in level1.forbidden_flags, (profile.profile_id, atom)
        for field, cap in level2.max_counts.items():
            floors = [atom for atom in level3.require_any
                      if atom[0] == "field" and atom[1] == field]
            assert floors and all(minimum == cap + 1 for _, _, minimum in floors), (
                profile.profile_id, field)


# ---------------------------------------------------------------------------
# 3) RUBRIKA POD PROFILOM — živi dokazi moraju sada proći nivo 1
# ---------------------------------------------------------------------------

def test_live_pyramid_evidence_is_level_1_under_its_profile():
    profile = profile_for(*PYRAMID)
    assert difficulty_evidence_errors(LIVE_PYRAMID_L1, 1, profile=profile) == ()


def test_live_pythagoras_evidence_is_level_1_under_its_profile():
    profile = profile_for(*PYTHAGORAS)
    assert difficulty_evidence_errors(LIVE_PYRAMID_L1, 1, profile=profile) == ()


def test_live_system_evidence_is_level_1_under_its_profile():
    profile = profile_for(*SYSTEM_WORDS)
    assert difficulty_evidence_errors(LIVE_SYSTEM_L1, 1, profile=profile) == ()


def test_same_evidence_stays_rejected_on_the_global_rubric():
    """Laka lekcija se NE popušta: isti dokazi bez profila i dalje padaju."""
    assert difficulty_evidence_errors(LIVE_PYRAMID_L1, 1) == (
        "level_1_is_not_direct_introductory_application",)
    assert difficulty_evidence_errors(LIVE_SYSTEM_L1, 1) == (
        "level_1_is_not_direct_introductory_application",)


PYRAMID_NOT_L1 = {
    "obrnuto rješavanje (iz V do H)": ev(2, 1, 4, 0),
    "pretvaranje jedinica uz račun": ev(1, 1, 3, 2),
    "drugi nezavisan uslov": ev(1, 2, 3, 0),
    "četiri operacije": ev(1, 1, 4, 0),
    "kombinovanje koncepata": ev(1, 1, 3, 0, combines=True),
    "višefazno izvođenje": ev(3, 2, 6, 1),
}


@pytest.mark.parametrize("label,evidence", sorted(PYRAMID_NOT_L1.items()))
def test_harder_pyramid_shapes_stay_rejected_at_level_1(label, evidence):
    profile = profile_for(*PYRAMID)
    assert difficulty_evidence_errors(evidence, 1, profile=profile), label


SYSTEM_NOT_L1 = {
    "tri uslova": ev(2, 3, 2, 1),
    "četiri operacije": ev(2, 2, 4, 1),
    "kombinovanje s procentima": ev(2, 2, 2, 1, combines=True),
    "tri koraka rezonovanja": ev(3, 2, 2, 1),
}


@pytest.mark.parametrize("label,evidence", sorted(SYSTEM_NOT_L1.items()))
def test_complex_system_shapes_stay_rejected_at_level_1(label, evidence):
    profile = profile_for(*SYSTEM_WORDS)
    assert difficulty_evidence_errors(evidence, 1, profile=profile), label


def test_profiled_level_2_and_3_remain_progressively_harder():
    profile = profile_for(*PYRAMID)
    # Direktan zadatak (nivo 1) NE SMIJE proći kao nivo 2 — lažno nizak dokaz
    # za traženi viši nivo deterministički pada.
    assert difficulty_evidence_errors(LIVE_PYRAMID_L1, 2, profile=profile)
    # Obrnuti zadatak jeste nivo 2.
    assert difficulty_evidence_errors(ev(2, 1, 4, 0), 2, profile=profile) == ()
    # Višefazno izvođenje nije nivo 2, ali jeste nivo 3.
    assert difficulty_evidence_errors(ev(3, 2, 6, 1), 2, profile=profile)
    assert difficulty_evidence_errors(ev(3, 2, 6, 1), 3, profile=profile) == ()

    system_profile = profile_for(*SYSTEM_WORDS)
    assert difficulty_evidence_errors(LIVE_SYSTEM_L1, 2, profile=system_profile)
    assert difficulty_evidence_errors(ev(3, 2, 4, 1), 2, profile=system_profile) == ()
    assert difficulty_evidence_errors(ev(4, 3, 6, 2), 2, profile=system_profile)
    assert difficulty_evidence_errors(ev(4, 3, 6, 2), 3, profile=system_profile) == ()


def test_negative_counts_are_rejected_under_a_profile():
    profile = profile_for(*PYRAMID)
    assert difficulty_evidence_errors(ev(steps=-1), 1, profile=profile)


def test_unknown_target_level_is_rejected_under_a_profile():
    profile = profile_for(*PYRAMID)
    assert difficulty_evidence_errors(ev(), 4, profile=profile)


# ---------------------------------------------------------------------------
# 4) SVA TRI SLOJA AUTORITETA DIJELE PROFIL
# ---------------------------------------------------------------------------

def test_preflight_flags_a_profiled_draft_outside_level_1():
    context = build(*PYRAMID)
    profile = difficulty_profiles.resolve_for_context(context)
    payload = task(context, PYRAMID_TEXT, PYRAMID_OPTIONS,
                   task_evidence=ev(3, 2, 6, 1))
    codes = [issue.code for issue in package_preflight.collect_package_issues(
        payload, difficulty_profile=profile)]
    assert package_preflight.DIFFICULTY_OUTSIDE_TARGET_CODE in codes


def test_preflight_accepts_the_live_pyramid_draft_under_its_profile():
    context = build(*PYRAMID)
    profile = difficulty_profiles.resolve_for_context(context)
    payload = task(context, PYRAMID_TEXT, PYRAMID_OPTIONS,
                   task_evidence=LIVE_PYRAMID_L1)
    codes = [issue.code for issue in package_preflight.collect_package_issues(
        payload, difficulty_profile=profile)]
    assert package_preflight.DIFFICULTY_OUTSIDE_TARGET_CODE not in codes
    # Bez profila isti nacrt i dalje nosi nalaz — globalna rubrika netaknuta.
    codes = [issue.code for issue in package_preflight.collect_package_issues(payload)]
    assert package_preflight.DIFFICULTY_OUTSIDE_TARGET_CODE in codes


def test_validate_reviewer_accepts_the_live_pyramid_approval_under_its_profile():
    context = build(*PYRAMID)
    profile = difficulty_profiles.resolve_for_context(context)
    payload = task(context, PYRAMID_TEXT, PYRAMID_OPTIONS,
                   task_evidence=LIVE_PYRAMID_L1)
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="zapremina piramide", new_task=payload)
    reviewer = ReviewerFinal(decision="approve", checks=checks(), final=draft,
                             reviewed_difficulty_evidence=LIVE_PYRAMID_L1)
    validate_reviewer(reviewer, difficulty_profile=profile)
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(reviewer)   # bez profila: stara kontradikcija ostaje


def test_validate_reviewer_still_rejects_an_overcomplex_profiled_approval():
    context = build(*PYRAMID)
    profile = difficulty_profiles.resolve_for_context(context)
    overcomplex = ev(3, 2, 6, 1)
    payload = task(context, PYRAMID_TEXT, PYRAMID_OPTIONS, task_evidence=overcomplex)
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="zapremina piramide", new_task=payload)
    reviewer = ReviewerFinal(decision="approve", checks=checks(), final=draft,
                             reviewed_difficulty_evidence=overcomplex)
    with pytest.raises(UnifiedOutputError) as error:
        validate_reviewer(reviewer, difficulty_profile=profile)
    assert REVIEWER_EVIDENCE_OUTSIDE_TARGET in str(error.value)


# ---------------------------------------------------------------------------
# 5) CIJELI DVOPOZIVNI PUT — živi sudari sada objavljuju; teži oblici padaju
# ---------------------------------------------------------------------------

def _enable(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def test_live_pyramid_fresh_level_1_now_publishes(monkeypatch):
    _enable(monkeypatch)
    context, store, fake = build(*PYRAMID), SessionStore(), FakeLLM()
    queue(fake, context, task(context, PYRAMID_TEXT, PYRAMID_OPTIONS,
                              signature="pyramid-direct",
                              task_evidence=LIVE_PYRAMID_L1))

    response = run_practice_turn(store, fake, turn(*PYRAMID))
    session = store.peek(SESSION)

    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert session["current_task"] == PYRAMID_TEXT
    assert session["difficulty_level"] == 1
    assert session["current_task_difficulty_evidence"] == LIVE_PYRAMID_L1.model_dump()


def test_live_system_fresh_level_1_now_publishes(monkeypatch):
    """Batch #4: lekcija je aktivirana deterministički, pa se MODEL-ruta
    (koju ovaj test dokazuje) simulira privremenim uklanjanjem semantičkog
    ugovora — profil se od Batch #4 razrješava po porodici, ne po ugovoru."""
    from matbot.semantics import contracts as semantic_contracts

    _enable(monkeypatch)
    context, store, fake = build(*SYSTEM_WORDS), SessionStore(), FakeLLM()
    queue(fake, context, task(context, SYSTEM_TEXT, SYSTEM_OPTIONS,
                              signature="system-direct",
                              task_evidence=LIVE_SYSTEM_L1))

    with semantic_contracts.override_contracts({}):
        response = run_practice_turn(store, fake, turn(*SYSTEM_WORDS))
    session = store.peek(SESSION)

    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert session["current_task"] == SYSTEM_TEXT
    assert session["difficulty_level"] == 1


def test_overcomplex_pyramid_level_1_still_fails_closed(monkeypatch):
    _enable(monkeypatch)
    context, store, fake = build(*PYRAMID), SessionStore(), FakeLLM()
    overcomplex = ev(3, 2, 6, 1)
    queue(fake, context, task(context, PYRAMID_TEXT, PYRAMID_OPTIONS,
                              signature="pyramid-multistage",
                              task_evidence=overcomplex),
          decision="approve", reviewed=overcomplex)

    response = run_practice_turn(store, fake, turn(*PYRAMID))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2


def test_easy_lesson_level_1_still_rejects_three_operations(monkeypatch):
    """Kontrola: globalna rubrika lake lekcije NIJE popuštena kroz pipeline.

    Batch #4: lekcija je deterministički aktivirana, pa se model-ruta (koju
    ovaj test dokazuje) simulira privremenim uklanjanjem ugovora."""
    from matbot.semantics import contracts as semantic_contracts

    _enable(monkeypatch)
    context, store, fake = build(*EASY_CONTROL), SessionStore(), FakeLLM()
    dishonest = ev(1, 1, 3, 0)
    queue(fake, context,
          task(context,
               "Marko ima $24$ jabuke i podijeli ih na $4$ grupe pa pojede "
               "$2$. Koliko ostane u svakoj grupi? ",
               ("$4$", "$6$", "$5$", "$3$"),
               signature="easy-three-ops", task_evidence=dishonest),
          decision="approve", reviewed=dishonest)

    with semantic_contracts.override_contracts({}):
        response = run_practice_turn(store, fake, turn(*EASY_CONTROL))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2


# ---------------------------------------------------------------------------
# 6) PROMPT — Tutor i Recenzent dobijaju ISTI profil, i to samo profilirane
# ---------------------------------------------------------------------------

def test_profiled_lessons_send_the_profile_block_to_both_calls():
    for grade, topic in (PYRAMID, PYTHAGORAS, SYSTEM_WORDS):
        context = build(grade, topic)
        profile = difficulty_profiles.resolve_for_context(context)
        block = profile.prompt_block()
        assert "LESSON-RELATIVE DIFFICULTY PROFILE" in block
        for text in (tutor_prompts.build_tutor_instructions(context),
                     tutor_prompts.build_reviewer_instructions(context)):
            assert block in text, topic


def test_unprofiled_lessons_keep_the_prompt_unchanged():
    # F5J: globalni ACTIVE-TARGETS blok smije POMINJATI profil (kaže šta ga
    # zamjenjuje kad postoji), ali sam profil-blok („server authority for
    # THIS lesson“) ne smije postojati za neprofilisanu lekciju.
    context = build(*EASY_CONTROL)
    for text in (tutor_prompts.build_tutor_instructions(context),
                 tutor_prompts.build_reviewer_instructions(context)):
        assert "server authority for THIS lesson" not in text


# ---------------------------------------------------------------------------
# 7) EVALUACIONA PROVJERA MJERI ISTO ŠTO I SERVER (živi F5H nalaz)
# ---------------------------------------------------------------------------
# U F5H talasu je server ISPRAVNO objavio sva četiri profilirana paketa, a
# `check_package_clean` ih je lažno oborio jer je pozivao preflight BEZ
# profila — harness je mjerio globalnom rubrikom koju server više ne
# primjenjuje na te lekcije.

def _observation(grade, topic, package):
    from tools.practice_eval import checks as eval_checks

    return eval_checks.TurnObservation(
        scenario_id="t", step_index=0, step_kind="text", topic_id=topic,
        grade=grade, request_payload={}, http_status=200, response={},
        session_before=None, session_after=None, sdk_calls=2,
        final_task_package=package)


def test_eval_package_clean_uses_the_lesson_relative_profile():
    from tools.practice_eval import checks as eval_checks

    context = build(*PYRAMID)
    package = task(context, PYRAMID_TEXT, PYRAMID_OPTIONS,
                   task_evidence=LIVE_PYRAMID_L1)
    result = eval_checks.check_package_clean(_observation(*PYRAMID, package))
    assert result.outcome == "pass", result.detail


def test_eval_package_clean_keeps_the_global_rubric_for_easy_lessons():
    from tools.practice_eval import checks as eval_checks

    context = build(*EASY_CONTROL)
    package = task(context, "Marko dijeli $15$ olovaka u $5$ grupa. Koliko u svakoj?",
                   ("$3$", "$5$", "$2$", "$4$"),
                   task_evidence=ev(1, 1, 3, 0))
    result = eval_checks.check_package_clean(_observation(*EASY_CONTROL, package))
    assert result.outcome == "fail"
    assert "difficulty_evidence_outside_target" in result.detail
