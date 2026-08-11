r"""FINAL40 BLOKATORI OBJAVE (kampanja `final40_1df3852_20260811-140856`).

Šest ručno potvrđenih lažnih prihvatanja, svedenih na ČETIRI korijenska uzroka.
Svaki je popravljen PROŠIRENJEM postojećeg vlasnika — nijedan novi modul,
nijedan novi podatkovni fajl, nijedna grana po ID-ju lekcije.

    A  FW-S07  mcq_integrity            goli simbol skupa je gasio cio orakl
    B  FW-F03  stem_disclosure          stem imenuje klasu koju pitanje traži
       FW-F06
    C  FW-G03  geometrycheck            tačke NA KRACIMA uz nenulti unutrašnji ugao
    D  FW-G04  semantic_practice        zahtjev traži arhetip koji lekcija zabranjuje

FW-G05 NIJE ovdje: njegov defekt (zahtjev imenuje dvije komponente, objavljen
zadatak nosi jednu) nije serverski dokaziv bez opšteg razumijevača prirodnog
jezika. Ostaje promptno vlasništvo i ručna rubrika — vidi izvještaj popravke.

Doslovni objavljeni paketi su zamrznuti kao fiksture. Sve je čist deterministički
kod ili FakeLLM: ZERO poziva modela.
"""
import logging

import pytest

from matbot import geometrycheck, mcq_integrity, semantic_practice
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.stem_disclosure import stem_answer_disclosure
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import package_preflight, pipeline
from matbot.tutor.schema import UnifiedOutputError
from tests.conftest import (FakeLLM, make_reviewer_final, make_task_payload,
                            make_tutor_draft)

# ---------------------------------------------------------------------------
# DOSLOVNO OBJAVLJENI PAKETI (zamrznuto iz results.jsonl te kampanje)
# ---------------------------------------------------------------------------

FW_S07_QUESTION = (r"Riješi nejednačinu $-5<x+1<-3$ u skupu cijelih brojeva "
                   r"$\mathbb{Z}$. Odaberi tačan skup rješenja.")
FW_S07_OPTIONS = [r"$-6<x<-4$", r"$\{-5,-4\}$", r"$\{-5\}$", r"$[-5,-4]$"]
FW_S07_MARKED = 2

FW_F03_QUESTION = (r"Data je funkcija prikazana tačkama u koordinatnom sistemu: "
                   r"$ (1,2)$, $ (2,3)$, $ (3,2)$. Predstavlja li ovaj skup "
                   r"tačaka funkciju (da li svakom $x$ pripada najviše jedan $y$)?")
FW_F06_QUESTION = (r"Funkcija je zadana tačkama u koordinatnom sistemu "
                   r"$ (1,5)$, $ (2,5)$, $ (3,6)$. Da li ovaj skup tačaka "
                   r"predstavlja funkciju?")
YES_OPTION = (r"Da — svaki različit $x$ ima tačno jedan pripadajući $y$, pa je "
              r"to funkcija.")
NO_OPTIONS = [
    r"Ne — jer se vrijednost $y=2$ pojavljuje više puta za različite $x$.",
    r"Ne — postoje dvije tačke sa istim $x$ koji daju različit $y$, pa to nije funkcija.",
    r"Ne — zato što su vrijednosti $y$ iste za dvije tačke, pa to nije funkcija.",
]

FW_G03_QUESTION = (
    r"Ugao je $\angle ABC$ i mjera $\angle ABC$ je $40^\circ$. Na kraku $AB$ i "
    r"kraku $BC$ nalaze se tačke $D, E, F, G$ tako da su mjere uglova dati "
    r"ovako: $m\angle ABD=20^\circ$, $m\angle ABE=10^\circ$, "
    r"$m\angle ABF=15^\circ$, $m\angle ABG=5^\circ$. Koji krak dijeli ugao "
    r"$\angle ABC$ na dva jednaka dijela?")

FW_G04_MESSAGE = (
    "Kreiraj samostalan MCQ sa četiri opcije za izabranu lekciju. Traži da "
    "učenik izabere koji skup podataka jednoznačno određuje trougao za "
    "konstrukciju do kongruentnosti. Osiguraj da je tačno jedna opcija tačna. "
    "Ne rješavaj zadatak učeniku.")
FW_G04_PUBLISHED = (r"Trougao ima osnovicu $a=10$ cm i visinu na tu osnovicu "
                    r"$h_a=6$ cm. Koja je površina trougla?")


# ===========================================================================
# A — CJELOBROJNI DOMEN: RELACIJA I KONAČAN SKUP SU ISTI SKUP
# ===========================================================================

def test_fw_s07_two_options_denote_the_same_integer_solution_set():
    """Živi blokator: nad $\\mathbb{Z}$ su $-6<x<-4$ i $\\{-5\\}$ isti skup."""
    result = mcq_integrity.evaluate_linear_solve_mcq(
        FW_S07_QUESTION, FW_S07_OPTIONS)
    assert result.applicable is True
    assert result.valid is False
    assert result.reason_code == "multiple_correct_options"
    assert result.correct_indices == (0, 2)


def test_fw_s07_is_blocked_at_publication():
    failure, _ = mcq_integrity.publication_failure(
        FW_S07_QUESTION, FW_S07_OPTIONS, FW_S07_MARKED,
        FW_S07_OPTIONS[FW_S07_MARKED])
    assert failure == "multiple_correct_options"


def test_the_root_cause_was_a_bare_domain_symbol_not_missing_mathematics():
    """Orakl je defekt oduvijek znao dokazati — gasio ga je ZAPIS domena."""
    without_symbol = FW_S07_QUESTION.replace(r" $\mathbb{Z}$", "")
    assert mcq_integrity.evaluate_linear_solve_mcq(
        without_symbol, FW_S07_OPTIONS).reason_code == "multiple_correct_options"


@pytest.mark.parametrize("segment,expected", [
    (r"\mathbb{Z}", (None, "Z")),
    (r"\mathbb{N}_0", (None, "N0")),
    ("ℝ", (None, "R")),
    (r"x\in\mathbb{Z}", ("x", "Z")),        # zatečeni oblik — nepromijenjen
])
def test_domain_only_segments_are_evidence_not_unreadable_conditions(
        segment, expected):
    assert mcq_integrity._segment_domain_declaration(segment) == expected


@pytest.mark.parametrize("segment", ["Z", "x+1", r"\mathbb{Z}_0", r"x^2>4"])
def test_conservative_segments_stay_unreadable(segment):
    """Golo slovo i sve ostalo i dalje ne postaju domen (moglo bi biti uslov)."""
    assert mcq_integrity._segment_domain_declaration(segment) is None


def test_the_normalization_never_creates_a_new_unverifiable_rejection():
    """Dostupnost: normalizacija smije DODATI dokaz, nikad nov zatvoreni pad.

    Doslovni FW-X04 paket (skupovni zapis s `\\colon` koji parser ne čita):
    prije popravke orakl je ćutao, pa mora ćutati i poslije — inače bi
    „ne mogu pročitati" postalo presuda."""
    question = (r"Riješi nejednačinu $x+1<4$ isključivo u skupu cijelih "
                r"brojeva $\mathbb{Z}$. Koji je tačan skup svih rješenja?")
    options = [r"$\{x\in\mathbb{Z}\colon x\le2\}$",
               r"$\{x\in\mathbb{Z}\colon x\ge2\}$",
               r"$\{x\in\mathbb{Z}\colon x\le3\}$",
               r"$\{x\in\mathbb{Z}\colon x<4\}$"]
    result = mcq_integrity.evaluate_linear_solve_mcq(question, options)
    assert result.applicable is False
    assert mcq_integrity.publication_failure(question, options, 0, options[0])[0] == ""


def test_an_already_reachable_unverifiable_package_still_fails_closed():
    """Kontrola: postojeća `unverifiable` kapija se NE slabi (bez golog simbola)."""
    question = ("Riješi nejednačinu $x+1<4$ isključivo u skupu cijelih brojeva. "
                "Koji je tačan skup svih rješenja?")
    options = [r"$\{x\in\mathbb{Z}\colon x\le2\}$",
               r"$\{x\in\mathbb{Z}\colon x\ge2\}$",
               r"$\{x\in\mathbb{Z}\colon x\le3\}$",
               r"$\{x\in\mathbb{Z}\colon x<4\}$"]
    result = mcq_integrity.evaluate_linear_solve_mcq(question, options)
    assert result.applicable is True
    assert result.reason_code == mcq_integrity.UNVERIFIABLE_SOLUTION_OPTION_CODE


@pytest.mark.parametrize("question,options,marked", [
    # FW-S06 / FW-R02 klasa: jedna tačna opcija nad Z — mora ostati objavljiva.
    (r"Riješi nejednačinu $-3<x+1<-1$ u skupu cijelih brojeva $\mathbb{Z}$.",
     [r"$\{-3\}$", r"$\{-2\}$", r"$\{-4,-3\}$", r"$\{-1\}$"], 0),
    # Kontinuirani domen se NIKAD ne diskretizuje.
    (r"Riješi nejednačinu $2(x-1)<x+4$ u skupu realnih brojeva $\mathbb{R}$.",
     [r"$x<6$", r"$x>6$", r"$x\le6$", r"$x<3$"], 0),
])
def test_valid_domain_packages_still_publish(question, options, marked):
    failure, _ = mcq_integrity.publication_failure(
        question, options, marked, options[marked])
    assert failure == ""


# ===========================================================================
# B — TVRDNJA SEMANTIČKE KLASE U TEKSTU ZADATKA
# ===========================================================================

@pytest.mark.parametrize("question", [FW_F03_QUESTION, FW_F06_QUESTION])
def test_class_assertion_stem_discloses_the_answer(question):
    detail = stem_answer_disclosure(
        question, [NO_OPTIONS[0], NO_OPTIONS[1], NO_OPTIONS[2], YES_OPTION], 3)
    assert detail
    assert "semantic-class assertion" in detail


def test_class_assertion_detection_is_label_independent():
    """Ne prepoznaje se fikstura nego STRUKTURA — druga klasa, druge riječi."""
    question = ("Data je jednakokraka figura zadana stranicama $5$, $5$ i $8$. "
                "Da li je ova figura jednakokraka?")
    detail = stem_answer_disclosure(
        question, ["Ne — stranice se razlikuju.", "Ne — nijedna nije jednaka.",
                   "Ne — to je raznostranična figura.",
                   "Da — dvije stranice su jednake."], 3)
    assert detail and "semantic-class assertion" in detail


@pytest.mark.parametrize("question,options,marked,why", [
    (("Data je relacija prikazana tačkama $(1,2)$, $(1,3)$. Da li ovaj skup "
      "tačaka predstavlja funkciju?"),
     [NO_OPTIONS[0], NO_OPTIONS[1], NO_OPTIONS[2], YES_OPTION], 3,
     "tvrđena klasa je RELACIJA, tražena je FUNKCIJA"),
    (r"Data je funkcija $f(x)=2x+1$. Kolika je vrijednost $f(3)$?",
     ["$5$", "$6$", "$7$", "$8$"], 2, "pitanje traži vrijednost, ne klasu"),
    (r"Prikazana je tabela funkcije $f$. Koja je vrijednost $f(2)$?",
     ["$1$", "$2$", "$3$", "$4$"], 1, "čitanje vrijednosti iz tabele"),
    ((r"Dat je trougao $ABC$ sa stranicama $5$, $5$ i $8$. Da li je trougao "
      r"$ABC$ jednakokraki?"),
     ["Da — dvije stranice su jednake.", "Ne — sve su različite.",
      "Ne — to je pravougli trougao.", "Da — sve tri su jednake."], 0,
     "stem daje SUBJEKT (trougao), pitanje traži DRUGO svojstvo"),
    (("U lekciji o funkcijama posmatramo parove. Da li ovaj skup tačaka "
      "predstavlja funkciju?"),
     [NO_OPTIONS[0], NO_OPTIONS[1], NO_OPTIONS[2], YES_OPTION], 3,
     "puko pominjanje riječi nije tvrdnja o objektu"),
    (r"Data je funkcija $f$. Koji od navedenih parova pripada grafiku funkcije?",
     ["$(1,2)$", "$(2,3)$", "$(3,4)$", "$(4,5)$"], 0,
     "nije polarno pitanje"),
])
def test_class_assertion_false_positive_controls(question, options, marked, why):
    assert stem_answer_disclosure(question, options, marked) == "", why


def test_class_assertion_does_not_depend_on_which_option_is_marked():
    """Ranije se OVDJE očekivala tišina — namjerno POOŠTRENO (FW-F06).

    Do 5057749 se tražilo da označena opcija bude POTVRDNA, pa je ovaj paket
    prolazio uz obrazloženje „označeno je NE, to je drugi defekt". To je bila
    greška u vlasništvu: stem tvrdi da objekat JESTE funkcija, a pitanje traži
    da učenik utvrdi je li funkcija — odgovor je saopšten bez obzira na to koja
    je opcija označena. (Ovaj konkretan paket je uz to i matematički pogrešan:
    $(1,2)$ i $(1,3)$ dijele $x=1$, pa tvrdnja iz stema nije tačna. Dva defekta
    ne poništavaju jedan drugi — tišina bi bila pogrešna u oba slučaja.)
    """
    question = (r"Data je funkcija prikazana tačkama $(1,2)$, $(1,3)$. Da li "
                r"ovaj skup predstavlja funkciju?")
    options = [NO_OPTIONS[0], NO_OPTIONS[1], NO_OPTIONS[2], YES_OPTION]
    verdicts = {marked: stem_answer_disclosure(question, options, marked)
                for marked in range(4)}
    assert all(verdicts.values()), verdicts
    assert len(set(verdicts.values())) == 1, "presuda ne smije zavisiti od oznake"


def test_the_two_earlier_disclosure_classes_are_untouched():
    """FW-G03 klase iz ranijih kampanja moraju i dalje padati."""
    entity = ("Zrak $BD$ leži između zraka $BA$ i $BC$, dok zrak $BE$ ne leži "
              "između njih. Koji od navedenih zraka leži između zraka $BA$ i "
              "$BC$?")
    assert stem_answer_disclosure(
        entity, ["$BA$", "$BC$", "$BD$", "$BE$"], 2)
    point = ("Tačka $D$ leži između krakova $BA$ i $BC$ ugla $\\angle ABC$. "
             "Koji krak dijeli ugao $\\angle ABC$ na dva dijela?")
    assert stem_answer_disclosure(
        point, ["krak $BA$", "krak $BC$", "krak $BD$", "krak $BE$"], 2)


# ===========================================================================
# C — TAČKE NA KRACIMA UGLA UZ NENULTI UNUTRAŠNJI UGAO
# ===========================================================================

def test_fw_g03_arm_point_premise_is_contradictory():
    assert geometrycheck.geometry_relation_contradictions(FW_G03_QUESTION) == (
        geometrycheck.COINCIDENT_RAYS_NONZERO_ANGLE,)


def test_fw_g03_is_blocked_by_the_scope_free_geometry_gate():
    """Lekcija o uglovima nema geometrijski scope — kapija svejedno mora raditi."""
    issues = geometrycheck.find_geometry_issues(FW_G03_QUESTION, "", [])
    assert any(code.startswith(geometrycheck.GEOMETRY_RELATION_CONTRADICTION)
               for code in issues)


@pytest.mark.parametrize("text", [
    (r"Mjera $\angle PQR$ je $90^\circ$. Na kraku $PQ$ i kraku $QR$ leže tačke "
     r"$S, T, U$ tako da je $m\angle PQS=30^\circ$, $m\angle PQT=45^\circ$."),
    (r"Mjera $\angle XYZ$ je $100^\circ$. Na kraku $ZY$ i kraku $YX$ nalaze se "
     r"tačke $K$ pri čemu je $m\angle XYK=25^\circ$."),
    (r"Mjera $\angle MNO$ je $50^\circ$. Na kraku $MN$ i kraku $NO$ nalaze se "
     r"tačke $W$ tako da je $m\angle WNO=20^\circ$."),
])
def test_arm_point_contradiction_is_label_and_orientation_independent(text):
    assert geometrycheck.geometry_relation_contradictions(text) == (
        geometrycheck.COINCIDENT_RAYS_NONZERO_ANGLE,)


@pytest.mark.parametrize("text,why", [
    ((r"Mjera $\angle ABC$ je $40^\circ$. Na kraku $AB$ i kraku $BC$ nalaze se "
      r"tačke $D$ tako da je $m\angle ABD=0^\circ$."),
     "ugao 0 je tačno ono što položaj na kraku znači"),
    ((r"Mjera $\angle ABC$ je $40^\circ$. Na kraku $AB$ i kraku $BC$ nalaze se "
      r"tačke $D$ tako da je $m\angle ABD=40^\circ$."),
     "tačka na DRUGOM kraku daje pun ugao"),
    ((r"Mjera $\angle ABC$ je $40^\circ$. Unutar ugla nalaze se tačke $D, E$ "
      r"tako da je $m\angle ABD=20^\circ$, $m\angle ABE=10^\circ$."),
     "unutar ugla NIJE na kraku — legitiman zadatak"),
    ((r"Mjera $\angle ABC$ je $40^\circ$. Na pravoj $AB$ i pravoj $BC$ nalaze "
      r"se tačke $D$ tako da je $m\angle ABD=20^\circ$."),
     "prava nije krak: smjer nije dokaziv"),
    ((r"Ugao $\angle ABC$. Na kraku $AB$ i kraku $BC$ nalaze se tačke $D, E$ "
      r"tako da je $m\angle ABD=20^\circ$."),
     "mjera cijelog ugla nije zapisana"),
    ((r"Mjera $\angle ABC$ je $40^\circ$. Na kraku $AB$ nalaze se tačke $D$ "
      r"tako da je $m\angle ABD=20^\circ$."),
     "samo jedan krak — tjeme nije dokazivo iz para slova"),
    ((r"Mjera $\angle ABC$ je $40^\circ$. Na kraku $AB$ i kraku $XY$ nalaze se "
      r"tačke $D$ tako da je $m\angle ABD=20^\circ$."),
     "kraci ne dijele tjeme"),
    (r"Ugao $\angle ABC$. Na kraku $AB$ i kraku $BC$ nalaze se tačke $D, E, F$.",
     "nema nijedne mjere"),
])
def test_arm_point_positive_controls_stay_silent(text, why):
    assert geometrycheck.geometry_relation_contradictions(text) == (), why


def test_the_earlier_coincident_ray_form_still_fires():
    text = (r"Na kraku $BA$ nalaze se zraci $BD$, $BE$. Mjera $\angle ABD$ je "
            r"$20^\circ$.")
    assert geometrycheck.geometry_relation_contradictions(text) == (
        geometrycheck.COINCIDENT_RAYS_NONZERO_ANGLE,)


# ===========================================================================
# D — ZAHTJEV TRAŽI ARHETIP KOJI UGOVOR LEKCIJE ZABRANJUJE
# ===========================================================================

def test_fw_g04_request_conflicts_with_the_lesson_contract():
    context = lesson_context_module.build(7, "7-04-023")
    codes = semantic_practice.request_conflicts(
        context.practice_contract, FW_G04_MESSAGE)
    assert codes == ("request_forbidden:construction_determination_request",)


def test_fw_g04_substitute_task_is_rejected_with_the_request_conflict_code():
    """Živi defekt: recenzent je zahtjev tiho zamijenio zadatkom o površini."""
    context = lesson_context_module.build(7, "7-04-023")
    task = make_task_payload(
        text=FW_G04_PUBLISHED,
        options=["16 cm$^2$", "30 cm$^2$", "60 cm$^2$", "3 cm$^2$"],
        correct_option_index=1, expected="30 cm$^2$")
    task = task.model_copy(update={"selected_lesson_id": context.topic_id,
                                   "selected_lesson_title": context.title})
    issues = package_preflight.collect_package_issues(
        task, contract=context.semantic_contract,
        practice_contract=context.practice_contract,
        practice_policy=context.practice_policy,
        student_message=FW_G04_MESSAGE)
    assert package_preflight.REQUEST_CONTRACT_CONFLICT_CODE in {
        issue.code for issue in issues}


def test_the_request_conflict_recipe_demands_fail_closed_not_a_substitute():
    issue = package_preflight.PackageIssue(
        package_preflight.REQUEST_CONTRACT_CONFLICT_CODE, detail="x")
    block = package_preflight.format_for_reviewer([issue])
    assert "NOT repairable" in block
    assert "Return `fail_closed`." in block
    assert "silently substitute" in block


@pytest.mark.parametrize("lesson,message", [
    (("7-04-023", "Daj mi zadatak o visinama trougla i ortocentru.")),
    (("7-04-016", "Daj mi zadatak o podudarnosti trouglova po SSU.")),
    (("8-05-009", "Daj mi zadatak o mreži trostrane prizme.")),
])
def test_ordinary_in_lesson_requests_never_conflict(lesson, message):
    grade = int(lesson[0])
    context = lesson_context_module.build(grade, lesson)
    assert context is not None and context.practice_contract is not None
    assert semantic_practice.request_conflicts(
        context.practice_contract, message) == ()


def test_a_lesson_without_a_contract_can_never_conflict():
    context = lesson_context_module.build(6, "6-12-004")
    assert context.practice_contract is None
    assert semantic_practice.request_conflicts(None, FW_G04_MESSAGE) == ()


def test_an_empty_message_never_conflicts():
    context = lesson_context_module.build(7, "7-04-023")
    for message in ("", "   ", None):
        assert semantic_practice.request_conflicts(
            context.practice_contract, message) == ()


# ===========================================================================
# SERVERSKA PRIMJENA OD KRAJA DO KRAJA — nijedan nesiguran paket ne mijenja stanje
# ===========================================================================

# Poruka MORA biti van zatvorenog skupa jednostavnih zahtjeva, inače lekcija s
# potpunim determinističkim generatorom ode nultom rutom i model-put se uopšte
# ne testira (živi propust ovog fajla pri pisanju).
MODEL_ROUTE_MESSAGE = ("Kreiraj samostalan MCQ sa četiri opcije za izabranu "
                       "lekciju i osiguraj da je tačno jedna opcija tačna.")


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


BLOCKER_PACKAGES = [
    pytest.param(7, "7-02-019", FW_S07_QUESTION, FW_S07_OPTIONS, 2, "",
                 id="FW-S07"),
    pytest.param(6, "6-10-007", FW_F03_QUESTION,
                 [NO_OPTIONS[0], NO_OPTIONS[1], NO_OPTIONS[2], YES_OPTION], 3,
                 "", id="FW-F03"),
    pytest.param(6, "6-10-007", FW_F06_QUESTION,
                 [NO_OPTIONS[0], NO_OPTIONS[1], NO_OPTIONS[2], YES_OPTION], 3,
                 "", id="FW-F06"),
    pytest.param(6, "6-09-001", FW_G03_QUESTION,
                 ["Krak $BD$", "Krak $BG$", "Krak $BF$", "Krak $BE$"], 0, "",
                 id="FW-G03"),
    pytest.param(7, "7-04-023", FW_G04_PUBLISHED,
                 ["16 cm$^2$", "30 cm$^2$", "60 cm$^2$", "3 cm$^2$"], 1,
                 FW_G04_MESSAGE, id="FW-G04"),
]


@pytest.mark.parametrize("grade,lesson,text,options,marked,message",
                         BLOCKER_PACKAGES)
def test_every_blocker_package_is_a_preflight_finding(
        grade, lesson, text, options, marked, message):
    """Nalaz mora stići RECENZENTU — tada ga `correct` još može popraviti."""
    context = lesson_context_module.build(grade, lesson)
    task = make_task_payload(text=text, options=options,
                             correct_option_index=marked,
                             expected=options[marked])
    task = task.model_copy(update={"selected_lesson_id": context.topic_id,
                                   "selected_lesson_title": context.title})
    issues = package_preflight.collect_package_issues(
        task, contract=context.semantic_contract,
        practice_contract=context.practice_contract,
        practice_policy=context.practice_policy,
        student_message=message)
    assert issues, "nijedan deterministički nalaz nad dokazano lošim paketom"
    assert package_preflight.format_for_reviewer(issues)


@pytest.mark.parametrize("grade,lesson,text,options,marked,message",
                         BLOCKER_PACKAGES)
def test_no_blocker_package_can_reach_publication(
        grade, lesson, text, options, marked, message):
    """Posljednja kapija: paket pada PRIJE ijedne mutacije sesije."""
    context = lesson_context_module.build(grade, lesson)
    task = make_task_payload(text=text, options=options,
                             correct_option_index=marked,
                             expected=options[marked])
    task = task.model_copy(update={"selected_lesson_id": context.topic_id,
                                   "selected_lesson_title": context.title})
    if message:
        # Sukob zahtjeva i ugovora se ne dokazuje iz paketa nego iz PORUKE:
        # objavu tada zatvara invarijanta nad recenzentovim paketom.
        issues = package_preflight.collect_package_issues(
            task, contract=context.semantic_contract,
            practice_contract=context.practice_contract,
            student_message=message)
        assert package_preflight.REQUEST_CONTRACT_CONFLICT_CODE in {
            issue.code for issue in issues}
        return
    with pytest.raises(UnifiedOutputError):
        pipeline._validate_task_server_side(task, context)


@pytest.mark.parametrize("grade,lesson,text,options,marked",
                         [(p.values[0], p.values[1], p.values[2], p.values[3],
                           p.values[4]) for p in BLOCKER_PACKAGES
                          if not p.values[5]])
def test_reviewer_cannot_publish_a_blocker_package(
        universal, grade, lesson, text, options, marked, caplog):
    """Tutor predloži, recenzent (pogrešno) odobri — objave NEMA, bez trećeg poziva."""
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    context = lesson_context_module.build(grade, lesson)
    task = make_task_payload(text=text, options=options,
                             correct_option_index=marked,
                             expected=options[marked])
    task = task.model_copy(update={"selected_lesson_id": context.topic_id,
                                   "selected_lesson_title": context.title})
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))
    session_id = f"blocker-{lesson}-{marked}"

    response = run_practice_turn(store, fake, _turn(
        session_id, grade, lesson, MODEL_ROUTE_MESSAGE))

    assert fake.call_count == 2                     # nikad treći poziv
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(session_id) is None           # nijedna mutacija stanja


def test_a_repaired_package_publishes_normally(universal):
    """Popravka MORA biti objavljiva — inače je kapija samo nedostupnost."""
    context = lesson_context_module.build(7, "7-02-019")
    repaired = make_task_payload(
        text=(r"Riješi nejednačinu $-5<x+1<-3$ u skupu cijelih brojeva "
              r"$\mathbb{Z}$. Odaberi tačan skup rješenja."),
        options=[r"$\{-5\}$", r"$\{-4\}$", r"$\{-6,-5\}$", r"$\{-5,-4\}$"],
        correct_option_index=0, expected=r"$\{-5\}$")
    repaired = repaired.model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title})
    draft = make_tutor_draft(intent="generate_task", new_task=repaired)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(store, fake, _turn(
        "repaired", 7, "7-02-019", MODEL_ROUTE_MESSAGE))

    assert fake.call_count == 2                     # model-put, ne nulta ruta
    assert response["status"] == "ready"
    assert response["answer"] != SAFE_ERROR_MESSAGE
    assert r"\{-5\}" in store.peek("repaired")["expected_answer_summary"]
