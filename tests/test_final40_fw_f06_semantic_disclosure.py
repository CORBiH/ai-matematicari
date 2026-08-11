"""FW-F06 — otkrivanje klase kroz stem, nezavisno od OBLIKA OPCIJA.

ŽIVI BLOKATOR IZDANJA. Kanonski FINAL40 na `5057749` objavio je paket u kojem
stem tvrdi da je objekat FUNKCIJA, a pitanje traži da učenik utvrdi je li
funkcija. Ograničeni detektor je prepoznao OBA semantička dijela defekta
(`_queried_class = 'funkciju'`, `_stem_asserts_class = True`) i ipak zaćutao —
jer je tadašnji uslov dokazivao „ovo je DA/NE pitanje" preko PRVE RIJEČI OPCIJA,
a opcije su bile pune rečenice („Skup tačaka predstavlja funkciju…").

To je bio pogrešan signal. Otkrivanje klase je svojstvo PARA stem+pitanje: ako
stem kaže da objekat JESTE klase C, a pitanje traži da učenik utvrdi je li
klase C, odgovor stoji u zadatku bez obzira na to kako je tačna opcija napisana
i koja je opcija označena. Uslov se sada mjeri nad PITANJEM (upitna čestica
`li`).

Fikstura `FW_F06_LIVE_*` je ZAMRZNUTI objavljeni paket iz
`scratchpad/practice_eval/final40_5057749_20260811-155353`, prepisan doslovno.
Ne uređivati je da bi test prošao — ona je dokaz da je baš ovaj paket zaustavljen.
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

# Lekcija na kojoj je defekt objavljen (FINAL40 FW-F06, 6. razred).
FW_F06_GRADE = 6
FW_F06_LESSON = "6-10-007"
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
# ZAMRZNUTA ŽIVA FIKSTURA — doslovno iz objavljenog FINAL40 paketa na 5057749
# ===========================================================================

FW_F06_LIVE_QUESTION = (
    r"Funkcija je zadana tačkama u koordinatnom sistemu: $(1,5)$, $(2,5)$, "
    r"$(3,6)$. Koja tvrdnja ispravno opisuje da li ovaj skup tačaka predstavlja "
    r"funkciju?")

FW_F06_LIVE_OPTIONS = [
    (r"Skup tačaka ne predstavlja funkciju zato što se vrijednost $y=5$ "
     r"ponavlja za različite $x$, a to krši definiciju funkcije."),
    (r"Skup tačaka predstavlja funkciju na domenu $\{1,2,3\}$ jer svaki $x$ iz "
     r"domene ima tačno jedno odgovarajuće $y$."),
    (r"Skup tačaka ne predstavlja funkciju jer za $x=1$ postoje dva različita "
     r"$y$."),
    (r"Skup tačaka ne predstavlja funkciju jer postoji neki $x\in\{1,2,3\}$ za "
     r"koji ne postoji pridruženi $y$."),
]

FW_F06_LIVE_MARKED = 1

# Isti stem, ali polarne opcije — oblik koji je i STARI detektor hvatao.
POLAR_OPTIONS = [
    r"Ne — vrijednost $y=5$ se ponavlja.",
    r"Ne — za $x=1$ postoje dva različita $y$.",
    r"Ne — neki $x$ nema pridruženi $y$.",
    r"Da — svaki $x$ ima tačno jedno $y$.",
]


# ===========================================================================
# LOŠI PAKETI — moraju biti oboreni
# ===========================================================================

def test_bad_a_the_exact_live_package_is_now_caught():
    """Doslovan objavljeni paket sa `5057749` — jedini razlog ovog izdanja."""
    detail = stem_answer_disclosure(
        FW_F06_LIVE_QUESTION, FW_F06_LIVE_OPTIONS, FW_F06_LIVE_MARKED)
    assert detail
    assert detail.startswith(STEM_ANSWER_DISCLOSURE_CODE)
    assert "semantic-class assertion" in detail
    assert "funkciju" in detail


def test_bad_b_the_same_stem_with_polar_options_is_still_caught():
    """Regresija u drugom smjeru: stari, već pokriveni oblik ne smije ispasti."""
    assert stem_answer_disclosure(FW_F06_LIVE_QUESTION, POLAR_OPTIONS, 3)


def test_bad_c_equivalent_wording_is_caught_too():
    """Prepoznaje se STRUKTURA, ne fikstura — drugi „da li" oblik, druge riječi."""
    question = (r"Data je funkcija zadana tačkama $(1,2)$, $(2,3)$, $(3,4)$. "
                r"Da li ovaj skup tačaka predstavlja funkciju?")
    detail = stem_answer_disclosure(question, FW_F06_LIVE_OPTIONS, 1)
    assert detail and "semantic-class assertion" in detail


# ===========================================================================
# KORIJEN UZROKA — presuda ne smije zavisiti od OBLIKA ODGOVORA
# ===========================================================================

def test_option_form_does_not_change_the_verdict():
    """Dokaz korijena uzroka FW-F06.

    Isti stem koji otkriva klasu mora dobiti ISTU presudu i s rečeničnim i s
    polarnim opcijama. Da je ovo vrijedilo na `5057749`, paket ne bi izašao.
    """
    prose = stem_answer_disclosure(
        FW_F06_LIVE_QUESTION, FW_F06_LIVE_OPTIONS, FW_F06_LIVE_MARKED)
    polar = stem_answer_disclosure(FW_F06_LIVE_QUESTION, POLAR_OPTIONS, 3)
    assert prose == polar != ""


def test_verdict_does_not_depend_on_which_option_is_marked():
    """Tvrdnja je u STEMU; oznaka je ne može ukloniti."""
    verdicts = {i: stem_answer_disclosure(FW_F06_LIVE_QUESTION,
                                          FW_F06_LIVE_OPTIONS, i)
                for i in range(len(FW_F06_LIVE_OPTIONS))}
    assert all(verdicts.values()), verdicts
    assert len(set(verdicts.values())) == 1


# ===========================================================================
# DOBRI PAKETI — kontrola lažnih pozitiva (§9)
# ===========================================================================

GOOD_PACKAGES = [
    # A — tvrđena klasa je RELACIJA, tražena je FUNKCIJA. Ovo je popravak koji
    #     Recenzent stvarno radi; ne smije biti kažnjen.
    ((r"Data je relacija prikazana tačkama $(1,2)$, $(2,3)$, $(3,2)$. Da li "
      r"ovaj skup tačaka predstavlja funkciju?"),
     FW_F06_LIVE_OPTIONS, 1,
     "stem tvrdi relaciju, pitanje traži funkciju"),
    # A2 — doslovan ŽIVI FW-F03 paket sa istog FINAL40 pokretanja, koji je
    #      objavljen ISPRAVNO i mora ostati objavljiv.
    ((r"Data je relacija prikazana tačkama u koordinatnom sistemu: "
      r"$ (1,2),\ (2,3),\ (3,2) $. Predstavlja li ova tabela funkciju?"),
     [r"Ne — postoji tačka sa istim $x$ i drugačijim $y$.",
      r"Ne — vrijednosti $y$ za $x=1$ i $x=3$ su iste.",
      r"Da — za svako $x$ iz $\{1,2,3\}$ postoji tačno jedno $y$.",
      r"Da — jer je vrijednost $y$ strogo rastuća kada se povećava $x$."], 2,
     "živi FW-F03 paket sa 5057749 — ispravno objavljen"),
    # B–C — funkcija je legitimno DATA, a pitanje traži vrijednost/domen.
    (r"Funkcija $f$ je zadana tačkama $(1,2)$, $(2,3)$, $(3,2)$. Kolika je "
     r"vrijednost $f(3)$?",
     ["$2$", "$3$", "$5$", "$4$"], 0, "traži vrijednost, ne klasu"),
    (r"Funkcija je zadana tačkama $(1,2)$, $(2,3)$. Kolika je vrijednost "
     r"funkcije za $x=2$?",
     ["$3$", "$2$", "$5$", "$4$"], 0, "vrijednost funkcije, ne klasa"),
    (r"Funkcija je zadana tačkama $(1,2)$, $(2,3)$. Koji je domen funkcije?",
     [r"$\{1,2\}$", r"$\{2,3\}$", r"$\{1,3\}$", r"$\{2\}$"], 0,
     "domen funkcije, ne klasa"),
    # D — živi FW-F01 oblik: funkcija je data, pitanje traži DRUGO svojstvo.
    (r"Funkcija je zadana skupom tačaka $\{(1,2),(2,3),(3,2),(4,5)\}$. Je li "
     r"slika elementa $1$ jedinstvena?",
     ["Ne može se utvrditi jer nedostaju koordinate.",
      "Ne, element $1$ ima dvije različite slike.",
      "Da, slika elementa $1$ je jedinstvena i iznosi $2$.",
      "Da, slika elementa $1$ nije jedinstvena."], 2,
     "traženo svojstvo je jedinstvenost slike, ne pripadnost klasi"),
    # E — subjekt smije biti dat; pitanje traži drugu osobinu tog subjekta.
    (r"Dat je trougao $ABC$ sa stranicama $5$, $5$ i $8$. Da li je trougao "
     r"$ABC$ jednakokraki?",
     ["Da — dvije stranice su jednake.", "Ne — sve su različite.",
      "Ne — to je pravougli trougao.", "Da — sve tri su jednake."], 0,
     "stem daje SUBJEKT (trougao), pitanje traži svojstvo"),
]


@pytest.mark.parametrize("question,options,marked,why", GOOD_PACKAGES)
def test_good_packages_stay_publishable(question, options, marked, why):
    assert stem_answer_disclosure(question, options, marked) == "", why


def test_polar_gate_still_silences_non_polar_questions():
    """Uslov je UŽI od pukog brisanja: pitanja bez `li` ostaju dozvoljena.

    Bez ovog uslova bi „Kolika je vrijednost funkcije?" i „Koji je domen
    funkcije?" bili lažno oboreni — mjereno, ne pretpostavljeno.
    """
    for ask in ("Kolika je vrijednost funkcije?", "Koji je domen funkcije?"):
        assert stem_disclosure.POLAR_QUESTION_TOKEN not in \
            stem_disclosure._tokenize(ask)
    for ask in ("Da li ovaj skup predstavlja funkciju?",
                "Predstavlja li ova tabela funkciju?",
                "Je li ovaj skup funkcija?"):
        assert stem_disclosure.POLAR_QUESTION_TOKEN in \
            stem_disclosure._tokenize(ask)


# ===========================================================================
# PROIZVODNI PUT — nije dovoljno da detektor „vidi", mora i ZAUSTAVITI
# ===========================================================================

def _live_task(context):
    task = make_task_payload(
        text=FW_F06_LIVE_QUESTION, options=list(FW_F06_LIVE_OPTIONS),
        correct_option_index=FW_F06_LIVE_MARKED,
        expected=FW_F06_LIVE_OPTIONS[FW_F06_LIVE_MARKED])
    return task.model_copy(update={"selected_lesson_id": context.topic_id,
                                   "selected_lesson_title": context.title})


def test_preflight_reports_the_live_package_to_the_reviewer():
    """Prva tačka: nalaz nad NACRTOM ide recenzentu, ne ćuti."""
    context = lesson_context_module.build(FW_F06_GRADE, FW_F06_LESSON)
    issues = package_preflight.collect_package_issues(
        _live_task(context), contract=context.semantic_contract,
        practice_contract=context.practice_contract)
    assert STEM_ANSWER_DISCLOSURE_CODE in {issue.code for issue in issues}


def test_the_live_package_cannot_reach_publication():
    """Posljednja kapija prije IJEDNE mutacije sesije."""
    context = lesson_context_module.build(FW_F06_GRADE, FW_F06_LESSON)
    with pytest.raises(UnifiedOutputError):
        pipeline._validate_task_server_side(_live_task(context), context)


def test_reviewer_approval_does_not_publish_the_live_package(universal, caplog):
    """Tutor predloži, recenzent (pogrešno) odobri — objave NEMA, bez trećeg poziva.

    Ovo je tačno redoslijed događaja koji je proizveo živi FW-F06 na 5057749.
    """
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    context = lesson_context_module.build(FW_F06_GRADE, FW_F06_LESSON)
    draft = make_tutor_draft(intent="generate_task", new_task=_live_task(context))
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(store, fake, _turn(
        "fw-f06-live", FW_F06_GRADE, FW_F06_LESSON, MODEL_ROUTE_MESSAGE))

    assert fake.call_count == 2                     # nikad treći poziv
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("fw-f06-live") is None        # nijedna mutacija stanja


def test_a_repaired_fw_f06_package_publishes_normally(universal):
    """Popravka MORA biti objavljiva — inače je kapija samo nedostupnost.

    Recenzentov stvarni popravak je zamjena tvrđene klase („skup tačaka" /
    „relacija" umjesto „funkcija"), isti potez koji je FW-F03 ispravno objavio.
    """
    context = lesson_context_module.build(FW_F06_GRADE, FW_F06_LESSON)
    repaired = make_task_payload(
        text=(r"Dat je skup tačaka u koordinatnom sistemu: $(1,5)$, $(2,5)$, "
              r"$(3,6)$. Da li ovaj skup tačaka predstavlja funkciju?"),
        options=list(FW_F06_LIVE_OPTIONS),
        correct_option_index=FW_F06_LIVE_MARKED,
        expected=FW_F06_LIVE_OPTIONS[FW_F06_LIVE_MARKED])
    repaired = repaired.model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title})
    assert stem_answer_disclosure(
        repaired.text, list(FW_F06_LIVE_OPTIONS), FW_F06_LIVE_MARKED) == ""

    draft = make_tutor_draft(intent="generate_task", new_task=repaired)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(store, fake, _turn(
        "fw-f06-repaired", FW_F06_GRADE, FW_F06_LESSON, MODEL_ROUTE_MESSAGE))

    assert fake.call_count == 2                     # model-put, ne nulta ruta
    assert response["status"] == "ready"
    assert response["answer"] != SAFE_ERROR_MESSAGE


# ===========================================================================
# FALSIFIKACIJA (§14) — dokaz da baš NOVI uslov zaustavlja živi paket
# ===========================================================================

def test_falsification_reverting_the_new_condition_republishes_fw_f06(monkeypatch):
    """Vrati SAMO novi uslov na stari (oblik opcija) i živi paket opet prolazi.

    Ako ovaj test padne, znači da paket zaustavlja nešto drugo, pa je promjena
    u `stem_disclosure` bila nepotrebna — a to bi bio razlog da se povuče.
    """
    old_affirmative = frozenset({"da", "tacno", "tačno", "jeste", "jest"})
    old_negative = frozenset({"ne", "netacno", "netačno", "nije"})

    def old_gate(ask, _options=FW_F06_LIVE_OPTIONS, _marked=FW_F06_LIVE_MARKED):
        openers = [(stem_disclosure._tokenize(t) or [""])[0] for t in _options]
        if not any(o in old_affirmative for o in openers):
            return False
        if not any(o in old_negative for o in openers):
            return False
        return openers[_marked] in old_affirmative

    monkeypatch.setattr(stem_disclosure, "_asks_polar_class_question", old_gate)
    assert stem_answer_disclosure(
        FW_F06_LIVE_QUESTION, FW_F06_LIVE_OPTIONS, FW_F06_LIVE_MARKED) == "", (
        "s vraćenim starim uslovom paket MORA opet proći — inače novi uslov "
        "nije ono što ga zaustavlja")
