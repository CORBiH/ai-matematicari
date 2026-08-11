"""TASK 3 — ČUVAR VJERNOSTI IZRIČITOM ZAHTJEVU (DISC talas na 8a8f04d).

Potvrđena P1 klasa: učenik postavi jednoznačan matematički uslov, zadatak ga
TIHO promijeni, a paket ostane iznutra matematički ispravan — pa ga nijedna
postojeća kapija ne vidi.

  A020 (najteži): traženo „x+2=2 isključivo u domenu N={1,2,3,...}“, objavljeno
        „u domeni $\\mathbb{Z}$“ s označenim $\\{0\\}$. Nad Z je $\\{0\\}$ tačno;
        nad TRAŽENIM N je skup PRAZAN (N počinje od 1 po konvenciji projekta).
  A009: traženo $-2<x<2$, objavljeno $-2<x+1<2$ (skup $-3<x<1$).
  A010: traženo $-2<x<0$, objavljeno $-3<-2x<-1$ (skup $1/2<x<3/2$).
  A023: tražena nejednačina $1/3<x\\le 5/6$, objavljena JEDNAČINA $2x+3=7$.

Sve četiri poruke i sva četiri objavljena zadatka su DOSLOVNI zapisi iz
scratchpad/practice_eval/pre_release_discovery_100_8a8f04d/results.jsonl.

ZERO model poziva: sve je čist deterministički kod.
"""
import pytest

from matbot import mcq_integrity, request_fidelity
from matbot.request_fidelity import (DOMAIN_MISMATCH, RELATION_MISMATCH,
                                     REQUEST_FIDELITY_CODE, TASK_TYPE_MISMATCH,
                                     request_fidelity_failures)

# ---------------------------------------------------------------------------
# TAČNI ŽIVI ZAPISI
# ---------------------------------------------------------------------------

A009_MSG = ("Kreiraj samostalan MCQ sa četiri opcije: riješi -2<x<2 isključivo "
            "u domenu Z (cijeli brojevi). Traži cijeli skup rješenja, koristi "
            "raznolike prikaze skupa i osiguraj da je tačno jedna opcija "
            "matematički tačna u navedenom domenu. Ne rješavaj zadatak učeniku.")
A009_TASK = (r"Riješi nejednačinu $-2 < x+1 < 2$ isključivo u domenu $Z$ "
             r"(cijeli brojevi). Izaberi tačan skup rješenja.")

A010_MSG = ("Kreiraj samostalan MCQ sa četiri opcije: riješi -2<x<0 isključivo "
            "u domenu Z (cijeli brojevi). Traži cijeli skup rješenja, koristi "
            "raznolike prikaze skupa i osiguraj da je tačno jedna opcija "
            "matematički tačna u navedenom domenu. Ne rješavaj zadatak učeniku.")
A010_TASK = (r"Riješi nejednačinu $-3<-2x<-1$ isključivo u domenu $\mathbb{Z}$ "
             r"(cijeli brojevi). Koji je skup svih cijelih rješenja?")

A020_MSG = ("Kreiraj samostalan MCQ sa četiri opcije: riješi x+2=2 isključivo "
            "u domenu N={1,2,3,...}. Traži cijeli skup rješenja, koristi "
            "raznolike prikaze skupa i osiguraj da je tačno jedna opcija "
            "matematički tačna u navedenom domenu. Ne rješavaj zadatak učeniku.")
A020_TASK = (r"Riješi jednačinu $x+2=2$ isključivo u domeni $\mathbb{Z}$ "
             r"(skup svih cijelih brojeva). Koji je cijeli skup rješenja u toj "
             r"domeni?")
A020_OPTIONS = (r"$\{1\}$", r"$\varnothing$", r"$\{0\}$", r"$\{2\}$")
A020_MARKED = 2      # istorijski označeno $\{0\}$ — tačno nad Z, prazno nad N

A023_MSG = ("Kreiraj samostalan MCQ sa četiri opcije: riješi 1/3<x<=5/6 "
            "isključivo u domenu Q (racionalni brojevi). Traži cijeli skup "
            "rješenja, koristi raznolike prikaze skupa i osiguraj da je tačno "
            "jedna opcija matematički tačna u navedenom domenu. Ne rješavaj "
            "zadatak učeniku.")
A023_TASK = (r"Riješi jednačinu $2x+3=7$ u skupu racionalnih brojeva "
             r"$\mathbb{Q}$. Odaberi tačnu vrijednost $x$.")


# ---------------------------------------------------------------------------
# 1) ISTORIJSKE REGRESIJE
# ---------------------------------------------------------------------------

def test_disc_a020_natural_domain_may_not_publish_as_integers():
    """NAJJAČA tražena regresija: N nije Z. Nalaz mora IMENOVATI oba skupa,
    jer je generičko „neka bude relevantno“ upravo ono što je drift pustilo."""
    failures = request_fidelity_failures(A020_MSG, A020_TASK)
    assert failures == ("domain_mismatch: requested N, task Z",)


def test_disc_a020_marked_option_is_empty_over_the_requested_domain():
    """Zašto je drift ozbiljan: nad TRAŽENIM N skup rješenja je PRAZAN, pa
    istorijski označeno $\\{0\\}$ nije samo „drugi zadatak“ nego pogrešan
    odgovor na postavljeno pitanje."""
    faithful = (r"Riješi jednačinu $x+2=2$ isključivo u skupu prirodnih "
                r"brojeva. Koji je cijeli skup rješenja?")
    result = mcq_integrity.evaluate_linear_solve_mcq(faithful, A020_OPTIONS)
    assert result.applicable
    assert result.solution_display == "∅"
    # $\{0\}$ se nad N presijeca u prazan skup — dakle NIJE poseban odgovor
    assert result.option_displays[2] == "∅"


def test_disc_a009_relation_drift_is_detected():
    failures = request_fidelity_failures(A009_MSG, A009_TASK)
    assert failures == ("relation_mismatch: requested '-2 < x < 2', "
                        "task '-3 < x < 1'",)


def test_disc_a010_relation_drift_is_detected():
    failures = request_fidelity_failures(A010_MSG, A010_TASK)
    assert failures == ("relation_mismatch: requested '-2 < x < 0', "
                        "task '1/2 < x < 3/2'",)


def test_disc_a023_relation_and_task_type_drift_are_detected():
    """Tražena nejednačina, objavljena jednačina — i skup rješenja i vrsta."""
    failures = request_fidelity_failures(A023_MSG, A023_TASK)
    assert failures == (
        "relation_mismatch: requested '1/3 < x <= 5/6', task 'x = 2'",
        "task_type_mismatch: requested inequality, task equation")


@pytest.mark.parametrize("message,faithful", [
    (A009_MSG, r"Riješi nejednačinu $-2<x<2$ isključivo u domenu "
               r"cijelih brojeva."),
    (A010_MSG, r"Riješi nejednačinu $-2<x<0$ isključivo u domenu "
               r"cijelih brojeva."),
    (A020_MSG, r"Riješi jednačinu $x+2=2$ isključivo u skupu prirodnih "
               r"brojeva."),
    (A023_MSG, r"Riješi nejednačinu $\frac{1}{3}<x\le\frac{5}{6}$ u skupu "
               r"racionalnih brojeva."),
])
def test_faithful_versions_of_all_four_requests_are_clean(message, faithful):
    """Kontrola: kad zadatak POŠTUJE isti zahtjev, čuvar ćuti."""
    assert request_fidelity_failures(message, faithful) == ()


# ---------------------------------------------------------------------------
# 2) DOMEN — Q/R/Z/N/N0
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("request_phrase,task_phrase", [
    ("u skupu prirodnih brojeva", "u skupu prirodnih brojeva"),
    ("u skupu cijelih brojeva", r"za $x\in\mathbb{Z}$"),
    ("u skupu racionalnih brojeva", "u skupu racionalnih brojeva"),
    ("u skupu realnih brojeva", "u skupu realnih brojeva"),
    (r"u $\mathbb{N}_0$", r"u $\mathbb{N}_0$"),
])
def test_preserved_domain_is_clean(request_phrase, task_phrase):
    message = f"Riješi x+2=5 {request_phrase}."
    task = rf"Riješi jednačinu $x+2=5$ {task_phrase}."
    assert request_fidelity_failures(message, task) == (), request_phrase


@pytest.mark.parametrize("message,task,expected", [
    # N → Z: tačna živa klasa A020
    ("Riješi x+2=5 u skupu prirodnih brojeva.",
     "Riješi jednačinu $x+2=5$ u skupu cijelih brojeva.", "requested N, task Z"),
    # N i N0 NISU isti skup — nula je razlika
    (r"Riješi x+2=5 u $\mathbb{N}$.", r"Riješi jednačinu $x+2=5$ u $\mathbb{N}_0$.",
     "requested N, task N0"),
    (r"Riješi x+2=5 u $\mathbb{N}_0$.", r"Riješi jednačinu $x+2=5$ u $\mathbb{N}$.",
     "requested N0, task N"),
    # Z → N
    ("Riješi x+2=5 u skupu cijelih brojeva.",
     "Riješi jednačinu $x+2=5$ u skupu prirodnih brojeva.", "requested Z, task N"),
    # Q → R
    ("Riješi x+2=5 u skupu racionalnih brojeva.",
     "Riješi jednačinu $x+2=5$ u skupu realnih brojeva.", "requested Q, task R"),
])
def test_domain_swap_is_a_violation(message, task, expected):
    failures = request_fidelity_failures(message, task)
    assert failures == (f"{DOMAIN_MISMATCH}: {expected}",)


def test_requested_domain_must_be_explicitly_preserved_not_assumed():
    """Zadatak koji traženi domen NE navodi ne dokazuje da u njemu radi —
    tražimo izričit dokaz, ne pretpostavku (uputa §5)."""
    failures = request_fidelity_failures(
        "Riješi x+2=5 u skupu prirodnih brojeva.",
        r"Riješi jednačinu $x+2=5$.")
    assert failures == (f"{DOMAIN_MISMATCH}: requested N, "
                        "task states no provable domain",)


def test_no_requested_domain_means_the_domain_check_stays_out():
    """Bez izričitog učenikovog domena zadatak slobodno bira — druga politika
    je vlasnik te odluke, ne ovaj čuvar (uputa §14)."""
    assert request_fidelity_failures(
        "Riješi x+2=5", r"Riješi jednačinu $x+2=5$ u skupu cijelih brojeva.") == ()


def test_unsupported_or_conflicting_requested_domain_is_skipped():
    for message in ("Riješi x+2=5 u skupu parnih brojeva.",
                    "Riješi x+2=5 u skupu prirodnih i cijelih brojeva."):
        assert request_fidelity_failures(
            message, r"Riješi jednačinu $x+2=5$ u skupu cijelih brojeva.") == (), message


# ---------------------------------------------------------------------------
# 3) RELACIJA — kanonska ekvivalencija, ne poređenje zapisa
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,task", [
    # tražena preformulacija iz upute §13
    ("Riješi x>3", r"Riješi nejednačinu $x+2>5$."),
    # B003: preformulacija s ISTIM skupom rješenja NIJE drift koji blokira
    ("Riješi 2x=6", r"Riješi jednačinu $x=3$."),
    ("Riješi 2x=6", r"Riješi jednačinu $6=2x$."),
    # ista relacija, drugi zapis granice
    (r"Riješi x<=4", r"Riješi nejednačinu $x\le 4$."),
    # obrnut poredak strana je isti skup
    ("Riješi x>3", r"Riješi nejednačinu $3<x$."),
])
def test_solution_set_identical_reformulation_is_allowed(message, task):
    assert request_fidelity_failures(message, task) == (), message


def test_discrete_domain_equivalent_reformulation_is_allowed():
    """Nad Z su $x>3$ i $x\\ge 4$ ISTI skup — kad su domeni saglasni, poredi
    se i presjek s domenom, pa se korektna preformulacija ne obara."""
    assert request_fidelity_failures(
        "Riješi x>3 u skupu cijelih brojeva.",
        r"Riješi nejednačinu $x\ge 4$ u skupu cijelih brojeva.") == ()


def test_same_reformulation_over_q_is_a_violation():
    """Kontrola uz prethodni test: nad Q $x>3$ i $x\\ge 4$ NISU isti skup."""
    failures = request_fidelity_failures(
        "Riješi x>3 u skupu racionalnih brojeva.",
        r"Riješi nejednačinu $x\ge 4$ u skupu racionalnih brojeva.")
    assert failures == ("relation_mismatch: requested 'x > 3', task 'x >= 4'",)


@pytest.mark.parametrize("message,task", [
    ("Riješi x>3", r"Riješi nejednačinu $x<3$."),          # obrnut smjer
    ("Riješi 2x+1=5", r"Riješi nejednačinu $2x+1<5$."),     # jednačina → nejednačina
    ("Riješi x+1=7", r"Riješi jednačinu $x+1=9$."),         # druga jednačina
    ("Riješi -2<x<2", r"Riješi nejednačinu $-2<x<3$."),     # druga granica
])
def test_materially_different_relation_is_a_violation(message, task):
    failures = request_fidelity_failures(message, task)
    assert any(detail.startswith(RELATION_MISMATCH) for detail in failures), \
        (message, failures)


def test_unreadable_task_relation_cannot_prove_a_relation_violation():
    """Zadatak bez pročitljive relacije ne dokazuje prekršaj — preskoči,
    nikad ne pogađaj (doktrina cijelog projekta)."""
    assert request_fidelity_failures(
        "Riješi x>3", "Koji od ponuđenih brojeva je djeljiv sa 25?") == ()


# ---------------------------------------------------------------------------
# 4) VRSTA ZADATKA — samo kad je izričita i dokaziva
# ---------------------------------------------------------------------------

def test_explicit_inequality_request_answered_with_an_equation():
    failures = request_fidelity_failures(
        "Riješi mi jednu nejednačinu.", r"Riješi jednačinu $x+2=5$.")
    assert failures == (f"{TASK_TYPE_MISMATCH}: requested inequality, "
                        "task equation",)


def test_explicit_equation_request_answered_with_an_inequality():
    failures = request_fidelity_failures(
        "Riješi mi jednu jednačinu.", r"Riješi nejednačinu $x+2<5$.")
    assert failures == (f"{TASK_TYPE_MISMATCH}: requested equation, "
                        "task inequality",)


def test_matching_task_type_is_clean():
    assert request_fidelity_failures(
        "Riješi mi jednu nejednačinu.", r"Riješi nejednačinu $x+2<5$.") == ()
    assert request_fidelity_failures(
        "Riješi mi jednu jednačinu.", r"Riješi jednačinu $x+2=5$.") == ()


def test_negated_word_is_read_before_the_substring():
    """`nejednačina` SADRŽI `jednačina` kao podniz — negirani oblik se mora
    pročitati prvi, inače bi svaki zahtjev za nejednačinom bio pročitan kao
    zahtjev za jednačinom."""
    statement = mcq_integrity.read_solve_statement("Daj mi nejednačinu.")
    assert statement.stated_kind == mcq_integrity.RELATION_INEQUALITY


def test_ambiguous_wording_skips_the_task_type_check():
    assert request_fidelity_failures(
        "Riješi mi neki zadatak.", r"Riješi jednačinu $x+2=5$.") == ()


# ---------------------------------------------------------------------------
# 5) EKSTRAKCIJA — nejednoznačno se PRESKAČE, nikad ne pogađa
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    # dvije RAZLIČITE relacije — ne zna se koja je zahtjev
    "Riješi 2x=6 i 3x=12",
    "Riješi x>3 pa onda x<10",
    # nema izričitog uslova
    "Riješi mi ovo, molim te.",
    "Ne razumijem ovaj zadatak.",
])
def test_ambiguous_or_empty_requests_are_skipped(message):
    assert request_fidelity_failures(
        message, r"Riješi jednačinu $x=5$ u skupu cijelih brojeva.") == (), message


def test_the_same_relation_written_twice_is_not_ambiguous():
    """Ponovljen ISTI zahtjev (proza + $…$) ostaje jedan zahtjev."""
    failures = request_fidelity_failures(
        r"Riješi x>3, dakle $x>3$.", r"Riješi nejednačinu $x<3$.")
    assert any(detail.startswith(RELATION_MISMATCH) for detail in failures)


def test_domain_enumeration_is_read_as_domain_not_as_a_relation():
    """`N={1,2,3,...}` je DEFINICIJA DOMENA, ne jednačina — i prvi član
    razlikuje N od N0."""
    natural = mcq_integrity.read_solve_statement("riješi x+2=2 u domenu N={1,2,3,...}")
    assert natural.domain_status == "supported" and natural.domain == "N"
    assert natural.solution_display() == "x = 0"      # relacija je x+2=2
    with_zero = mcq_integrity.read_solve_statement("riješi x+2=2 u domenu N={0,1,2,...}")
    assert with_zero.domain == "N0"


def test_incidental_number_set_mention_without_a_solve_request_is_ignored():
    """Bez direktive rješavanja usputno pominjanje skupa NIJE uslov zadatka —
    inače bi „ne razumijem cijele brojeve“ obaralo svaki zadatak."""
    assert request_fidelity_failures(
        "Ne razumijem cijele brojeve.",
        r"Riješi jednačinu $x+2=5$ u skupu prirodnih brojeva.") == ()


# ---------------------------------------------------------------------------
# 6) NASTAVCI RAZGOVORA — bez ljepljivog stanja (uputa §10)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "teže", "lakše", "Daj mi teži zadatak.", "Daj mi drugi zadatak.",
    "Još jedan, molim.", "Objasni mi ovo.", "Pomoć.", "Ne znam.",
])
def test_ordinary_follow_ups_carry_no_constraint(message):
    assert request_fidelity_failures(
        message, r"Riješi jednačinu $x+2=5$ u skupu cijelih brojeva.") == (), message


def test_constraint_does_not_stick_to_the_next_turn():
    """Uslov iz PRETHODNE poruke se ne nasljeđuje: čuvar čita isključivo
    poruku tekućeg turna i ne pamti ništa."""
    first = request_fidelity_failures(
        "Riješi x+2=2 u skupu prirodnih brojeva.",
        r"Riješi jednačinu $x+2=2$ u skupu cijelih brojeva.")
    assert first, "prvi turn MORA prijaviti drift"
    second = request_fidelity_failures(
        "Daj mi teži zadatak.",
        r"Riješi jednačinu $2x+4=10$ u skupu cijelih brojeva.")
    assert second == (), "nastavak ne smije naslijediti stari domen"


# ---------------------------------------------------------------------------
# 7) PREFLIGHT — nalaz i recept stižu recenzentu prije drugog poziva
# ---------------------------------------------------------------------------

def _task_payload(text, options, marked, lesson_id="9-04-002",
                  lesson_title="Ekvivalentne jednačine"):
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
            task_family="linear_equation", operation_or_relation="solve",
            normalized_parameters=[SignatureParameter(name="case", value="t3")],
            required_conditions=[], relevant_objects=["jednačina"],
            answer_type="multiple_choice"))


def test_preflight_reports_the_request_fidelity_issue():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(A020_TASK, A020_OPTIONS, A020_MARKED),
        student_message=A020_MSG)
    found = [issue for issue in issues if issue.code == REQUEST_FIDELITY_CODE]
    assert found, [issue.code for issue in issues]
    assert found[0].detail == "domain_mismatch: requested N, task Z"


def test_preflight_without_a_student_message_reports_nothing_new():
    """Postojeći pozivaoci bez nove riječi-argumenta ostaju bajt za bajt isti."""
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(A020_TASK, A020_OPTIONS, A020_MARKED))
    assert [i for i in issues if i.code == REQUEST_FIDELITY_CODE] == []


def test_reviewer_recipe_names_the_exact_domain_rule():
    """Recept mora imenovati TAČAN skup, ne reći „neka bude relevantno“ —
    generičko uputstvo je upravo ono što je pustilo N→Z drift."""
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(A020_TASK, A020_OPTIONS, A020_MARKED),
        student_message=A020_MSG)
    block = preflight.format_for_reviewer(issues)
    assert REQUEST_FIDELITY_CODE in block
    assert DOMAIN_MISMATCH in block and RELATION_MISMATCH in block
    assert "{1,2,3,...}" in block and "EXCLUDES 0" in block
    # lekcija uvijek pobjeđuje nad zahtjevom
    assert "fail_closed" in block


def test_preflight_detail_leaks_no_visible_content():
    """CLAUDE.md pravilo 7: detalj nosi samo serverski izvedene činjenice."""
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(A023_TASK, ("$x=1$", "$x=0$", "$x=2$", r"$x=\frac{7}{2}$"), 2),
        student_message=A023_MSG)
    described = preflight.describe_issues(issues)
    for leaked in ("Kreiraj samostalan MCQ", "Odaberi tačnu vrijednost",
                   "raznolike prikaze"):
        assert leaked not in described, leaked


# ---------------------------------------------------------------------------
# 8) STVARNI DVOPOZIVNI PUT — recenzent popravlja ili se pada zatvoreno
# ---------------------------------------------------------------------------

A020_REPAIRED_TASK = (r"Riješi jednačinu $x+2=2$ isključivo u skupu prirodnih "
                      r"brojeva. Koji je cijeli skup rješenja?")
A020_REPAIRED_OPTIONS = (r"$\varnothing$", r"$\{1\}$", r"$\{2\}$", r"$\{1,2\}$")


def _turn(topic, session_id, message):
    return {
        "session_id": session_id, "grade": int(topic[0]),
        "selected_topic": topic, "selected_oblast": "",
        "student_message": message, "intent": "", "difficulty_request": "",
        "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


def _queue(fake, draft_task, *, decision="approve", final_task=None):
    from matbot.tutor.schema import ReviewerChecks, ReviewerFinal, TutorDraft
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="izabrana lekcija", new_task=draft_task)
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


def _run(monkeypatch, session_id, message, draft_task, **queue_kwargs):
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore
    from tests.conftest import FakeLLM

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    store, fake = SessionStore(), FakeLLM()
    _queue(fake, draft_task, **queue_kwargs)
    response = run_practice_turn(store, fake, _turn("9-04-002", session_id, message))
    return response, store.peek(session_id), fake


def test_a020_unchanged_approval_fails_closed(monkeypatch):
    """A: recenzent odobri nepromijenjen paket s N→Z driftom → sigurno padanje
    prije mutacije sesije, bez trećeg poziva."""
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    response, session, fake = _run(
        monkeypatch, "rf-a020-bad", A020_MSG,
        _task_payload(A020_TASK, A020_OPTIONS, A020_MARKED))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert session is None
    assert fake.call_count == 2


def test_a020_reviewer_repair_of_the_domain_publishes(monkeypatch):
    """B: recenzent ispravi Z → N u ISTOM drugom pozivu i paket prolazi sve
    ostale kapije — čuvar ne blokira ispravljen paket."""
    response, session, fake = _run(
        monkeypatch, "rf-a020-fixed", A020_MSG,
        _task_payload(A020_TASK, A020_OPTIONS, A020_MARKED),
        decision="correct",
        final_task=_task_payload(A020_REPAIRED_TASK, A020_REPAIRED_OPTIONS, 0))
    assert response.get("status") == "ready"
    assert session is not None
    assert fake.call_count == 2
    # Recenzent je STVARNO dobio serverski nalaz u ulazu drugog poziva.
    _instructions, reviewer_input = fake.reviewer_calls[0]
    assert REQUEST_FIDELITY_CODE in reviewer_input
    assert "requested N, task Z" in reviewer_input


def test_faithful_package_publishes_untouched(monkeypatch):
    """Kontrola protiv prekomjernog blokiranja: vjeran paket prolazi odmah."""
    response, session, _fake = _run(
        monkeypatch, "rf-a020-ok", A020_MSG,
        _task_payload(A020_REPAIRED_TASK, A020_REPAIRED_OPTIONS, 0))
    assert response.get("status") == "ready"
    assert session is not None


def test_generic_follow_up_message_does_not_block_publication(monkeypatch):
    """Obična poruka bez uslova ne smije ništa oboriti (uputa §10)."""
    response, session, _fake = _run(
        monkeypatch, "rf-generic", "Daj mi zadatak.",
        _task_payload(r"Riješi jednačinu $x+2=5$ u skupu cijelih brojeva.",
                      (r"$\{3\}$", r"$\{2\}$", r"$\{7\}$", r"$\varnothing$"), 0))
    assert response.get("status") == "ready"
    assert session is not None


# ---------------------------------------------------------------------------
# 9) ODVOJENOST OD LEKCIJSKE/PEDAGOŠKE KAPIJE (Task 2 ostaje nadređen)
# ---------------------------------------------------------------------------

def test_faithfully_reproduced_off_lesson_request_is_still_blocked():
    """Vjernost zahtjevu NIJE dozvola za izlazak iz lekcije: tačan živi E009
    zahtjev je vjerno prepisan, ali semantički ugovor lekcije i dalje odbija
    paket. Dvije nezavisne kapije, obje moraju proći."""
    from matbot import semantic_practice
    from matbot.tutor import package_preflight as preflight

    e009_message = ("Napravi zadatak o podudarnosti trouglova i zahtijevaj "
                    "odgovarajući kriterij, ne samo jednake površine.")
    e009_task = (r"U jednakokrakom trouglu $ABC$ vrijedi $AB=AC$. Simetrala "
                 r"ugla $A$ siječe stranicu $BC$ u tački $D$. Koji kriterij "
                 r"podudarnosti garantuje da su trouglovi $ABD$ i $ACD$ "
                 r"podudarni?")
    options = ("SSS (stranica - stranica - stranica)",
               "SAS (stranica - ugao - stranica)",
               "RHS (pravougli - hipotenuza - kateta)",
               "ASA (ugao - stranica - ugao)")
    # Zahtjev je vjerno ispunjen — ovaj čuvar nema šta da prijavi...
    assert request_fidelity_failures(e009_message, e009_task) == ()
    # ...ali lekcijski ugovor (Task 2) i dalje obara paket.
    contract = semantic_practice.contract_for("7-04-022")
    issues = preflight.collect_package_issues(
        _task_payload(e009_task, options, 1, lesson_id="7-04-022",
                      lesson_title="Simetrale uglova i centar upisane kružnice"),
        practice_contract=contract, student_message=e009_message)
    assert any(issue.code == preflight.SEMANTIC_FIDELITY_CODE
               for issue in issues), [i.code for i in issues]


def test_request_fidelity_module_holds_no_lesson_or_scenario_identity():
    """Čuvar je univerzalan: nijedan ID lekcije ni scenarija u IZVRŠNOM kodu.

    Docstring modula NAMJERNO imenuje žive nalaze — to je institucionalna
    memorija koju CLAUDE.md izričito traži. Grana po scenariju bi bila
    prekršaj; navođenje uzroka u komentaru nije."""
    import ast
    import re
    from pathlib import Path

    source = Path(request_fidelity.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    tree.body = [node for node in tree.body
                 if not (isinstance(node, ast.Expr)
                         and isinstance(node.value, ast.Constant)
                         and isinstance(node.value.value, str))]
    executable = ast.dump(tree)
    assert not re.search(r"\b\d-\d{2}-\d{3}\b", executable)
    for scenario in ("A009", "A010", "A020", "A023", "DISC"):
        assert scenario not in executable, scenario
