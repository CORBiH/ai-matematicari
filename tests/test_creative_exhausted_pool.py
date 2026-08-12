"""ISCRPLJEN BAZEN ARHETIPA: kad svježeg tipa nema, model se NE zove.

ODLUKA JE DONESENA NA ŽIVIM MJERENJIMA. Kampanja nad kandidatom c0bbf59
(14 kreativnih turnova, 25 poziva) razdvojila se savršeno po jednom pitanju —
je li ciljni arhetip bio svjež:

  • SVJEŽ cilj: 6 pokušaja, 4 stigla do recenzenta, 4 objavljena (100%),
    sva matematika tačna, nijedno odbijanje;
  • VEĆ VIĐEN cilj: 8 pokušaja, 4 stigla do recenzenta, i sva četiri nacrta
    bila su KOZMETIČKI PRESVUČENI zadaci. Recenzent je tri odbio, a četvrti
    pustio i on je OBJAVLJEN:

        objavljeno : „Jasna ima 96 naljepnica i pokloni 5/12 od toga.
                      Koliko naljepnica ostane nakon darivanja?“
        objavljeno : „Marko ima 180 naljepnica. Daje 7/12 od njih prijateljima.
                      Koliko naljepnica ostane nakon darivanja?“

    isti predmet, ISTA završna rečenica doslovno, isti niz koraka, isti traženi
    podatak — promijenjeni samo ime i brojevi.

ZAŠTO TO NIJE SAMO MODELOVA GREŠKA: `facts_failure` bira rješavač po ciljnom
arhetipu i traži tačno `wordfacts.REQUIRED_FACTS[arhetip]`. Svaki paket koji
prođe tu kapiju IMA istu strukturu zavisnosti, isti niz koraka i isti traženi
podatak — slobodni su samo brojevi i proza. „Isti arhetip, materijalno drugačiji
primjer“ u ovom IR-u dakle NE POSTOJI, pa recenzent nije mogao ni odgovoriti
tačno: pitanje nije bilo odgovorivo.

UGOVOR KOJI OVAJ FAJL ZAKLJUČAVA:
  eskalacija se dešava SAMO za svjež arhetip; kad ga nema, turn ide
  determinističkim generatorom — trenutno, tačno i s nula poziva. Presvučeni
  zadatak time postaje nemoguć po konstrukciji, a ne stvar modelove presude.
"""
import json

import pytest

from matbot.difficulty_level import MAX_LEVEL
from matbot.practice import run_practice_turn
from matbot.semantics import contracts as semantic_contracts
from matbot.session_store import SessionStore
from matbot.tutor import creative_escalation as esc
from tests.conftest import FakeLLM

PILOT_LESSON, GRADE = "6-04-015", 6
# Lekcije ISTE porodice koje NISU pilot — dokaz da se ništa nije proširilo.
NON_PILOT = (("6-03-010", 6), ("6-05-011", 6), ("8-04-016", 8))

HARDER = "Daj mi teži zadatak."
NEW = "Daj mi novi zadatak."
EASIER = "Daj mi lakši zadatak."
VARIETY = "Daj mi drugačiji tip zadatka."


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def turn(session_id, message, lesson=PILOT_LESSON, grade=GRADE):
    return {"session_id": session_id, "grade": grade, "selected_topic": lesson,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": "", "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}


def context_for(lesson=PILOT_LESSON):
    return type("C", (), {
        "topic_id": lesson,
        "semantic_contract": semantic_contracts.contract_for(lesson)})()


def supported_for(lesson=PILOT_LESSON):
    return esc._contract_archetypes(context_for(lesson))


def climb_to_max(store, fake, session_id, lesson=PILOT_LESSON):
    for message in ("Daj mi zadatak.", HARDER, HARDER):
        assert run_practice_turn(store, fake, turn(session_id, message, lesson)
                                 )["status"] == "ready"
    assert fake.call_count == 0
    return store.peek(session_id)


def seed_published(store, session_id, names, lesson=PILOT_LESSON):
    """Historija OBJAVA sa zadatim arhetipima (najstarije → najnovije)."""
    session = store.peek(session_id)
    session["recent_task_signatures"] = [
        {"lesson_id": lesson,
         "structured_signature": json.dumps({"operation_or_relation": name}),
         "structured_signature_hash": f"seed-{name}"}
        for name in names]
    store.save(session)


def exhaust(store, session_id, lesson=PILOT_LESSON):
    """Stanje u kojem SVJEŽEG tipa nema — tačno kao u živoj kampanji.

    Prozor objava je tri, a bazen ima četiri tipa, pa sama historija objava
    NIKAD ne isprazni bazen: uvijek ostane bar jedan tip koji učenik nije
    vidio u zadnja tri zadatka. Bazen se prazni tek kad se PALI POKUŠAJI
    (`recent_creative_targets`) poklope s ostatkom — a to je stanje u kojem je
    živa kampanja i proizvela presvučene zadatke."""
    supported = supported_for(lesson)
    seed_published(store, session_id, supported[1:], lesson)
    session = store.peek(session_id)
    session["recent_creative_targets"] = [
        {"lesson_id": lesson, "archetype": name} for name in supported[:1]]
    store.save(session)
    return supported


def attempt_names(session, lesson=PILOT_LESSON):
    return [r.get("archetype")
            for r in (session.get("recent_creative_targets") or [])
            if isinstance(r, dict) and r.get("lesson_id") == lesson]


def attempted_turn(store, fake, payload):
    """Turn kojem je model DOZVOLJEN, ali nacrt nije pripremljen.

    FakeLLM tada namjerno diže AssertionError — ovdje se mjeri samo je li
    poziv uopšte POKUŠAN, jer upravo to razlikuje svjež cilj od iscrpljenog
    bazena."""
    try:
        return run_practice_turn(store, fake, payload)
    except AssertionError:
        return {}


def transition_at_max():
    from matbot import difficulty_level
    return difficulty_level.transition(MAX_LEVEL, "harder")


# ---------------------------------------------------------------------------
# A) PLANER — svjež arhetip je JEDINI dozvoljen cilj
# ---------------------------------------------------------------------------

def test_a_exhausted_pool_produces_no_escalation_decision():
    supported = supported_for()
    session = {"difficulty_level": MAX_LEVEL,
               "recent_task_signatures": [
                   {"lesson_id": PILOT_LESSON,
                    "structured_signature": json.dumps(
                        {"operation_or_relation": name}),
                    "structured_signature_hash": f"h-{name}"}
                   for name in supported[1:]],
               "recent_creative_targets": [
                   {"lesson_id": PILOT_LESSON, "archetype": supported[0]}]}
    assert esc.decide(context_for(), session, "harder_task",
                      transition_at_max()) is None


def test_a_published_history_alone_never_exhausts_the_pool():
    """VAŽNO ZA DOSTUPNOST: prozor je tri, bazen četiri.

    Bez ijednog palog pokušaja uvijek postoji svjež tip, pa nova politika NE
    gasi kreativnu rutu u normalnoj upotrebi — zaustavlja je tek kad se
    pokušaji nagomilaju."""
    supported = supported_for()
    assert len(supported) > esc.RECENT_WINDOW
    published = list(supported)
    for _ in range(12):
        window = tuple(published[-esc.RECENT_WINDOW:])
        target = esc.select_target(supported, window)
        assert target, published[-6:]
        published.append(target)


def test_a_one_fresh_archetype_still_escalates():
    supported = supported_for()
    session = {"difficulty_level": MAX_LEVEL,
               "recent_task_signatures": [
                   {"lesson_id": PILOT_LESSON,
                    "structured_signature": json.dumps(
                        {"operation_or_relation": name}),
                    "structured_signature_hash": f"h-{name}"}
                   for name in supported[1:]]}
    decision = esc.decide(context_for(), session, "harder_task",
                          transition_at_max())
    assert decision is not None
    assert decision.target_archetype == supported[0]
    assert decision.target_archetype not in decision.recent_archetypes


def test_a_attempted_archetype_does_not_count_as_fresh():
    """Pokušan pa pao cilj NIJE svjež — inače bi se isti nacrt tražio iznova."""
    supported = supported_for()
    session = {"difficulty_level": MAX_LEVEL,
               "recent_task_signatures": [
                   {"lesson_id": PILOT_LESSON,
                    "structured_signature": json.dumps(
                        {"operation_or_relation": name}),
                    "structured_signature_hash": f"h-{name}"}
                   for name in supported[1:]],
               "recent_creative_targets": [
                   {"lesson_id": PILOT_LESSON, "archetype": supported[0]}]}
    assert esc.decide(context_for(), session, "harder_task",
                      transition_at_max()) is None


def test_a_live_hard09_pair_can_never_be_targeted_again():
    """ŽIVI REGRES: `fraction_remainder` je bio u prozoru, pa ipak izabran.

    Tačno stanje sesije A prije HARD09 — planer tada bira `fraction_remainder`
    i objavljen je presvučen zadatak. Sada takav cilj ne postoji."""
    published = ("fraction_of_quantity", "fraction_remainder",
                 "multi_fraction_remainder")
    attempted = ("fraction_of_fraction", "fraction_of_quantity")
    assert esc.select_target(supported_for(), published, attempted) == ""


# ---------------------------------------------------------------------------
# B) HARDER @ MAX — iscrpljen bazen ide DETERMINISTIČKI, bez ijednog poziva
# ---------------------------------------------------------------------------

def test_b_harder_at_max_with_exhausted_pool_is_zero_call(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "ex-harder")
    exhaust(store, "ex-harder")
    before_session = store.peek("ex-harder")
    before = before_session["current_task"]

    response = run_practice_turn(store, fake, turn("ex-harder", HARDER))

    assert response["status"] == "ready"
    assert fake.call_count == 0                      # NIJEDAN poziv
    session = store.peek("ex-harder")
    assert session["difficulty_level"] == MAX_LEVEL  # nivo se ne mijenja
    assert session["current_task"] != before         # ali zadatak jeste nov
    # Turn bez poziva NIJE pokušaj — historija pokušaja se ne dira.
    assert attempt_names(session) == attempt_names(before_session)


def test_b_student_is_told_this_is_already_the_highest_level(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "ex-intro")
    exhaust(store, "ex-intro")
    answer = run_practice_turn(store, fake, turn("ex-intro", HARDER))["answer"]
    # Postojeća, poštena serverska rečenica za maksimum — ne izmišlja se nova.
    from matbot.tutor.pipeline import INTRO_AT_HARDEST_LEVEL
    assert INTRO_AT_HARDEST_LEVEL in answer
    for leak in ("recenzent", "tutor", "model", "arhetip", "kreativn"):
        assert leak not in answer.lower()


def test_b_harder_at_max_with_a_fresh_archetype_still_reaches_the_model(universal):
    """Kontrola: eskalacija NIJE ugašena — sa svježim tipom se i dalje dešava."""
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "ex-fresh")
    seed_published(store, "ex-fresh", supported_for()[1:])
    decision = esc.decide(context_for(), store.peek("ex-fresh"), "harder_task",
                          transition_at_max())
    assert decision is not None
    assert decision.reason == esc.REASON_MAX_LEVEL_HARDER
    # Bez nacrta u redu čekanja turn pada sigurno, ali je model POKUŠAN —
    # tačno to razlikuje svjež cilj od iscrpljenog bazena.
    attempted_turn(store, fake, turn("ex-fresh", HARDER))
    assert fake.call_count >= 1


# ---------------------------------------------------------------------------
# C) IZRIČITA RAZNOLIKOST — ista politika, i BEZ pada na nezaštićen model-put
# ---------------------------------------------------------------------------

def test_c_variety_with_exhausted_pool_is_zero_call(universal):
    """Bez ove kapije „drugačiji tip“ bi pao na MODELSKI put bez bloka
    eskalacije: poziv bez cilja i bez provjere raznolikosti."""
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "ex-var")
    exhaust(store, "ex-var")
    before = store.peek("ex-var")["current_task"]

    response = run_practice_turn(store, fake, turn("ex-var", VARIETY))

    assert response["status"] == "ready"
    assert fake.call_count == 0
    session = store.peek("ex-var")
    assert session["current_task"] != before
    assert session["difficulty_level"] == MAX_LEVEL


def test_c_variety_with_a_fresh_archetype_still_escalates():
    supported = supported_for()
    session = {"difficulty_level": MAX_LEVEL, "current_task": "neki zadatak",
               "recent_task_signatures": [
                   {"lesson_id": PILOT_LESSON,
                    "structured_signature": json.dumps(
                        {"operation_or_relation": name}),
                    "structured_signature_hash": f"h-{name}"}
                   for name in supported[1:]]}
    decision = esc.decide(context_for(), session, "", None,
                          explicit_variety=True)
    assert decision is not None
    assert decision.reason == esc.REASON_EXPLICIT_VARIETY
    assert decision.target_archetype == supported[0]


def test_c_variety_on_a_non_pilot_lesson_is_untouched(universal):
    """Kapija je pilotska: 533 ostale lekcije zadržavaju zatečeni put."""
    for lesson, grade in NON_PILOT:
        store, fake = SessionStore(), FakeLLM()
        assert not esc.is_pilot_lesson(context_for(lesson))
        assert run_practice_turn(store, fake, turn("np", "Daj mi zadatak.",
                                                   lesson, grade)
                                 )["status"] == "ready"
        assert fake.call_count == 0                 # generator postoji i radi
        attempted_turn(store, fake, turn("np", VARIETY, lesson, grade))
        # Bez pilota nema eskalacije, pa ni determinističkog preusmjeravanja:
        # zahtjev za raznolikošću ide zatečenim (modelskim) putem.
        assert fake.call_count >= 1, lesson


# ---------------------------------------------------------------------------
# D) RUTIRANJE KOJE SE NE SMIJE POMJERITI
# ---------------------------------------------------------------------------

def test_d_new_at_max_stays_deterministic_and_zero_call(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "ctl-new")
    exhaust(store, "ctl-new")
    for _ in range(3):
        assert run_practice_turn(store, fake, turn("ctl-new", NEW)
                                 )["status"] == "ready"
    assert fake.call_count == 0
    assert store.peek("ctl-new")["difficulty_level"] == MAX_LEVEL


def test_d_easier_at_max_stays_deterministic(universal):
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "ctl-easier")
    exhaust(store, "ctl-easier")
    assert run_practice_turn(store, fake, turn("ctl-easier", EASIER)
                             )["status"] == "ready"
    assert fake.call_count == 0
    assert store.peek("ctl-easier")["difficulty_level"] == MAX_LEVEL - 1


def test_d_lower_levels_never_escalate(universal):
    """Ispod maksimuma „teže“ je i dalje čista deterministička ljestvica."""
    store, fake = SessionStore(), FakeLLM()
    assert run_practice_turn(store, fake, turn("ladder", "Daj mi zadatak.")
                             )["status"] == "ready"
    for expected in (2, 3):
        assert run_practice_turn(store, fake, turn("ladder", HARDER)
                                 )["status"] == "ready"
        assert store.peek("ladder")["difficulty_level"] == expected
    assert fake.call_count == 0


# ---------------------------------------------------------------------------
# E) HISTORIJE — dvije projekcije ostaju razdvojene
# ---------------------------------------------------------------------------

def test_e_zero_call_fallback_never_writes_attempt_history(universal):
    """Turn bez poziva nije POKUŠAJ — u historiju pokušaja ne smije ništa.

    Bazen se namjerno prazni PRIJE svakog turna: objavljen deterministički
    zadatak pomjera prozor i može ponovo osloboditi svjež tip, pa bi drugi turn
    inače legitimno otišao na model."""
    store, fake = SessionStore(), FakeLLM()
    climb_to_max(store, fake, "hist")

    for message in (HARDER, VARIETY):
        exhaust(store, "hist")
        before = store.peek("hist")
        attempts_before = attempt_names(before)
        published_before = len(before["recent_task_signatures"])

        assert run_practice_turn(store, fake, turn("hist", message)
                                 )["status"] == "ready", message
        assert fake.call_count == 0, message

        session = store.peek("hist")
        assert attempt_names(session) == attempts_before, message
        assert len(session["recent_task_signatures"]) == published_before + 1
