r"""MIKRO-POPRAVKA POSLIJE CILJANOG RECHECKA (kampanja `f40repair_targeted_2012b31`).

Ciljana kampanja je potvrdila da su A/B/C/D1 popravke žive i ispravne, i našla
DVA preostala ponašanja:

    E  R-FW-G03  stem sam zapiše DEFINICIJU jednakog dijeljenja  → serverski
    F  R-FW-G05  izričito tražena komponenta tiho nestane        → promptni

E je strukturno dokaziv i zato dobija četvrtu ograničenu klasu u POSTOJEĆEM
vlasniku `matbot/stem_disclosure.py`.

F NIJE serverski dokaziv bez opšteg razumijevača prirodnog jezika — ta je
procjena donesena u prethodnoj arhitektonskoj analizi i ovdje se NE obrće zbog
jedne fiksture. Zato se ovdje NE pravi lažna deterministička kapija: provjerava
se da kanonsko pravilo STVARNO stiže u oba poziva i da nijedan validator ne
tvrdi dokaz koji nema.

ZERO poziva modela: sve je čist deterministički kod ili FakeLLM.
"""
import pytest

from matbot import geometrycheck, request_fidelity
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.stem_disclosure import STEM_ANSWER_DISCLOSURE_CODE, stem_answer_disclosure
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import package_preflight, pipeline
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.schema import UnifiedOutputError
from tests.conftest import (FakeLLM, make_reviewer_final, make_task_payload,
                            make_tutor_draft)

# --- DOSLOVNA OBJAVA IZ CILJANE KAMPANJE (zamrznuto) ------------------------

R_FW_G03_QUESTION = (
    r"Raspravljen je ugao $\angle ABC$ čiji su kraci $BA$ i $BC$. Unutar tog "
    r"ugla nalazi se tačka $D$ između krakova $BA$ i $BC$ takva da je "
    r"$\angle ABD = \angle DBC$. Također, neka je $E$ tačka koja leži izvan "
    r"ugla $\angle ABC$. Koji krak dijeli ugao $\angle ABC$ na dva jednaka "
    r"dijela?")
R_FW_G03_OPTIONS = [
    r"Krak $BE$, gdje tačka $E$ leži izvan ugla $\angle ABC$",
    r"Krak $BC$",
    r"Krak $BA$",
    r"Krak $BD$, gdje tačka $D$ leži između krakova $BA$ i $BC$ i važi "
    r"$\angle ABD=\angle DBC$",
]

# Kanonski FW-G05 zahtjev, doslovno iz wave_final40.jsonl.
FW_G05_MESSAGE = (
    "Kreiraj samostalan MCQ sa četiri opcije za izabranu lekciju. Traži zadatak "
    "o simetrali ugla i tački u kojoj se simetrale sijeku. Osiguraj da je tačno "
    "jedna opcija tačna. Ne rješavaj zadatak učeniku.")


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


def _lesson_task(context, text, options, marked):
    payload = make_task_payload(text=text, options=list(options),
                                correct_option_index=marked,
                                expected=options[marked])
    return payload.model_copy(update={"selected_lesson_id": context.topic_id,
                                      "selected_lesson_title": context.title})


# ===========================================================================
# E — STEM ZAPISUJE SAMU DEFINICIJU JEDNAKOG DIJELJENJA
# ===========================================================================

def test_r_fw_g03_equal_subangle_definition_is_disclosure():
    detail = stem_answer_disclosure(R_FW_G03_QUESTION, R_FW_G03_OPTIONS, 3)
    assert detail
    assert "equal-subangle definition" in detail


def test_r_fw_g03_geometry_itself_is_coherent():
    """Premisa je ISPRAVNA — zato je ovo otkrivanje, a ne protivrječnost.

    Popravka C je odradila svoje: tačka je UNUTAR ugla, ne na kraku."""
    assert geometrycheck.geometry_relation_contradictions(R_FW_G03_QUESTION) == ()


def test_r_fw_g03_is_a_preflight_finding_and_cannot_publish():
    context = lesson_context_module.build(6, "6-09-001")
    task = _lesson_task(context, R_FW_G03_QUESTION, R_FW_G03_OPTIONS, 3)
    issues = package_preflight.collect_package_issues(
        task, contract=context.semantic_contract,
        practice_contract=context.practice_contract,
        practice_policy=context.practice_policy)
    assert STEM_ANSWER_DISCLOSURE_CODE in {issue.code for issue in issues}
    with pytest.raises(UnifiedOutputError):
        pipeline._validate_task_server_side(task, context)


def test_r_fw_g03_reviewer_approval_cannot_publish_it(universal):
    """Tutor predloži, recenzent (pogrešno) odobri — objave NEMA, 2 poziva."""
    context = lesson_context_module.build(6, "6-09-001")
    task = _lesson_task(context, R_FW_G03_QUESTION, R_FW_G03_OPTIONS, 3)
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(store, fake, _turn(
        "g03-micro", 6, "6-09-001",
        "Kreiraj samostalan MCQ sa četiri opcije za izabranu lekciju."))

    assert fake.call_count == 2                     # nikad treći poziv
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("g03-micro") is None          # nijedna mutacija stanja


@pytest.mark.parametrize("question,options,marked", [
    # Sintetičke oznake — detektor ne smije prepoznavati fiksturu.
    ((r"Dat je ugao $\angle XYZ$. Unutar ugla je tačka $P$ takva da je "
      r"$\angle XYP = \angle PYZ$. Koji krak dijeli ugao $\angle XYZ$ na dva "
      r"jednaka dijela?"),
     [r"Krak $YX$", r"Krak $YP$", r"Krak $YZ$", r"Krak $YQ$"], 1),
    # Obrnut redoslijed slova u oznakama podugova.
    ((r"Ugao $\angle ABC$. Tačka $D$ je unutar ugla i važi "
      r"$\angle DBA = \angle CBD$. Koji krak je simetrala ugla $\angle ABC$?"),
     [r"Krak $BA$", r"Krak $BC$", r"Krak $BD$", r"Krak $BE$"], 2),
    # Zapis s `m\angle` — oblik kojim je izvorni FW-G03 bio napisan.
    ((r"Ugao $\angle ABC$. Unutar ugla je tačka $D$ takva da je "
      r"$m\angle ABD = m\angle DBC$. Koji krak dijeli ugao $\angle ABC$ na dva "
      r"jednaka dijela?"),
     [r"Krak $BA$", r"Krak $BD$", r"Krak $BC$", r"Krak $BE$"], 1),
])
def test_equal_subangle_disclosure_is_label_independent(question, options, marked):
    assert stem_answer_disclosure(question, options, marked)


@pytest.mark.parametrize("question,options,marked,why", [
    ((r"Ugao $\angle ABC$. Unutar ugla je tačka $D$. Koji krak dijeli ugao "
      r"$\angle ABC$ na dva jednaka dijela?"),
     [r"Krak $BA$", r"Krak $BD$", r"Krak $BC$", r"Krak $BE$"], 1,
     "unutrašnja tačka bez jednakosti NE dokazuje simetralu"),
    ((r"Ugao $\angle ABC$ ima mjeru $40^\circ$, a $\angle ABD$ ima mjeru "
      r"$20^\circ$. Koji krak dijeli ugao $\angle ABC$ na dva jednaka dijela?"),
     [r"Krak $BA$", r"Krak $BD$", r"Krak $BC$", r"Krak $BE$"], 1,
     "brojevne mjere traže račun — ovaj detektor ih NIKAD ne poredi"),
    ((r"Ugao $\angle ABC$ mjeri $60^\circ$ i važi $\angle ABD = \angle DBC$. "
      r"Kolika je mjera ugla $\angle ABD$?"),
     [r"$20^\circ$", r"$30^\circ$", r"$45^\circ$", r"$60^\circ$"], 1,
     "pitanje traži MJERU, ne koji zrak"),
    ((r"Ugao $\angle ABC$. Unutar ugla je tačka $D$ takva da je "
      r"$\angle ABD = \angle DBC$. Koji krak dijeli ugao $\angle ABC$ na dva "
      r"jednaka dijela?"),
     [r"Krak $BA$", r"Krak $BC$", r"Krak $BE$", r"Krak $BF$"], 0,
     "kandidat nije među opcijama — nema jedinstvene implikacije"),
    ((r"Krak $BD$ je simetrala ugla $\angle ABC$ koji mjeri $80^\circ$. Kolika "
      r"je mjera ugla $\angle ABD$?"),
     [r"$40^\circ$", r"$80^\circ$", r"$20^\circ$", r"$160^\circ$"], 0,
     "rečeno je da JESTE simetrala, a traži se vrijednost"),
    (r"Za ugao $\angle ABC$ navedite koje su tačno tjeme i krakovi tog ugla.",
     [r"Tjeme: $B$; krakovi: $BA$ i $BC$", r"Tjeme: $C$; krakovi: $CA$ i $CB$",
      r"Tjeme: $B$; krakovi: $AB$ i $AC$", r"Tjeme: $A$; krakovi: $AB$ i $AC$"],
     0, "FW-G01 kontrola"),
    (r"Ugao $\angle BAC$. Koji krak polazi iz tjemena ugla $\angle BAC$?",
     [r"Krak $AB$", r"Krak $BC$", r"Krak $CD$", r"Krak $DE$"], 0,
     "FW-G02 kontrola"),
    ((r"Ugao $\angle ABC$. Za ugao $\angle XYZ$ važi $\angle XYP = \angle PYZ$. "
      r"Koji krak dijeli ugao $\angle ABC$ na dva jednaka dijela?"),
     [r"Krak $BA$", r"Krak $BD$", r"Krak $BC$", r"Krak $BE$"], 1,
     "jednakost pripada DRUGOM uglu"),
    ((r"Imam ugao $\angle ABC$. Zabodem iglu šestara u tjeme $B$ i povučem luk "
      r"koji siječe oba kraka u tačkama $D$ i $E$. Koji je sljedeći ispravan "
      r"korak da konstruišem simetralu ugla?"),
     [r"Zabodem iglu šestara u $D$ i povučem luk.", r"Povučem liniju iz $B$.",
      r"Iz $D$ povučem pravac paralelan kraku.", r"Zabodem iglu izvan ugla."],
     0, "obična konstrukcijska ljestvica ostaje dostupna"),
    ((r"U trouglovima $ABC$ i $DEF$ vrijedi $AB=DE$, $AC=DF$ i "
      r"$\angle BAC = \angle EDF$. Jesu li trouglovi podudarni?"),
     ["Da, po SUS.", "Ne.", "Da, po SSS.", "Ne može se odrediti."], 0,
     "podudarnost dva trougla — živa klasa iz zamrznutog korpusa"),
])
def test_equal_subangle_false_positive_controls(question, options, marked, why):
    assert stem_answer_disclosure(question, options, marked) == "", why


def test_a_coherent_numerical_bisector_task_still_publishes(universal):
    """Dostupnost: zadatak koji STVARNO traži rasuđivanje ostaje objavljiv."""
    context = lesson_context_module.build(6, "6-09-001")
    task = _lesson_task(
        context,
        (r"Ugao $\angle ABC$ ima mjeru $50^\circ$. Zraci $BD$, $BE$ i $BF$ "
         r"polaze iz tjemena i leže unutar ugla, pri čemu je "
         r"$m\angle ABD=25^\circ$, $m\angle ABE=10^\circ$ i "
         r"$m\angle ABF=40^\circ$. Koji krak dijeli ugao $\angle ABC$ na dva "
         r"jednaka dijela?"),
        [r"Krak $BE$", r"Krak $BD$", r"Krak $BF$", r"Krak $BC$"], 1)
    assert stem_answer_disclosure(
        task.text, [option.text for option in task.options], 1) == ""
    assert geometrycheck.geometry_relation_contradictions(task.text) == ()
    pipeline._validate_task_server_side(task, context)      # ne baca


# ===========================================================================
# F — IZRIČIT ZAHTJEV: PROMPTNO VLASNIŠTVO, NIKAD LAŽNA SERVERSKA KAPIJA
# ===========================================================================

def test_the_explicit_request_rule_ships_byte_identically_to_both_calls():
    context = lesson_context_module.build(6, "6-12-004")
    block = tutor_prompts._EXPLICIT_REQUEST_RULE
    assert block in tutor_prompts.build_tutor_instructions(context)
    assert block in tutor_prompts.build_reviewer_instructions(context)
    # Pomoć NEMA ugovor izrade zadatka — blok tamo ne smije curiti.
    assert block not in tutor_prompts.build_help_instructions(context)


def test_the_explicit_request_rule_states_both_role_duties():
    block = tutor_prompts._EXPLICIT_REQUEST_RULE
    assert "TUTOR:" in block and "RECENZENT:" in block
    assert "fail_closed" in block
    assert "LEKCIJA I DALJE IMA PREDNOST" in block       # lekcija > zahtjev
    assert len(block) < 1600, "blok mora ostati kompaktan, bez prompt akrecije"


@pytest.mark.parametrize("grade,lesson", [
    (6, "6-12-004"), (7, "7-04-023"), (8, "8-02-002"), (9, "9-03-004"),
])
def test_the_rule_reaches_both_prompts_for_every_grade(grade, lesson):
    context = lesson_context_module.build(grade, lesson)
    block = tutor_prompts._EXPLICIT_REQUEST_RULE
    assert block in tutor_prompts.build_tutor_instructions(context)
    assert block in tutor_prompts.build_reviewer_instructions(context)


def test_no_deterministic_gate_claims_to_prove_request_completeness():
    """POŠTENJE DOKAZA: server NE smije tvrditi da mjeri potpunost zahtjeva.

    `request_fidelity` ostaje autoritet SAMO za klase koje stvarno dokazuje
    (domen, relacija, vrsta zadatka). Zadatak koji je vjeran lekciji, a ispustio
    je izričito traženu komponentu, MORA proći determinističke kapije — inače bi
    ovaj fajl tvrdio dokaz koji ne postoji."""
    partial = (r"Imam ugao $\angle ABC$. Zabodem iglu šestara u tjeme $B$ i "
               r"povučem luk koji siječe oba kraka u tačkama $D$ i $E$. Koji je "
               r"sljedeći ispravan korak da konstruišem simetralu ugla?")
    assert request_fidelity.request_fidelity_failures(
        FW_G05_MESSAGE, partial) == ()


@pytest.mark.parametrize("decision,publishes", [("approve", True),
                                                ("fail_closed", False)])
def test_the_g05_turn_costs_exactly_two_calls_either_way(
        universal, decision, publishes):
    """Ugovor poziva se ne mijenja: ni potpun zahtjev ni odbijanje ne traže treći."""
    context = lesson_context_module.build(6, "6-12-004")
    full = _lesson_task(
        context,
        (r"U trouglu $ABC$ konstruisane su simetrale uglova $A$ i $B$ i one se "
         r"sijeku u tački $S$. Šta je tačka $S$ za trougao $ABC$?"),
        [r"Centar upisane kružnice", r"Težište trougla", r"Ortocentar trougla",
         r"Centar opisane kružnice"], 0)
    draft = make_tutor_draft(intent="generate_task", new_task=full)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    if decision == "approve":
        fake.queue(make_reviewer_final(final=draft))
    else:
        # `intent_mishandled` je kod koji je recenzent UŽIVO vratio na istoj
        # klasi (FW-G04), i pripada zatvorenom skupu univerzalnog recenzenta.
        fake.queue(make_reviewer_final(decision="fail_closed",
                                       fail_reason_code="intent_mishandled"))
    session_id = f"g05-{decision}"

    response = run_practice_turn(store, fake, _turn(
        session_id, 6, "6-12-004", FW_G05_MESSAGE))

    assert fake.call_count == 2
    if publishes:
        assert response["status"] == "ready"
        assert "$S$" in store.peek(session_id)["current_task"]
    else:
        assert response["answer"] == SAFE_ERROR_MESSAGE
        assert store.peek(session_id) is None


def test_a_reviewer_repair_that_restores_the_component_publishes(universal):
    """Ispravka u DRUGOM pozivu je dozvoljena i mora ostati objavljiva."""
    context = lesson_context_module.build(6, "6-12-004")
    partial = _lesson_task(
        context,
        (r"Imam ugao $\angle ABC$. Koji je prvi korak konstrukcije simetrale "
         r"tog ugla?"),
        [r"Zabodem šestar u tjeme $B$ i povučem luk.",
         r"Izmjerim ugao uglomjerom.", r"Spojim sredine krakova.",
         r"Povučem paralelu s krakom."], 0)
    repaired = _lesson_task(
        context,
        (r"U trouglu $ABC$ simetrale uglova $A$ i $B$ sijeku se u tački $S$. "
         r"Koja je osobina tačke $S$?"),
        [r"Jednako je udaljena od svih stranica trougla.",
         r"Jednako je udaljena od svih tjemena trougla.",
         r"Dijeli svaku težišnicu u odnosu $2:1$.",
         r"Leži na sredini najduže stranice."], 0)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_tutor_draft(intent="generate_task", new_task=partial))
    fake.queue(make_reviewer_final(
        decision="correct",
        final=make_tutor_draft(intent="generate_task", new_task=repaired)))

    response = run_practice_turn(store, fake, _turn(
        "g05-repair", 6, "6-12-004", FW_G05_MESSAGE))

    assert fake.call_count == 2
    assert response["status"] == "ready"
    assert "$S$" in store.peek("g05-repair")["current_task"]
