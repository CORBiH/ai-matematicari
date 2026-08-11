"""Deterministički nalaz o TEŽINI nacrta mora stići recenzentu prije 2. poziva.

ŽIVI NALAZ (release gate b8a0f7b, scenario `same_level_new`, 11 SDK poziva,
lekcija 6. razreda o pravilima djeljivosti, traženi nivo 1):

    Tutor zadatak:   „Koji od navedenih brojeva je djeljiv sa $6$?“
    Tutor dokaz:     steps=1 conditions=1 operations=1 combines_concepts=TRUE
    Recenzent dokaz: steps=1 conditions=1 operations=2 combines_concepts=TRUE
    Recenzent:       approve, difficulty_evidence_valid=true
    Server:          ODBIO na `stage=reviewer_payload` s
                     `reviewer_approved_difficulty_evidence_outside_target`

Serverska invarijanta POSLIJE recenzenta odradila je svoj posao i sesija je
ostala netaknuta. Ali: Tutor je već SAM prijavio `combines_concepts=true`, pa je
ZAJEDNIČKI validator `difficulty_evidence_errors` mogao dokazati prekršaj nivoa
1 i PRIJE drugog poziva. Taj nalaz nije ulazio u preflight blok, pa je recenzent
morao sam primijetiti neslaganje — i pogrešno je odobrio.

Ovaj modul dokazuje:
  • nalaz o težini se računa nad Tutorovim nacrtom i ulazi u ulaz 2. poziva;
  • recenzent ga smije popraviti u ISTOM drugom pozivu (`correct`);
  • nepromijenjeno `approve` i dalje pada na zatečenoj invarijanti;
  • pragovi se NE mijenjaju — poziva se isti validator, ništa se ne prepisuje;
  • uredan nacrt ne pravi nikakav blok (ulaz ostaje čist).

Vrijednosti dokaza su doslovno one iz artefakta.
"""
import copy

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import package_preflight as preflight
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE
from matbot.tutor.schema import (REVIEWER_EVIDENCE_OUTSIDE_TARGET, DifficultyEvidence,
                                 ReviewerChecks, ReviewerFinal, SignatureParameter,
                                 TaskPayload, TaskSignature, TutorDraft, TutorOption,
                                 difficulty_evidence_errors)
from tests.conftest import FakeLLM

SESSION = "diff-preflight"
GRADE, TOPIC = 6, "6-03-004"

# Tekst zadatka JESTE sačuvan u artefaktu.
LIVE_TASK_TEXT = "Koji od navedenih brojeva je djeljiv sa $6$?"
LIVE_OPTIONS = ("$12$", "$14$", "$16$", "$20$")          # tačno jedan djeljiv sa 6
# Direktna zamjena nivoa 1: JEDNO izričito pravilo djeljivosti.
DIRECT_TASK_TEXT = "Koji od navedenih brojeva je djeljiv sa $5$?"
DIRECT_OPTIONS = ("$15$", "$12$", "$14$", "$16$")        # tačno jedan djeljiv sa 5


def evidence(**updates):
    """Ispravan dokaz nivoa 1; `updates` ga namjerno izvodi van nivoa."""
    values = dict(
        reasoning_steps=1, condition_count=1, operation_count=1,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)
    values.update(updates)
    return DifficultyEvidence(**values)


# Doslovno iz artefakta.
LIVE_TUTOR_EVIDENCE = evidence(combines_concepts=True)
LIVE_REVIEWER_EVIDENCE = evidence(operation_count=2, combines_concepts=True)


def turn(grade=GRADE, topic=TOPIC, message="Daj mi drugi zadatak."):
    return {
        "session_id": SESSION, "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


def task(context, text, options, *, level=1, task_evidence=None, signature="one"):
    return TaskPayload(
        selected_lesson_id=context.topic_id, selected_lesson_title=context.title,
        target_difficulty_level=level, text=text, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(options)],
        correct_option_index=0, correct_option_id="a",
        expected_answer=options[0],
        solution=f"Tačan odgovor je {options[0]}.",
        difficulty=("easy", "standard", "hard")[level - 1],
        difficulty_evidence=task_evidence if task_evidence is not None else evidence(),
        task_signature=TaskSignature(
            task_family="divisibility", operation_or_relation="select",
            normalized_parameters=[SignatureParameter(name="case", value=signature)],
            required_conditions=["rule"], relevant_objects=["number"],
            answer_type="multiple_choice"))


def checks(**changes):
    base = dict(math_correct=True, marked_option_correct=True, inside_lesson=True,
                intent_handled=True, difficulty_direction_correct=True,
                response_addresses_student=True, task_solvable_and_unambiguous=True,
                mathjax_valid=True, language_age_appropriate=True,
                independently_solved=True, independent_answer="provjereno",
                task_package_consistent=True, difficulty_evidence_valid=True,
                task_signature_consistent=True,
                stem_requires_student_reasoning=True)
    base.update(changes)
    return ReviewerChecks(**base)


def queue(fake, draft_task, *, decision="approve", final_task=..., reviewed=...):
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="izabrana lekcija", new_task=draft_task)
    final_task = draft_task if final_task is ... else final_task
    reviewed = final_task.difficulty_evidence if reviewed is ... else reviewed
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision=decision, checks=checks(),
        final=draft.model_copy(update={"new_task": final_task}),
        reviewed_difficulty_evidence=reviewed))
    return draft


def publish_valid_first(store, fake, context):
    """Objavi uredan zadatak nivoa 1 da sesija ima prethodno stanje."""
    queue(fake, task(context, DIRECT_TASK_TEXT, DIRECT_OPTIONS, signature="committed"))
    assert run_practice_turn(store, fake, turn())["status"] == "ready"
    return copy.deepcopy(store.peek(SESSION))


# ---------------------------------------------------------------------------
# 1) NALAZ POSTOJI I ODGOVARA ŽIVIM VRIJEDNOSTIMA
# ---------------------------------------------------------------------------

def test_shared_validator_proves_the_live_tutor_draft_is_not_level_one():
    """Isti validator koji koriste i recenzentska invarijanta i objava."""
    assert difficulty_evidence_errors(LIVE_TUTOR_EVIDENCE, 1) == (
        "level_1_is_not_direct_introductory_application",)
    assert difficulty_evidence_errors(LIVE_REVIEWER_EVIDENCE, 1) == (
        "level_1_is_not_direct_introductory_application",)


def test_preflight_reports_the_live_tutor_difficulty_mismatch():
    context = build(GRADE, TOPIC)
    issues = preflight.collect_package_issues(
        task(context, LIVE_TASK_TEXT, LIVE_OPTIONS, task_evidence=LIVE_TUTOR_EVIDENCE))
    found = [i for i in issues if i.code == preflight.DIFFICULTY_OUTSIDE_TARGET_CODE]
    assert len(found) == 1
    detail = found[0].detail
    assert "target Level 1" in detail
    assert "level_1_is_not_direct_introductory_application" in detail
    # Sigurna, strukturisana polja dokaza.
    for part in ("steps=1", "conditions=1", "operations=1",
                 "representation_changes=0", "flags=combines"):
        assert part in detail, part


def test_difficulty_diagnostic_carries_no_task_content():
    context = build(GRADE, TOPIC)
    issues = preflight.collect_package_issues(
        task(context, LIVE_TASK_TEXT, LIVE_OPTIONS, task_evidence=LIVE_TUTOR_EVIDENCE))
    described = preflight.describe_issues(issues)
    assert len(described) <= 300
    for leaked in (LIVE_TASK_TEXT, "djeljiv", "$12$", "Tačan odgovor", context.title):
        assert leaked not in described, leaked


def test_preflight_reuses_the_shared_validator_instead_of_reimplementing_it():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "matbot" / "tutor"
              / "package_preflight.py").read_text(encoding="utf-8")
    assert "difficulty_evidence_errors" in source
    assert "evidence_diagnostics" in source
    # Nijedan vlastiti prag ni vlastito pravilo nivoa.
    for reimplemented in ("level_1_is_not_direct", "level_2_", "level_3_",
                          "def difficulty_evidence_errors", "reasoning_steps >"):
        assert reimplemented not in source, reimplemented


# ---------------------------------------------------------------------------
# 2) TAČAN ŽIVI SCENARIO KROZ CIJELI DVOPOZIVNI PUT
# ---------------------------------------------------------------------------

def test_A_invalid_approval_still_rejected_with_prior_session_intact(monkeypatch, caplog):
    """Test A: recenzent odobri nacrt čiji dokaz i dalje pada na nivou 1."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    before = publish_valid_first(store, fake, context)

    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_OPTIONS, signature="live",
                     task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="approve", reviewed=LIVE_REVIEWER_EVIDENCE)
    response = run_practice_turn(store, fake, turn())

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert store.peek(SESSION) == before          # prethodna sesija netaknuta
    assert fake.call_count == 4                   # 2 + 2, nikad 3 po turnu
    # Klasifikacija ostaje reviewer_payload_rejection (zatečena invarijanta).
    assert "stage=reviewer_payload" in caplog.text
    assert REVIEWER_EVIDENCE_OUTSIDE_TARGET in caplog.text
    # Nalaz je STVARNO stigao recenzentu prije nego što je pogriješio.
    assert preflight.DIFFICULTY_OUTSIDE_TARGET_CODE in fake.reviewer_calls[1][1]


def test_B_reviewer_replaces_the_task_in_the_same_second_call(monkeypatch):
    """Test B: recenzent zamijeni zadatak direktnim nivoom 1 i objavi ga."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    replacement = task(context, DIRECT_TASK_TEXT, DIRECT_OPTIONS, signature="direct")
    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_OPTIONS, signature="live",
                     task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="correct", final_task=replacement, reviewed=evidence())

    response = run_practice_turn(store, fake, turn())
    session = store.peek(SESSION)

    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1
    # Recenzent je dobio deterministički nalaz u ulazu drugog poziva.
    reviewer_input = fake.reviewer_calls[0][1]
    assert "SERVER-DETECTED DRAFT ISSUES" in reviewer_input
    assert preflight.DIFFICULTY_OUTSIDE_TARGET_CODE in reviewer_input
    assert "target Level 1" in reviewer_input
    assert "level_1_is_not_direct_introductory_application" in reviewer_input
    # Objavljen je RECENZENTOV zamjenski zadatak.
    assert session["current_task"] == DIRECT_TASK_TEXT
    assert LIVE_TASK_TEXT not in response["answer"]
    assert response["answer"].startswith("Evo zadatka.")
    assert session["lesson_id"] == TOPIC
    assert session["difficulty_level"] == 1
    assert session["current_task_difficulty_evidence"] == evidence().model_dump()
    assert set(o["text"] for o in session["current_options"]) == set(DIRECT_OPTIONS)


def test_reviewer_may_still_fail_closed_on_the_finding(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="lekcija",
                       new_task=task(context, LIVE_TASK_TEXT, LIVE_OPTIONS,
                                     task_evidence=LIVE_TUTOR_EVIDENCE))
    fake.queue(draft)
    fake.queue(ReviewerFinal(decision="fail_closed", checks=checks(),
                             fail_reason_code="unsafe_or_unverifiable",
                             final=None, reviewed_difficulty_evidence=None))

    assert run_practice_turn(store, fake, turn())["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2


def test_lowering_counts_without_changing_the_task_is_not_blocked_by_preflight(monkeypatch):
    """Granica koju priznajemo: server ne može semantički pročitati zadatak.

    Kad recenzent spusti brojeve a zadatak ostavi isti, deterministički sloj to
    NE MOŽE dokazati (i po odluci projekta ne gradi semantički parser za 534
    lekcije). Zato zabranu nosi recenzentov prompt, a server ostaje dosljedan:
    objavljen dokaz je doslovno recenzentova tvrdnja, pa je revizibilna."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    same_task = task(context, LIVE_TASK_TEXT, LIVE_OPTIONS, signature="relabelled",
                     task_evidence=LIVE_TUTOR_EVIDENCE)
    queue(fake, same_task, decision="correct",
          final_task=same_task.model_copy(update={"difficulty_evidence": evidence()}),
          reviewed=evidence())

    assert run_practice_turn(store, fake, turn())["status"] == "ready"
    assert fake.call_count == 2
    # Zapisan je TAČNO recenzentov dokaz — ništa se ne „popravlja“ u tišini.
    assert store.peek(SESSION)["current_task_difficulty_evidence"] == evidence().model_dump()
    # A prompt to izričito zabranjuje (jedini sloj koji to može).
    instructions = tutor_prompts.build_reviewer_instructions(context)
    assert "never fix this by lowering the reported counts" in instructions.lower()


# ---------------------------------------------------------------------------
# 3) TUTOR PODCJENJUJE BROJEVE — jedan sumnjiv broj ne skriva napredni flag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("updates,expected_code", [
    pytest.param({"combines_concepts": True},
                 "level_1_is_not_direct_introductory_application", id="live-combines-only"),
    pytest.param({"operation_count": 3},
                 "level_1_is_not_direct_introductory_application", id="three-operations"),
    pytest.param({"reasoning_steps": 3},
                 "level_1_is_not_direct_introductory_application", id="excessive-steps"),
    pytest.param({"condition_count": 4},
                 "level_1_is_not_direct_introductory_application", id="excessive-conditions"),
    pytest.param({"requires_construction": True},
                 "level_1_is_not_direct_introductory_application", id="construction-flag"),
    pytest.param({"requires_proof_or_justification": True},
                 "level_1_is_not_direct_introductory_application", id="proof-flag"),
    pytest.param({"representation_change_count": 2},
                 "level_1_is_not_direct_introductory_application", id="two-representation-changes"),
    pytest.param({"requires_explanation": True},
                 "level_1_is_not_direct_introductory_application", id="explanation-flag"),
    # Faza 4C: samo poređenje je legitiman nivo 1; poređenje UZ dodatni korak nije.
    pytest.param({"requires_comparison": True, "reasoning_steps": 2},
                 "level_1_is_not_direct_introductory_application", id="comparison-flag"),
])
def test_level_one_underreporting_is_still_detected(updates, expected_code):
    """Minimalni brojevi ne mogu sakriti napredan flag — i obratno."""
    context = build(GRADE, TOPIC)
    issues = preflight.collect_package_issues(
        task(context, LIVE_TASK_TEXT, LIVE_OPTIONS, task_evidence=evidence(**updates)))
    found = [i for i in issues if i.code == preflight.DIFFICULTY_OUTSIDE_TARGET_CODE]
    assert len(found) == 1
    assert expected_code in found[0].detail


def test_valid_level_one_evidence_creates_no_difficulty_issue():
    context = build(GRADE, TOPIC)
    issues = preflight.collect_package_issues(
        task(context, DIRECT_TASK_TEXT, DIRECT_OPTIONS, task_evidence=evidence()))
    assert issues == ()


# ---------------------------------------------------------------------------
# 4) ISTI MEHANIZAM NA SVA TRI NIVOA (pragovi se ne diraju)
# ---------------------------------------------------------------------------

LEVEL_CASES = [
    # (nivo, izmjene dokaza, očekuje li se nalaz)
    pytest.param(1, {}, False, id="L1-direct-ok"),
    pytest.param(1, {"combines_concepts": True}, True, id="L1-combines-issue"),
    pytest.param(1, {"operation_count": 3}, True, id="L1-three-operations-issue"),
    pytest.param(1, {"requires_proof_or_justification": True}, True, id="L1-proof-issue"),
    pytest.param(2, {"reasoning_steps": 2, "condition_count": 2, "operation_count": 2},
                 False, id="L2-bounded-pair-ok"),
    pytest.param(2, {}, True, id="L2-too-thin-issue"),
    pytest.param(2, {"reasoning_steps": 2, "condition_count": 2, "operation_count": 2,
                     "requires_construction": True}, True, id="L2-construction-issue"),
    pytest.param(2, {"reasoning_steps": 2, "condition_count": 2, "operation_count": 2,
                     "requires_proof_or_justification": True}, True, id="L2-proof-issue"),
    pytest.param(2, {"reasoning_steps": 3, "condition_count": 2, "operation_count": 2},
                 True, id="L2-excessive-steps-issue"),
    pytest.param(3, {"reasoning_steps": 3, "condition_count": 3, "operation_count": 3,
                     "requires_proof_or_justification": True, "combines_concepts": True},
                 False, id="L3-advanced-ok"),
    pytest.param(3, {"requires_construction": True}, False, id="L3-construction-ok"),
    pytest.param(3, {}, True, id="L3-lacks-advanced-signal-issue"),
]


@pytest.mark.parametrize("level,updates,expect_issue", LEVEL_CASES)
def test_preflight_matches_the_shared_validator_at_every_level(level, updates, expect_issue):
    """Nalaz preflighta mora biti TAČNO ono što zajednički validator kaže."""
    context = build(GRADE, TOPIC)
    task_evidence = evidence(**updates)
    issues = preflight.collect_package_issues(
        task(context, DIRECT_TASK_TEXT, DIRECT_OPTIONS, level=level,
             task_evidence=task_evidence))
    found = [i for i in issues if i.code == preflight.DIFFICULTY_OUTSIDE_TARGET_CODE]
    shared = difficulty_evidence_errors(task_evidence, level)
    assert bool(found) is expect_issue
    assert bool(found) is bool(shared)      # nikad vlastita procjena
    if found:
        for code in shared:
            assert code in found[0].detail


# ---------------------------------------------------------------------------
# 5) UREDAN NACRT NE PRAVI ŠUM
# ---------------------------------------------------------------------------

def test_clean_draft_adds_no_block_to_reviewer_input(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, DIRECT_TASK_TEXT, DIRECT_OPTIONS, signature="clean"))

    assert run_practice_turn(store, fake, turn())["status"] == "ready"
    reviewer_input = fake.reviewer_calls[0][1]
    assert "SERVER-DETECTED DRAFT ISSUES" not in reviewer_input
    assert preflight.DIFFICULTY_OUTSIDE_TARGET_CODE not in reviewer_input
    # Ulaz završava kako je i ranije završavao — bez praznog bloka.
    assert reviewer_input.endswith("Vrati strukturisanu odluku prema šemi.")
    assert fake.call_count == 2


def test_empty_issue_tuple_still_formats_to_nothing():
    assert preflight.format_for_reviewer(()) == ""


# ---------------------------------------------------------------------------
# 6) OBA SLOJA RADE NEZAVISNO (odbrana u dubini)
# ---------------------------------------------------------------------------

def test_post_reviewer_invariant_holds_even_without_any_preflight_finding(monkeypatch):
    """Kad je Tutorov nacrt uredan a recenzent SAM uvede loš dokaz, zatečena
    invarijanta poslije drugog poziva i dalje mora odbiti turn."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    clean_draft = task(context, DIRECT_TASK_TEXT, DIRECT_OPTIONS, signature="clean")
    assert preflight.collect_package_issues(clean_draft) == ()
    queue(fake, clean_draft, decision="approve", reviewed=LIVE_REVIEWER_EVIDENCE)

    assert run_practice_turn(store, fake, turn())["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2


def test_new_difficulty_issue_never_reclassifies_a_reviewer_payload_rejection(monkeypatch, caplog):
    """Nova provjera ne smije preoteti klasifikaciju zatečenom sloju.

    `collect_package_issues` se pokreće i nad KONAČNIM paketom (tamo odbija sa
    `reviewer_final_mcq_integrity_rejection`). Da nova stavka nije bezopasna
    tamo, isti živi pad bi odjednom mijenjao klasu. Ne mijenja: `validate_reviewer`
    trči PRIJE i odbija kontradikciju na `stage=reviewer_payload`, a kad prođe,
    server upiše recenzentov dokaz u konačni paket — pa je taj dokaz po
    konstrukciji već valjan za deklarisani nivo i nova stavka ne može okinuti."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_OPTIONS, signature="live",
                     task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="approve", reviewed=LIVE_REVIEWER_EVIDENCE)

    assert run_practice_turn(store, fake, turn())["answer"] == SAFE_ERROR_MESSAGE
    assert "stage=reviewer_payload" in caplog.text
    assert "stage=reviewer_final_mcq" not in caplog.text
    assert "reviewer_final_mcq_integrity_rejection" not in caplog.text
    assert fake.call_count == 2


def test_published_final_package_never_carries_a_difficulty_issue(monkeypatch):
    """Kad se turn objavi, konačni paket je po konstrukciji bez tog nalaza."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    replacement = task(context, DIRECT_TASK_TEXT, DIRECT_OPTIONS, signature="fixed")
    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_OPTIONS, signature="flawed",
                     task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="correct", final_task=replacement, reviewed=evidence())

    assert run_practice_turn(store, fake, turn())["status"] == "ready"
    assert preflight.collect_package_issues(replacement) == ()


def test_preflight_finding_alone_never_rejects_the_tutor_draft(monkeypatch):
    """Nacrt s nalazom NE SMIJE pasti prije recenzenta — on je predmet ispravke."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    replacement = task(context, DIRECT_TASK_TEXT, DIRECT_OPTIONS, signature="fixed")
    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_OPTIONS, signature="flawed",
                     task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="correct", final_task=replacement, reviewed=evidence())

    # Drugi poziv se STVARNO dogodio i turn je objavljen.
    assert run_practice_turn(store, fake, turn())["status"] == "ready"
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1


# ---------------------------------------------------------------------------
# 7) PROMPT I KLASIFIKACIJA
# ---------------------------------------------------------------------------

def test_reviewer_instructions_cover_the_difficulty_finding():
    instructions = tutor_prompts.build_reviewer_instructions(build(GRADE, TOPIC))
    lowered = instructions.lower()
    assert "difficulty_evidence_outside_target" in lowered
    assert "replace the\n  task" in lowered or "replace the task" in lowered
    assert "never fix this by lowering the reported counts" in lowered
    assert "relabelling the level" in lowered
    assert "keep the exact selected lesson" in lowered
    # Univerzalno: MOTOR ne dodaje djelioce ni ID lekcije. Riječ „djeljiv“
    # SMIJE postojati — dolazi iz KOMPAJLIRANOG semantičkog ugovora lekcije
    # (podatak, kapacitetna ekspanzija), nikad iz koda motora; to dokazuje
    # provjera na lekciji BEZ ugovora ispod.
    for word in ("divisor 6", "6-03-004"):
        assert word not in instructions
    uncontracted = tutor_prompts.build_reviewer_instructions(build(6, "6-01-001"))
    assert "djeljiv" not in uncontracted


def test_gate_classification_separates_the_two_rejection_layers():
    from scratchpad import run_difficulty_canary as canary

    assert canary._classify_failure([
        "tutor_rejected request_id=a topic=6-03-004 stage=reviewer_payload "
        f"intent=generate_task detail={REVIEWER_EVIDENCE_OUTSIDE_TARGET}: "
        "decision=approve target_level=1"
    ]) == "reviewer_payload_rejection"
    assert canary._classify_failure([
        "tutor_rejected request_id=a topic=t stage=reviewer_final_mcq intent=generate_task "
        "detail=reviewer_final_mcq_integrity_rejection: decision=approve"
    ]) == "reviewer_final_mcq_integrity_rejection"
    assert canary._classify_failure([
        "tutor_rejected request_id=a topic=t stage=publication intent=generate_task detail=x"
    ]) == "publication_validation_rejection"
    assert canary._classify_failure([
        "tutor_rejected request_id=a topic=t stage=tutor_draft intent=generate_task detail=x"
    ]) == "tutor_payload_rejection"


def test_successful_turn_may_emit_a_safe_preflight_diagnostic(monkeypatch, caplog):
    """Nalaz na uspješnom turnu je INFO dijagnostika, ne klasa greške."""
    import logging

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    replacement = task(context, DIRECT_TASK_TEXT, DIRECT_OPTIONS, signature="fixed")
    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_OPTIONS, signature="flawed",
                     task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="correct", final_task=replacement, reviewed=evidence())

    assert run_practice_turn(store, fake, turn())["status"] == "ready"
    assert "tutor_draft_preflight" in caplog.text
    assert preflight.DIFFICULTY_OUTSIDE_TARGET_CODE in caplog.text
    assert "tutor_rejected" not in caplog.text

    from scratchpad import run_difficulty_canary as canary
    preflight_lines = [record.getMessage() for record in caplog.records
                       if record.getMessage().startswith("tutor_draft_preflight ")]
    assert preflight_lines
    # Dijagnostika uspješnog turna se NE smije klasifikovati kao pad.
    assert canary._classify_failure(preflight_lines) == "unknown_rejection"


# ---------------------------------------------------------------------------
# Kapacitetna ekspanzija: ovi testovi ispituju MODEL-strategiju (Tutor +
# Recenzent) i na lekcijama koje produkcija sada rutira deterministički
# (blocking ugovor + potpun generator). Izričito isključenje je ISTI mehanizam
# koji služi i kao produkcijski rollback (MATBOT_DETERMINISTIC_PRACTICE=
# disabled) — model-put time ostaje trajno testiran, bajt za bajt kakav je bio.
# ---------------------------------------------------------------------------
import pytest as _pytest_capex


@_pytest_capex.fixture(autouse=True)
def _model_route_only_capex(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
