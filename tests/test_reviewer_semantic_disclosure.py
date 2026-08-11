r"""SEMANTIČKO SAMOOTKRIVANJE — vlasništvo RECENZENTA (ciljani recheck 7c13eb9).

ŽIVI BLOKATOR: objavljen je zadatak

    „Tačka $D$ leži unutra tog ugla (tj. zraka $\overrightarrow{BD}$ PROLAZI
     IZMEĐU $\overrightarrow{BA}$ i $\overrightarrow{BC}$). Koji krak DIJELI
     ugao $\angle ABC$ na dva dijela?"        označeno: $\overrightarrow{BD}$

Geometrija je koherentna, i SVAKI ograničeni detektor s pravom vraća
NOT_PROVEN: stem parafrazira traženu osobinu drugim riječima, pa mjerač
preklapanja tokena po vlastitoj doktrini ćuti. Dokazivanje ekvivalencije
parafraze traži razumijevač prirodnog jezika — izričito van arhitekture.

Zato klasa dobija MODEL-SEMANTIČKOG vlasnika: obaveznu provjeru
`stem_requires_student_reasoning` u recenzentovoj šemi, svrstanu u
`MODEL_ONLY_BLOCKING_CHECKS`. Netačna vrijednost čini i `approve` i `correct`
nemogućim; ispravan ishod je popravljen zadatak ili `fail_closed`.

OVAJ FAJL NE TVRDI DETERMINISTIČKI DOKAZ. Testovi mjere UGOVOR (šema, matrica
autoriteta, promptovi, ponašanje turna), nikad da server ume dokazati parafrazu.

ZERO poziva modela: sve je čist deterministički kod ili FakeLLM.
"""
import pytest

from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.stem_disclosure import stem_answer_disclosure
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor import reviewer_authority
from matbot.tutor.schema import ReviewerChecks, UnifiedOutputError, validate_reviewer
from tests.conftest import (FakeLLM, make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

CHECK = "stem_requires_student_reasoning"

# --- BAD 1: doslovna objava iz ciljanog rechecka (zamrznuto) ----------------
BAD_1_TEXT = (
    r"U tački $B$ leže zraci $\overrightarrow{BA}$ i $\overrightarrow{BC}$ koji "
    r"određuju ugao $\angle ABC$. Tačka $D$ leži unutra tog ugla (tj. zraka "
    r"$\overrightarrow{BD}$ prolazi između $\overrightarrow{BA}$ i "
    r"$\overrightarrow{BC}$). Koji krak dijeli ugao $\angle ABC$ na dva dijela?")
BAD_1_OPTIONS = [r"$\overrightarrow{BE}$", r"$\overrightarrow{BD}$",
                 r"$\overrightarrow{BC}$", r"$\overrightarrow{BA}$"]

# --- BAD 2: iste oznake preimenovane -----------------------------------------
BAD_2_TEXT = (
    r"Dat je ugao $\angle XYZ$ s kracima $YX$ i $YZ$. Zraka $YP$ prolazi između "
    r"$YX$ i $YZ$. Koji krak dijeli ugao $\angle XYZ$ na dva dijela?")
BAD_2_OPTIONS = [r"$YX$", r"$YZ$", r"$YP$", r"$YQ$"]

# --- BAD 3: parafraza IZVAN geometrije ---------------------------------------
BAD_3_TEXT = (
    "Marko je jedini učenik koji je sakupio više bodova od svih ostalih. "
    "Ko od navedenih učenika ima najviše bodova?")
BAD_3_OPTIONS = ["Ana", "Marko", "Ivana", "Tarik"]

# --- GOOD kontrole -----------------------------------------------------------
GOOD_F02_TEXT = (r"Funkcija $f$ je zadana tačkama u koordinatnom sistemu: "
                 r"$(1,2)$, $(2,3)$, $(3,2)$, $(4,5)$. Kolika je vrijednost "
                 r"$f(3)$?")
GOOD_F02_OPTIONS = ["$4$", "$3$", "$5$", "$2$"]
GOOD_G01_TEXT = r"Ugao $\angle ABC$ je dat. Kako se imenuju tjeme i krakovi tog ugla?"
GOOD_G01_OPTIONS = ["Tjeme: $B$; krakovi: $AB$ i $CB$",
                    "Tjeme: $B$; krakovi: $BA$ i $BC$",
                    "Tjeme: $C$; krakovi: $CB$ i $CA$",
                    "Tjeme: $A$; krakovi: $AB$ i $AC$"]
GOOD_G03_NUMERIC_TEXT = (
    r"Ugao $\angle ABC$ ima mjeru $50^\circ$. Zraci $BD$, $BE$ i $BF$ polaze iz "
    r"tjemena i leže unutar ugla, pri čemu je $m\angle ABD=25^\circ$, "
    r"$m\angle ABE=10^\circ$ i $m\angle ABF=40^\circ$. Koji krak dijeli ugao "
    r"$\angle ABC$ na dva jednaka dijela?")
GOOD_G03_NUMERIC_OPTIONS = [r"Krak $BE$", r"Krak $BD$", r"Krak $BF$", r"Krak $BC$"]
GOOD_CONSTRUCTION_TEXT = (
    r"Imam ugao $\angle ABC$. Zabodem iglu šestara u tjeme $B$ i povučem luk "
    r"koji siječe oba kraka u tačkama $P$ i $Q$. Koji je sljedeći ispravan "
    r"korak konstrukcije simetrale?")
GOOD_CONSTRUCTION_OPTIONS = [
    "Izmjerim ugao uglomjerom i podijelim ga na pola.",
    "Spojim tačke $P$ i $Q$ pravom linijom.",
    "Zabodem šestar u $P$ pa u $Q$ i povučem lukove koji se sijeku.",
    "Povučem paralelu s jednim krakom."]


def _turn(session_id, grade, lesson, message):
    return {
        "session_id": session_id, "grade": grade, "selected_topic": lesson,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _task(context, text, options, marked=1):
    payload = make_task_payload(text=text, options=list(options),
                                correct_option_index=marked,
                                expected=options[marked])
    return payload.model_copy(update={"selected_lesson_id": context.topic_id,
                                      "selected_lesson_title": context.title})


# ===========================================================================
# 1. UGOVOR AUTORITETA — provjera je model-semantička i BLOKIRAJUĆA
# ===========================================================================

def test_the_check_exists_and_is_model_only_blocking():
    assert CHECK in ReviewerChecks.model_fields
    assert CHECK in reviewer_authority.MODEL_ONLY_BLOCKING_CHECKS
    # Nikad ne smije biti savjetodavna ni proglašena serverski dokazanom.
    assert CHECK not in reviewer_authority.ADVISORY_CHECKS
    assert CHECK not in reviewer_authority.DETERMINISTIC_AUTHORITY_CHECKS
    assert CHECK not in reviewer_authority.AUTHORITATIVE_VALIDATOR


def test_a_false_check_is_a_blocking_failure():
    checks = make_reviewer_checks(**{CHECK: False})
    assert CHECK in reviewer_authority.blocking_failed_checks(checks)
    # …i nikad se ne svrstava među puke dijagnostičke.
    assert CHECK not in reviewer_authority.diagnostic_failed_checks(checks)


@pytest.mark.parametrize("decision", ["approve", "correct"])
def test_neither_approve_nor_correct_survives_a_false_check(decision):
    """Provjera opisuje paket KOJI SE OBJAVLJUJE — oba ishoda su tada laž."""
    task = make_task_payload()
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    reviewer = make_reviewer_final(
        decision=decision, final=draft,
        checks=make_reviewer_checks(**{CHECK: False}))
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(reviewer, draft)


def test_fail_closed_remains_available_and_needs_no_package():
    reviewer = make_reviewer_final(decision="fail_closed",
                                   fail_reason_code="ambiguous_task")
    validate_reviewer(reviewer, make_tutor_draft(intent="generate_task"))


def test_a_true_check_does_not_by_itself_authorise_anything():
    """Samoprijava NIJE dokaz: deterministički nalaz i dalje obara paket."""
    context = lesson_context_module.build(6, "6-09-001")
    disclosing = (r"Zrak $BD$ leži između zraka $BA$ i $BC$, dok zrak $BE$ ne "
                  r"leži između njih. Koji od navedenih zraka leži između "
                  r"zraka $BA$ i $BC$?")
    options = ["$BA$", "$BC$", "$BD$", "$BE$"]
    assert stem_answer_disclosure(disclosing, options, 2)      # server dokazuje
    task = _task(context, disclosing, options, marked=2)
    from matbot.tutor import pipeline
    with pytest.raises(UnifiedOutputError):
        pipeline._validate_task_server_side(task, context)


# ===========================================================================
# 2. PROMPT UGOVOR — obje uloge, bez akrecije
# ===========================================================================

def test_the_reviewer_rule_ships_and_names_the_paraphrase_duty():
    context = lesson_context_module.build(6, "6-09-001")
    reviewer = tutor_prompts.build_reviewer_instructions(context)
    assert tutor_prompts._REVIEWER_STEM_REASONING_RULE in reviewer
    assert CHECK in reviewer
    assert "PARAPHRASE" in reviewer.upper()
    assert "fail_closed" in tutor_prompts._REVIEWER_STEM_REASONING_RULE
    # Recept mora zabraniti „popravku" koja samo preformuliše istu činjenicu.
    assert "NOT a repair" in tutor_prompts._REVIEWER_STEM_REASONING_RULE


def test_the_reviewer_rule_lists_the_non_disclosure_boundaries():
    rule = tutor_prompts._REVIEWER_STEM_REASONING_RULE
    assert "f(3)" in rule                    # klasa data, traži se vrijednost
    assert "isosceles" in rule               # subjekt dat, traži se svojstvo
    assert "compare" in rule and "infer" in rule


def test_the_tutor_gets_the_matching_authoring_rule():
    context = lesson_context_module.build(6, "6-09-001")
    tutor = tutor_prompts.build_tutor_instructions(context)
    assert "TEKST ZADATKA SMIJE DATI PODATKE, ALI NE I ODGOVOR" in tutor
    assert "parafrazom" in tutor


def test_the_new_blocks_stay_compact():
    assert len(tutor_prompts._REVIEWER_STEM_REASONING_RULE) < 2000
    # Pomoć nema ugovor izrade zadatka — pravilo tamo ne smije curiti.
    context = lesson_context_module.build(6, "6-09-001")
    assert (tutor_prompts._REVIEWER_STEM_REASONING_RULE
            not in tutor_prompts.build_help_instructions(context))


@pytest.mark.parametrize("grade,lesson", [
    (6, "6-09-001"), (7, "7-04-023"), (8, "8-02-002"), (9, "9-03-004")])
def test_the_reviewer_rule_reaches_every_grade(grade, lesson):
    context = lesson_context_module.build(grade, lesson)
    assert (tutor_prompts._REVIEWER_STEM_REASONING_RULE
            in tutor_prompts.build_reviewer_instructions(context))


# ===========================================================================
# 3. PONAŠANJE TURNA — BAD slučajevi ne mogu biti odobreni
# ===========================================================================

BAD_CASES = [
    pytest.param(6, "6-09-001", BAD_1_TEXT, BAD_1_OPTIONS, 1, id="BAD1-live"),
    pytest.param(6, "6-09-001", BAD_2_TEXT, BAD_2_OPTIONS, 2, id="BAD2-renamed"),
    pytest.param(6, "6-13-006", BAD_3_TEXT, BAD_3_OPTIONS, 1, id="BAD3-nongeometry"),
]


@pytest.mark.parametrize("grade,lesson,text,options,marked", BAD_CASES)
def test_bad_cases_are_not_deterministically_provable(
        grade, lesson, text, options, marked):
    """POŠTENJE: server NE dokazuje ovu klasu — zato je i dobila recenzenta.

    Kad bi ovo počelo da pada, klasa bi postala serverska i ovaj sloj bi bio
    suvišan; test to čini vidljivim umjesto da tiho zastari."""
    assert stem_answer_disclosure(text, options, marked) == ""


@pytest.mark.parametrize("grade,lesson,text,options,marked", BAD_CASES)
def test_a_reviewer_that_reports_the_disclosure_cannot_publish(
        universal, grade, lesson, text, options, marked):
    context = lesson_context_module.build(grade, lesson)
    assert context is not None
    draft = make_tutor_draft(intent="generate_task",
                             new_task=_task(context, text, options, marked))
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(
        final=draft, checks=make_reviewer_checks(**{CHECK: False})))
    session_id = f"bad-{lesson}-{marked}"

    response = run_practice_turn(store, fake, _turn(
        session_id, grade, lesson,
        "Kreiraj samostalan MCQ sa četiri opcije za izabranu lekciju."))

    assert fake.call_count == 2                     # nikad treći poziv
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(session_id) is None           # nijedna mutacija stanja


@pytest.mark.parametrize("grade,lesson,text,options,marked", BAD_CASES)
def test_a_reviewer_that_fails_closed_costs_the_same_two_calls(
        universal, grade, lesson, text, options, marked):
    context = lesson_context_module.build(grade, lesson)
    draft = make_tutor_draft(intent="generate_task",
                             new_task=_task(context, text, options, marked))
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(decision="fail_closed",
                                   fail_reason_code="ambiguous_task"))
    session_id = f"failclosed-{lesson}-{marked}"

    response = run_practice_turn(store, fake, _turn(
        session_id, grade, lesson,
        "Kreiraj samostalan MCQ sa četiri opcije za izabranu lekciju."))

    assert fake.call_count == 2
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(session_id) is None


def test_a_reviewer_repair_that_removes_the_decisive_fact_publishes(universal):
    """Ispravka u DRUGOM pozivu mora ostati objavljiva — inače je ovo samo
    nedostupnost."""
    context = lesson_context_module.build(6, "6-09-001")
    disclosing = _task(context, BAD_1_TEXT, BAD_1_OPTIONS, 1)
    repaired = _task(context, GOOD_G03_NUMERIC_TEXT, GOOD_G03_NUMERIC_OPTIONS, 1)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_tutor_draft(intent="generate_task", new_task=disclosing))
    fake.queue(make_reviewer_final(
        decision="correct",
        final=make_tutor_draft(intent="generate_task", new_task=repaired),
        checks=make_reviewer_checks(**{CHECK: True})))

    response = run_practice_turn(store, fake, _turn(
        "repair", 6, "6-09-001",
        "Kreiraj samostalan MCQ sa četiri opcije za izabranu lekciju."))

    assert fake.call_count == 2
    assert response["status"] == "ready"
    assert r"25^\circ" in store.peek("repair")["current_task"]


# ===========================================================================
# 4. GOOD KONTROLE — odobrenje ostaje moguće
# ===========================================================================

GOOD_CASES = [
    pytest.param(6, "6-10-007", GOOD_F02_TEXT, GOOD_F02_OPTIONS, 3,
                 id="GOOD1-function-value"),
    pytest.param(6, "6-09-001", GOOD_G01_TEXT, GOOD_G01_OPTIONS, 1,
                 id="GOOD2-angle-elements"),
    pytest.param(6, "6-09-001", GOOD_G03_NUMERIC_TEXT, GOOD_G03_NUMERIC_OPTIONS,
                 1, id="GOOD3-numeric-comparison"),
    pytest.param(6, "6-12-004", GOOD_CONSTRUCTION_TEXT, GOOD_CONSTRUCTION_OPTIONS,
                 2, id="GOOD4-construction"),
]


@pytest.mark.parametrize("grade,lesson,text,options,marked", GOOD_CASES)
def test_good_cases_publish_with_a_truthful_check(
        universal, grade, lesson, text, options, marked):
    context = lesson_context_module.build(grade, lesson)
    draft = make_tutor_draft(intent="generate_task",
                             new_task=_task(context, text, options, marked))
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))
    session_id = f"good-{lesson}-{marked}"

    response = run_practice_turn(store, fake, _turn(
        session_id, grade, lesson,
        "Kreiraj samostalan MCQ sa četiri opcije za izabranu lekciju."))

    assert fake.call_count == 2
    assert response["status"] == "ready"
    assert response["answer"] != SAFE_ERROR_MESSAGE
    assert store.peek(session_id)["current_task"]


@pytest.mark.parametrize("grade,lesson,text,options,marked", GOOD_CASES)
def test_good_cases_are_not_flagged_by_any_bounded_detector(
        grade, lesson, text, options, marked):
    assert stem_answer_disclosure(text, options, marked) == ""


# ===========================================================================
# 5. IZVJEŠTAJNO POŠTENJE — ništa ne tvrdi deterministički dokaz
# ===========================================================================

def test_the_release_contract_still_calls_the_class_manual():
    from tools.practice_eval import release_contract

    spot = release_contract.blind_spot("stem_answer_disclosure")
    assert spot.strength == release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED
    assert CHECK in spot.owner
    assert "never server proof" in spot.owner
    # Ograničeni mjerač i dalje nosi ograničen dokaz, nikad spremnost izdanja.
    assert "stem_answer_disclosure_safe" in release_contract.BOUNDED_CLASS_CHECKS
    assert release_contract.strength_for_check(
        "stem_answer_disclosure_safe", "pass") == \
        release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED


def test_no_evaluator_check_was_added_for_the_paraphrase_class():
    """Nema determinističkog mjerača koji bi tvrdio da je parafraza dokazana."""
    from tools.practice_eval import checks as check_lib

    assert CHECK not in check_lib.known_check_names()
