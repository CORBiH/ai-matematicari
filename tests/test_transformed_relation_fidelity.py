r"""„DOBIJENA“ RELACIJA MORA IMATI ISTI SKUP RJEŠENJA KAO TRAŽENA.

Jedini blokator ciljanog živog rechecka. Poruka, objavljeni zadatak, opcije i
označeni odgovor ispod su DOSLOVNI zapisi iz
`scratchpad/practice_eval/targeted_finalfix_b5692c0_20260810-012457/results.jsonl`.

    poruka:  „…za nejednačinu $x>3$. U tekstu pokušaj preoblikovanje tako što
             lijevoj strani dodaš 2, a desnoj strani 4, pa nastavi sa
             dobijenom nejednačinom…“
    objava:  „Početna nejednačina je $x>3$. Dodajemo $2$ s lijeve strane i $4$
             s desne strane pa dobijamo $x+2>7$. Riješi dobijenu nejednačinu
             $x+2>7$ i nađi cijeli skup rješenja.“
    opcije:  $(3,\infty)$ · $[5,\infty)$ · $(5,\infty)$ · $(4,\infty)$
    označeno: $(5,\infty)$

Označeni odgovor je TAČAN za $x+2>7$, ali $x+2>7$ NIJE isti skup rješenja kao
traženo $x>3$ — dakle učenik rješava drugi zadatak od onog koji je tražio.

ZAŠTO JE PROŠLO: `read_solve_statement` po ugovoru vraća najviše JEDNU
relaciju, a dvije RAZLIČITE proglašava nejednoznačnošću (`has_relation=False`).
Zadatak koji preoblikuje NAMJERNO nosi dvije — polaznu i dobijenu — pa je
provjera relacije bila isključena, a `missing_requested_relation` traži da
relacije uopšte NEMA. Oba nalaza su zato ćutala.

ZERO poziva modela: sve je čist deterministički kod ili FakeLLM.
"""
import pytest

from matbot import mcq_integrity
from matbot.request_fidelity import (DOMAIN_MISMATCH,
                                     MISSING_DISTINCT_TRANSFORMED_RELATION,
                                     MISSING_REQUESTED_RELATION,
                                     RELATION_MISMATCH, REQUEST_FIDELITY_CODE,
                                     TASK_TYPE_MISMATCH,
                                     TRANSFORMED_RELATION_MISMATCH,
                                     TRANSFORMED_RELATION_NOT_EQUIVALENT,
                                     request_fidelity_failures)

# ---------------------------------------------------------------------------
# TAČNI ŽIVI ZAPISI
# ---------------------------------------------------------------------------

LIVE_MSG = (
    "Kreiraj samostalan MCQ sa četiri opcije za nejednačinu $x>3$. U tekstu "
    "pokušaj preoblikovanje tako što lijevoj strani dodaš 2, a desnoj strani "
    "4, pa nastavi sa dobijenom nejednačinom. Traži cijeli skup rješenja i "
    "tačno jednu matematički tačnu opciju. Ne rješavaj zadatak učeniku.")
LIVE_TASK = (
    r"Početna nejednačina je $x>3$. Dodajemo $2$ s lijeve strane i $4$ s desne "
    r"strane pa dobijamo $x+2>7$. Riješi dobijenu nejednačinu $x+2>7$ i nađi "
    r"cijeli skup rješenja.")
LIVE_OPTIONS = (r"$(3,\infty)$", r"$[5,\infty)$", r"$(5,\infty)$", r"$(4,\infty)$")
LIVE_MARKED = 2

# Ispravka koju recenzent MORA napraviti: isti KORAK, ali na obje strane.
REPAIRED_TASK = (
    r"Početna nejednačina je $x>3$. Dodajemo $2$ na obje strane pa dobijamo "
    r"$x+2>5$. Riješi dobijenu nejednačinu $x+2>5$ i nađi cijeli skup rješenja.")
REPAIRED_OPTIONS = (r"$(5,\infty)$", r"$[3,\infty)$", r"$(3,\infty)$", r"$(4,\infty)$")
REPAIRED_MARKED = 2

LIVE_DETAIL = (f"{TRANSFORMED_RELATION_MISMATCH}: requested 'x > 3', "
               "task's transformed relation 'x > 5'")


# ---------------------------------------------------------------------------
# 1) ISTORIJSKI PAKET
# ---------------------------------------------------------------------------

def test_live_package_is_a_request_fidelity_violation():
    assert request_fidelity_failures(LIVE_MSG, LIVE_TASK) == (LIVE_DETAIL,)


def test_repaired_task_with_the_same_solution_set_is_clean():
    assert request_fidelity_failures(LIVE_MSG, REPAIRED_TASK) == ()


def test_the_marked_answer_was_internally_correct_for_the_wrong_relation():
    """Zašto se nalaz vodi kao vjernost zahtjevu, a ne kao pogrešan odgovor:
    $(5,\\infty)$ JESTE tačan za $x+2>7$ — pogrešna je relacija, ne račun."""
    published = mcq_integrity.read_solve_relations(LIVE_TASK)
    assert [relation.display() for relation in published] == [
        "x > 3", "x > 5", "x > 5"]
    requested = mcq_integrity.read_solve_statement(LIVE_MSG)
    assert requested.solution_display() == "x > 3"
    assert not mcq_integrity.solution_sets_match(
        requested.solution, published[1].solution)


# ---------------------------------------------------------------------------
# 2) POZITIVNE KONTROLE — nijedna ne smije pasti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,task", [
    # A: traženo preoblikovanje, bez citirane polazne relacije
    ("Riješi x>3", r"Riješi nejednačinu $x+2>5$."),
    # B: i polazna i dobijena su navedene, i ekvivalentne su
    ("Riješi x>3",
     r"Početna nejednačina je $x>3$. Dodajemo $2$ na obje strane pa dobijamo "
     r"$x+2>5$. Riješi dobijenu nejednačinu."),
    # C: jednačina — ista kanonska politika skupa rješenja
    ("Riješi 2x=6",
     r"Početna jednačina je $2x=6$, pa dobijamo $x=3$. Riješi dobijenu jednačinu."),
    # D: ista relacija dvaput, bez ijedne tvrdnje o preoblikovanju
    ("Riješi x>3",
     r"Riješi nejednačinu $x>3$. Provjeri da je $x>3$ zaista tačno."),
    # obrnut poredak strana je isti skup
    ("Riješi x>3",
     r"Polazna je $x>3$, pa dobijamo $5<x+2$. Riješi dobijenu nejednačinu."),
    # dobijena relacija stoji PRIJE polazne
    ("Riješi x>3",
     r"Dobijena nejednačina je $x+2>5$, a polazna je bila $x>3$. Riješi je."),
])
def test_positive_controls_stay_allowed(message, task):
    assert request_fidelity_failures(message, task) == (), (message, task)


def test_multiple_relations_alone_are_never_a_violation():
    """Uputa §6.D: postojanje više relacija samo po sebi ne smije oboriti
    zadatak — mora postojati TVRDNJA o preoblikovanju."""
    assert request_fidelity_failures(
        "Riješi x>3",
        r"Riješi nejednačinu $x+2>5$. Uporedi je sa $x>10$ iz prošlog "
        r"časa.") == ()


def test_discrete_domain_equivalence_still_holds_for_the_transformed_relation():
    """Nad Z su $x>3$ i $x\\ge 4$ ISTI skup — preoblikovanje koje nad traženim
    domenom daje isti skup ostaje dozvoljeno."""
    assert request_fidelity_failures(
        "Riješi x>3 u skupu cijelih brojeva.",
        r"Polazna je $x>3$ u skupu cijelih brojeva, pa dobijamo $x\ge 4$. "
        r"Riješi dobijenu nejednačinu.") == ()


# ---------------------------------------------------------------------------
# 3) NEGATIVNE KONTROLE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task,expected", [
    # tačan živi oblik
    (LIVE_TASK, "x > 5"),
    # tvrdnja o dobijenoj relaciji s drugim skupom rješenja
    (r"Polazna je $x>3$, a dobijena je $x>5$. Riješi dobijenu nejednačinu.",
     "x > 5"),
    # imperativ preoblikovanja + neekvivalentan rezultat
    (r"Preoblikuj $x>3$ tako da dobiješ $x+2>7$ pa riješi dobijenu nejednačinu.",
     "x > 5"),
    # množenje negativnim brojem bez okretanja znaka
    (r"Polazna je $x>3$, pa množenjem dobijamo $-x>-3$. Riješi je.",
     "x < 3"),
])
def test_non_equivalent_transformed_relations_are_violations(task, expected):
    failures = request_fidelity_failures("Riješi x>3", task)
    assert failures == (
        f"{TRANSFORMED_RELATION_MISMATCH}: requested 'x > 3', "
        f"task's transformed relation '{expected}'",), task


def test_single_relation_drift_is_still_the_old_relation_mismatch():
    """Novi nalaz ne smije progutati ni udvostručiti postojeći: kad zadatak
    ima TAČNO JEDNU relaciju, presuđuje provjera relacije kao i dosad."""
    failures = request_fidelity_failures(
        "Riješi x>3", r"Riješi dobijenu nejednačinu $x+2>7$.")
    assert failures == ("relation_mismatch: requested 'x > 3', task 'x > 5'",)
    assert not any(TRANSFORMED_RELATION_MISMATCH in detail for detail in failures)


# ---------------------------------------------------------------------------
# 4) GRANICE — što se namjerno NE tvrdi
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task", [
    # nelinearna „dobijena“ relacija — ne čita se, ne pogađa se
    r"Polazna je $x>3$, pa dobijamo $x^2>9$. Riješi dobijenu nejednačinu.",
    # iza markera uopšte nema relacije
    r"Polazna je $x>3$. Preoblikovanjem dobijamo traženo. Riješi to.",
    # nema tvrdnje o preoblikovanju
    r"Riješi nejednačinu $x>3$ i $2y=4$.",
])
def test_unprovable_shapes_preserve_conservative_behaviour(task):
    assert request_fidelity_failures("Riješi x>3", task) == (), task


def test_request_without_an_explicit_relation_adds_no_request_constraint():
    """Generička poruka NE nasljeđuje uslov iz prethodnog turna — provjere
    vezane za ZAHTJEV (domen, relacija, vrsta) i dalje ćute.

    Ranije je ovaj test tvrdio da tada nema NIJEDNOG nalaza, pa je i doslovno
    neistinit korak (`$x>3$` → `$x+2>7$`) prolazio čim poruka nije nosila
    relaciju. Živi ciljani recheck je pokazao da je to premalo: neistinita
    izvedba je pogrešna matematika bez obzira na to šta je učenik pitao, pa je
    sidro za nju SAM zadatak (vidi `TRANSFORMED_RELATION_NOT_EQUIVALENT`).
    Namjera testa ostaje ista i provjerava se strože: nijedan nalaz izveden iz
    ZAHTJEVA se ne pojavljuje."""
    request_derived = (DOMAIN_MISMATCH, RELATION_MISMATCH, TASK_TYPE_MISMATCH,
                       TRANSFORMED_RELATION_MISMATCH,
                       MISSING_REQUESTED_RELATION,
                       MISSING_DISTINCT_TRANSFORMED_RELATION)
    for message in ("Daj mi teži zadatak.", "Ne znam.", "Objasni mi ovo."):
        details = request_fidelity_failures(message, LIVE_TASK)
        assert not [detail for detail in details
                    if detail.startswith(request_derived)], (message, details)
        # ...a sam zadatak i dalje objavljuje neistinit korak.
        assert [detail for detail in details
                if detail.startswith(TRANSFORMED_RELATION_NOT_EQUIVALENT)], message


def test_a_clean_task_is_untouched_by_a_generic_follow_up():
    """Kontrola uz prethodni test: bez samoprotivrječnosti nema nalaza."""
    for message in ("Daj mi teži zadatak.", "Ne znam.", "Objasni mi ovo."):
        assert request_fidelity_failures(message, REPAIRED_TASK) == (), message


def test_task_without_a_solve_directive_is_not_this_finding():
    assert request_fidelity_failures(
        "Riješi x>3",
        r"Polazna je $x>3$, a dobijena je $x+2>7$. Koja tvrdnja opisuje "
        r"taj korak?") == ()


# ---------------------------------------------------------------------------
# 5) FW-R02 REGRESIJA — prethodni nalaz mora ostati netaknut
# ---------------------------------------------------------------------------

FW_R02_MSG = (
    "Kreiraj samostalan MCQ sa četiri opcije: riješi nejednačinu $x>3$, ali je "
    "u tekstu zadatka obavezno preoblikuj dodavanjem iste nenulte cijele "
    "konstante na obje strane; ne prepisuj relaciju doslovno.")
FW_R02_TASK = (
    r"Na obje strane originalne nejednačine dodan je isti nenulti cijeli broj "
    r"$2$. Riješite dobijenu nejednačinu i izaberite cijeli skup rješenja u "
    r"$\mathbb{Q}$.")


def test_missing_requested_relation_is_still_blocked():
    failures = request_fidelity_failures(FW_R02_MSG, FW_R02_TASK)
    assert failures == (
        f"{MISSING_REQUESTED_RELATION}: the task refers to a relation it never "
        "writes; requested 'x > 3'",)


def test_the_two_findings_are_mutually_exclusive():
    """Jedan traži da relacije NEMA, drugi da je pročitljiva ali pogrešna —
    nikad se ne prijavljuju zajedno."""
    for message, task in ((FW_R02_MSG, FW_R02_TASK), (LIVE_MSG, LIVE_TASK)):
        details = request_fidelity_failures(message, task)
        assert len(details) == 1, details
        assert not (MISSING_REQUESTED_RELATION in details[0]
                    and TRANSFORMED_RELATION_MISMATCH in details[0])


# ---------------------------------------------------------------------------
# 6) UREĐEN ČITAČ RELACIJA — ugovor nove funkcije
# ---------------------------------------------------------------------------

def test_read_solve_relations_preserves_textual_order_and_spans():
    relations = mcq_integrity.read_solve_relations(LIVE_TASK)
    assert [relation.display() for relation in relations] == ["x > 3", "x > 5", "x > 5"]
    assert [relation.start for relation in relations] == sorted(
        relation.start for relation in relations)
    for relation in relations:
        assert LIVE_TASK[relation.start:relation.end] in ("x>3", "x+2>7")


def test_read_solve_relations_uses_exact_fractions():
    relations = mcq_integrity.read_solve_relations(
        r"Polazna je $\frac{1}{3}<x$, pa dobijamo $3x>1$.")
    assert [relation.display() for relation in relations] == ["x > 1/3", "x > 1/3"]


def test_read_solve_relations_skips_what_it_cannot_read():
    assert mcq_integrity.read_solve_relations(
        r"Riješi $x^2>4$ i $|x|<3$ i $\sin x = 0$.") == ()


def test_read_solve_statement_contract_is_unchanged():
    """Jednorelacijski API i dalje vraća NAJVIŠE jednu relaciju i dalje
    proglašava dvije različite nejednoznačnima."""
    single = mcq_integrity.read_solve_statement("Riješi x>3")
    assert single.has_relation and single.solution_display() == "x > 3"
    twice = mcq_integrity.read_solve_statement(r"Riješi x>3, dakle $x>3$.")
    assert twice.has_relation, "isti skup zapisan dvaput nije nejednoznačnost"
    ambiguous = mcq_integrity.read_solve_statement(LIVE_TASK)
    assert not ambiguous.has_relation and ambiguous.relation_ambiguous


def test_span_tokenizer_matches_the_plain_tokenizer():
    from matbot.mathsegments import tokenize_math, tokenize_math_spans

    for text in (LIVE_TASK, "", "bez matematike", r"$$y=2$$ i $x=1$",
                 "nezatvoren $x>3 ostatak", r"escaped \$5 nije math"):
        spans = tokenize_math_spans(text)
        assert [(kind, content) for kind, content, _s, _e in spans] == \
            tokenize_math(text), text
        for _kind, content, start, end in spans:
            assert text[start:end] == content, text


# ---------------------------------------------------------------------------
# 7) PREFLIGHT I RECEPT ZA RECENZENTA
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
            normalized_parameters=[SignatureParameter(name="case", value="a3")],
            required_conditions=[], relevant_objects=["nejednačina"],
            answer_type="multiple_choice"))


def test_preflight_reports_the_transformed_relation_drift():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        student_message=LIVE_MSG)
    found = [issue for issue in issues if issue.code == REQUEST_FIDELITY_CODE]
    assert found, [issue.code for issue in issues]
    assert found[0].detail == LIVE_DETAIL


def test_reviewer_recipe_explains_why_two_different_amounts_are_wrong():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        student_message=LIVE_MSG)
    block = preflight.format_for_reviewer(issues)
    assert TRANSFORMED_RELATION_MISMATCH in block
    assert "same solution set" in block
    assert "$x+2>5$" in block and "$x+2>7$" in block
    assert "fail_closed" in block
    # postojeći recepti moraju ostati u istom bloku
    assert MISSING_REQUESTED_RELATION in block and RELATION_MISMATCH in block


def test_preflight_detail_leaks_no_visible_content():
    """CLAUDE.md pravilo 7: detalj nosi samo serverski izvedene činjenice."""
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        student_message=LIVE_MSG)
    described = preflight.describe_issues(issues)
    for leaked in ("Početna nejednačina", "s lijeve strane", "Dodajemo",
                   "Kreiraj samostalan MCQ", "pokušaj preoblikovanje"):
        assert leaked not in described, leaked


# ---------------------------------------------------------------------------
# 8) STVARAN DVOPOZIVNI PUT
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
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    response, session, fake = _run(
        monkeypatch, "tra3-bad",
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert session is None, "ništa se ne smije upisati u sesiju"
    assert fake.call_count == 2, "bez trećeg poziva"


def test_reviewer_repair_to_an_equivalent_relation_publishes(monkeypatch):
    response, session, fake = _run(
        monkeypatch, "tra3-fixed",
        _task_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED),
        decision="correct",
        final_task=_task_payload(REPAIRED_TASK, REPAIRED_OPTIONS, REPAIRED_MARKED))
    assert response.get("status") == "ready"
    assert session is not None
    assert fake.call_count == 2
    assert "$x+2>5$" in session["current_task"]
    assert "$x+2>7$" not in session["current_task"]
    # Recenzent je STVARNO dobio nalaz i recept u ulazu drugog poziva.
    _instructions, reviewer_input = fake.reviewer_calls[0]
    assert TRANSFORMED_RELATION_MISMATCH in reviewer_input
    assert "same solution set" in reviewer_input
