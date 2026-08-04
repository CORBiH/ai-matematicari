"""Deterministički MCQ nalazi moraju stići recenzentu PRIJE drugog poziva.

ŽIVI NALAZ (release gate 00bbd45, scenario 11 od 12, rotirajuća lekcija
8. razreda o površini, traženi nivo 1):

    Tutor:      valjan zadatak, dokaz težine uredan
    Recenzent:  approve, math_correct=true, task_package_consistent=true
    Objava:     ODBIJENO → `semantically_duplicate_options: [(0, 2)]`

Dvije vidljive opcije bile su različiti stringovi a ista vrijednost. Provjera
`option_equivalence.find_equivalent_option_pairs` ISPRAVNO ih je uhvatila, ali
tek u objavi — poslije oba poziva. Recenzent nikad nije saznao za nalaz, pa ga
nije mogao popraviti iako `correct` upravo to omogućava, i turn je propao uz
potrošena dva poziva.

Ovaj modul dokazuje:
  • nalaz se otkriva PRIJE drugog poziva i ulazi u recenzentov ulaz;
  • recenzent ga smije i može popraviti u ISTOM drugom pozivu;
  • nepromijenjeno `approve` pada deterministički, prije mutacije sesije;
  • pravilo je univerzalno (razni oblici ekvivalencije, razne lekcije);
  • granica normalizatora se ne širi nagađanjem.

Tačni živi stringovi opcija NISU sačuvani u artefaktu, pa se ovdje koristi
dokazano ekvivalentan par ($16\\sqrt{3}$ / $\\sqrt{768}$) — ne tvrdi se da su
to bile originalne opcije.
"""
import copy

import pytest

from matbot import option_equivalence
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import package_preflight as preflight
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from matbot.tutor.pipeline import REVIEWER_FINAL_INTEGRITY_CODE, SAFE_ERROR_MESSAGE
from matbot.tutor.schema import (DifficultyEvidence, ReviewerChecks, ReviewerFinal,
                                 SignatureParameter, TaskPayload, TaskSignature,
                                 TutorDraft, TutorOption)
from tests.conftest import FakeLLM

SESSION = "preflight"
GRADE, TOPIC = 8, "8-04-008"

# Živi oblik zadatka (tekst JESTE sačuvan u artefaktu).
LIVE_TASK_TEXT = (
    "Jednakostraničan trougao ima stranicu dužine $8\\,\\text{cm}$. "
    "Izračunaj njegovu površinu."
)
# a i c su ISTA vrijednost napisana drugačije; b i d su stvarno različiti.
LIVE_DUPLICATE_OPTIONS = (
    "$16\\sqrt{3}\\,\\text{cm}^2$", "$32\\,\\text{cm}^2$",
    "$\\sqrt{768}\\,\\text{cm}^2$", "$64\\,\\text{cm}^2$",
)
# Recenzentova ispravka: duplikat c zamijenjen stvarno drugom vrijednošću.
REPAIRED_OPTIONS = (
    "$16\\sqrt{3}\\,\\text{cm}^2$", "$32\\,\\text{cm}^2$",
    "$48\\,\\text{cm}^2$", "$64\\,\\text{cm}^2$",
)


def turn(grade=GRADE, topic=TOPIC, message="Daj mi zadatak."):
    return {
        "session_id": SESSION, "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


def evidence():
    return DifficultyEvidence(
        reasoning_steps=1, condition_count=1, operation_count=1,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


def task(context, options, *, correct=0, expected=None, signature="one",
         text=LIVE_TASK_TEXT, solution="Površina se računa po formuli za lekciju."):
    return TaskPayload(
        selected_lesson_id=context.topic_id, selected_lesson_title=context.title,
        target_difficulty_level=1, text=text, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(options)],
        correct_option_index=correct, correct_option_id="abcd"[correct],
        expected_answer=options[correct] if expected is None else expected,
        solution=solution, difficulty="easy", difficulty_evidence=evidence(),
        task_signature=TaskSignature(
            task_family="area", operation_or_relation="compute",
            normalized_parameters=[SignatureParameter(name="case", value=signature)],
            required_conditions=["a>0"], relevant_objects=["figure"],
            answer_type="multiple_choice"))


def assert_rejected_at_reviewer_stage(caplog):
    """Odbijeno na RECENZENTOVOM paketu, ne u objavi.

    Bez ove provjere test ne bi razlikovao novu invarijantu od zatečene provjere
    u objavi — ona je isti paket odbijala i ranije, ali tek poslije drugog poziva
    i pod pogrešnom klasifikacijom."""
    assert "stage=reviewer_final_mcq" in caplog.text
    assert "stage=publication" not in caplog.text
    assert REVIEWER_FINAL_INTEGRITY_CODE in caplog.text


def checks(**changes):
    base = dict(math_correct=True, marked_option_correct=True, inside_lesson=True,
                intent_handled=True, difficulty_direction_correct=True,
                response_addresses_student=True, task_solvable_and_unambiguous=True,
                mathjax_valid=True, language_age_appropriate=True,
                independently_solved=True, independent_answer="provjereno",
                task_package_consistent=True, difficulty_evidence_valid=True,
                task_signature_consistent=True)
    base.update(changes)
    return ReviewerChecks(**base)


def queue(fake, draft_task, *, decision="approve", final_task=...):
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="izabrana lekcija", new_task=draft_task)
    final_task = draft_task if final_task is ... else final_task
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision=decision, checks=checks(),
        final=draft.model_copy(update={"new_task": final_task}),
        reviewed_difficulty_evidence=final_task.difficulty_evidence))
    return draft


# ---------------------------------------------------------------------------
# 1) NALAZ POSTOJI I POKLAPA SE S ŽIVIM PADOM
# ---------------------------------------------------------------------------

def test_live_duplicate_pair_is_the_one_the_gate_reported():
    """Par (0, 2) iz živog loga, izražen stabilnim ID-jevima opcija."""
    context = build(GRADE, TOPIC)
    issues = preflight.collect_package_issues(task(context, LIVE_DUPLICATE_OPTIONS))
    duplicates = [i for i in issues if i.code == preflight.SEMANTIC_DUPLICATE_CODE]
    assert len(duplicates) == 1
    assert duplicates[0].option_indexes == (0, 2)
    assert duplicates[0].option_ids == ("a", "c")
    assert preflight.semantic_duplicate_index_pairs(issues) == ((0, 2),)


def test_repaired_package_has_no_deterministic_issues():
    context = build(GRADE, TOPIC)
    assert preflight.collect_package_issues(task(context, REPAIRED_OPTIONS)) == ()


def test_preflight_never_raises_on_a_broken_package():
    """Dijagnostika ne smije nikad srušiti turn — ni na besmislenom paketu."""
    context = build(GRADE, TOPIC)
    broken = task(context, LIVE_DUPLICATE_OPTIONS).model_copy(update={
        "correct_option_index": 9, "expected_answer": "  "})
    issues = preflight.collect_package_issues(broken)
    assert any(i.code == "task_structure_invalid" for i in issues)
    assert preflight.collect_package_issues(None) == ()


def test_issue_block_is_bounded_and_leaks_no_visible_content():
    context = build(GRADE, TOPIC)
    issues = preflight.collect_package_issues(task(context, LIVE_DUPLICATE_OPTIONS))
    block = preflight.format_for_reviewer(issues)
    assert "SERVER-DETECTED DRAFT ISSUES" in block
    assert "semantically_duplicate_options: option IDs a and c" in block
    assert len(issues) <= preflight.MAX_ISSUES
    # Ograničen log red nikad ne nosi vidljiv sadržaj.
    described = preflight.describe_issues(issues)
    assert len(described) <= 300
    for leaked in (LIVE_TASK_TEXT, "16\\sqrt{3}", "768", "Površina se računa"):
        assert leaked not in described, leaked
    assert preflight.format_for_reviewer(()) == ""


# ---------------------------------------------------------------------------
# 2) TAČAN ŽIVI OBLIK KROZ CIJELI DVOPOZIVNI PUT
# ---------------------------------------------------------------------------

def test_A_unchanged_approval_is_rejected_before_publication(monkeypatch, caplog):
    """Test A: recenzent odobri nepromijenjen paket s duplikatom → pad."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, LIVE_DUPLICATE_OPTIONS), decision="approve")

    response = run_practice_turn(store, fake, turn())

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert store.peek(SESSION) is None            # sesija netaknuta
    assert fake.call_count == 2                   # bez retryja i trećeg poziva
    # Odbijeno na recenzentovom paketu, NE u objavi.
    assert "stage=reviewer_final_mcq" in caplog.text
    assert "stage=publication" not in caplog.text
    assert REVIEWER_FINAL_INTEGRITY_CODE in caplog.text
    # Dijagnostika nosi tražena polja...
    assert "decision=approve" in caplog.text
    assert "in_tutor_preflight=True" in caplog.text
    assert "unchanged=True" in caplog.text
    assert "option IDs a and c" in caplog.text
    # ...i nijedan vidljiv sadržaj.
    for leaked in ("16\\sqrt{3}", "Površina se računa", "Jednakostraničan"):
        assert leaked not in caplog.text, leaked


def test_B_reviewer_repairs_the_duplicate_in_the_same_second_call(monkeypatch):
    """Test B: recenzent zamijeni duplikat i paket se objavljuje."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    repaired = task(context, REPAIRED_OPTIONS, signature="repaired")
    queue(fake, task(context, LIVE_DUPLICATE_OPTIONS), decision="correct",
          final_task=repaired)

    response = run_practice_turn(store, fake, turn())
    session = store.peek(SESSION)

    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1

    # Recenzent je STVARNO dobio serverski nalaz u ulazu drugog poziva.
    _instructions, reviewer_input = fake.reviewer_calls[0]
    assert "SERVER-DETECTED DRAFT ISSUES" in reviewer_input
    assert "semantically_duplicate_options: option IDs a and c" in reviewer_input

    # Objavljen je ISPRAVLJEN paket: četiri semantički različite opcije.
    published = [option["text"] for option in session["current_options"]]
    assert sorted(published) == sorted(REPAIRED_OPTIONS)
    assert option_equivalence.find_equivalent_option_pairs(published) == []
    assert option_equivalence.find_textual_duplicate_pairs(published) == []
    # Tačno jedna tačna opcija, a ID/indeks i expected_answer se slažu.
    correct_id = session["correct_option_id"]
    correct_text = next(o["text"] for o in session["current_options"]
                        if o["id"] == correct_id)
    assert correct_text == REPAIRED_OPTIONS[0]
    assert session["expected_answer_summary"] == REPAIRED_OPTIONS[0]


def test_reviewer_input_is_unchanged_when_the_draft_is_clean(monkeypatch):
    """Bez dokazanog defekta ulaz drugog poziva ostaje kakav je i bio."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, REPAIRED_OPTIONS, signature="clean"))

    assert run_practice_turn(store, fake, turn())["status"] == "ready"
    assert "SERVER-DETECTED DRAFT ISSUES" not in fake.reviewer_calls[0][1]
    assert fake.call_count == 2


def test_rejection_preserves_a_previously_published_session(monkeypatch, caplog):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, REPAIRED_OPTIONS, signature="committed"))
    assert run_practice_turn(store, fake, turn())["status"] == "ready"
    before = copy.deepcopy(store.peek(SESSION))

    queue(fake, task(context, LIVE_DUPLICATE_OPTIONS, signature="rejected"))
    assert run_practice_turn(store, fake, turn())["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) == before
    assert fake.call_count == 4      # 2 + 2, nikad 3 po turnu
    assert_rejected_at_reviewer_stage(caplog)


def test_reviewer_correction_that_keeps_an_equivalent_pair_still_fails(monkeypatch, caplog):
    """Preformatiranje iste vrijednosti NIJE ispravka."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    # c zamijenjen zaokruženim zapisom ISTE vrijednosti (16√3 ≈ 27,71).
    still_equivalent = ("$16\\sqrt{3}\\,\\text{cm}^2$", "$32\\,\\text{cm}^2$",
                        "$27,71\\,\\text{cm}^2$", "$64\\,\\text{cm}^2$")
    queue(fake, task(context, LIVE_DUPLICATE_OPTIONS), decision="correct",
          final_task=task(context, still_equivalent, signature="reformatted"))

    assert run_practice_turn(store, fake, turn())["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2
    assert_rejected_at_reviewer_stage(caplog)


def test_reviewer_fail_closed_remains_available(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="lekcija",
                       new_task=task(context, LIVE_DUPLICATE_OPTIONS))
    fake.queue(draft)
    fake.queue(ReviewerFinal(decision="fail_closed", checks=checks(),
                             fail_reason_code="ambiguous_task", final=None,
                             reviewed_difficulty_evidence=None))

    assert run_practice_turn(store, fake, turn())["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2


# ---------------------------------------------------------------------------
# 3) OBLICI EKVIVALENCIJE KOJE PRODUKCIJSKI NORMALIZATOR VEĆ DOKAZUJE
# ---------------------------------------------------------------------------

PROVEN_EQUIVALENT = [
    pytest.param("$16\\sqrt{3}$", "$\\sqrt{768}$", id="radical-vs-simplified-radical"),
    pytest.param("$16\\sqrt{3}$", "$27,71$", id="exact-vs-rounded-decimal"),
    pytest.param("$\\frac{1}{2}$", "$\\frac{2}{4}$", id="simplified-vs-unsimplified-fraction"),
    pytest.param("$0,5$", "$\\frac{1}{2}$", id="decimal-vs-fraction"),
    pytest.param("$3\\cdot4$", "$12$", id="product-vs-evaluated-number"),
    pytest.param("$12\\,\\text{cm}^2$", "$12\\;\\text{cm}^2$", id="units-with-mathjax-spacing"),
    pytest.param("$-\\frac{3}{4}$", "$-0,75$", id="negative-fraction-vs-decimal"),
    pytest.param("$-6$", "$-(6)$", id="negative-parenthesised"),
    pytest.param("$a\\sqrt{2}$", "$\\sqrt{2}a$", id="commutative-symbolic"),
    pytest.param("$\\frac{64\\sqrt{3}}{4}$", "$16\\sqrt{3}$", id="fraction-vs-simplified-radical"),
]


@pytest.mark.parametrize("first,second", PROVEN_EQUIVALENT)
def test_cross_format_equivalence_is_detected_by_preflight(first, second):
    context = build(GRADE, TOPIC)
    options = (first, "$999$", second, "$1000$")
    issues = preflight.collect_package_issues(task(context, options))
    pairs = preflight.semantic_duplicate_index_pairs(issues)
    assert (0, 2) in pairs, f"{first} vs {second}"


@pytest.mark.parametrize("first,second", PROVEN_EQUIVALENT)
def test_cross_format_equivalence_blocks_publication(monkeypatch, caplog, first, second):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, (first, "$999$", second, "$1000$")))

    assert run_practice_turn(store, fake, turn())["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2
    assert_rejected_at_reviewer_stage(caplog)


# GRANICA KOJU SVJESNO NE ŠIRIMO: `\%` i `^\circ` su namjerno na listi
# nepodržanih konstrukcija (`option_equivalence._DISALLOWED_RE`), pa se
# „$50\%$“ i „$0,5$“ NE mogu dokazati kao ista vrijednost. Nedokazivo se
# tretira kao RAZLIČITO (isti princip kao mathcheck: preskoči, ne pogađaj).
# Ovaj test fiksira zatečeni ugovor umjesto da se parser širi nagađanjem.
@pytest.mark.parametrize("first,second", [
    ("$50\\%$", "$0,5$"),
    ("$30^\\circ$", "$30$"),
])
def test_percent_and_degree_equivalence_is_deliberately_not_proven(first, second):
    assert option_equivalence.options_are_equivalent(first, second) is False


# ---------------------------------------------------------------------------
# 4) OPCIJE KOJE MORAJU OSTATI RAZLIČITE (nema slabljenja validatora)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first,second", [
    pytest.param("$16\\sqrt{3}$", "$8\\sqrt{3}$", id="different-radical-coefficient"),
    pytest.param("$\\frac{1}{2}$", "$\\frac{2}{3}$", id="different-fractions"),
    pytest.param("$0,5$", "$0,05$", id="decimal-order-of-magnitude"),
    pytest.param("$12\\,\\text{cm}^2$", "$12\\,\\text{cm}$", id="different-dimension-units"),
    pytest.param("$30^\\circ$", "$60^\\circ$", id="different-degree-values"),
    pytest.param("$D=a\\sqrt{3}$", "$d=a\\sqrt{3}$", id="different-defined-quantity"),
])
def test_legitimately_distinct_options_publish(monkeypatch, first, second):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    options = (first, "$111$", second, "$222$")
    assert preflight.collect_package_issues(task(context, options)) == ()
    queue(fake, task(context, options, signature=f"{first}-{second}"))

    assert run_practice_turn(store, fake, turn())["status"] == "ready"
    assert fake.call_count == 2


# ---------------------------------------------------------------------------
# 5) INTERAKCIJA S „VIŠE TAČNIH ODGOVORA“
# ---------------------------------------------------------------------------

def test_duplicate_of_the_correct_answer_is_reported(monkeypatch):
    """Duplikat TAČNE opcije znači dva tačna odgovora — mora se prijaviti."""
    context = build(GRADE, TOPIC)
    options = ("$16\\sqrt{3}\\,\\text{cm}^2$", "$32\\,\\text{cm}^2$",
               "$\\sqrt{768}\\,\\text{cm}^2$", "$64\\,\\text{cm}^2$")
    issues = preflight.collect_package_issues(task(context, options, correct=0))
    assert preflight.semantic_duplicate_index_pairs(issues) == ((0, 2),)


def test_two_equivalent_distractors_are_reported():
    context = build(GRADE, TOPIC)
    options = ("$16\\sqrt{3}$", "$\\frac{1}{2}$", "$0,5$", "$64$")
    issues = preflight.collect_package_issues(task(context, options, correct=0))
    assert preflight.semantic_duplicate_index_pairs(issues) == ((1, 2),)


def test_two_separate_equivalence_groups_are_both_reported():
    context = build(GRADE, TOPIC)
    options = ("$\\frac{1}{2}$", "$3\\cdot4$", "$0,5$", "$12$")
    issues = preflight.collect_package_issues(task(context, options, correct=0))
    assert set(preflight.semantic_duplicate_index_pairs(issues)) == {(0, 2), (1, 3)}


def test_all_four_different_strings_but_one_equivalent_pair(monkeypatch, caplog):
    """Četiri različita stringa nisu četiri različite vrijednosti."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    options = ("$\\frac{2}{4}$", "$0,25$", "$0,5$", "$0,75$")
    queue(fake, task(context, options))

    assert run_practice_turn(store, fake, turn())["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2
    assert_rejected_at_reviewer_stage(caplog)


def test_exactly_one_correct_option_survives_a_repair(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    repaired = task(context, REPAIRED_OPTIONS, signature="one-correct")
    queue(fake, task(context, LIVE_DUPLICATE_OPTIONS), decision="correct",
          final_task=repaired)
    assert run_practice_turn(store, fake, turn())["status"] == "ready"

    session = store.peek(SESSION)
    texts = [option["text"] for option in session["current_options"]]
    correct_id = session["correct_option_id"]
    assert sum(1 for option in session["current_options"]
               if option["id"] == correct_id) == 1
    assert len(texts) == 4 and len(set(texts)) == 4
    assert option_equivalence.find_equivalent_option_pairs(texts) == []


# ---------------------------------------------------------------------------
# 6) UGOVOR: JEDNA IMPLEMENTACIJA, ZATVORENA ŠEMA, BEZ GRANA PO LEKCIJI
# ---------------------------------------------------------------------------

def test_reviewer_instructions_state_the_preflight_mandate():
    instructions = tutor_prompts.build_reviewer_instructions(build(GRADE, TOPIC))
    lowered = instructions.lower()
    assert "server-detected draft issues" in lowered
    assert "deterministic server facts" in lowered
    assert "`approve` is forbidden while any reported issue remains" in lowered
    assert "never just" in lowered and "reformat" in lowered
    assert "the server re-runs those same validators on your final package" in lowered


def test_preflight_delegates_instead_of_reimplementing_validators():
    """Nema drugog MCQ validatora: modul samo SAKUPLJA nalaze postojećih."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "matbot" / "tutor"
              / "package_preflight.py").read_text(encoding="utf-8")
    for delegated in ("option_equivalence.find_textual_duplicate_pairs",
                      "option_equivalence.find_equivalent_option_pairs_with_types",
                      "mcq_integrity.publication_failure",
                      "find_numeric_inconsistencies",
                      "validate_task"):
        assert delegated in source, delegated
    # Nijedno vlastito pravilo ekvivalencije/uporedivanja opcija.
    for reimplemented in ("def options_are_equivalent", "def canonicalize_expression",
                          "def find_equivalent_option_pairs"):
        assert reimplemented not in source, reimplemented


def test_no_lesson_identity_in_the_preflight_module():
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parent.parent
    source = (root / "matbot" / "tutor" / "package_preflight.py").read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d{2}-\d{3}\b", source)
    for word in ("trougao", "jednakostrani", "površin", "sqrt", "cm"):
        assert word not in source.lower(), word


def test_gate_classifies_the_new_rejection_distinctly():
    from scratchpad import run_difficulty_canary as canary

    message = (
        "tutor_rejected request_id=abc topic=8-04-008 stage=reviewer_final_mcq "
        f"intent=generate_task detail={REVIEWER_FINAL_INTEGRITY_CODE}: "
        "decision=approve in_tutor_preflight=True unchanged=True "
        "semantically_duplicate_options: option IDs a and c (numeric_exact)"
    )
    assert canary._classify_failure([message]) == "reviewer_final_mcq_integrity_rejection"
    assert REVIEWER_FINAL_INTEGRITY_CODE in canary.FAILURE_CLASSES
    # Zatečene klase ostaju netaknute.
    assert canary._classify_failure([
        "tutor_rejected request_id=a topic=t stage=publication intent=generate_task "
        "detail=semantically_duplicate_options: [(0, 2)]"
    ]) == "publication_validation_rejection"
    assert canary._classify_failure([
        "tutor_rejected request_id=a topic=t stage=tutor_draft intent=generate_task detail=x"
    ]) == "tutor_payload_rejection"
