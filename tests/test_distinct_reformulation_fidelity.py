r"""TRAŽENO PREOBLIKOVANJE KOJE JE SAMO PREPISANA POLAZNA RELACIJA.

Jedini objavljen prekršaj vjernosti zahtjevu u FINAL-40 talasu na kandidatu
`c04d2a1a5b46d892b05768682b434aa7639deef6`. Poruka, objavljeni zadatak, opcije
i označeni odgovor ispod su DOSLOVNI zapisi iz
`scratchpad/practice_eval/final40_c04d2a1_20260810-112206/results.jsonl`.

    poruka:  „…riješi nejednačinu $x>3$, ali je u tekstu zadatka obavezno
             preoblikuj dodavanjem iste nenulte cijele konstante na obje
             strane; ne prepisuj relaciju doslovno…“
    objava:  „Riješi nejednačinu dobijenu dodavanjem iste nenulte cijele
             konstante na obje strane originalne relacije $x>3$. Ne prepisuj
             izvorni oblik; rješenje navedite kao skup u skupu racionalnih
             brojeva $Q$.“
    opcije:  $\{x\in Q\mid x>3\}$ · $\{x\in Q\mid x>5\}$ ·
             $\{x\in Q\mid x\ge3\}$ · $\{x\in Q\mid x>2\}$
    označeno: $\{x\in Q\mid x>3\}$   (recenzent: `approve`, sesija upisana)

Tekst tvrdi DOBIJENU nejednačinu, a jedina relacija koju napiše je doslovno
tražena polazna. Označeni odgovor je tačan za $x>3$, pa je svaka postojeća
kapija ćutala s pravom: relacija JESTE zapisana (`missing_requested_relation`
traži da je nema), zadatak nosi tačno jednu relaciju (`transformed_relation_
mismatch` traži više njih), a skup rješenja je identičan traženom (provjera
relacije poredi skupove, ne zapis). Nedostajalo je jedino ono što je učenik
tražio: DRUGI ZAPIS.

ZERO poziva modela: sve je čist deterministički kod ili FakeLLM.
"""
import pytest

from matbot import mcq_integrity
from matbot.request_fidelity import (MISSING_DISTINCT_TRANSFORMED_RELATION,
                                     MISSING_REQUESTED_RELATION,
                                     RELATION_MISMATCH, REQUEST_FIDELITY_CODE,
                                     TRANSFORMED_RELATION_MISMATCH,
                                     request_fidelity_failures)

# ---------------------------------------------------------------------------
# TAČNI ŽIVI ZAPISI
# ---------------------------------------------------------------------------

LIVE_MSG = (
    "Kreiraj samostalan MCQ sa četiri opcije: riješi nejednačinu $x>3$, ali je "
    "u tekstu zadatka obavezno preoblikuj dodavanjem iste nenulte cijele "
    "konstante na obje strane; ne prepisuj relaciju doslovno. Traži cijeli skup "
    "rješenja i osiguraj da je tačno jedna opcija matematički tačna u navedenom "
    "domenu. Ne rješavaj zadatak učeniku.")
LIVE_TASK = (
    r"Riješi nejednačinu dobijenu dodavanjem iste nenulte cijele konstante na "
    r"obje strane originalne relacije $x>3$. Ne prepisuj izvorni oblik; "
    r"rješenje navedite kao skup u skupu racionalnih brojeva $Q$.")
LIVE_OPTIONS = (r"$\{x\in Q\mid x>3\}$", r"$\{x\in Q\mid x>5\}$",
                r"$\{x\in Q\mid x\ge3\}$", r"$\{x\in Q\mid x>2\}$")
LIVE_MARKED = 0

# Ispravka koju recenzent MORA napraviti: preoblikovana relacija je STVARNO
# zapisana, a skup rješenja ostaje traženi.
REPAIRED_TASK = (
    r"Riješi nejednačinu $x+2>5$ dobijenu dodavanjem broja $2$ na obje strane "
    r"originalne relacije. Rješenje navedi kao skup u skupu racionalnih "
    r"brojeva $Q$.")
REPAIRED_OPTIONS = (r"$\{x\in Q\mid x>5\}$", r"$\{x\in Q\mid x>3\}$",
                    r"$\{x\in Q\mid x\ge3\}$", r"$\{x\in Q\mid x>2\}$")
REPAIRED_MARKED = 1

LIVE_DETAIL = (f"{MISSING_DISTINCT_TRANSFORMED_RELATION}: the task announces a "
               "transformed relation but writes no relation distinct from the "
               "requested one; requested 'x > 3'")


# ---------------------------------------------------------------------------
# 1) ISTORIJSKI PAKET (uputa §11.1)
# ---------------------------------------------------------------------------

def test_live_package_is_a_request_fidelity_violation():
    assert request_fidelity_failures(LIVE_MSG, LIVE_TASK) == (LIVE_DETAIL,)


def test_the_marked_answer_was_mathematically_correct_for_what_was_written():
    """Zašto se nalaz vodi kao vjernost zahtjevu, a ne kao pogrešna matematika:
    $x>3$ JESTE tačno riješeno — samo nije preoblikovano."""
    requested = mcq_integrity.read_solve_statement(LIVE_MSG)
    published = mcq_integrity.read_solve_statement(LIVE_TASK)
    assert requested.solution_display() == "x > 3"
    assert published.has_relation and published.solution_display() == "x > 3"
    assert mcq_integrity.solution_sets_match(
        requested.solution, published.solution)


def test_repaired_task_that_actually_writes_a_new_form_is_clean():
    assert request_fidelity_failures(LIVE_MSG, REPAIRED_TASK) == ()


# ---------------------------------------------------------------------------
# 2) POZITIVNE KONTROLE (uputa §7) — nijedna ne smije pasti
# ---------------------------------------------------------------------------

def test_ordinary_solve_request_answered_verbatim_is_allowed():
    """§7.A — najčešći slučaj u proizvodu: bez izričitog zahtjeva za drugim
    zapisom, zadatak smije doslovno riješiti napisanu relaciju."""
    for task in (r"Riješi nejednačinu $x>3$ i odaberi skup rješenja.",
                 r"Riješi dobijenu nejednačinu $x>3$.",
                 r"Polazna nejednačina je $x>3$. Riješi dobijenu nejednačinu."):
        assert request_fidelity_failures("Riješi x>3", task) == (), task


@pytest.mark.parametrize("task", [
    # §7.B — tražena preformulacija je STVARNO zapisana
    r"Riješi nejednačinu $x+2>5$ i odaberi cijeli skup rješenja.",
    # §7.C — i polazna i dobijena stoje u tekstu, i ekvivalentne su
    r"Polazna nejednačina je $x>3$. Dodajemo $2$ na obje strane pa dobijamo "
    r"$x+2>5$. Riješi dobijenu nejednačinu.",
    # zamjena strana je drugi ZAPIS istog skupa — traženo je upravo to
    r"Riješi dobijenu nejednačinu $3<x$ i odaberi cijeli skup rješenja.",
])
def test_explicit_reformulation_that_was_performed_is_allowed(task):
    assert request_fidelity_failures(LIVE_MSG, task) == (), task


def test_equation_reformulation_request_is_allowed(monkeypatch):
    """§7.D — traženo $2x=6$, napisano $x=3$: drugi zapis, isti skup."""
    message = ("Riješi jednačinu $2x=6$, ali je u tekstu obavezno preoblikuj; "
               "ne prepisuj relaciju doslovno.")
    assert request_fidelity_failures(
        message, r"Riješi dobijenu jednačinu $x=3$.") == ()


@pytest.mark.parametrize("message", [
    "Daj mi drugi zadatak.", "Daj mi teži zadatak.", "teže", "Ne znam.",
    "Objasni mi ovo.",
])
def test_generic_follow_ups_are_unaffected(message):
    """§7.E — bez izričite relacije u poruci nema šta da se sačuva."""
    assert request_fidelity_failures(message, LIVE_TASK) == (), message


def test_reformulation_word_without_an_explicit_relation_is_ignored():
    """Sama riječ „preoblikuj“ bez pročitljive relacije ne uvodi ništa."""
    assert request_fidelity_failures(
        "Preoblikuj mi ovaj zadatak da bude lakši.", LIVE_TASK) == ()


def test_discrete_domain_equivalent_new_form_is_allowed():
    """Nad Z su $x>3$ i $x\\ge 4$ isti skup, a zapis JESTE drugi."""
    message = ("Riješi nejednačinu $x>3$ u skupu cijelih brojeva, ali je "
               "preoblikuj; ne prepisuj relaciju doslovno.")
    assert request_fidelity_failures(
        message,
        r"Riješi dobijenu nejednačinu $x\ge 4$ u skupu cijelih brojeva.") == ()


# ---------------------------------------------------------------------------
# 3) GRANICE — što se namjerno NE tvrdi (uputa §11.9)
# ---------------------------------------------------------------------------

def test_unsupported_nonlinear_reformulation_stays_conservative():
    """Nepročitana ali PRISUTNA relacija znači „ne zna se šta je napisano“ —
    nikad presudu. Bez ovog uslova bi nedokazivo postalo dokaz."""
    assert request_fidelity_failures(
        LIVE_MSG,
        r"Riješi dobijenu nejednačinu $x^2>9$ nastalu iz originalne relacije "
        r"$x>3$.") == ()


def test_task_that_claims_no_transformation_is_not_this_finding():
    """Bez tvrdnje o preoblikovanju zadatak ne obećava ništa što nije uradio;
    o zapisu tada odlučuje samo skup rješenja, kao i dosad."""
    assert request_fidelity_failures(
        LIVE_MSG, r"Riješi nejednačinu $x>3$ i odaberi skup rješenja.") == ()


def test_task_without_a_solve_directive_is_not_this_finding():
    assert request_fidelity_failures(
        LIVE_MSG,
        r"Koja tvrdnja opisuje dobijenu nejednačinu ako je polazna $x>3$?") == ()


def test_set_builder_option_notation_inside_the_task_text_is_skipped():
    """Relacijski znak u zapisu koji čitač ne ume da pročita ostavlja nalaz
    nedokazanim — konzervativno preskakanje, ne presuda."""
    assert request_fidelity_failures(
        LIVE_MSG,
        r"Riješi dobijenu nejednačinu iz $x>3$ i zapiši rezultat kao "
        r"$\{x\in Q\mid x>3\}$.") == ()


# ---------------------------------------------------------------------------
# 4) TRI NALAZA SU RAZLUČIVA (uputa §9)
# ---------------------------------------------------------------------------

TR_A3_MSG = (
    "Kreiraj samostalan MCQ sa četiri opcije za nejednačinu $x>3$. U tekstu "
    "pokušaj preoblikovanje tako što lijevoj strani dodaš 2, a desnoj strani "
    "4, pa nastavi sa dobijenom nejednačinom.")
TR_A3_TASK = (
    r"Početna nejednačina je $x>3$. Dodajemo $2$ s lijeve strane i $4$ s desne "
    r"strane pa dobijamo $x+2>7$. Riješi dobijenu nejednačinu $x+2>7$.")
NO_RELATION_TASK = (
    r"Na obje strane originalne nejednačine dodan je isti nenulti cijeli broj "
    r"$2$. Riješite dobijenu nejednačinu i izaberite cijeli skup rješenja u "
    r"$\mathbb{Q}$.")


def test_zero_relation_is_still_missing_requested_relation():
    """§9.A — nijedna relacija nije zapisana (istorijski FW-R02 oblik)."""
    failures = request_fidelity_failures(LIVE_MSG, NO_RELATION_TASK)
    assert failures == (
        f"{MISSING_REQUESTED_RELATION}: the task refers to a relation it never "
        "writes; requested 'x > 3'",)


def test_only_the_repeated_original_is_the_new_finding():
    """§9.B — relacija JESTE zapisana, ali je doslovno tražena polazna."""
    assert request_fidelity_failures(LIVE_MSG, LIVE_TASK) == (LIVE_DETAIL,)


def test_non_equivalent_transformed_relation_is_still_tr_a3():
    """§9.C — zapisana „dobijena“ relacija drugog skupa rješenja (TR-A3)."""
    failures = request_fidelity_failures(TR_A3_MSG, TR_A3_TASK)
    assert failures == (
        f"{TRANSFORMED_RELATION_MISMATCH}: requested 'x > 3', task's "
        "transformed relation 'x > 5'",)


@pytest.mark.parametrize("message,task", [
    (LIVE_MSG, NO_RELATION_TASK),
    (LIVE_MSG, LIVE_TASK),
    (TR_A3_MSG, TR_A3_TASK),
])
def test_the_three_findings_are_mutually_exclusive(message, task):
    details = request_fidelity_failures(message, task)
    assert len(details) == 1, details
    codes = [code for code in (MISSING_REQUESTED_RELATION,
                               MISSING_DISTINCT_TRANSFORMED_RELATION,
                               TRANSFORMED_RELATION_MISMATCH)
             if code in details[0]]
    assert len(codes) == 1, codes


def test_single_relation_drift_is_still_the_old_relation_mismatch():
    """Neekvivalentna JEDINA relacija i dalje pada kao `relation_mismatch` —
    novi nalaz je ne smije ni progutati ni udvostručiti."""
    failures = request_fidelity_failures(
        LIVE_MSG, r"Riješi dobijenu nejednačinu $x+2>7$.")
    assert failures == ("relation_mismatch: requested 'x > 3', task 'x > 5'",)


# ---------------------------------------------------------------------------
# 5) PREFLIGHT I RECEPT ZA RECENZENTA (uputa §10)
# ---------------------------------------------------------------------------

def _task_payload(text, options, marked, lesson_id="7-03-019",
                  lesson_title="Nejednačine u skupu Q"):
    from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter,
                                     TaskPayload, TaskSignature, TutorOption)
    return TaskPayload(
        selected_lesson_id=lesson_id, selected_lesson_title=lesson_title,
        target_difficulty_level=1, text=text, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(options)],
        correct_option_index=marked, correct_option_id="abcd"[marked],
        expected_answer=options[marked],
        solution="Serverski test.", difficulty="easy",
        difficulty_evidence=DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        task_signature=TaskSignature(
            task_family="linear_inequality", operation_or_relation="solve",
            normalized_parameters=[SignatureParameter(name="case", value="r02")],
            required_conditions=[], relevant_objects=["nejednačina"],
            answer_type="multiple_choice"))


def test_preflight_reports_the_missing_distinct_reformulation():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        student_message=LIVE_MSG)
    found = [issue for issue in issues if issue.code == REQUEST_FIDELITY_CODE]
    assert found, [issue.code for issue in issues]
    assert found[0].detail == LIVE_DETAIL


def test_reviewer_recipe_says_copying_the_original_never_satisfies_it():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        student_message=LIVE_MSG)
    block = preflight.format_for_reviewer(issues)
    assert MISSING_DISTINCT_TRANSFORMED_RELATION in block
    # sva četiri tražena elementa recepta (uputa §10)
    assert "DIFFERENT but EQUIVALENT written" in block
    assert "merely copying" in block
    assert "WRITE THE TRANSFORMED RELATION ITSELF" in block
    assert "solution set must stay EXACTLY equal" in block
    assert "fail_closed" in block
    # postojeći recepti moraju ostati u istom bloku
    assert MISSING_REQUESTED_RELATION in block
    assert TRANSFORMED_RELATION_MISMATCH in block
    assert RELATION_MISMATCH in block


def test_preflight_detail_leaks_no_visible_content():
    """CLAUDE.md pravilo 7: detalj nosi samo serverski izvedene činjenice."""
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        student_message=LIVE_MSG)
    described = preflight.describe_issues(issues)
    for leaked in ("dodavanjem iste nenulte", "izvorni oblik", "racionalnih",
                   "Kreiraj samostalan MCQ", "preoblikuj dodavanjem"):
        assert leaked not in described, leaked


# ---------------------------------------------------------------------------
# 6) STVARAN DVOPOZIVNI PUT (uputa §11.2 i §11.3)
# ---------------------------------------------------------------------------

def _turn(session_id, message):
    return {
        "session_id": session_id, "grade": 7, "selected_topic": "7-03-019",
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


def _run(monkeypatch, session_id, draft_task, *, decision="approve",
         final_task=None):
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore
    from matbot.tutor.schema import ReviewerChecks, ReviewerFinal, TutorDraft
    from tests.conftest import FakeLLM

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    store, fake = SessionStore(), FakeLLM()
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="nejednačine", new_task=draft_task)
    final_task = draft_task if final_task is None else final_task
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision=decision,
        checks=ReviewerChecks(
            math_correct=True, marked_option_correct=True, inside_lesson=True,
            intent_handled=True, difficulty_direction_correct=True,
            response_addresses_student=True,
            task_solvable_and_unambiguous=True, mathjax_valid=True,
            language_age_appropriate=True, independently_solved=True,
            independent_answer="provjereno", task_package_consistent=True,
            difficulty_evidence_valid=True, task_signature_consistent=True,
 stem_requires_student_reasoning=True),
        final=draft.model_copy(update={"new_task": final_task}),
        reviewed_difficulty_evidence=final_task.difficulty_evidence))
    response = run_practice_turn(store, fake, _turn(session_id, LIVE_MSG))
    return response, store.peek(session_id), fake


def test_unchanged_reviewer_approval_fails_closed(monkeypatch):
    """§11.2 — recenzent ostavi samo $x>3$ → siguran pad prije mutacije
    sesije, tačno dva poziva, bez trećeg."""
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    response, session, fake = _run(
        monkeypatch, "mdtr-bad",
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert session is None, "ništa se ne smije upisati u sesiju"
    assert fake.call_count == 2, "bez trećeg poziva"


def test_reviewer_repair_to_a_written_reformulation_publishes(monkeypatch):
    """§11.3 — recenzent dopiše $x+2>5$ u ISTOM drugom pozivu → objava."""
    response, session, fake = _run(
        monkeypatch, "mdtr-fixed",
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        decision="correct",
        final_task=_task_payload(REPAIRED_TASK, REPAIRED_OPTIONS,
                                 REPAIRED_MARKED))
    assert response.get("status") == "ready"
    assert session is not None
    assert fake.call_count == 2
    assert "$x+2>5$" in session["current_task"]
    # Recenzent je STVARNO dobio nalaz i recept u ulazu drugog poziva.
    _instructions, reviewer_input = fake.reviewer_calls[0]
    assert MISSING_DISTINCT_TRANSFORMED_RELATION in reviewer_input
    assert "merely copying" in reviewer_input


def test_ordinary_solve_request_still_publishes_verbatim(monkeypatch):
    """Kontrola protiv prekomjernog blokiranja: bez izričitog zahtjeva za
    drugim zapisom, doslovno riješena relacija se i dalje objavljuje."""
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore
    from matbot.tutor.schema import ReviewerChecks, ReviewerFinal, TutorDraft
    from tests.conftest import FakeLLM

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    task = _task_payload(
        r"Riješi nejednačinu $x>3$ i odaberi cijeli skup rješenja.",
        (r"$\{x\in Q\mid x>3\}$", r"$\{x\in Q\mid x>5\}$",
         r"$\{x\in Q\mid x\ge3\}$", r"$\{x\in Q\mid x>2\}$"), 0)
    store, fake = SessionStore(), FakeLLM()
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="nejednačine", new_task=task)
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision="approve",
        checks=ReviewerChecks(
            math_correct=True, marked_option_correct=True, inside_lesson=True,
            intent_handled=True, difficulty_direction_correct=True,
            response_addresses_student=True,
            task_solvable_and_unambiguous=True, mathjax_valid=True,
            language_age_appropriate=True, independently_solved=True,
            independent_answer="provjereno", task_package_consistent=True,
            difficulty_evidence_valid=True, task_signature_consistent=True,
 stem_requires_student_reasoning=True),
        reviewed_difficulty_evidence=task.difficulty_evidence))
    response = run_practice_turn(
        store, fake, _turn("mdtr-plain", "Riješi nejednačinu $x>3$."))
    assert response.get("status") == "ready"
    assert store.peek("mdtr-plain") is not None
    assert fake.call_count == 2
