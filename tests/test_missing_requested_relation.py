r"""ZADATAK KOJI TRAŽI DA SE RIJEŠI RELACIJA KOJU NIKAD NE NAPIŠE.

Jedini objavljen prekršaj vjernosti zahtjevu u cijeloj završnoj živoj kampanji
od 40 scenarija (44 turna, 83 poziva). Poruka i objavljeni zadatak ispod su
DOSLOVNI zapisi iz
`scratchpad/practice_eval/final40_d049a56_retry1/results.jsonl`.

    poruka:  „…riješi nejednačinu $x>3$, ali je u tekstu zadatka obavezno
             preoblikuj dodavanjem iste nenulte cijele konstante na obje
             strane; ne prepisuj relaciju doslovno…“
    objava:  „Na obje strane originalne nejednačine dodan je isti nenulti
             cijeli broj $2$. Riješite dobijenu nejednačinu i izaberite cijeli
             skup rješenja u $\mathbb{Q}$…“
    opcije:  $x\geq 4$ · $x>5$ · $x>2$ · $x>3$   (označeno $x>3$)

Zadatak nije samostalan: nijedna nejednačina u njemu ne stoji, pa učenik nema
šta riješiti, a među opcijama stoji i doslovno prepisana polazna relacija.
Provjera relacije je ĆUTALA po vlastitom (i dalje tačnom) pravilu „zadatak bez
pročitljive relacije ne dokazuje prekršaj relacije“ — a ovdje relacije NEMA,
što je strukturno dokaziva razlika.

ZERO poziva modela: sve je čist deterministički kod ili FakeLLM.
"""
import pytest

from matbot.request_fidelity import (MISSING_REQUESTED_RELATION,
                                     REQUEST_FIDELITY_CODE,
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
    r"Na obje strane originalne nejednačine dodan je isti nenulti cijeli broj "
    r"$2$. Riješite dobijenu nejednačinu i izaberite cijeli skup rješenja u "
    r"$\mathbb{Q}$. Ne prepisujte originalnu relaciju doslovno; odaberite "
    r"ispravan skup rješenja.")
LIVE_OPTIONS = (r"$x\geq 4$", r"$x>5$", r"$x>2$", r"$x>3$")
LIVE_MARKED = 3

# Ispravka koju recenzent MORA napisati: ista preformulacija, ali relacija je
# stvarno u tekstu.
REPAIRED_TASK = (
    r"Riješite nejednačinu $x+2>5$ i izaberite cijeli skup rješenja u "
    r"$\mathbb{Q}$.")
REPAIRED_OPTIONS = (r"$x>5$", r"$x>3$", r"$x>2$", r"$x\geq 4$")
REPAIRED_MARKED = 1


# ---------------------------------------------------------------------------
# 1) ISTORIJSKI PAKET — ne smije se objaviti
# ---------------------------------------------------------------------------

def test_live_package_is_reported_as_a_request_fidelity_violation():
    failures = request_fidelity_failures(LIVE_MSG, LIVE_TASK)
    assert failures == (
        f"{MISSING_REQUESTED_RELATION}: the task refers to a relation it never "
        "writes; requested 'x > 3'",)


def test_repaired_task_that_actually_writes_the_relation_is_clean():
    """Recenzentova ispravka: ISTA tražena preformulacija, ali zapisana."""
    assert request_fidelity_failures(LIVE_MSG, REPAIRED_TASK) == ()


# ---------------------------------------------------------------------------
# 2) POZITIVNE KONTROLE — nijedna ne smije pasti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,task", [
    # tražena preformulacija dodavanjem konstante na obje strane
    ("Riješi x>3", r"Riješi nejednačinu $x+2>5$."),
    # ekvivalencija skupa rješenja (jednačina)
    ("Riješi 2x=6", r"Riješi jednačinu $x=3$."),
    # deiktička riječ POSTOJI, ali je relacija zapisana → nema nalaza
    ("Riješi x>3",
     r"Dobijena nejednačina glasi $x+2>5$. Riješi je i odaberi skup rješenja."),
    ("Riješi x>3",
     r"Originalnoj nejednačini $x>3$ dodan je broj $2$ na obje strane. "
     r"Riješi dobijenu nejednačinu $x+2>5$."),
    # relacija je zapisana samo LaTeX komandom (bez golog < ili >)
    (r"Riješi x\le 3",
     r"Riješi dobijenu nejednačinu $x+2\le 5$ i odaberi skup rješenja."),
])
def test_positive_controls_stay_allowed(message, task):
    assert request_fidelity_failures(message, task) == (), (message, task)


@pytest.mark.parametrize("message", [
    "Daj mi teži zadatak.", "Daj mi drugi zadatak.", "teže", "Ne znam.",
    "Objasni mi ovo.",
])
def test_request_without_an_explicit_relation_adds_no_new_requirement(message):
    """Bez izričite relacije u PORUCI nema šta da se sačuva — nijedan zahtjev
    za očuvanjem relacije se ne uvodi (uputa §4)."""
    assert request_fidelity_failures(message, LIVE_TASK) == (), message


def test_different_relation_is_still_the_old_relation_mismatch():
    """Novi nalaz ne smije progutati postojeći: kad relacija JESTE zapisana a
    skup rješenja je drugi, i dalje se prijavljuje `relation_mismatch`."""
    failures = request_fidelity_failures(
        "Riješi x>3", r"Riješi dobijenu nejednačinu $x+2>7$.")
    assert failures == ("relation_mismatch: requested 'x > 3', task 'x > 5'",)


def test_unreadable_but_present_relation_is_still_skipped():
    """Zapisana relacija koju parser ne ume da pročita ($x^2>4$) ostaje
    PRESKOČENA — nedokazivo nikad ne postaje presuda."""
    assert request_fidelity_failures(
        "Riješi x>3",
        r"Riješi dobijenu nejednačinu $x^2>4$ nad $\mathbb{Q}$.") == ()


def test_task_without_a_solve_directive_is_not_this_finding():
    """Tekst koji ne traži rješavanje (npr. prepoznavanje) ne pada ovdje."""
    assert request_fidelity_failures(
        "Riješi x>3",
        "Koja od ponuđenih tvrdnji opisuje originalnu nejednačinu?") == ()


def test_deictic_words_alone_without_a_solve_request_are_not_enough():
    assert request_fidelity_failures(
        "Riješi x>3", "Objasni šta znači dobijena nejednačina.") == ()


# ---------------------------------------------------------------------------
# 3) PREFLIGHT I RECEPT ZA RECENZENTA
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


def test_preflight_reports_the_missing_relation():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        student_message=LIVE_MSG)
    found = [issue for issue in issues if issue.code == REQUEST_FIDELITY_CODE]
    assert found, [issue.code for issue in issues]
    assert found[0].detail.startswith(MISSING_REQUESTED_RELATION)


def test_reviewer_recipe_demands_the_relation_be_written_into_the_task():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        student_message=LIVE_MSG)
    block = preflight.format_for_reviewer(issues)
    assert MISSING_REQUESTED_RELATION in block
    assert "WRITE THE COMPLETE resulting relation" in block
    assert "not self-contained" in block


def test_preflight_detail_leaks_no_visible_content():
    """CLAUDE.md pravilo 7: detalj nosi samo serverski izvedene činjenice."""
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        student_message=LIVE_MSG)
    described = preflight.describe_issues(issues)
    for leaked in ("Na obje strane", "nenulti", "Ne prepisujte",
                   "Kreiraj samostalan MCQ"):
        assert leaked not in described, leaked


# ---------------------------------------------------------------------------
# 4) STVARAN DVOPOZIVNI PUT — ispravka objavljuje, propuštena pada zatvoreno
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
 stem_requires_student_reasoning=True,
 exactly_one_option_correct=True),
        final=draft.model_copy(update={"new_task": final_task}),
        reviewed_difficulty_evidence=final_task.difficulty_evidence))
    response = run_practice_turn(store, fake, _turn(session_id, LIVE_MSG))
    return response, store.peek(session_id), fake


def test_unchanged_reviewer_approval_fails_closed(monkeypatch):
    """Recenzent vidi nalaz i ipak odobri nepromijenjen paket → sigurno
    padanje prije mutacije sesije, bez trećeg poziva."""
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    response, session, fake = _run(
        monkeypatch, "mrr-bad", _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert session is None
    assert fake.call_count == 2


def test_reviewer_writes_the_relation_and_the_task_publishes(monkeypatch):
    """Tražen tok: nalaz → recenzent dopiše POTPUNU preoblikovanu relaciju u
    ISTOM (drugom i posljednjem) pozivu → paket se objavljuje."""
    response, session, fake = _run(
        monkeypatch, "mrr-fixed",
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        decision="correct",
        final_task=_task_payload(REPAIRED_TASK, REPAIRED_OPTIONS, REPAIRED_MARKED))
    assert response.get("status") == "ready"
    assert session is not None
    assert fake.call_count == 2
    assert "$x+2>5$" in session["current_task"]
    # Recenzent je STVARNO dobio serverski nalaz i recept u ulazu drugog poziva.
    _instructions, reviewer_input = fake.reviewer_calls[0]
    assert MISSING_REQUESTED_RELATION in reviewer_input
    assert "WRITE THE COMPLETE resulting relation" in reviewer_input
