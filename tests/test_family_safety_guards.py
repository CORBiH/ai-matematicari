"""TASK 2 — USKI PORODIČNI SIGURNOSNI ČUVARI (DISC-100 talas na 8a8f04d).

Tri dokazane klase, zamrznute kao tačne istorijske regresije:

  C008: MCQ nad funkcijom zadanom tabelom parova objavljen s označenim
        odgovorom koji brka JEDINSTVENOST SLIKE ulaza (ulaz 1 se javlja
        jednom → f(1)=2 jedinstveno) s INJEKTIVNOŠĆU (f(1)=f(3)=2 znači da
        dva RAZLIČITA ulaza dijele izlaz — to ne kvari jedinstvenost slike
        ulaza 1). Tutor i recenzent dijelili istu zabludu; nijedan
        deterministički sloj nije provjeravao ovu matematiku.
  D005: dva zadatka u istom lancu tvrdila su da krak dijeli ugao iako
        notacija to dokazano isključuje (zrak ne polazi iz tjemena; zrak je
        granični krak istog ugla).
  E010: arhetip „koji skup podataka jednoznačno određuje trougao“ dobio je
        dvije tačne opcije — mitigacija je podatkovni semantički ugovor
        lekcije 7-04-023 (zabrana arhetipa), ne geometrijski motor.
  E009: na učenikov izričit zahtjev objavljen je zadatak o KRITERIJIMA
        PODUDARNOSTI na lekciji o simetralama uglova i centru upisane
        kružnice — PEDAGOGY_FALSE_ACCEPT zbog RUPE U PODACIMA: lekcija nije
        imala semantički ugovor, pa je drift prošao. Mitigacija je isključivo
        red u data/semantic_practice_contracts.json.

ZERO model poziva: sve je čist deterministički kod.
"""
import pytest

from matbot import geometrycheck, semantic_practice
from matbot.mcq_integrity import (evaluate_function_table_mcq,
                                  publication_failure)


# ---------------------------------------------------------------------------
# 1) C008 — TAČNA ISTORIJSKA REGRESIJA (funkcija ≠ injekcija)
# ---------------------------------------------------------------------------

C008_TASK = (r"Funkcija $f$ je zadana tabelom parova: "
             r"$\{(1,2),(2,3),(3,2),(4,5)\}$. Je li slika elementa $1$ "
             r"jedinstvena? Odaberi tačan opis.")
C008_OPTIONS = ("Da — slika je jedinstvena i iznosi $2$.",
                "Ne — slika nije jedinstvena jer $f(1)=2$ i $f(3)=2$.",
                "Ne — slika nije jedinstvena jer $f(2)=3$ i $f(4)=3$.",
                "Da — slika je jedinstvena i iznosi $3$.")
C008_MARKED = 1     # istorijski označena (matematički POGREŠNA) opcija


def test_disc_c008_exact_replay_is_rejected():
    """Ulaz 1 se u tabeli javlja tačno jednom → f(1)=2 JEDINSTVENO; opcija
    koja jedinstvenost poriče zbog f(3)=2 tvrdi neinjektivnost, ne
    nejedinstvenost — server sada tu razliku dokazuje i odbija paket."""
    result = evaluate_function_table_mcq(C008_TASK, C008_OPTIONS)
    assert result.applicable and result.valid
    assert result.solution_display == "slika(1) jedinstvena = da, f(1) = 2"
    assert result.correct_index == 0
    failure, verdict = publication_failure(
        C008_TASK, C008_OPTIONS, C008_MARKED, C008_OPTIONS[C008_MARKED])
    assert failure == "marked_option_math_mismatch"
    assert verdict.solution_display == "slika(1) jedinstvena = da, f(1) = 2"


def test_disc_c008_correct_marking_passes():
    failure, _ = publication_failure(C008_TASK, C008_OPTIONS, 0, C008_OPTIONS[0])
    assert failure == ""


def test_disc_c008_wrong_value_da_option_is_not_correct():
    """„Da … iznosi $3$“ tvrdi pogrešnu vrijednost slike — nikad tačna."""
    result = evaluate_function_table_mcq(C008_TASK, C008_OPTIONS)
    assert result.correct_indices == (0,)


def test_disc_c008_preflight_carries_the_finding_to_the_reviewer():
    from matbot.tutor import package_preflight as preflight
    from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter,
                                     TaskPayload, TaskSignature, TutorOption)
    task = TaskPayload(
        selected_lesson_id="6-10-007",
        selected_lesson_title="Prikaz funkcije tabelom i grafički",
        target_difficulty_level=1, text=C008_TASK, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(C008_OPTIONS)],
        correct_option_index=C008_MARKED, correct_option_id="b",
        expected_answer=C008_OPTIONS[C008_MARKED],
        solution="Serverski test.", difficulty="easy",
        difficulty_evidence=DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        task_signature=TaskSignature(
            task_family="function_table", operation_or_relation="image_unique",
            normalized_parameters=[SignatureParameter(name="case", value="c008")],
            required_conditions=[], relevant_objects=["funkcija"],
            answer_type="multiple_choice"))
    issues = preflight.collect_package_issues(task)
    found = [issue for issue in issues
             if issue.code == "marked_option_math_mismatch"]
    assert found, [issue.code for issue in issues]
    assert "server solved: slika(1) jedinstvena = da, f(1) = 2" in found[0].detail


# ---------------------------------------------------------------------------
# 1b) UNIVERZALNI DVOPOZIVNI PUT — čuvar je dostignut na STVARNOM
#     publikacijskom putu (isti put koji je objavio C008 i D005 u talasu).
#     Harness prati obrazac tests/test_reviewer_mcq_preflight.py.
# ---------------------------------------------------------------------------

def _universal_turn(topic, session_id):
    return {
        "session_id": session_id, "grade": int(topic[0]),
        "selected_topic": topic, "selected_oblast": "",
        "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "",
        "last_tutor_task": "", "interaction_type": "student_question",
        "selected_option_id": "", "client_turn_id": "",
    }


def _universal_task(context, text, options, marked):
    from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter,
                                     TaskPayload, TaskSignature, TutorOption)
    return TaskPayload(
        selected_lesson_id=context.topic_id,
        selected_lesson_title=context.title,
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
            task_family="guard_replay", operation_or_relation="replay",
            normalized_parameters=[SignatureParameter(name="case", value="t2")],
            required_conditions=[], relevant_objects=["zadatak"],
            answer_type="multiple_choice"))


def _queue_universal(fake, task_payload):
    from matbot.tutor.schema import ReviewerChecks, ReviewerFinal, TutorDraft
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="izabrana lekcija", new_task=task_payload)
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
            difficulty_evidence_valid=True, task_signature_consistent=True),
        final=draft,
        reviewed_difficulty_evidence=task_payload.difficulty_evidence))


def _run_universal_replay(monkeypatch, topic, text, options, marked, session_id):
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore
    from matbot.tutor.lesson_context import build
    from tests.conftest import FakeLLM

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    context = build(int(topic[0]), topic)
    store, fake = SessionStore(), FakeLLM()
    _queue_universal(fake, _universal_task(context, text, options, marked))
    response = run_practice_turn(store, fake, _universal_turn(topic, session_id))
    return response, store.peek(session_id), fake


def test_disc_c008_universal_two_call_path_rejects_the_package(monkeypatch):
    """Tačan istorijski C008 paket kroz univerzalni Tutor+Reviewer put
    (recenzent odobrava, kao u talasu) — odbijen deterministički, prije
    mutacije sesije, bez trećeg poziva."""
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    response, session, fake = _run_universal_replay(
        monkeypatch, "8-04-008", C008_TASK, C008_OPTIONS, C008_MARKED,
        "guard-c008")
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert session is None
    assert fake.call_count == 2


# ---------------------------------------------------------------------------
# 2) FUNKCIJSKE KONTROLE (pozitivne i negativne)
# ---------------------------------------------------------------------------

def test_value_of_element_is_derived_exactly():
    result = evaluate_function_table_mcq(
        r"Data je funkcija $\{(1,2),(2,3),(3,2)\}$. Kolika je $f(1)$?",
        ("$2$", "$3$", "$1$", "$5$"))
    assert result.applicable and result.valid
    assert result.solution_display == "f(1) = 2"
    assert result.correct_index == 0


def test_image_of_element_wording_is_also_supported():
    result = evaluate_function_table_mcq(
        r"Funkcija je data sa $\{(1,2),(2,3),(3,2)\}$. "
        r"Koja je slika elementa $3$?",
        ("$2$", "$3$", "$1$", "$5$"))
    assert result.applicable and result.valid
    assert result.solution_display == "f(3) = 2"
    assert result.correct_index == 0


def test_shared_output_is_a_function():
    """{(1,2),(3,2)}: različiti ulazi SMIJU dijeliti izlaz — funkcija DA.
    Upravo razlika funkcija/injekcija koju je C008 pobrkao."""
    result = evaluate_function_table_mcq(
        r"Da li skup parova $\{(1,2),(3,2)\}$ predstavlja funkciju?",
        ("Da — svaki ulaz ima tačno jedan izlaz.",
         "Ne — dva ulaza dijele isti izlaz.",
         "Ne — parovi se ponavljaju.",
         "Ne može se odrediti."))
    assert result.applicable and result.valid
    assert result.solution_display == "funkcija = da"
    assert result.correct_index == 0


def test_repeated_input_with_two_outputs_is_not_a_function():
    result = evaluate_function_table_mcq(
        r"Da li skup parova $\{(1,2),(1,3),(2,4)\}$ predstavlja funkciju?",
        ("Da — svaki ulaz ima izlaz.",
         "Ne — ulaz $1$ ima dva različita izlaza.",
         "Da — parova je konačno mnogo.",
         "Da — izlazi su različiti."))
    assert result.applicable and result.valid
    assert result.solution_display == "funkcija = ne"
    assert result.correct_index == 1


def test_repeated_identical_pair_has_set_semantics():
    """{(1,2),(1,2),(2,3)}: ponovljen IDENTIČAN par ne kvari funkciju
    (skupovna semantika) — zamrznuto testom po zahtjevu."""
    result = evaluate_function_table_mcq(
        r"Da li skup parova $\{(1,2),(1,2),(2,3)\}$ predstavlja funkciju?",
        ("Da — svaki ulaz ima tačno jedan izlaz.",
         "Ne — par $(1,2)$ se ponavlja.",
         "Ne — ulaz $1$ ima dva izlaza.",
         "Ne može se odrediti."))
    assert result.applicable and result.valid
    assert result.solution_display == "funkcija = da"
    assert result.correct_index == 0


def test_image_uniqueness_positive_and_negative():
    result = evaluate_function_table_mcq(
        r"Skup $\{(1,2),(2,3)\}$. Je li slika elementa $1$ jedinstvena?",
        ("Da.", "Ne.", "Ne zna se.", "Da, iznosi $3$."))
    assert result.applicable and result.valid
    assert result.solution_display == "slika(1) jedinstvena = da, f(1) = 2"
    assert result.correct_index == 0

    result = evaluate_function_table_mcq(
        r"Skup $\{(1,2),(1,3)\}$. Je li slika elementa $1$ jedinstvena?",
        ("Da.", "Ne — element $1$ ima dvije različite slike.",
         "Da, iznosi $2$.", "Da, iznosi $3$."))
    assert result.applicable and result.valid
    assert result.solution_display == "slika(1) jedinstvena = ne"
    assert result.correct_index == 1


def test_marked_wrong_value_option_yields_mismatch():
    question = r"Data je funkcija $\{(1,2),(2,3),(3,2)\}$. Odredi $f(2)$."
    options = ("$3$", "$2$", "$1$", "$6$")
    failure, result = publication_failure(question, options, 1, options[1])
    assert failure == "marked_option_math_mismatch"
    assert result.correct_index == 0


def test_duplicate_correct_value_options_are_rejected():
    question = r"Data je funkcija $\{(1,2),(2,3)\}$. Kolika je $f(1)$?"
    options = ("$2$", "$2$", "$3$", "$4$")
    result = evaluate_function_table_mcq(question, options)
    assert result.applicable and not result.valid
    assert result.reason_code == "multiple_correct_options"


def test_no_correct_value_option_is_rejected():
    question = r"Data je funkcija $\{(1,2),(2,3)\}$. Kolika je $f(1)$?"
    options = ("$3$", "$4$", "$5$", "$6$")
    result = evaluate_function_table_mcq(question, options)
    assert result.applicable and not result.valid
    assert result.reason_code == "no_correct_option"


def test_partially_unreadable_options_fail_closed():
    """Zadatak dokazano u dometu + dio opcija nečitljiv → zatvoreno padanje
    (Task 1 doktrina), nikad tiho odobrenje."""
    question = r"Skup $\{(1,2),(2,3)\}$. Je li slika elementa $1$ jedinstvena?"
    options = ("Da.", "Ne.", "Možda.", "Zavisi od tabele.")
    result = evaluate_function_table_mcq(question, options)
    assert result.applicable and not result.valid
    assert result.reason_code == "unverifiable_solution_option"
    failure, _ = publication_failure(question, options, 0, options[0])
    assert failure == "unverifiable_solution_option"


@pytest.mark.parametrize("question,options", [
    # bez eksplicitnog skupa parova — proza o funkciji ćuti
    ("Da li je svaka rastuća funkcija injektivna?",
     ("Da.", "Ne.", "Samo na intervalu.", "Zavisi.")),
    # dva skupa parova — ne zna se koji je zadatak
    (r"Skupovi $\{(1,2),(2,3)\}$ i $\{(1,4),(2,5)\}$. Kolika je $f(1)$?",
     ("$2$", "$4$", "$3$", "$5$")),
    # decimalni zapis člana para se NE pogađa (zarez je razdjelnik para)
    (r"Skup $\{(1,2,5),(2,3)\}$. Kolika je $f(1)$?",
     ("$2$", "$3$", "$4$", "$5$")),
    # negacija obrće šta je tačan odgovor — ćutanje
    (r"Skup $\{(1,2),(2,3)\}$. Koja tvrdnja NIJE tačna o slici elementa 1, "
     r"je li jedinstvena?",
     ("Da.", "Ne.", "A.", "B.")),
    # element koji ne postoji u tabeli — nedokazivo, ćutanje
    (r"Skup $\{(1,2),(2,3)\}$. Je li slika elementa $7$ jedinstvena?",
     ("Da.", "Ne.", "Ne zna se.", "Nema je.")),
    # nijedna opcija nema čitljiv Da/Ne verdikt — oblik nije dokazano ova klasa
    (r"Skup $\{(1,2),(2,3)\}$. Je li slika elementa $1$ jedinstvena?",
     ("jedinstvena", "nije jedinstvena", "dvije slike", "tri slike")),
    # kvalitativna svojstva (injektivnost) nisu podržana direktiva
    (r"Skup $\{(1,2),(3,2)\}$. Da li su svi izlazi međusobno različiti?",
     ("Da.", "Ne.", "Samo neki.", "Zavisi.")),
])
def test_unsupported_function_forms_stay_silent(question, options):
    result = evaluate_function_table_mcq(question, options)
    assert not result.applicable, question
    failure, _ = publication_failure(question, options, 0, options[0])
    assert failure == "", question


def test_existing_oracle_dispatch_is_untouched():
    """Novi orakl se pita TEK kad svi postojeći ćute — djeljivost i rješavanje
    zadržavaju prednost bajt za bajt."""
    failure, result = publication_failure(
        "Koji od sljedećih brojeva je djeljiv sa 25?",
        ("75", "30", "40", "60"), 0, "75")
    assert failure == "" and result.applicable
    failure, _ = publication_failure(
        r"Riješi jednačinu: $x+4=9$", ("$5$", "$3$", "$13$", "$4$"),
        1, "$3$")
    assert failure == "marked_option_math_mismatch"


# ---------------------------------------------------------------------------
# 3) D005 — KOHERENTNOST TVRDNJE „KRAK DIJELI UGAO“
# ---------------------------------------------------------------------------

D005_STEP2_TASK = (
    r"Koje je tjeme (vrh) i koji je jedan od krakova ugla označenog sa "
    r"$\angle CBA$? Ako je tačno da krak $\overrightarrow{BA}$ dijeli ugao "
    r"$\angle BAC$, navedi koji je to drugi ugao i pokaži zašto tvoj izbor "
    r"koristi isti krak.")
D005_STEP4_TASK = (
    r"Koje je tjeme (vrh) i koji su oba kraka ugla označenog sa $\angle CBD$? "
    r"Ako je tačno da krak $\overrightarrow{BC}$ dijeli ugao $\angle ABC$ i da "
    r"krak $\overrightarrow{BD}$ dijeli ugao $\angle ABD$, navedi koji su to "
    r"drugi uglovi i pokaži zašto tvoji izbori koriste iste krakove.")


def test_disc_d005_step2_vertex_mismatch_is_proven():
    """Zrak BA počinje u B; ugao BAC ima tjeme A — zrak koji ne polazi iz
    tjemena ne može dijeliti taj ugao. Čista notacijska kontradikcija."""
    issues = geometrycheck.find_geometry_issues(D005_STEP2_TASK, "")
    assert issues == [geometrycheck.ANGLE_DIVIDER_VERTEX_MISMATCH]


def test_disc_d005_step4_boundary_ray_is_proven():
    """BC/BD su GRANIČNI kraci uglova ABC/ABD (C/D je krajnja tačka kraka) —
    granični krak ne može biti i novi unutrašnji djelilac istog ugla."""
    issues = geometrycheck.find_geometry_issues(D005_STEP4_TASK, "")
    assert issues == [geometrycheck.ANGLE_DIVIDER_BOUNDARY_RAY]


def test_disc_d005_findings_survive_a_real_scope_too():
    """Nalaz ne zavisi od scope-a: isti tekst pod „plane“ scope-om nosi isti
    kod (uz netaknute postojeće provjere notacije)."""
    issues = geometrycheck.find_geometry_issues(D005_STEP2_TASK, "plane",
                                                ["trougao"])
    assert geometrycheck.ANGLE_DIVIDER_VERTEX_MISMATCH in issues


def test_disc_d005_full_practice_path_rejects_both_replays():
    """Integracija: oba istorijska D005 paketa kroz run_practice_turn —
    deterministički nalaz na stvarnom publikacijskom putu, bez modela."""
    from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
    from matbot.session_store import SessionStore
    from tests.conftest import FakeLLM, make_options, make_output, make_task
    from tests.test_practice import turn_payload

    step2_options = (
        r"Vrh $C$ i jedan krak $\overrightarrow{CB}$; drugi ugao je $\angle ABC$",
        r"Vrh $A$ i jedan krak $\overrightarrow{AC}$; drugi ugao je $\angle BCA$",
        r"Vrh $B$ i jedan krak $\overrightarrow{BC}$; drugi ugao je $\angle CAB$",
        r"Vrh $B$ i jedan krak $\overrightarrow{BA}$; drugi ugao je $\angle BAC$")
    step4_options = (
        r"Vrh $D$; krakovi $\overrightarrow{DB}$ i $\overrightarrow{DC}$",
        r"Vrh $B$; krakovi $\overrightarrow{BC}$ i $\overrightarrow{BD}$; "
        r"drugi uglovi su $\angle ABC$ i $\angle ABD$",
        r"Vrh $C$; krakovi $\overrightarrow{CB}$ i $\overrightarrow{CD}$",
        r"Vrh $B$; krakovi $\overrightarrow{BC}$ i $\overrightarrow{BD}$; "
        r"drugi uglovi su $\angle ACB$ i $\angle ADB$")
    for task_text, options_texts, marked in (
            (D005_STEP2_TASK, step2_options, 3),
            (D005_STEP4_TASK, step4_options, 1)):
        store, fake = SessionStore(), FakeLLM()
        fake.queue(make_output(
            reply="Evo zadatka.",
            new_task=make_task(text=task_text,
                               expected=options_texts[marked],
                               options=make_options(*options_texts),
                               correct_option_index=marked),
        ))
        result = run_practice_turn(store, fake, turn_payload())
        assert result["answer"] == SAFE_ERROR_MESSAGE, task_text[:40]
        session = store.peek("sess-1")
        assert session is None or not session.get("current_task")


@pytest.mark.parametrize("task_text,marked_text", [
    (D005_STEP2_TASK,
     r"Vrh $B$ i jedan krak $\overrightarrow{BA}$; drugi ugao je $\angle BAC$"),
    (D005_STEP4_TASK,
     r"Vrh $B$; krakovi $\overrightarrow{BC}$ i $\overrightarrow{BD}$; "
     r"drugi uglovi su $\angle ABC$ i $\angle ABD$"),
])
def test_disc_d005_universal_two_call_path_rejects_both(monkeypatch,
                                                        task_text, marked_text):
    """Oba istorijska D005 paketa i kroz UNIVERZALNI dvopozivni put (onaj koji
    ih je u talasu objavio): recenzent odobrava, publikacija ih odbija na
    determinističkom geometrijskom nalazu — sesija netaknuta, dva poziva."""
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    options = (marked_text,
               r"Vrh $C$ i krak $\overrightarrow{CB}$",
               r"Vrh $A$ i krak $\overrightarrow{AC}$",
               r"Ne može se odrediti bez slike")
    response, session, fake = _run_universal_replay(
        monkeypatch, "8-04-008", task_text, options, 0, "guard-d005")
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert session is None
    assert fake.call_count == 2


# ---------------------------------------------------------------------------
# 4) GEOMETRIJSKE POZITIVNE KONTROLE — bez očiglednih lažnih pozitiva
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    # koherentan djelilac: zrak iz tjemena ka tački koja nije granična
    r"Krak $\overrightarrow{BD}$ dijeli ugao $\angle ABC$ na dva dijela.",
    r"Ako krak $\overrightarrow{AE}$ dijeli ugao $\angle BAC$, koliko uglova nastaje?",
    r"Simetrala $\overrightarrow{OS}$ polovi ugao $\angle AOB$.",
    # obična konstatacija granice — NEMA glagola dijeljenja
    r"Ugao $\angle ABC$ je određen kracima $\overrightarrow{BA}$ i $\overrightarrow{BC}$.",
    r"Data su dva kraka ugla: $\overrightarrow{BA}$ i $\overrightarrow{BC}$. "
    r"Koje slovo označava vrh?",
    # negirana tvrdnja nije tvrdnja dijeljenja
    r"Krak $\overrightarrow{BA}$ ne dijeli ugao $\angle BAC$.",
    # simboli bez glagola u istoj rečenici
    r"Posmatraj krak $\overrightarrow{BA}$. U drugom zadatku je ugao $\angle BAC$.",
    # „dijeli“ o nečem drugom (broju), bez zrak→ugao veze
    r"Broj $6$ dijeli broj $12$. Ugao $\angle ABC$ je oštar.",
])
def test_coherent_or_nonclaim_geometry_text_never_triggers(text):
    issues = geometrycheck.find_geometry_issues(text, "")
    assert issues == [], text


def test_distractor_role_and_intentional_policy_still_skip():
    assert geometrycheck.find_geometry_issues(
        D005_STEP2_TASK, "", role=geometrycheck.ROLE_DISTRACTOR) == []
    assert geometrycheck.find_geometry_issues(
        D005_STEP2_TASK, "", policy=geometrycheck.POLICY_ALLOW_INTENTIONAL) == []


def test_existing_notation_checks_are_untouched_by_the_new_rule():
    issues = geometrycheck.find_geometry_issues(
        r"Krug ima prečnik $D=10\,\text{cm}$. Izračunaj obim kruga.",
        "plane", ["krug"])
    assert issues == [geometrycheck.CIRCLE_DIAMETER_USES_D]
    # scope "" bez tvrdnje o djeliocu i dalje ne prijavljuje ništa
    assert geometrycheck.find_geometry_issues(
        r"Krug ima prečnik $D=10\,\text{cm}$.", "") == []


# ---------------------------------------------------------------------------
# 5) E010 — PODATKOVNA MITIGACIJA SEMANTIČKIM UGOVOROM LEKCIJE 7-04-023
# ---------------------------------------------------------------------------

E010_TASK = ("Koji od navedenih skupova podataka jednoznačno (tj. dovoljna su "
             "za izvedbu jedne konstrukcije trougla do kongruentnosti) "
             "određuje trougao ABC tako da se može konstruisati bez mjerenja "
             "dodatnih uglova ili stranica?")


def test_disc_e010_lesson_contract_forbids_the_ambiguous_archetype():
    contract = semantic_practice.contract_for("7-04-023")
    assert contract is not None
    assert contract.enforcement == "blocking"
    assert "construction_determination_request" in contract.forbidden_features
    failures = semantic_practice.fidelity_failures(contract, E010_TASK)
    assert failures == ("semantic_forbidden:construction_determination_request",)


@pytest.mark.parametrize("legit", [
    "Konstruiši visinu iz vrha $C$ na stranicu $AB$.",
    "Gdje se nalazi ortocentar tupouglog trougla?",
    "Kolika je visina $h_c$ trougla ako je površina $24$, a $AB=8$?",
    "Koliko visina ima svaki trougao i u kojoj tački se sijeku?",
])
def test_disc_e010_legitimate_lesson_content_stays_allowed(legit):
    contract = semantic_practice.contract_for("7-04-023")
    assert semantic_practice.fidelity_failures(contract, legit) == (), legit


def test_disc_e010_contract_reaches_preflight():
    """Nalaz stiže recenzentu PRIJE drugog poziva kroz postojeći
    SEMANTIC_FIDELITY put — bez ijedne nove kapije."""
    from matbot.tutor import package_preflight as preflight
    from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter,
                                     TaskPayload, TaskSignature, TutorOption)
    contract = semantic_practice.contract_for("7-04-023")
    options = ("Osnovica $AB$ i visina $h_c$.",
               "Visine $h_a$, $h_b$ i $h_c$.",
               "Osnovica $AB$, stopa $D$ i dužina $CD$.",
               "Osnovica $AB$ i uglovi kod $A$ i $B$.")
    task = TaskPayload(
        selected_lesson_id="7-04-023",
        selected_lesson_title="Visine trougla i ortocentar",
        target_difficulty_level=1, text=E010_TASK, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(options)],
        correct_option_index=2, correct_option_id="c",
        expected_answer=options[2],
        solution="Serverski test.", difficulty="easy",
        difficulty_evidence=DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        task_signature=TaskSignature(
            task_family="construction_data", operation_or_relation="determine",
            normalized_parameters=[SignatureParameter(name="case", value="e010")],
            required_conditions=[], relevant_objects=["trougao"],
            answer_type="multiple_choice"))
    issues = preflight.collect_package_issues(task, practice_contract=contract)
    found = [issue for issue in issues
             if issue.code == preflight.SEMANTIC_FIDELITY_CODE]
    assert found, [issue.code for issue in issues]
    assert "construction_determination_request" in found[0].detail


# ---------------------------------------------------------------------------
# 6) E009 — VJERNOST LEKCIJI 7-04-022 (RUPA U PODACIMA, ne u arhitekturi)
# ---------------------------------------------------------------------------
# ŽIVI DISC NALAZ (E009, PEDAGOGY_FALSE_ACCEPT): izabrana lekcija je bila
# „Simetrale uglova i centar upisane kružnice“, a učenik je izričito tražio
# „zadatak o podudarnosti trouglova i odgovarajući kriterij“. Tutor je zahtjev
# poslušao, recenzent vratio `correct` i objavljen je zadatak o KRITERIJIMA
# PODUDARNOSTI — gradivo lekcija 7-04-013..016, ne ove. Susjedne lekcije o
# podudarnosti već nose `congruence_criterion` ugovor; ova lekcija ga nije
# imala u suprotnom smjeru, pa drift nije imao ko zaustaviti.
#
# Mitigacija je ISKLJUČIVO podatak: nova zabrana u
# data/semantic_practice_contracts.json, uz POSTOJEĆU generičku osobinu
# `congruence_semantics_present` — nijedna nova linija Pythona.

E009_LESSON = "7-04-022"
E009_TASK = (r"U jednakokrakom trouglu $ABC$ vrijedi $AB=AC$. Simetrala ugla "
             r"$A$ siječe stranicu $BC$ u tački $D$. Koji kriterij "
             r"podudarnosti garantuje da su trouglovi $ABD$ i $ACD$ podudarni?")
E009_OPTIONS = ("SSS (stranica - stranica - stranica)",
                "SAS (stranica - ugao - stranica)",
                "RHS (pravougli - hipotenuza - kateta)",
                "ASA (ugao - stranica - ugao)")
E009_MARKED = 1


def test_disc_e009_lesson_now_has_a_blocking_contract():
    contract = semantic_practice.contract_for(E009_LESSON)
    assert contract is not None, "rupa u podacima mora biti zatvorena"
    assert contract.enforcement == "blocking"
    assert contract.requirement_type == "angle_bisector_incenter_semantics"
    assert "congruence_semantics_present" in contract.forbidden_features
    assert contract.evidence


def test_disc_e009_reuses_the_existing_feature_library():
    """Mitigacija ne uvodi novu osobinu: koristi se ISTA generička provjera
    koju susjedne lekcije o podudarnosti već koriste kao ZAHTJEV."""
    congruence = semantic_practice.contract_for("7-04-013")
    assert "congruence_semantics_present" in congruence.required_features
    contract = semantic_practice.contract_for(E009_LESSON)
    assert "congruence_semantics_present" in contract.forbidden_features


def test_disc_e009_exact_off_lesson_package_is_a_proven_violation():
    contract = semantic_practice.contract_for(E009_LESSON)
    failures = semantic_practice.fidelity_failures(
        contract, E009_TASK, " ".join(E009_OPTIONS))
    assert failures == ("semantic_forbidden:congruence_semantics_present",)


def test_disc_e009_drift_hidden_only_in_the_options_is_also_caught():
    """Ugovor se provjerava i nad OPCIJAMA (živi K07 obrazac): zadatak koji
    podudarnost pomene tek u ponuđenim odgovorima ne smije proći."""
    contract = semantic_practice.contract_for(E009_LESSON)
    failures = semantic_practice.fidelity_failures(
        contract, "Koji je tačan zaključak za trougao $ABC$?",
        "Trouglovi $ABD$ i $ACD$ su podudarni po SUS.")
    assert failures == ("semantic_forbidden:congruence_semantics_present",)


@pytest.mark.parametrize("legit", [
    # presjek simetrala i ime tačke — jezgro lekcije
    "Simetrale uglova trougla sijeku se u jednoj tački. Kako se zove ta tačka?",
    # konstrukcija simetrale ugla
    r"Konstruiši simetralu ugla $\angle ABC$ i označi tačku presjeka sa $AC$.",
    # svojstvo jednake udaljenosti / upisana kružnica
    "Centar upisane kružnice jednako je udaljen od svih stranica trougla. "
    "Šta predstavlja ta udaljenost?",
    # račun ugla koji simetrala obrazuje
    r"Ugao $\angle A$ ima $80^\circ$. Koliki ugao simetrala tog ugla zaklapa "
    r"sa stranicom $AB$?",
    # poluprečnik upisane kružnice
    r"Poluprečnik upisane kružnice je $r=3\,\text{cm}$. Kolika je udaljenost "
    r"centra od stranice $BC$?",
])
def test_disc_e009_legitimate_lesson_content_stays_allowed(legit):
    contract = semantic_practice.contract_for(E009_LESSON)
    assert semantic_practice.fidelity_failures(contract, legit) == (), legit


def test_disc_e009_neighbouring_congruence_lessons_are_unaffected():
    """Zabrana je LEKCIJSKA: na lekcijama o podudarnosti isti sadržaj ostaje
    ne samo dozvoljen nego OBAVEZAN — granica se nije pomjerila."""
    for lesson_id in ("7-04-013", "7-04-014", "7-04-015", "7-04-016"):
        contract = semantic_practice.contract_for(lesson_id)
        assert semantic_practice.fidelity_failures(
            contract, E009_TASK, " ".join(E009_OPTIONS)) == (), lesson_id


def test_disc_e009_contract_reaches_preflight_with_the_reviewer_recipe():
    """Nalaz stiže recenzentu PRIJE drugog poziva postojećim
    SEMANTIC_FIDELITY putem, s receptom za zamjenu zadatka."""
    from matbot.tutor import package_preflight as preflight
    contract = semantic_practice.contract_for(E009_LESSON)
    task = _universal_task(
        type("Ctx", (), {"topic_id": E009_LESSON,
                         "title": "Simetrale uglova i centar upisane kružnice"})(),
        E009_TASK, E009_OPTIONS, E009_MARKED)
    issues = preflight.collect_package_issues(task, practice_contract=contract)
    found = [issue for issue in issues
             if issue.code == preflight.SEMANTIC_FIDELITY_CODE]
    assert found, [issue.code for issue in issues]
    assert "congruence_semantics_present" in found[0].detail
    assert preflight.SEMANTIC_FIDELITY_CODE in preflight.format_for_reviewer(issues)


def test_disc_e009_universal_two_call_path_cannot_publish(monkeypatch):
    """STVARNI publikacijski put: recenzent odobrava (kao u talasu), a
    serverska invarijanta nad njegovim paketom odbija objavu — sesija
    netaknuta, tačno dva poziva, bez modela."""
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    response, session, fake = _run_universal_replay(
        monkeypatch, E009_LESSON, E009_TASK, E009_OPTIONS, E009_MARKED,
        "guard-e009")
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert session is None
    assert fake.call_count == 2


def test_disc_e009_legitimate_task_still_publishes_on_the_same_lesson(monkeypatch):
    """KONTROLA PROTIV PREKOMJERNOG BLOKIRANJA: uredan zadatak o simetralama
    na ISTOJ lekciji i dalje prolazi cio dvopozivni put."""
    legit_text = ("Simetrale uglova trougla sijeku se u jednoj tački. "
                  "Kako se zove ta tačka?")
    options = ("Centar upisane kružnice", "Ortocentar", "Težište",
               "Centar opisane kružnice")
    response, session, _fake = _run_universal_replay(
        monkeypatch, E009_LESSON, legit_text, options, 0, "guard-e009-ok")
    assert response.get("status") == "ready"
    assert session is not None
