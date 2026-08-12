"""FW-F03 — okvir tvrdnje klase mora pokriti i „predstavljena“/„opisana“.

ŽIVI BLOKATOR IZDANJA. Kanonski FINAL40 na `9bf9f10` objavio je paket

    „FUNKCIJA JE PREDSTAVLJENA skupom tačaka u koordinatnom sistemu:
     $(1,2)$, $(2,3)$, $(3,2)$. Da li ovaj skup tačaka predstavlja FUNKCIJU?“

Stem tvrdi da je objekat funkcija, a pitanje traži da učenik utvrdi je li
funkcija — odgovor stoji u zadatku. Matematika je pri tome bila tačna, pa
nijedna druga kapija nije imala šta prijaviti.

TRI OD ČETIRI USLOVA SU VEĆ BILA ISPUNJENA. Izmjereno nad tim paketom:

    _queried_class(ask)                     = "funkciju"
    upitna čestica `li`                     = True
    _stem_asserts_class(context, "funkcij") = False      ← jedini pad
    stem_answer_disclosure                  = ""

Uzrok je bio isključivo rječnik: `_GIVEN_WORDS` je zatvoren skup TAČNIH
oblika, a `predstavljena` u njemu nije bila. Zamjena samo glagola u istoj
rečenici to dokazuje — `zadana`, `data`, `prikazana`, `definisana`, `navedena`
i `nacrtana` isti paket OBARAJU, a `predstavljena` i `opisana` ga puštaju.

ŠTA JE OVAJ TEST, A ŠTA NIJE. Dokazuje se pokrivenost DVIJE nove porodice
participa u punom rodno-brojevnom nizu i, jednako važno, da veza subjekt↔klasa
nije popuštena: „Skup tačaka JE PREDSTAVLJEN u koordinatnom sistemu…“ uz isto
pitanje mora ostati OBJAVLJIV, jer tu piše KAKO je skup prikazan, a nigdje da
je funkcija. Klasa i dalje ne tvrdi ništa o parafrazama izvan ovog okvira.

Fikstura `FW_F03_LIVE_*` je ZAMRŽNUT objavljeni paket iz
`scratchpad/practice_eval/final40_9bf9f10_20260811-201638`, prepisan doslovno.
Ne uređivati je da bi test prošao.
"""
import logging

import pytest

from matbot import stem_disclosure
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.stem_disclosure import (STEM_ANSWER_DISCLOSURE_CODE,
                                    stem_answer_disclosure)
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import package_preflight, pipeline
from matbot.tutor.schema import UnifiedOutputError
from tests.conftest import (FakeLLM, make_reviewer_final, make_task_payload,
                            make_tutor_draft)

# Lekcija na kojoj je defekt objavljen (FINAL40 FW-F03, 6. razred).
FW_F03_GRADE = 6
FW_F03_LESSON = "6-10-007"
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


# ===========================================================================
# ZAMRZNUTA ŽIVA FIKSTURA — doslovno iz objavljenog paketa na 9bf9f10
# ===========================================================================

FW_F03_LIVE_QUESTION = (
    r"Funkcija je predstavljena skupom tačaka u koordinatnom sistemu: "
    r"$(1,2)$, $(2,3)$, $(3,2)$. Da li ovaj skup tačaka predstavlja funkciju?")

FW_F03_LIVE_OPTIONS = [
    (r"Da — svaki različit $x$ iz skupa $(1,2),(2,3),(3,2)$ ima tačno jednu "
     r"pripadajuću $y$-vrijednost."),
    (r"Ne — nije funkcija jer se u skupu pojavljuje ista $y$-vrijednost za "
     r"različite $x$."),
    (r"Ne — nije funkcija jer za neki $x$ u skupu postoje dvije različite "
     r"tačke sa istim $x$."),
    (r"Ne — nije funkcija jer jedna od navedenih tačaka nema $y$-koordinatu."),
]

FW_F03_LIVE_MARKED = 0


# ===========================================================================
# KORIJEN UZROKA — tri uslova su već bila ispunjena, pao je samo rječnik
# ===========================================================================

def test_root_cause_only_the_assertion_frame_was_missing():
    """Mjerenje iz izvještaja o blokatoru, kao izvršni test."""
    context, ask = stem_disclosure._split_ask(FW_F03_LIVE_QUESTION)
    assert stem_disclosure._queried_class(ask) == "funkciju"
    assert stem_disclosure._asks_polar_class_question(ask) is True
    # Ovo je vraćalo False na `da71524` i JEDINO je ovo popravljeno.
    assert stem_disclosure._stem_asserts_class(context, "funkcij") is True


def test_the_exact_live_package_is_now_caught():
    """Doslovan objavljeni paket sa `9bf9f10` — jedini razlog ovog izdanja."""
    detail = stem_answer_disclosure(
        FW_F03_LIVE_QUESTION, FW_F03_LIVE_OPTIONS, FW_F03_LIVE_MARKED)
    assert detail
    assert detail.startswith(STEM_ANSWER_DISCLOSURE_CODE)
    assert "semantic-class assertion" in detail
    assert "funkciju" in detail


def test_verdict_does_not_depend_on_which_option_is_marked():
    """Tvrdnja je u STEMU; oznaka je ne može ukloniti."""
    verdicts = {index: stem_answer_disclosure(
        FW_F03_LIVE_QUESTION, FW_F03_LIVE_OPTIONS, index)
        for index in range(len(FW_F03_LIVE_OPTIONS))}
    assert all(verdicts.values()), verdicts
    assert len(set(verdicts.values())) == 1


# ===========================================================================
# DRUGA NOVA PORODICA — dokaz da ovo nije zakrpa za jedan string
# ===========================================================================

def test_opisana_triggers_the_same_invariant():
    """`opisana` je druga porodica koja je bježala u istom mjerenju."""
    question = (r"Funkcija je opisana skupom tačaka u koordinatnom sistemu: "
                r"$(1,2)$, $(2,3)$, $(3,2)$. Da li ovaj skup tačaka "
                r"predstavlja funkciju?")
    detail = stem_answer_disclosure(question, FW_F03_LIVE_OPTIONS, 0)
    assert detail and "semantic-class assertion" in detail


# ===========================================================================
# MORFOLOGIJA — tačno ono što je implementirano, ni riječ više
# ===========================================================================
# Particip se slaže s NAZIVOM KLASE, a klasa nije uvijek ženskog roda jednine.
# Svaki oblik ispod je provjeren kroz PUN put `stem_answer_disclosure`, u
# rečenici u kojoj tvrđena klasa jeste i tražena klasa.
MORPHOLOGY = [
    ("predstavljena", r"Funkcija je predstavljena skupom tačaka.",
     r"Da li ovaj skup tačaka predstavlja funkciju?"),
    ("predstavljen", r"Polinom je predstavljen zbirom monoma.",
     r"Da li je ovaj izraz polinom?"),
    ("predstavljeno", r"Preslikavanje je predstavljeno tablicom.",
     r"Da li je ovo preslikavanje?"),
    ("predstavljene", r"Funkcije su predstavljene tablicama.",
     r"Da li su ovo funkcije?"),
    ("predstavljeni", r"Polinomi su predstavljeni zbirovima monoma.",
     r"Da li su ovo polinomi?"),
    ("opisana", r"Funkcija je opisana skupom tačaka.",
     r"Da li ovaj skup tačaka predstavlja funkciju?"),
    ("opisan", r"Polinom je opisan zbirom monoma.",
     r"Da li je ovaj izraz polinom?"),
    ("opisano", r"Preslikavanje je opisano tablicom.",
     r"Da li je ovo preslikavanje?"),
    ("opisane", r"Relacije su opisane skupovima parova.",
     r"Da li su ovo relacije?"),
    ("opisani", r"Polinomi su opisani zbirovima monoma.",
     r"Da li su ovo polinomi?"),
]


@pytest.mark.parametrize("form,stem_text,ask", MORPHOLOGY)
def test_every_claimed_participle_form_actually_blocks(form, stem_text, ask):
    """Pokrivenost se TVRDI samo za oblike koji su ovdje i dokazani."""
    assert form in stem_disclosure._GIVEN_WORDS
    detail = stem_answer_disclosure(f"{stem_text} {ask}",
                                    FW_F03_LIVE_OPTIONS, 0)
    assert detail, f"{form}: okvir tvrdnje klase nije prepoznat"


def test_both_new_families_are_complete_paradigms():
    """Bez rupa u rodu/broju — rupa je bila cio uzrok ovog blokatora."""
    for stem in ("predstavljen", "opisan"):
        forms = {stem + ending for ending in ("", "a", "o", "i", "e")}
        assert forms <= stem_disclosure._GIVEN_WORDS, sorted(
            forms - stem_disclosure._GIVEN_WORDS)


def test_previously_covered_families_are_untouched():
    """Zatečeni oblici se NE diraju — njihove rupe su zaseban zadatak."""
    previous = {
        "dat", "data", "dato", "dati", "date", "dana", "dan", "dano",
        "zadat", "zadata", "zadan", "zadana", "zadano", "zadani", "zadane",
        "prikazan", "prikazana", "prikazano", "prikazani",
        "definisan", "definisana", "definiran", "definirana",
        "nacrtan", "nacrtana", "naveden", "navedena", "navedeno",
    }
    assert previous <= stem_disclosure._GIVEN_WORDS
    # Ništa osim dvije nove porodice nije dodato.
    added = stem_disclosure._GIVEN_WORDS - previous
    assert added == {
        "predstavljen", "predstavljena", "predstavljeno",
        "predstavljeni", "predstavljene",
        "opisan", "opisana", "opisano", "opisani", "opisane",
    }


# ===========================================================================
# KRITIČNA GRANICA — „prikazan objekat" NIJE „objekat je te klase"
# ===========================================================================
# Ovo je najvažnija kontrola u fajlu. Sam particip NE SMIJE obarati zadatak;
# obara ga tek particip koji stoji uz kopulu uz SAM NAZIV tražene klase.
ALLOWED = [
    (r"Skup tačaka je predstavljen u koordinatnom sistemu: $(1,2)$, $(2,3)$, "
     r"$(3,2)$. Da li ovaj skup tačaka predstavlja funkciju?",
     "objekat je PRIKAZAN, klasa nije tvrđena — traženi popravak"),
    (r"Skup tačaka je opisan koordinatama: $(1,2)$, $(2,3)$, $(3,2)$. "
     r"Da li ovaj skup tačaka predstavlja funkciju?",
     "isto, druga nova porodica"),
    (r"Skup tačaka u koordinatnom sistemu je $(1,2)$, $(2,3)$, $(3,2)$. "
     r"Predstavlja li ovaj skup tačaka funkciju?",
     "željena popravljena formulacija"),
    (r"Funkcija je predstavljena skupom tačaka: $(1,2)$, $(2,3)$. "
     r"Kolika je vrijednost funkcije za $x = 3$?",
     "klasa imenovana, ali pitanje traži VRIJEDNOST"),
    (r"Funkcija je opisana formulom $f(x) = 2x + 1$. Koji je domen funkcije?",
     "pitanje traži DOMEN"),
    (r"Funkcija je predstavljena tablicom. Koji je skup vrijednosti funkcije?",
     "pitanje traži SKUP VRIJEDNOSTI"),
    (r"Relacija je predstavljena skupom tačaka: $(1,2)$, $(2,3)$, $(3,2)$. "
     r"Da li ovaj skup tačaka predstavlja funkciju?",
     "tvrđena klasa je RELACIJA, tražena je FUNKCIJA"),
    (r"Ovaj skup tačaka nije predstavljen kao funkcija. "
     r"Da li ovaj skup tačaka predstavlja funkciju?",
     "odričan oblik ne stvara okvir tvrdnje"),
    (r"Funkcija se može predstaviti tablicom, formulom ili grafikom. "
     r"Da li ovaj skup tačaka predstavlja funkciju?",
     "definicija pojma, ne tvrdnja o OVOM objektu"),
    (r"U koordinatnom sistemu predstavljene su tačke $(1,2)$, $(2,3)$. "
     r"Da li ovaj skup tačaka predstavlja funkciju?",
     "particip bez okvira kopula+klasa"),
]


@pytest.mark.parametrize("question,why", ALLOWED)
def test_legitimate_wordings_stay_publishable(question, why):
    assert stem_answer_disclosure(question, FW_F03_LIVE_OPTIONS, 0) == "", why


def test_the_participle_alone_never_decides():
    """Ista riječ, dvije rečenice: presuda dolazi od okvira, ne od riječi."""
    disclosing = (r"Funkcija je predstavljena skupom tačaka. "
                  r"Da li ovaj skup tačaka predstavlja funkciju?")
    legitimate = (r"Skup tačaka je predstavljen u koordinatnom sistemu. "
                  r"Da li ovaj skup tačaka predstavlja funkciju?")
    assert "predstavlj" in disclosing and "predstavlj" in legitimate
    assert stem_answer_disclosure(disclosing, FW_F03_LIVE_OPTIONS, 0)
    assert stem_answer_disclosure(legitimate, FW_F03_LIVE_OPTIONS, 0) == ""


# ===========================================================================
# PROIZVODNI PUT — nije dovoljno da detektor „vidi", mora i ZAUSTAVITI
# ===========================================================================

def _live_task(context):
    task = make_task_payload(
        text=FW_F03_LIVE_QUESTION, options=list(FW_F03_LIVE_OPTIONS),
        correct_option_index=FW_F03_LIVE_MARKED,
        expected=FW_F03_LIVE_OPTIONS[FW_F03_LIVE_MARKED])
    return task.model_copy(update={"selected_lesson_id": context.topic_id,
                                   "selected_lesson_title": context.title})


def test_preflight_reports_the_live_package_to_the_reviewer():
    """Prva tačka: nalaz nad NACRTOM ide recenzentu, ne ćuti."""
    context = lesson_context_module.build(FW_F03_GRADE, FW_F03_LESSON)
    issues = package_preflight.collect_package_issues(
        _live_task(context), contract=context.semantic_contract,
        practice_contract=context.practice_contract)
    assert STEM_ANSWER_DISCLOSURE_CODE in {issue.code for issue in issues}


def test_the_live_package_cannot_reach_publication():
    """Posljednja kapija prije IJEDNE mutacije sesije."""
    context = lesson_context_module.build(FW_F03_GRADE, FW_F03_LESSON)
    with pytest.raises(UnifiedOutputError):
        pipeline._validate_task_server_side(_live_task(context), context)


def test_reviewer_approval_does_not_publish_the_live_package(universal, caplog):
    """Tutor predloži, recenzent (pogrešno) odobri — objave NEMA, bez trećeg poziva.

    Tačno redoslijed događaja koji je proizveo živi FW-F03 na 9bf9f10."""
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    context = lesson_context_module.build(FW_F03_GRADE, FW_F03_LESSON)
    draft = make_tutor_draft(intent="generate_task", new_task=_live_task(context))
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(store, fake, _turn(
        "fw-f03-live", FW_F03_GRADE, FW_F03_LESSON, MODEL_ROUTE_MESSAGE))

    assert fake.call_count == 2                     # nikad treći poziv
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("fw-f03-live") is None        # nijedna mutacija stanja


def test_a_repaired_fw_f03_package_publishes_normally(universal):
    """Popravka MORA biti objavljiva — inače je kapija samo nedostupnost."""
    context = lesson_context_module.build(FW_F03_GRADE, FW_F03_LESSON)
    repaired = make_task_payload(
        text=(r"Skup tačaka je predstavljen u koordinatnom sistemu: $(1,2)$, "
              r"$(2,3)$, $(3,2)$. Da li ovaj skup tačaka predstavlja funkciju?"),
        options=list(FW_F03_LIVE_OPTIONS),
        correct_option_index=FW_F03_LIVE_MARKED,
        expected=FW_F03_LIVE_OPTIONS[FW_F03_LIVE_MARKED])
    repaired = repaired.model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title})
    assert stem_answer_disclosure(
        repaired.text, list(FW_F03_LIVE_OPTIONS), FW_F03_LIVE_MARKED) == ""

    draft = make_tutor_draft(intent="generate_task", new_task=repaired)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(store, fake, _turn(
        "fw-f03-repaired", FW_F03_GRADE, FW_F03_LESSON, MODEL_ROUTE_MESSAGE))

    assert fake.call_count == 2                     # model-put, ne nulta ruta
    assert response["status"] == "ready"
    assert response["answer"] != SAFE_ERROR_MESSAGE


# ===========================================================================
# FALSIFIKACIJA — dokaz da baš NOVA porodica zaustavlja živi paket
# ===========================================================================

def test_falsification_removing_the_new_family_republishes_fw_f03(monkeypatch):
    """Skini SAMO dvije nove porodice i živi paket opet prolazi.

    Ako ovaj test padne, paket zaustavlja nešto drugo, pa proširenje rječnika
    nije mehanizam popravke — a to bi bio razlog da se povuče."""
    without_new_families = frozenset(
        word for word in stem_disclosure._GIVEN_WORDS
        if not (word.startswith("predstavljen") or word.startswith("opisan")))
    monkeypatch.setattr(stem_disclosure, "_GIVEN_WORDS", without_new_families)

    assert stem_answer_disclosure(
        FW_F03_LIVE_QUESTION, FW_F03_LIVE_OPTIONS, FW_F03_LIVE_MARKED) == "", (
        "sa skinutim novim porodicama paket MORA opet proći — inače nova "
        "pokrivenost nije ono što ga zaustavlja")
    # Kontrola u istom testu: već pokrivena porodica i dalje obara isti oblik,
    # pa monkeypatch nije slomio detektor uopšte.
    assert stem_answer_disclosure(
        FW_F03_LIVE_QUESTION.replace("predstavljena", "zadana", 1),
        FW_F03_LIVE_OPTIONS, FW_F03_LIVE_MARKED)
