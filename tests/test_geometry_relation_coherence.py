r"""GEOMETRIJSKA PREMISA KOJA NE POSTOJI — ciljani blokator izdanja (FW-G03).

ŽIVI NALAZ (ciljana kampanja `final40_blocker_targeted_b9151fc_20260811-120800`,
5/5 scenarija, 10 SDK poziva, recenzent `approve`). Objavljeno:

    „Ugao $\angle ABC$ iznosi $60^\circ$. NA KRAKU $BA$ nalaze se četiri
     zadata kraka iz tačke $B$: $BD$, $BE$, $BF$ i $BG$. Poznato je da su
     uglovi mjereni od kraka $BA$ redom: $\angle ABD=10^\circ$,
     $\angle ABE=30^\circ$, $\angle ABF=20^\circ$ i $\angle ABG=40^\circ$.
     Koji od navedenih krakova dijeli ugao $\angle ABC$ na dva jednaka djela?“

    opcije: krak $BG$ · krak $BE$ · krak $BF$ · krak $BD$      označeno: $BE$

Označeni odgovor JESTE ono što je autor htio ($60:2=30$), pa ovo NIJE pogrešan
označen odgovor. Neistinita je PREMISA: zrak koji leži NA zraku $BA$ jeste taj
isti zrak, pa je ugao između njih tačno $0^\circ$ — nikad $10^\circ$. Učenik je
dobio zadatak o konfiguraciji koja ne postoji.

ZAŠTO NIJEDNA POSTOJEĆA KAPIJA NIJE MOGLA REAGOVATI:
  • `mathcheck` nema nijednu jednakost koju bi oborio;
  • `option_equivalence` vidi četiri različite opcije;
  • `mcq_integrity` nema primjenjiv orakl za ovaj oblik;
  • `stem_disclosure` s pravom ćuti — presudna činjenica NIJE data u tekstu;
  • koherentnost djelioca (DISC-D005) traži TVRDNJU o dijeljenju uz zapis
    `\overrightarrow`; ovdje je zapis goli par slova i tvrdnja je o POLOŽAJU;
  • evaluatorski `geometry_ok` je vratio SKIP jer lekcija o uglovima nema
    geometrijski scope.

DOKAZ JE EGZAKTAN, NE PROCJENA:

    zrak VP i zrak VQ su ISTI zrak  ⟹  ugao između njih je tačno 0°.

ZERO poziva modela: sve je čist deterministički kod ili FakeLLM.
"""
import json
import pathlib

import pytest

from matbot import geometrycheck
from matbot.geometrycheck import (COINCIDENT_RAYS_NONZERO_ANGLE,
                                  GEOMETRY_RELATION_CONTRADICTION,
                                  geometry_relation_contradictions)
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import package_preflight
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor.schema import UnifiedOutputError
from tests.conftest import (make_reviewer_final, make_task_payload,
                            make_tutor_draft, queue_two_call)

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "final40_blockers.json")
    .read_text(encoding="utf-8"))["fixtures"]
BY_ID = {fixture["id"]: fixture for fixture in FIXTURES}
GEOM_BAD = BY_ID["G03_GEOM_BAD"]
GEOM_GOOD = BY_ID["G03_GEOM_GOOD"]


def _package(fixture):
    context = lesson_context_module.build(fixture["grade"], fixture["topic_id"])
    marked = fixture["options"][fixture["marked_index"]]
    task = make_task_payload(
        text=fixture["question"], options=tuple(fixture["options"]),
        correct_option_index=fixture["marked_index"],
        expected=marked, solution=marked)
    return context, task.model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title})


# ===========================================================================
# 1) TAČAN ŽIVI PAKET
# ===========================================================================

def test_the_live_package_is_a_proven_geometric_contradiction():
    assert geometry_relation_contradictions(GEOM_BAD["question"]) == (
        COINCIDENT_RAYS_NONZERO_ANGLE,)


def test_the_finding_is_not_about_the_marked_answer():
    """Označeno $BE$ je i dalje ono što bi tačan zadatak tražio.

    Zato se nalaz NE smije voditi kao pogrešan označen odgovor: ista opcija je
    tačna i u koherentnoj verziji zadatka (ispod), a razlika je isključivo u
    premisi."""
    assert GEOM_GOOD["options"] == GEOM_BAD["options"]
    assert GEOM_GOOD["marked_index"] == GEOM_BAD["marked_index"]
    assert geometry_relation_contradictions(GEOM_GOOD["question"]) == ()


def test_the_reason_carries_a_class_never_the_content():
    """CLAUDE.md pravilo 7: dijagnostika nosi kod i klasu, nikad sadržaj."""
    reasons = geometry_relation_contradictions(GEOM_BAD["question"])
    blob = " ".join(reasons)
    for leaked in ("BD", "BE", "BF", "BG", "BA", "10", "30", "40", "60",
                   "6-09-001"):
        assert leaked not in blob, leaked


def test_the_detector_works_with_arbitrary_labels():
    """Nijedna tačka, zrak ni mjera nije konstanta proizvoda."""
    assert geometry_relation_contradictions(
        "Na zraku $PQ$ nalaze se zraci $PR$ i $PS$. Poznato je da je "
        "$\\angle QPR=25^\\circ$.") == (COINCIDENT_RAYS_NONZERO_ANGLE,)
    assert geometry_relation_contradictions(
        "Na kraku $MN$ leži krak $MK$, a $\\angle NMK$ iznosi $70^\\circ$."
    ) == (COINCIDENT_RAYS_NONZERO_ANGLE,)


def test_product_source_carries_no_fixture_constants():
    """Uputa §7: nijedna oznaka iz živog zadatka ne smije biti konstanta."""
    source = (pathlib.Path(__file__).resolve().parent.parent
              / "matbot" / "geometrycheck.py").read_text(encoding="utf-8")
    section = source.split("KOHERENTNOST GEOMETRIJSKE PREMISE")[1]
    body = section.split('"""')[0]
    for banned in ("FW-G03", '"BD"', '"BE"', '"BA"', "== 10", "== 30", "== 40"):
        assert banned not in body, banned


# ===========================================================================
# 2) EGZAKTNOST — nulti ugao je jedina dozvoljena mjera za isti zrak
# ===========================================================================

@pytest.mark.parametrize("degrees,expected_contradiction", [
    ("0", False),        # isti zrak → 0° je TAČNO, ne protivrječnost
    ("0,0", False),
    ("1", True),
    ("10", True),
    ("90", True),
    ("180", True),       # suprotni zraci nisu isti zrak — i dalje protivrječno
])
def test_only_a_zero_angle_is_consistent_with_coincident_rays(
        degrees, expected_contradiction):
    text = (f"Krak $BD$ leži na kraku $BA$, pa je $\\angle ABD={degrees}^\\circ$.")
    found = bool(geometry_relation_contradictions(text))
    assert found is expected_contradiction, degrees


def test_an_angle_on_a_different_pair_is_never_the_finding():
    """Poklapanje (B; A,D) ne kaže ništa o uglu $\\angle ABC$."""
    assert geometry_relation_contradictions(
        "Krak $BD$ leži na kraku $BA$. Ugao $\\angle ABC$ iznosi $60^\\circ$."
    ) == ()


def test_rays_from_different_vertices_are_never_compared():
    assert geometry_relation_contradictions(
        "Na kraku $BA$ nalazi se krak $CD$, a $\\angle ACD=30^\\circ$.") == ()


# ===========================================================================
# 3) „NA KRAKU“ NIJE „UNUTAR UGLA“ (uputa §14)
# ===========================================================================

INSIDE_ANGLE_FORMS = (
    "Kraci $BD$ i $BE$ leže unutar ugla $\\angle ABC$, pri čemu je "
    "$\\angle ABD=10^\\circ$ i $\\angle ABE=30^\\circ$.",
    "Iz tjemena $B$ polaze kraci $BD$ i $BE$ u unutrašnjosti ugla "
    "$\\angle ABC$; $\\angle ABD=10^\\circ$.",
    "Krak $BE$ se nalazi između krakova $BA$ i $BC$, a $\\angle ABE=30^\\circ$.",
    "Krak $BD$ leži u uglu $\\angle ABC$ i $\\angle ABD=10^\\circ$.",
)


@pytest.mark.parametrize("text", INSIDE_ANGLE_FORMS)
def test_inside_the_angle_is_never_read_as_on_the_arm(text):
    assert geometry_relation_contradictions(text) == (), text


def test_the_two_premises_differ_only_by_the_relation_word():
    """Ista rečenica, jedna riječ razlike — jedna pada, druga prolazi."""
    on_arm = "Krak $BD$ leži na kraku $BA$ i $\\angle ABD=10^\\circ$."
    inside = "Krak $BD$ leži unutar ugla $\\angle ABC$ i $\\angle ABD=10^\\circ$."
    assert geometry_relation_contradictions(on_arm) == (
        COINCIDENT_RAYS_NONZERO_ANGLE,)
    assert geometry_relation_contradictions(inside) == ()


# ===========================================================================
# 4) MATRICA LAŽNIH POZITIVA (uputa §13)
# ===========================================================================

FALSE_POSITIVE_MATRIX = (
    # A) tačka na kraku, bez ijedne mjere ugla
    "Tačka $D$ leži na kraku $BA$.",
    "Tačka $D$ pripada kraku $BA$ ugla $\\angle ABC$.",
    # B) poklopljeni zraci uz NULTI ugao — matematički ispravno
    "Krak $BD$ leži na kraku $BA$, pa je $\\angle ABD=0^\\circ$.",
    # C) četiri kraka iz tjemena unutar ugla
    "Iz tačke $B$ polaze kraci $BD$, $BE$, $BF$ i $BG$ koji leže unutar ugla "
    "$\\angle ABC$. Uglovi od kraka $BA$ su $\\angle ABD=10^\\circ$, "
    "$\\angle ABE=30^\\circ$, $\\angle ABF=20^\\circ$ i $\\angle ABG=40^\\circ$.",
    # D) mjere bez ijedne tvrdnje o položaju na kraku
    "$\\angle ABD=10^\\circ$, $\\angle ABE=30^\\circ$, $\\angle ABC=60^\\circ$.",
    # E) obično G01 imenovanje tjemena i krakova
    "Koje je tjeme i koji su krakovi ugla $\\angle ABC$?",
    "Ugao $\\angle ABC$ ima tjeme $B$ i krakove $BA$ i $BC$.",
    # tačke na OBA kraka, pitanje o uglu između njih
    "Tačka $D$ leži na kraku $BA$, a tačka $E$ na kraku $BC$. "
    "Ugao $\\angle DBE$ iznosi $60^\\circ$.",
    # tačka na PRAVOJ (ne zraku) — suprotna strana daje 180°, ne dokazuje se
    "Tačka $D$ leži na pravoj $BA$. Ugao $\\angle ABD$ iznosi $180^\\circ$.",
    # simetrala: legitimna tvrdnja o dijeljenju, bez ijednog poklapanja
    "Krak $BD$ je simetrala ugla $\\angle ABC$, pa je $\\angle ABD=30^\\circ$.",
    # trougao: uglovi bez ijedne tvrdnje o zracima
    "U trouglu $ABC$ je $\\angle ABC=60^\\circ$ i $\\angle BCA=70^\\circ$.",
)


@pytest.mark.parametrize("text", FALSE_POSITIVE_MATRIX)
def test_legitimate_geometry_is_never_a_contradiction(text):
    assert geometry_relation_contradictions(text) == (), text


@pytest.mark.parametrize("fixture_id", [
    "G03_GOOD", "G01_GOOD", "G06_GOOD_G05_STYLE", "G06_GOOD_EQUAL_DISTANCE",
    "G0804_GOOD_POLYGON", "G03_GEOM_GOOD"])
def test_every_frozen_positive_control_stays_clean(fixture_id):
    fixture = BY_ID[fixture_id]
    assert geometry_relation_contradictions(fixture["question"]) == (), fixture_id


def test_a_task_without_any_angle_measure_is_skipped():
    """Bez ijedne pročitane mjere nema šta protivrječiti — ćuti se."""
    assert geometry_relation_contradictions(
        "Na kraku $BA$ nalaze se kraci $BD$ i $BE$.") == ()


# ===========================================================================
# 5) UVEZANOST — preflight, objava, uloga distraktora
# ===========================================================================

def test_publication_rejects_the_contradictory_package():
    context, task = _package(GEOM_BAD)
    with pytest.raises(UnifiedOutputError) as error:
        tutor_pipeline._validate_task_server_side(task, context)
    assert GEOMETRY_RELATION_CONTRADICTION in str(error.value)
    assert COINCIDENT_RAYS_NONZERO_ANGLE in str(error.value)


def test_publication_accepts_the_coherent_package():
    context, task = _package(GEOM_GOOD)
    tutor_pipeline._validate_task_server_side(task, context)


def test_preflight_hands_the_finding_to_the_reviewer():
    """Bez ovoga bi nalaz stigao tek u objavi — kad su oba poziva potrošena."""
    _context, task = _package(GEOM_BAD)
    issues = package_preflight.collect_package_issues(task)
    codes = [issue.code for issue in issues]
    assert GEOMETRY_RELATION_CONTRADICTION in codes
    detail = next(issue.detail for issue in issues
                  if issue.code == GEOMETRY_RELATION_CONTRADICTION)
    assert detail == COINCIDENT_RAYS_NONZERO_ANGLE


def test_reviewer_recipe_repairs_the_premise_not_the_answer():
    """Obrazac F4E E01: goli kod bez recepta → recenzent vrati isti defekt."""
    _context, task = _package(GEOM_BAD)
    block = package_preflight.format_for_reviewer(
        package_preflight.collect_package_issues(task))
    assert GEOMETRY_RELATION_CONTRADICTION in block
    assert COINCIDENT_RAYS_NONZERO_ANGLE in block
    assert "KEEP the candidate rays" in block
    assert "do not change the marked option to repair this" in block


def test_the_finding_runs_without_a_geometry_scope():
    """Lekcije o uglovima rutiraju scope "" — provjera je namjerno prije kapije."""
    context = lesson_context_module.build(6, "6-09-001")
    assert context.geometry_scope == ""
    assert geometrycheck.find_geometry_issues(GEOM_BAD["question"], "", []) == [
        f"{GEOMETRY_RELATION_CONTRADICTION}:{COINCIDENT_RAYS_NONZERO_ANGLE}"]


def test_a_distractor_option_is_never_judged():
    """Namjerno pogrešna opcija smije nositi nemoguću konfiguraciju."""
    assert geometrycheck.find_geometry_issues(
        GEOM_BAD["question"], "", [],
        role=geometrycheck.ROLE_DISTRACTOR) == []


def test_the_code_is_registered_in_the_module_contract():
    assert GEOMETRY_RELATION_CONTRADICTION in geometrycheck.ALL_ISSUE_CODES


# ===========================================================================
# 6) CIO TURN — objava odbijena, sesija netaknuta, TAČNO DVA POZIVA
# ===========================================================================

def _turn(session_id, message):
    return {
        "session_id": session_id, "grade": 6, "selected_topic": "6-09-001",
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": f"{session_id}-turn-1",
    }


@pytest.fixture(autouse=True)
def _model_route_only(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def test_contradictory_package_never_publishes_and_never_mutates_state(
        store, fake_llm):
    _context, task = _package(GEOM_BAD)
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="approve", final=draft))

    response = tutor_pipeline.run_turn(
        store, fake_llm, _turn("geom-bad", GEOM_BAD["student_message"]))

    assert response.get("status") is None
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2                  # bez trećeg poziva
    assert store.peek("geom-bad") is None            # nijedna mutacija sesije


def test_reviewer_may_repair_the_premise_within_the_same_two_calls(
        store, fake_llm):
    _context, broken = _package(GEOM_BAD)
    _context, repaired = _package(GEOM_GOOD)
    queue_two_call(
        fake_llm,
        draft=make_tutor_draft(intent="generate_task", new_task=broken),
        reviewer=make_reviewer_final(
            decision="correct",
            final=make_tutor_draft(intent="generate_task", new_task=repaired)))

    response = tutor_pipeline.run_turn(
        store, fake_llm, _turn("geom-repair", GEOM_BAD["student_message"]))

    assert response.get("status") == "ready"
    assert fake_llm.call_count == 2
    assert store.peek("geom-repair")["current_task"] == GEOM_GOOD["question"]


def test_reviewer_returning_another_contradiction_is_rejected(store, fake_llm):
    """Recenzent koji „popravi“ mjere, a ostavi premisu, i dalje ne objavljuje."""
    _context, broken = _package(GEOM_BAD)
    still_broken = broken.model_copy(update={
        "text": ("Ugao $\\angle ABC$ iznosi $80^\\circ$. Na kraku $BA$ leže "
                 "kraci $BD$ i $BE$; $\\angle ABD=20^\\circ$ i "
                 "$\\angle ABE=40^\\circ$. Koji krak dijeli ugao "
                 "$\\angle ABC$ na dva jednaka dijela?")})
    queue_two_call(
        fake_llm,
        draft=make_tutor_draft(intent="generate_task", new_task=broken),
        reviewer=make_reviewer_final(
            decision="correct",
            final=make_tutor_draft(intent="generate_task", new_task=still_broken)))

    response = tutor_pipeline.run_turn(
        store, fake_llm, _turn("geom-unrepaired", GEOM_BAD["student_message"]))

    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2
    assert store.peek("geom-unrepaired") is None


def test_the_coherent_package_publishes_in_two_calls(store, fake_llm):
    _context, task = _package(GEOM_GOOD)
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="approve", final=draft))

    response = tutor_pipeline.run_turn(
        store, fake_llm, _turn("geom-good", GEOM_GOOD["student_message"]))

    assert response.get("status") == "ready"
    assert fake_llm.call_count == 2
    assert store.peek("geom-good")["current_task"] == GEOM_GOOD["question"]


# ===========================================================================
# 7) PARITET EVALUATORA (uputa §17)
# ===========================================================================

def _observation(fixture):
    from tools.practice_eval import checks as check_lib

    marked = fixture["options"][fixture["marked_index"]]
    session = {
        "current_task": fixture["question"],
        "current_options": [{"id": "abcd"[index], "text": text}
                            for index, text in enumerate(fixture["options"])],
        "correct_option_id": "abcd"[fixture["marked_index"]],
        "expected_answer_summary": marked,
        "solution_summary": marked,
        "lesson_id": fixture["topic_id"],
    }
    return check_lib.TurnObservation(
        scenario_id=fixture["id"], step_index=0, step_kind="text",
        topic_id=fixture["topic_id"], grade=fixture["grade"],
        request_payload={"student_message": fixture["student_message"]},
        http_status=200,
        response={"status": "ready", "answer": fixture["question"],
                  "effective_topic": fixture["topic_id"], "session_mode": "practice"},
        session_before=None, session_after=session, sdk_calls=2)


def test_evaluator_fails_exactly_what_production_blocks():
    from tools.practice_eval import checks as check_lib

    result = check_lib.resolve("geometry_relation_consistent")(
        _observation(GEOM_BAD))
    assert result.outcome == check_lib.FAIL
    assert COINCIDENT_RAYS_NONZERO_ANGLE in result.detail


@pytest.mark.parametrize("fixture_id", [
    "G03_GEOM_GOOD", "G03_GOOD", "G01_GOOD", "G0804_GOOD_POLYGON"])
def test_evaluator_passes_every_positive_control(fixture_id):
    from tools.practice_eval import checks as check_lib

    result = check_lib.resolve("geometry_relation_consistent")(
        _observation(BY_ID[fixture_id]))
    assert result.outcome == check_lib.PASS, (fixture_id, result)


def test_evaluator_calls_the_production_function_not_a_copy():
    import inspect

    from tools.practice_eval import checks as check_lib

    source = inspect.getsource(check_lib.check_geometry_relation_consistent)
    assert "geometrycheck.geometry_relation_contradictions(" in source


def test_the_evaluator_check_has_no_scope_gate():
    """`geometry_ok` se gasi bez scope-a i zato je živi paket propustio.

    Novi mjerač namjerno nema tu kapiju — inače bi ponovio istu slijepu tačku
    nad istom lekcijom."""
    from tools.practice_eval import checks as check_lib

    observation = _observation(GEOM_BAD)
    assert check_lib.check_geometry_ok(observation).outcome == check_lib.SKIP
    assert check_lib.check_geometry_relation_consistent(
        observation).outcome == check_lib.FAIL


def test_release_contract_states_bounded_evidence():
    from tools.practice_eval import release_contract

    assert "geometry_relation_consistent" in release_contract.BOUNDED_CLASS_CHECKS
    assert release_contract.strength_for_check(
        "geometry_relation_consistent", "fail") == \
        release_contract.DETERMINISTICALLY_FAILED
    assert release_contract.strength_for_check(
        "geometry_relation_consistent", "pass") == \
        release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED
    assert "geometric_premise_coherence" in release_contract.BLIND_SPOT_KEYS


# ===========================================================================
# 8) JEZIČKI NALAZ — što se NAMJERNO ne dira (uputa §19)
# ===========================================================================

def test_the_djela_typo_is_deliberately_not_a_terminology_rule():
    """Živi tekst je glasio „na dva jednaka djela“ umjesto „dijela“.

    To NIJE hrvatsko-bosanska varijanta nego ijekavska pravopisna greška, a
    „djela“ je potpuno legitimna bosanska riječ („umjetnička djela“, „dobra
    djela“). Slijepa zamjena bi je pokvarila — isti razlog zbog kojeg projekat
    NAMJERNO ne provodi „suma“→„zbir“ (CLAUDE.md, docs/CURRENT_STATE.md C-8).
    Nijedna postojeća mapa termina ne može ovo sigurno preuzeti, pa ostaje
    ručna rubrika. Ovaj test ZAMRZAVA tu odluku i štiti legitimnu upotrebu."""
    from matbot.terminology import contains_forbidden_term, normalize_terminology

    legitimate = "Učenici su analizirali književna djela i dobra djela."
    assert normalize_terminology(legitimate) == legitimate
    assert contains_forbidden_term(legitimate) is False
    # Ni pogrešan ni ispravan oblik nije „zabranjen termin“ — normalizator ih
    # ne dira ni u jednom smjeru.
    typo = "Podijeli ugao na dva jednaka djela."
    correct = "Podijeli ugao na dva jednaka dijela."
    assert normalize_terminology(typo) == typo
    assert normalize_terminology(correct) == correct
    assert contains_forbidden_term(typo) is False
