r"""FINAL40 BLOKATORI IZDANJA — FW-G03 (otkriven odgovor u tekstu) i FW-G06
(zapis van razreda). Kampanja `final40_2fe5636_20260811-101833`, 40/40
scenarija, 78 SDK poziva, 0 timeouta.

Svih sedam kanonskih FAIL statusa te kampanje bilo je bezbjedno zatvaranje
prije objave. Ručni semantički pregled je ipak našao DVA OBJAVLJENA paketa:

    FW-G03  6-09-001  „…zrak BD LEŽI IZMEĐU zraka BA i BC, dok zrak BE NE
                      leži između njih. Koji od navedenih zraka … (tj. LEŽI
                      IZMEĐU zraka BA i BC)?“   označeno: BD
                      → SEMANTIC + PEDAGOGY + REQUEST_FIDELITY false accept
                      → recenzent: decision=correct, `no_leak` PASS

    FW-G06  6-12-004  „U jednakostraničnom trouglu stranice su dužine 6.
                      Kolika je udaljenost incentra od svake stranice?“
                      označeno: $\sqrt{3}$
                      → PEDAGOGY false accept (matematika je TAČNA)
                      → recenzent: decision=approve

Doslovni objavljeni paketi žive u `tests/fixtures/final40_blockers.json`
(kampanjski artefakti su van git-a — `scratchpad/practice_eval/` je ignorisan,
pa je zamrznuti fixture jedini trajni dokaz).

KALIBRACIJA (offline, bez ijednog poziva modela): oba mjerača su prije
uključenja puštena preko 2698 zamrznutih objavljenih paketa iz svih 63
kampanjska `results.jsonl` zapisa (1996 različitih zadataka).

    stem_answer_disclosure   klasa izbora entiteta: 618 paketa
                             od toga s deklarativnim dijelom i izbornom
                             zamjenicom: 28
                             nalaza: 1 — FW-G03, dužina niza 6
                             SVAKI drugi paket u toj klasi ima najdužu
                             dokazanu tvrdnju dužine 0. Nema nijednog
                             graničnog slučaja — prag 3 nije kompromis
                             nego sredina praznog raspona.

    grade_capability         6. razred: 3 paketa s korijenom u CIJELOM
                             korpusu, i sva tri su isti FW-G06 defekt
                             (kampanje c04d2a1, c17538a, 2fe5636).
                             7. razred: 14 paketa na 4 lekcije, a 37 od 122
                             lekcije 7. razreda već dobija prompt blok koji
                             SAM uči formule s korijenom — zato granica važi
                             samo za 6. razred (vidi matbot/practice_policy.py).

ZERO poziva modela: sve je čist deterministički kod ili FakeLLM.
"""
import json
import pathlib

import pytest

from matbot import practice_policy, stem_disclosure
from matbot.stem_disclosure import STEM_ANSWER_DISCLOSURE_CODE, stem_answer_disclosure
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


def _package(fixture):
    """TaskPayload iz zamrznutog zapisa, s kanonskim identitetom lekcije."""
    context = lesson_context_module.build(fixture["grade"], fixture["topic_id"])
    assert context is not None, fixture["topic_id"]
    marked = fixture["options"][fixture["marked_index"]]
    task = make_task_payload(
        text=fixture["question"], options=tuple(fixture["options"]),
        correct_option_index=fixture["marked_index"],
        expected=marked, solution=marked)
    return context, task.model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title})


def _preflight_codes(fixture):
    context, task = _package(fixture)
    return [issue.code for issue in package_preflight.collect_package_issues(
        task, contract=context.semantic_contract,
        practice_contract=context.practice_contract,
        practice_policy=context.practice_policy,
        student_message=fixture["student_message"])]


# ===========================================================================
# 1) ZAMRZNUTI SKUP BLOKATORA — svaki očekivani ishod je EKSPLICITAN
# ===========================================================================

def test_the_frozen_blocker_set_is_complete():
    """Uputa §22: skup mora nositi tačno ove slučajeve, s izričitim ishodom."""
    assert set(BY_ID) == {"G03_BAD", "G03_GOOD", "G06_BAD",
                          "G06_GOOD_G05_STYLE", "G06_GOOD_EQUAL_DISTANCE",
                          "G01_GOOD",
                          # Pretkomitna provjera dosljednosti prompta: jedina
                          # lekcija 6. razreda čiji je prompt nosio formulu s
                          # korijenom mora ostati upotrebljiva.
                          "G0804_GOOD_POLYGON"}
    for fixture in FIXTURES:
        assert fixture["expected"] in ("blocked", "published"), fixture["id"]
        assert len(fixture["options"]) == 4, fixture["id"]


@pytest.mark.parametrize("fixture_id", [f["id"] for f in FIXTURES])
def test_frozen_blocker_publication_outcome(fixture_id):
    """Jedna tabela istine za OBA blokatora, na tački prije mutacije sesije."""
    fixture = BY_ID[fixture_id]
    context, task = _package(fixture)
    if fixture["expected"] == "published":
        tutor_pipeline._validate_task_server_side(task, context)
        assert _preflight_codes(fixture) == [], fixture_id
        return
    with pytest.raises(UnifiedOutputError) as error:
        tutor_pipeline._validate_task_server_side(task, context)
    for code in fixture["codes"]:
        assert code in str(error.value), fixture_id
        assert code in _preflight_codes(fixture), fixture_id


# ===========================================================================
# 2) FW-G03 — TAČAN ŽIVI PAKET I NJEGOVA STRUKTURA
# ===========================================================================

G03_BAD = BY_ID["G03_BAD"]
G03_GOOD = BY_ID["G03_GOOD"]


def test_live_g03_stem_discloses_the_marked_option():
    detail = stem_answer_disclosure(G03_BAD["question"], G03_BAD["options"],
                                    G03_BAD["marked_index"])
    assert detail.startswith(STEM_ANSWER_DISCLOSURE_CODE)
    # Dokaz je STRUKTURAN: imenuje se zajednički niz, ne lekcija ni entitet.
    assert "leži između zraka ba i bc" in detail
    assert "6-09-001" not in detail and "BD" not in detail


def test_the_guard_names_the_property_not_the_entity():
    """Uputa §4: zabranjeno je hardkodovati BD, BA/BC ili lekciju.

    Isti defekt s POTPUNO drugim entitetima i drugom osobinom mora pasti."""
    detail = stem_answer_disclosure(
        "Data su četiri broja: $P$, $Q$, $R$ i $S$. Poznato je da je broj $R$ "
        "djeljiv sa tri i sa pet, dok broj $Q$ nije djeljiv sa tri i sa pet. "
        "Koji je od navedenih brojeva djeljiv sa tri i sa pet?",
        ["$P$", "$Q$", "$R$", "$S$"], 2)
    assert detail.startswith(STEM_ANSWER_DISCLOSURE_CODE)


def test_the_corrected_g03_task_publishes():
    """Uputa §13: ispravka daje PODATKE iz kojih osobina slijedi, ne tvrdnju.

    Pitanje i sve četiri opcije su NEPROMIJENJENI — mijenja se samo to da
    tekst više ne izriče presudu umjesto učenika."""
    assert G03_GOOD["options"] == G03_BAD["options"]
    assert G03_GOOD["marked_index"] == G03_BAD["marked_index"]
    assert stem_answer_disclosure(G03_GOOD["question"], G03_GOOD["options"],
                                  G03_GOOD["marked_index"]) == ""


def test_removing_only_the_negated_clause_is_not_enough():
    """Brisanje „…dok BE NE leži…“ ne popravlja ništa: presuda o BD ostaje."""
    without_negation = G03_BAD["question"].split(", dok zrak")[0] + \
        ". Koji od navedenih zraka dijeli ugao $\\angle ABC$ na dva dijela " \
        "(tj. leži između zraka $\\overrightarrow{BA}$ i $\\overrightarrow{BC}$)?"
    assert stem_answer_disclosure(without_negation, G03_BAD["options"],
                                  G03_BAD["marked_index"])


# ===========================================================================
# 3) G03 MATRICA LAŽNIH POZITIVA (uputa §14)
# ===========================================================================
# Prvih pet su DOSLOVNI objavljeni paketi iz zamrznutih kampanja — svaki je
# odabran zato što označena vrijednost ILI entitet doslovno stoji u tekstu
# zadatka. Leksičko preklapanje samo po sebi nikad ne smije oboriti paket.

FALSE_POSITIVE_MATRIX = (
    # tačka A je geometrijski PODATAK, a označena je njena koordinata (H10)
    ("Na brojevnoj osi je označena tačka A koja se nalazi tri jedinice desno "
     "od nule. Koja je koordinata tačke A?",
     ["$2$", "$4$", "$3$", "$0$"], 2),
    # sve četiri opcije doslovno stoje u tekstu kao koordinate (FW-F05)
    ("Funkcija je zadana tačkama u koordinatnom sistemu: $(1,2)$, $(2,3)$, "
     "$(3,2)$, $(4,5)$. Koja je slika elementa $4$?",
     ["5", "3", "2", "4"], 0),
    # brojevna vrijednost iz uslova se pojavljuje i među opcijama (F11)
    ("Nađi cifru $x$ (0–9) takvu da je broj $1x8$ djeljiv sa 6 i broj $75x$ "
     "djeljiv sa 25. Koja je vrijednost cifre $x$?",
     ["2", "0", "3", "5"], 1),
    # promjenljiva i brojevi iz jednačine stoje i u opcijama (LSP0-C02)
    ("Riješi jednačinu $3x=12$. Koja je vrijednost $x$?",
     ["6", "3", "2", "4"], 3),
    # imenovani objekti (centar, presjek simetrala) u tekstu i pitanju (FW-X01)
    ("U trouglu $ABC$ označimo $I$ kao centar upisane kružnice (presjek "
     "simetrala uglova). Ako je unutrašnji ugao $A$ jednak $60^\\circ$, koliki "
     "je ugao $BIC$?",
     ["$150^\\circ$", "$120^\\circ$", "$30^\\circ$", "$60^\\circ$"], 1),
    # entitet je UVEDEN, ali njegova presudna osobina NIJE izrečena
    ("Iz vrha $B$ polaze zraci $\\overrightarrow{BA}$, $\\overrightarrow{BC}$, "
     "$\\overrightarrow{BD}$ i $\\overrightarrow{BE}$. Koji od navedenih zraka "
     "leži između zraka $\\overrightarrow{BA}$ i $\\overrightarrow{BC}$?",
     ["$\\overrightarrow{BC}$", "$\\overrightarrow{BD}$",
      "$\\overrightarrow{BA}$", "$\\overrightarrow{BE}$"], 1),
    # imenovani skupovi u tekstu i među opcijama, bez tvrdnje o osobini
    ("Dati su skupovi $A=\\{1,2\\}$, $B=\\{1,2,3\\}$, $C=\\{4\\}$ i "
     "$D=\\{2,3\\}$. Koji je od navedenih skupova podskup skupa $B$?",
     ["$A$", "$B$", "$C$", "$D$"], 0),
    # ista osobina je izrečena za DVA entiteta — tekst tada ne izdvaja odgovor
    ("Poznato je da zrak $\\overrightarrow{BD}$ leži između zraka "
     "$\\overrightarrow{BA}$ i $\\overrightarrow{BC}$, i da zrak "
     "$\\overrightarrow{BE}$ leži između zraka $\\overrightarrow{BA}$ i "
     "$\\overrightarrow{BC}$. Koji od navedenih zraka leži između zraka "
     "$\\overrightarrow{BA}$ i $\\overrightarrow{BC}$?",
     ["$\\overrightarrow{BC}$", "$\\overrightarrow{BD}$",
      "$\\overrightarrow{BA}$", "$\\overrightarrow{BE}$"], 1),
    # osobina je izrečena, ali za NEOZNAČENI entitet (distraktor)
    ("Zrak $\\overrightarrow{BE}$ leži između zraka $\\overrightarrow{BA}$ i "
     "$\\overrightarrow{BC}$. Koji od navedenih zraka leži između zraka "
     "$\\overrightarrow{BA}$ i $\\overrightarrow{BC}$?",
     ["$\\overrightarrow{BC}$", "$\\overrightarrow{BD}$",
      "$\\overrightarrow{BA}$", "$\\overrightarrow{BE}$"], 1),
)


@pytest.mark.parametrize("text,options,marked", FALSE_POSITIVE_MATRIX)
def test_lexical_overlap_alone_is_never_a_disclosure(text, options, marked):
    assert stem_answer_disclosure(text, options, marked) == "", text[:60]


@pytest.mark.parametrize("fixture_id",
                         ["G01_GOOD", "G06_GOOD_G05_STYLE",
                          "G06_GOOD_EQUAL_DISTANCE", "G03_GOOD",
                          "G0804_GOOD_POLYGON"])
def test_frozen_positive_controls_are_never_disclosures(fixture_id):
    fixture = BY_ID[fixture_id]
    assert stem_answer_disclosure(fixture["question"], fixture["options"],
                                  fixture["marked_index"]) == "", fixture_id


def test_sentence_only_options_are_outside_the_supported_class():
    """Uputa §5: dokazuje se samo klasa IZBORA ENTITETA.

    G05/G01 imaju rečenične opcije. Modul o njima NIŠTA ne tvrdi — i to je
    zapisano kao ograničen dokaz u release_contract.BLIND_SPOTS, ne kao PASS
    koji bi značio ispravnost."""
    fixture = BY_ID["G06_GOOD_G05_STYLE"]
    assert [stem_disclosure._entity_key(option)
            for option in fixture["options"]] == ["", "", "", ""]


def test_a_task_without_a_declarative_context_is_skipped():
    """Jedna rečenica koja JESTE pitanje nema deklarativni dio da bi otkrila."""
    assert stem_answer_disclosure(
        "Koji od navedenih zraka leži između zraka $\\overrightarrow{BA}$ i "
        "$\\overrightarrow{BC}$?",
        ["$\\overrightarrow{BC}$", "$\\overrightarrow{BD}$",
         "$\\overrightarrow{BA}$", "$\\overrightarrow{BE}$"], 1) == ""


def test_a_question_without_a_selector_is_skipped():
    """„Kolika je…“ je račun, ne izbor entiteta — klasa se ne prepoznaje."""
    assert stem_answer_disclosure(
        "Zrak $\\overrightarrow{BD}$ leži između zraka $\\overrightarrow{BA}$ "
        "i $\\overrightarrow{BC}$. Kolika je mjera ugla koji leži između "
        "zraka $\\overrightarrow{BA}$ i $\\overrightarrow{BC}$?",
        ["$30$", "$40$", "$50$", "$60$"], 0) == ""


# ===========================================================================
# 4) FW-G06 — KURIKULARNA SPOSOBNOST RAZREDA
# ===========================================================================

G06_BAD = BY_ID["G06_BAD"]


def test_live_g06_package_violates_the_grade_capability():
    codes = _preflight_codes(G06_BAD)
    assert practice_policy.GRADE_CAPABILITY_CODE in codes
    # Nije pogrešna matematika i nije van osnovne škole — ne smije nositi
    # nijedan od tih kodova (uputa §15).
    assert practice_policy.ADVANCED_SCOPE_CODE not in codes
    assert not [code for code in codes
                if "mismatch" in code and code != practice_policy.GRADE_CAPABILITY_CODE]


def test_grade_six_forbids_radical_notation_and_grade_eight_allows_it():
    six = practice_policy.resolve(grade=6, lesson_id="6-12-004")
    eight = practice_policy.resolve(grade=8, lesson_id="8-01-008")
    assert not six.radical_notation_allowed
    assert eight.radical_notation_allowed
    assert practice_policy.text_policy_failures(six, "$\\sqrt{3}$") == (
        practice_policy.GRADE_CAPABILITY_CODE,)
    assert practice_policy.text_policy_failures(eight, "$\\sqrt{3}$") == ()


def test_grade_seven_is_deliberately_untouched():
    """Granica stoji tačno tamo gdje je dokazana (vidi kalibraciju u docstringu).

    37 od 122 lekcije 7. razreda već dobija prompt blok s formulama koje
    koriste korijen; zabrana tamo bi protivrječila zatečenom promptu."""
    seven = practice_policy.resolve(grade=7, lesson_id="7-04-018")
    assert seven.radical_notation_allowed
    assert practice_policy.text_policy_failures(seven, "$a\\sqrt{3}$") == ()


def test_prose_about_roots_is_not_notation():
    """Skenira se samo MATEMATIKA: riječ „korijen“ u prozi nije zapis."""
    six = practice_policy.resolve(grade=6, lesson_id="6-12-004")
    assert practice_policy.text_policy_failures(
        six, "Korijen problema je pogrešna mjera.") == ()
    assert practice_policy.find_radical_notation("korijen jednačine") == []


def test_rounding_the_radical_away_is_still_out_of_grade():
    """Uputa §11: zabrana nije „nema simbola“ nego „nema te mašinerije“.

    Decimalna aproksimacija SAMOG korijena i dalje nosi zapis, jer paket u
    kojem se korijen negdje pojavljuje pada; paket bez ijednog korijena je
    izvan dosega ovog mjerača i sudi ga ručna rubrika."""
    six = practice_policy.resolve(grade=6, lesson_id="6-12-004")
    assert practice_policy.text_policy_failures(
        six, "$r=\\frac{6\\sqrt{3}}{6}=\\sqrt{3}\\approx1,73$") == (
        practice_policy.GRADE_CAPABILITY_CODE,)
    # Granica koju modul NE tvrdi: gola decimalna vrijednost je nedokaziva.
    assert practice_policy.text_policy_failures(six, "$1,73$") == ()


def test_grade_six_prompt_states_the_same_boundary():
    """Prompt i validator dijele JEDNU istinu (PP-1) — nikad dvije kopije."""
    from matbot import rules

    six = practice_policy.resolve(grade=6, lesson_id="6-12-004")
    text = practice_policy.radical_capability_rule_text(six)
    assert "\\sqrt" in text and "8. razredu" in text
    grade_block = rules._grade_rules(6)
    assert text in grade_block
    assert practice_policy.radical_capability_rule_text(
        practice_policy.resolve(grade=8, lesson_id="8-01-008")) == ""
    assert "\\sqrt" not in rules._grade_rules(8)


def test_lesson_6_12_004_stays_usable():
    """Uputa §16: popravka ne smije pretvoriti lekciju u neupotrebljivu.

    G05 (doslovno prihvaćen ručnim auditom) i zadatak o jednakoj udaljenosti
    oba prolaze kroz ISTU objavu koja obara G06."""
    for fixture_id in ("G06_GOOD_G05_STYLE", "G06_GOOD_EQUAL_DISTANCE"):
        context, task = _package(BY_ID[fixture_id])
        tutor_pipeline._validate_task_server_side(task, context)


# ===========================================================================
# 5) RECENZENT — NALAZ MORA IMATI LIJEK, INAČE SE VRAĆA ISTI PAKET
# ===========================================================================

def test_reviewer_input_carries_a_repair_recipe_for_both_findings():
    """Obrazac F4E E01: goli kod bez recepta → recenzent vrati isti defekt."""
    block = package_preflight.format_for_reviewer(
        package_preflight.collect_package_issues(
            _package(G03_BAD)[1], student_message=G03_BAD["student_message"]))
    assert STEM_ANSWER_DISCLOSURE_CODE in block
    # Recept mora zabraniti brisanje ENTITETA — inače zadatak postane nerješiv.
    assert "Do NOT delete the entity" in block

    context, task = _package(G06_BAD)
    block = package_preflight.format_for_reviewer(
        package_preflight.collect_package_issues(
            task, practice_policy=context.practice_policy,
            student_message=G06_BAD["student_message"]))
    assert practice_policy.GRADE_CAPABILITY_CODE in block
    assert "Rounding the root away to a decimal does NOT fix it" in block
    # Recept živi uz samu granicu, ne u motoru paketa (arhitektonska kapija
    # `test_no_lesson_identity_in_the_preflight_module`).
    assert practice_policy.grade_capability_repair_text() in block


# ===========================================================================
# 6) CIO TURN — objava odbijena, sesija netaknuta, TAČNO DVA POZIVA
# ===========================================================================

def _turn(session_id, topic_id, message):
    return {
        "session_id": session_id, "grade": 6, "selected_topic": topic_id,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": f"{session_id}-turn-1",
    }


@pytest.fixture(autouse=True)
def _model_route_only(monkeypatch):
    """Obje lekcije su bez determinističkog generatora; izričito isključenje
    je isti mehanizam koji služi i kao produkcijski rollback."""
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


@pytest.mark.parametrize("fixture_id", ["G03_BAD", "G06_BAD"])
def test_blocker_package_is_never_published_and_never_mutates_the_session(
        fixture_id, store, fake_llm):
    fixture = BY_ID[fixture_id]
    _context, task = _package(fixture)
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="approve", final=draft))

    session_id = f"blocker-{fixture_id.lower()}"
    response = tutor_pipeline.run_turn(
        store, fake_llm,
        _turn(session_id, fixture["topic_id"], fixture["student_message"]))

    assert response.get("status") is None                 # ugovor odbijanja
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2                       # bez trećeg poziva
    assert store.peek(session_id) is None                 # nijedna mutacija


@pytest.mark.parametrize("fixture_id", ["G03_GOOD", "G06_GOOD_G05_STYLE",
                                        "G06_GOOD_EQUAL_DISTANCE", "G01_GOOD",
                                        "G0804_GOOD_POLYGON"])
def test_positive_control_package_publishes_in_two_calls(
        fixture_id, store, fake_llm):
    fixture = BY_ID[fixture_id]
    _context, task = _package(fixture)
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="approve", final=draft))

    session_id = f"ok-{fixture_id.lower()}"
    response = tutor_pipeline.run_turn(
        store, fake_llm,
        _turn(session_id, fixture["topic_id"], fixture["student_message"]))

    assert response.get("status") == "ready", response["answer"]
    assert fake_llm.call_count == 2
    session = store.peek(session_id)
    assert session is not None
    assert session["current_task"] == fixture["question"]


def test_reviewer_may_repair_the_g03_draft_within_the_same_two_calls(
        store, fake_llm):
    """`correct` je za to i uveden: ispravka staje u DRUGI poziv, bez trećeg."""
    _context, broken = _package(G03_BAD)
    _context, repaired = _package(G03_GOOD)
    queue_two_call(
        fake_llm,
        draft=make_tutor_draft(intent="generate_task", new_task=broken),
        reviewer=make_reviewer_final(
            decision="correct",
            final=make_tutor_draft(intent="generate_task", new_task=repaired)))

    response = tutor_pipeline.run_turn(
        store, fake_llm,
        _turn("g03-repair", G03_BAD["topic_id"], G03_BAD["student_message"]))

    assert response.get("status") == "ready"
    assert fake_llm.call_count == 2
    assert store.peek("g03-repair")["current_task"] == G03_GOOD["question"]


def test_reviewer_returning_the_same_disclosing_task_is_never_published(
        store, fake_llm):
    """Recenzent je u živom nalazu vratio `correct` s ISTIM defektom."""
    _context, broken = _package(G03_BAD)
    draft = make_tutor_draft(intent="generate_task", new_task=broken)
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="correct", final=draft))

    response = tutor_pipeline.run_turn(
        store, fake_llm,
        _turn("g03-unchanged", G03_BAD["topic_id"], G03_BAD["student_message"]))

    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2
    assert store.peek("g03-unchanged") is None


# ===========================================================================
# 7) PARITET EVALUATORA — ISTA funkcija, nikad slabiji dvojnik (uputa §18)
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


@pytest.mark.parametrize("fixture_id,check_name", [
    ("G03_BAD", "stem_answer_disclosure_safe"),
    ("G06_BAD", "curriculum_task_form_consistent"),
])
def test_evaluator_fails_the_same_packages_production_blocks(fixture_id, check_name):
    from tools.practice_eval import checks as check_lib

    result = check_lib.resolve(check_name)(_observation(BY_ID[fixture_id]))
    assert result.outcome == check_lib.FAIL, result


@pytest.mark.parametrize("fixture_id", ["G03_GOOD", "G06_GOOD_G05_STYLE",
                                        "G06_GOOD_EQUAL_DISTANCE", "G01_GOOD",
                                        "G0804_GOOD_POLYGON"])
def test_evaluator_passes_every_positive_control(fixture_id):
    from tools.practice_eval import checks as check_lib

    observation = _observation(BY_ID[fixture_id])
    for name in ("stem_answer_disclosure_safe", "curriculum_task_form_consistent"):
        result = check_lib.resolve(name)(observation)
        assert result.outcome == check_lib.PASS, (fixture_id, name, result)


def test_evaluator_calls_the_production_function_not_a_copy():
    """Uputa §18: nikad slabiji dvojnik orakla — mjeri se ISTA funkcija."""
    import inspect

    from tools.practice_eval import checks as check_lib

    disclosure = inspect.getsource(check_lib.check_stem_answer_disclosure_safe)
    assert "stem_disclosure.stem_answer_disclosure(" in disclosure
    curriculum = inspect.getsource(check_lib.check_curriculum_task_form_consistent)
    assert "practice_policy.text_policy_failures(" in curriculum
    assert "lesson_context.build(" in curriculum


def test_neither_evaluator_pass_is_advertised_as_a_full_proof():
    """Ograničen dokaz mora biti ZAPISAN kao ograničen (uputa §18/§19)."""
    from tools.practice_eval import release_contract

    for name in ("stem_answer_disclosure_safe", "curriculum_task_form_consistent"):
        assert name in release_contract.BOUNDED_CLASS_CHECKS
        assert release_contract.strength_for_check(name, "pass") == \
            release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED
        assert release_contract.strength_for_check(name, "fail") == \
            release_contract.DETERMINISTICALLY_FAILED
    keys = set(release_contract.BLIND_SPOT_KEYS)
    assert {"stem_answer_disclosure", "grade_capability_of_published_task"} <= keys


def test_manual_pedagogy_rubrics_are_not_retired():
    """Uputa §19: dvije determinističke kapije NE ukidaju ručni pregled."""
    from tools.practice_eval import checks as check_lib

    assert "pedagogy" in check_lib.RUBRICS
    assert "grade_fit" in check_lib.RUBRICS
    assert "lesson_alignment" in check_lib.RUBRICS


# ===========================================================================
# 8) TEŽINA — što se NAMJERNO ne tvrdi (uputa §17)
# ===========================================================================

def test_g06_was_not_a_level_one_difficulty_violation():
    """Kontrolor težine nije bio zaobiđen — on ovaj defekt ne može ni vidjeti.

    „Prisjeti se formule i uvrsti“ je uredan dokaz nivoa 1 po kalibrisanoj
    rubrici, i to s pravom. Uputa §17 izričito zabranjuje pravilo „korijen
    znači teško“, pa se rubrika NE dira: defekt je sposobnost razreda, ne
    težina. Ovaj test zaključava tu granicu."""
    from matbot.tutor.schema import DifficultyEvidence, difficulty_evidence_errors

    evidence = DifficultyEvidence(
        reasoning_steps=1, condition_count=1, operation_count=2,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)
    assert difficulty_evidence_errors(evidence, 1) == ()
