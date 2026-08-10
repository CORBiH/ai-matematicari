r"""POZITIVNA RASPOLOŽIVOST ZA „RIJEŠI (NE)JEDNAČINU“ MCQ (živi T1 recheck).

Živi talas `scratchpad/practice_eval/t1_t3_recheck_b38d08a_20260810-133323/`:
nijedan objavljen paket nije bio matematički pogrešan (WRONG_MATH_PUBLISHED=0),
ali su OBA tražena pozitivna scenarija pala zatvoreno:

    R-T1Z  Z, traženo $\{4,5,6,\dots\}$  → `unverifiable_solution_option`
                                            (server solved: x >= 4 [cijeli])
    R-T1QP Q, traženo $\{x\in Q\mid x>3\}$ → Tutor `multiple_correct_options`
                                            + `missing_distinct_transformed_relation`;
                                            recenzent popravio prvo, ostavio drugo

U SVIH ŠEST scenarija tog talasa Tutorov nacrt je već nosio nalaz o opcijama
(3× `unverifiable_solution_option`, 3× `multiple_correct_options`) — uključujući
i jedini objavljen (R-T3P). To nije slučajnost nego rupa u UGOVORU AUTORSTVA:
prompt nigdje nije rekao šta opcija takvog zadatka mora značiti, koji su zapisi
serverski dokazivi, ni da odgovor zavisi od domena.

Ovi testovi dokazuju DVIJE stvari:
  1. svi zapisi koje novi ugovor preporučuje server STVARNO ume da pročita —
     obećanje u promptu se ne smije razlikovati od onoga što validator dokazuje;
  2. namjeravani uspješni tokovi se STVARNO objavljuju kroz dvopozivni put.

Nijedna provjera nije popuštena: sigurnosne regresije na dnu moraju i dalje
padati zatvoreno.

ZERO poziva modela: sve je čist deterministički kod ili FakeLLM.
"""
import pytest

from matbot import mcq_integrity
from matbot.request_fidelity import (MISSING_DISTINCT_TRANSFORMED_RELATION,
                                     TRANSFORMED_RELATION_NOT_EQUIVALENT,
                                     request_fidelity_failures)
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build

DISCRETE_FOR_CONTINUOUS = (
    mcq_integrity.DISCRETE_OPTIONS_FOR_CONTINUOUS_SOLUTION_CODE)
UNVERIFIABLE = mcq_integrity.UNVERIFIABLE_SOLUTION_OPTION_CODE

Q_QUESTION = r"Riješi nejednačinu $x>3$ u skupu racionalnih brojeva."
Z_QUESTION = r"Riješi nejednačinu $x>3$ u skupu cijelih brojeva."


# ===========================================================================
# 1) SVAKI ZAPIS KOJI PROMPT PREPORUČUJE MORA BITI DOKAZIV
# ===========================================================================
# Ovo je ugovor između prompta i validatora: ako prompt obeća oblik koji server
# ne ume da pročita, dobili bismo tačno onaj `unverifiable_solution_option` koji
# je i oborio R-T1Z.

@pytest.mark.parametrize("option,domain", [
    # relacija
    (r"$x>3$", ""), (r"$x\ge 4$", ""), (r"$x\ge4$", "Z"), (r"$-2<x<0$", ""),
    # interval
    (r"$(3,\infty)$", ""), (r"$[4,\infty)$", ""), (r"$(3,+\infty)$", ""),
    # skup s izričitim domenom
    (r"$\{x\in Q\mid x>3\}$", ""), (r"$\{x\in\mathbb{Q}\mid x>3\}$", ""),
    (r"$\{x\in\mathbb{Z}\mid x\ge4\}$", "Z"),
    # vrijednost / jednočlan skup
    (r"$3$", ""), (r"$\{3\}$", ""),
    # nabrajanje cijelih brojeva uz cjelobrojni domen
    (r"$\{4,5,6,\dots\}$", "Z"), (r"$\{4,5,6,\ldots\}$", "Z"),
    (r"$\{4,5,6,...\}$", "Z"), (r"$x\in\{4,5,6,\dots\}$", "Z"),
])
def test_every_recommended_notation_is_readable(option, domain):
    parsed = mcq_integrity._solve_option_set(option, "x", allow_bare_value=True,
                                             domain=domain)
    assert parsed is not None, option


@pytest.mark.parametrize("option", [
    r"$\{x\mid x>3\}$",                    # set-builder BEZ domena
    r"$\{x : x\ge 4\}$",                   # isto, drugi separator
    r"$\{4,5,6,\dots\}\subset\mathbb{Z}$",  # ukrašena opcija
    r"$4,5,6,\dots$",                      # nabrajanje bez zagrada
    r"$x\ge 4, x\in\mathbb{Z}$",           # dva uslova u jednoj opciji
    "svi cijeli brojevi veći od 3",        # riječi umjesto zapisa
])
def test_every_forbidden_notation_is_exactly_what_the_prompt_forbids(option):
    """Oblici koje ugovor izričito zabranjuje su upravo oni koje server ne čita —
    prompt time opisuje STVARNU granicu, ne izmišljenu."""
    assert mcq_integrity._solve_option_set(
        option, "x", allow_bare_value=True, domain="Z") is None, option


def test_the_authoring_rule_reaches_both_prompts():
    tutor = tutor_prompts.build_tutor_instructions(build(7, "7-03-019"))
    reviewer = tutor_prompts.build_reviewer_instructions(build(7, "7-03-019"))
    for text in (tutor, reviewer):
        assert "SKUP RJEŠENJA" in text
        assert r"$\{x\in\mathbb{Q}\mid x>3\}$" in text
        assert r"$(3,\infty)$" in text
        assert r"$\{4,5,6,\dots\}$" in text
        assert "7/2" in text                       # zašto nabrajanje pada nad Q
        assert r"$x+2>5$" in text                  # tražena preformulacija
        assert "TAČNO JEDNA opcija" in text


def test_the_authoring_rule_stays_in_the_cacheable_prefix():
    """Workstream K: pravilo je isto za svih 534 lekcije, pa mora ostati u
    STATIČKOM prefiksu (inače poskupljuje svaki poziv)."""
    a = tutor_prompts.build_tutor_instructions(build(6, "6-04-009"))
    b = tutor_prompts.build_tutor_instructions(build(9, "9-05-010"))
    shared = 0
    for first, second in zip(a, b):
        if first != second:
            break
        shared += 1
    assert a.index("SKUP RJEŠENJA") < shared


# ===========================================================================
# 2) Q POZITIVNE KONTROLE (uputa §9.D, §9.E)
# ===========================================================================

@pytest.mark.parametrize("marked", [
    r"$\{x\in Q\mid x>3\}$",           # §9.D
    r"$\{x\in\mathbb{Q}\mid x>3\}$",
    r"$(3,\infty)$",                   # §9.E
    r"$x>3$",
])
def test_q_complete_answers_publish(marked):
    options = (marked, r"$x>5$", r"$x\ge3$", r"$x>1$")
    result = mcq_integrity.evaluate_linear_solve_mcq(Q_QUESTION, options)
    assert result.applicable and result.valid, result.reason_code
    assert result.correct_indices == (0,)
    failure, _ = mcq_integrity.publication_failure(Q_QUESTION, options, 0, marked)
    assert failure == ""


def test_q_live_published_shape_from_the_recheck_still_validates():
    """Tačan objavljen paket iz R-T3P — jedini koji je prošao živi talas."""
    question = (r"Polazna nejednačina je $x>3$; dodavanjem 2 na obje strane "
                r"dobijamo $x+2>5$. Riješite dobijenu nejednačinu i navedite "
                r"kompletan skup rješenja (u skupu racionalnih brojeva).")
    options = (r"$\{x\in\mathbb{Q}\mid x>3\}$", r"$x\ge3$", r"$x>5$", r"$x>1$")
    failure, _ = mcq_integrity.publication_failure(question, options, 0, options[0])
    assert failure == ""
    assert request_fidelity_failures("Daj mi jedan zadatak.", question) == ()


# ===========================================================================
# 3) Z POZITIVNE KONTROLE (uputa §9.B, §9.C)
# ===========================================================================

@pytest.mark.parametrize("marked", [
    r"$x\ge 4$",                       # §9.B — jednostavna relacija
    r"$\{4,5,6,\dots\}$",              # §9.C — podržano nabrajanje
    r"$\{4,5,6,\ldots\}$",
    r"$\{x\in\mathbb{Z}\mid x\ge4\}$",
    r"$[4,\infty)$",
])
def test_z_complete_answers_publish(marked):
    options = (marked, r"$x\ge 6$", r"$x\ge 3$", r"$x\ge 5$")
    result = mcq_integrity.evaluate_linear_solve_mcq(Z_QUESTION, options)
    assert result.applicable and result.valid, result.reason_code
    assert result.correct_indices == (0,)
    failure, _ = mcq_integrity.publication_failure(Z_QUESTION, options, 0, marked)
    assert failure == ""


def test_z_relation_and_enumeration_denote_the_same_set():
    """Zašto su oba oblika dozvoljena nad Z: kanonski su ISTI skup."""
    relation = mcq_integrity._solve_option_set(r"$x\ge 4$", "x",
                                               allow_bare_value=True, domain="Z")
    enumeration = mcq_integrity._solve_option_set(r"$\{4,5,6,\dots\}$", "x",
                                                  allow_bare_value=True, domain="Z")
    assert relation == enumeration


# ===========================================================================
# 4) STVARAN DVOPOZIVNI PUT (uputa §9.A, §9.F, §9.G, §9.H)
# ===========================================================================

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
            normalized_parameters=[SignatureParameter(name="case", value="t1a")],
            required_conditions=[], relevant_objects=["nejednačina"],
            answer_type="multiple_choice"))


def _turn(session_id, message):
    return {
        "session_id": session_id, "grade": 7, "selected_topic": "7-03-019",
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


def _run(monkeypatch, session_id, message, draft_task, *, decision="approve",
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
            difficulty_evidence_valid=True, task_signature_consistent=True),
        final=draft.model_copy(update={"new_task": final_task}),
        reviewed_difficulty_evidence=final_task.difficulty_evidence))
    response = run_practice_turn(store, fake, _turn(session_id, message))
    return response, store.peek(session_id), fake


# Živa R-T1QP poruka: Q, izričito tražena preoblikovana relacija.
Q_REFORMULATION_MSG = (
    "Kreiraj samostalan MCQ sa četiri opcije u skupu racionalnih brojeva Q. "
    "Polazna nejednačina je $x>3$; dodaj 2 na obje strane i u zadatku napiši "
    "ekvivalentnu preoblikovanu relaciju $x+2>5$. Tačan odgovor napiši kao "
    "kompletan skup $\\{x\\in Q\\mid x>3\\}$, a distraktore učini matematički "
    "različitim. Osiguraj da je tačno jedna opcija tačna i ne rješavaj zadatak "
    "učeniku.")
Q_GOOD_TASK = (r"Polazna nejednačina je $x>3$; dodavanjem 2 na obje strane "
               r"dobijamo $x+2>5$. Riješi dobijenu nejednačinu i navedi "
               r"kompletan skup rješenja u skupu racionalnih brojeva.")
Q_GOOD_OPTIONS = (r"$\{x\in Q\mid x>3\}$", r"$x>5$", r"$x\ge3$", r"$x>1$")

# Nacrt koji ponavlja samo polaznu relaciju (živi R-T1QP defekt).
Q_MISSING_RELATION_TASK = (
    r"Polazna nejednačina je $x>3$. Riješi dobijenu nejednačinu nakon što na "
    r"obje strane dodaš isti nenulti cijeli broj i navedi kompletan skup "
    r"rješenja u skupu racionalnih brojeva.")

# Nacrt s cjelobrojnim nabrajanjima nad Q (živi R-T1Q defekt).
Q_ENUMERATION_OPTIONS = (r"$\{4,5,6,\dots\}$", r"$\{5,6,7,\dots\}$",
                         r"$\{3,4,5,\dots\}$", r"$\{2,3,4,\dots\}$")


def test_a_q_reformulation_with_continuous_options_publishes(monkeypatch):
    """§9.A — traženo x>3 s izričitom preformulacijom, finalno x+2>5,
    opcije u kontinuiranim zapisima → objava."""
    response, session, fake = _run(
        monkeypatch, "t1a-q", Q_REFORMULATION_MSG,
        _task_payload(Q_GOOD_TASK, Q_GOOD_OPTIONS, 0))
    assert response.get("status") == "ready", response.get("answer")
    assert session is not None
    assert fake.call_count == 2
    assert session["expected_answer_summary"] == r"$\{x\in Q\mid x>3\}$"


def test_b_z_relation_answer_publishes(monkeypatch):
    """§9.B — nad Z odgovor zapisan kao $x\\ge 4$."""
    task = (r"Riješi nejednačinu $x>3$ u skupu cijelih brojeva i navedi "
            r"kompletan skup rješenja.")
    options = (r"$x\ge 4$", r"$x\ge 6$", r"$x\ge 3$", r"$x\ge 5$")
    response, session, fake = _run(
        monkeypatch, "t1b-z", "Daj mi zadatak u skupu cijelih brojeva.",
        _task_payload(task, options, 0))
    assert response.get("status") == "ready", response.get("answer")
    assert session is not None and fake.call_count == 2
    assert session["expected_answer_summary"] == r"$x\ge 4$"


def test_c_z_supported_enumeration_publishes(monkeypatch):
    """§9.C — nad Z je $\\{4,5,6,\\dots\\}$ i dalje valjan potpun odgovor."""
    task = (r"Riješi nejednačinu $x>3$ u skupu cijelih brojeva i navedi "
            r"kompletan skup rješenja.")
    options = (r"$\{4,5,6,\dots\}$", r"$\{6,7,8,\dots\}$",
               r"$\{3,4,5,\dots\}$", r"$\{5,6,7,\dots\}$")
    response, session, fake = _run(
        monkeypatch, "t1c-z", "Daj mi zadatak u skupu cijelih brojeva.",
        _task_payload(task, options, 0))
    assert response.get("status") == "ready", response.get("answer")
    assert session is not None and fake.call_count == 2
    assert session["expected_answer_summary"] == r"$\{4,5,6,\dots\}$"


@pytest.mark.parametrize("session_id,marked,options", [
    ("t1d-q", r"$\{x\in Q\mid x>3\}$",
     (r"$\{x\in Q\mid x>3\}$", r"$x>5$", r"$x\ge3$", r"$x>1$")),      # §9.D
    ("t1e-q", r"$(3,\infty)$",
     (r"$(3,\infty)$", r"$(5,\infty)$", r"$[3,\infty)$", r"$(1,\infty)$")),  # §9.E
])
def test_d_and_e_q_representations_publish(monkeypatch, session_id, marked,
                                           options):
    task = (r"Riješi nejednačinu $x>3$ u skupu racionalnih brojeva i navedi "
            r"kompletan skup rješenja.")
    response, session, fake = _run(
        monkeypatch, session_id, "Daj mi zadatak.",
        _task_payload(task, options, 0))
    assert response.get("status") == "ready", response.get("answer")
    assert session is not None and fake.call_count == 2
    assert session["expected_answer_summary"] == marked


def test_f_reviewer_repairs_the_enumeration_draft_and_publishes(monkeypatch):
    """§9.F — Tutor napiše cjelobrojna nabrajanja nad Q, recenzent ih prepiše u
    kontinuirani zapis u ISTOM drugom pozivu."""
    task = (r"Riješi nejednačinu $x>3$ u skupu racionalnih brojeva i navedi "
            r"kompletan skup rješenja.")
    response, session, fake = _run(
        monkeypatch, "t1f-q", "Daj mi zadatak.",
        _task_payload(task, Q_ENUMERATION_OPTIONS, 0),
        decision="correct",
        final_task=_task_payload(
            task, (r"$\{x\in Q\mid x>3\}$", r"$x>5$", r"$x\ge3$", r"$x>1$"), 0))
    assert response.get("status") == "ready", response.get("answer")
    assert session is not None and fake.call_count == 2
    assert not any(r"\dots" in option["text"]
                   for option in session["current_options"])
    # Recenzent je STVARNO dobio nalaz i recept u ulazu drugog poziva.
    _instructions, reviewer_input = fake.reviewer_calls[0]
    assert DISCRETE_FOR_CONTINUOUS in reviewer_input
    assert "complete solution set" in reviewer_input


def test_g_reviewer_inserts_the_missing_transformed_relation(monkeypatch):
    """§9.G — Tutor propusti napisati x+2>5, recenzent ga dopiše → objava."""
    response, session, fake = _run(
        monkeypatch, "t1g-q", Q_REFORMULATION_MSG,
        _task_payload(Q_MISSING_RELATION_TASK, Q_GOOD_OPTIONS, 0),
        decision="correct",
        final_task=_task_payload(Q_GOOD_TASK, Q_GOOD_OPTIONS, 0))
    assert response.get("status") == "ready", response.get("answer")
    assert session is not None and fake.call_count == 2
    assert "$x+2>5$" in session["current_task"]
    _instructions, reviewer_input = fake.reviewer_calls[0]
    assert MISSING_DISTINCT_TRANSFORMED_RELATION in reviewer_input
    assert "WRITE THE TRANSFORMED RELATION ITSELF" in reviewer_input


def test_h_unrepaired_draft_fails_closed(monkeypatch):
    """§9.H — recenzent ne popravi nijedan od dva nalaza → siguran pad, bez
    ijednog upisa u sesiju i bez trećeg poziva."""
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    response, session, fake = _run(
        monkeypatch, "t1h-q", Q_REFORMULATION_MSG,
        _task_payload(Q_MISSING_RELATION_TASK, Q_ENUMERATION_OPTIONS, 0))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert session is None
    assert fake.call_count == 2


# ===========================================================================
# 5) SIGURNOSNE REGRESIJE (uputa §10) — ništa nije popušteno
# ===========================================================================

@pytest.mark.parametrize("domain_phrase", [
    "u skupu racionalnih brojeva", "u skupu realnih brojeva",
])
def test_integer_enumeration_over_a_continuous_domain_stays_blocked(domain_phrase):
    question = rf"Riješi nejednačinu $x>3$ {domain_phrase}."
    result = mcq_integrity.evaluate_linear_solve_mcq(question,
                                                     Q_ENUMERATION_OPTIONS)
    assert result.applicable and not result.valid
    assert result.reason_code == DISCRETE_FOR_CONTINUOUS


def test_non_equivalent_transformation_stays_blocked():
    task = (r"Polazna je $x>3$. Dodavanjem 2 na obje strane dobijamo $x+2>7$. "
            r"Riješi dobijenu nejednačinu.")
    failures = request_fidelity_failures("Daj mi zadatak.", task)
    assert [d for d in failures
            if d.startswith(TRANSFORMED_RELATION_NOT_EQUIVALENT)], failures


def test_repeated_original_reformulation_stays_blocked():
    failures = request_fidelity_failures(Q_REFORMULATION_MSG,
                                         Q_MISSING_RELATION_TASK)
    assert [d for d in failures
            if d.startswith(MISSING_DISTINCT_TRANSFORMED_RELATION)], failures


def test_a_request_that_dictates_both_forms_is_satisfied_by_writing_them():
    """ŽIVI LAŽNI ODBIJ (R-T1QP): učenik je u istoj poruci napisao i polaznu
    $x>3$ i ciljanu $x+2>5$. Zadatak koji je UPRAVO to uradio i dalje je padao,
    jer se poredilo sa SVIM relacijama iz poruke — scenario je bio neprolazan
    bez obzira na to šta model napiše."""
    assert request_fidelity_failures(Q_REFORMULATION_MSG, Q_GOOD_TASK) == ()


def test_writing_only_the_original_still_fails_when_the_request_dictated_both():
    """Sigurnosni smjer istog pravila: ako zadatak napiše SAMO polaznu $x>3$,
    nalaz i dalje mora pasti — sužavanje mjerila ne smije propustiti defekt."""
    failures = request_fidelity_failures(Q_REFORMULATION_MSG,
                                         Q_MISSING_RELATION_TASK)
    assert len(failures) == 1
    assert failures[0].startswith(MISSING_DISTINCT_TRANSFORMED_RELATION)


def test_the_historical_fw_r02_shape_is_untouched():
    """Poruka nosi SAMO $x>3$ — prva relacija je ujedno i jedina, pa se
    ponašanje ne mijenja ni za jedan slučaj."""
    message = ("Kreiraj samostalan MCQ: riješi nejednačinu $x>3$, ali je u "
               "tekstu obavezno preoblikuj dodavanjem iste nenulte cijele "
               "konstante na obje strane; ne prepisuj relaciju doslovno.")
    repeated = (r"Riješi nejednačinu dobijenu dodavanjem iste nenulte cijele "
                r"konstante na obje strane originalne relacije $x>3$.")
    written = (r"Riješi nejednačinu $x+2>5$ dobijenu dodavanjem broja $2$ na "
               r"obje strane originalne relacije.")
    assert request_fidelity_failures(message, repeated)[0].startswith(
        MISSING_DISTINCT_TRANSFORMED_RELATION)
    assert request_fidelity_failures(message, written) == ()


def test_two_equivalent_correct_options_stay_blocked():
    result = mcq_integrity.evaluate_linear_solve_mcq(
        Q_QUESTION, (r"$(3,\infty)$", r"$\{x\in Q\mid x>3\}$", r"$x>5$",
                     r"$x\ge3$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "multiple_correct_options"


@pytest.mark.parametrize("bad_option", [
    r"$\{x\mid x>3\}$",
    r"$x\ge 4, x\in\mathbb{Z}$",
    "svi racionalni brojevi veći od 3",
])
def test_unverifiable_answer_syntax_stays_blocked(bad_option):
    options = (bad_option, r"$x>5$", r"$x\ge3$", r"$x>1$")
    result = mcq_integrity.evaluate_linear_solve_mcq(Q_QUESTION, options)
    assert result.applicable and not result.valid
    assert result.reason_code == UNVERIFIABLE


def test_reviewer_recipe_forbids_reaching_for_another_exotic_notation():
    from matbot.tutor import package_preflight as preflight
    block = preflight.format_for_reviewer([preflight.PackageIssue(UNVERIFIABLE)])
    assert "Do NOT" in block and "exotic notation" in block
    assert "SIMPLEST supported form" in block
    assert r"(3,\infty)" in block
    assert r"\{x\in\mathbb{Q} \mid x>3\}" in block
    assert "over Z, N or N0" in block
    # granica se i dalje imenuje kao granica, ne kao prijedlog
    assert "can never be read" in block
