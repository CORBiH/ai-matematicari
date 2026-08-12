"""ROTACIJA CILJA i razdvojene historije (živi nalaz: 4/4 na isti arhetip).

Odbijen kreativni pokušaj ISPRAVNO ne mijenja historiju objava — učenik ga
nikad nije vidio. Ali planer je zato birao isti cilj iznova: četiri uzastopna
pokušaja u živoj kampanji ciljala su `multi_fraction_remainder`.

Rješenje NIJE zagaditi historiju objava, nego uvesti DRUGU, malu projekciju:
šta je generisanje nedavno POKUŠALO. Ova dva pojma se ovdje drže razdvojeno.
"""
import json

import pytest

from matbot.semantics import contracts as semantic_contracts
from matbot.session_store import SessionStore
from matbot.tutor import creative_escalation as esc

LESSON = "6-04-015"


def _supported():
    return tuple(dict(semantic_contracts.contract_for(LESSON)
                      .parameters)["creative_problem_types"])


SUPPORTED = _supported()


# ---------------------------------------------------------------------------
# §15 — planer uzima u obzir OBJE historije
# ---------------------------------------------------------------------------

def test_attempted_target_is_avoided_while_alternatives_exist():
    published = ("fraction_of_quantity", "fraction_remainder",
                 "fraction_of_fraction")
    first = esc.select_target(SUPPORTED, published, ())
    assert first == "multi_fraction_remainder"
    second = esc.select_target(SUPPORTED, published, (first,))
    assert second != first


def test_four_failed_attempts_do_not_repeat_a_target():
    """TAČAN živi scenario: četiri pokušaja, nijedna objava."""
    published = ("fraction_of_quantity", "fraction_remainder",
                 "fraction_of_fraction")
    attempted, targets = [], []
    for _ in range(4):
        target = esc.select_target(SUPPORTED, published, tuple(attempted))
        targets.append(target)
        attempted = (attempted + [target])[-esc.RECENT_TARGET_ATTEMPTS:]
    assert len(set(targets)) == 4, targets
    assert len(targets) == len(SUPPORTED)


def test_attempt_history_is_not_a_permanent_blacklist():
    """Kad ponestane svježih kandidata, pao cilj se smije vratiti."""
    published = ()
    attempted = tuple(SUPPORTED)          # sve pokušano
    assert esc.select_target(SUPPORTED, published, attempted) in SUPPORTED


def test_selection_without_attempts_is_byte_for_byte_the_old_rule():
    """Bez pokušaja ponašanje mora ostati zatečeno."""
    supported = ("fraction_of_quantity", "fraction_remainder")
    for recent, expected in [
            ((), "fraction_of_quantity"),
            (("fraction_of_quantity",), "fraction_remainder"),
            (("fraction_remainder", "fraction_of_quantity"), "fraction_remainder"),
            (("fraction_of_quantity", "fraction_remainder"), "fraction_of_quantity")]:
        assert esc.select_target(supported, recent) == expected
        assert esc.select_target(supported, recent, ()) == expected


# ---------------------------------------------------------------------------
# §16 — dvije projekcije, dva značenja
# ---------------------------------------------------------------------------

def test_attempt_projection_is_read_filtered_like_the_published_one():
    session = {"recent_creative_targets": [
        {"lesson_id": LESSON, "archetype": "fraction_remainder"},
        {"lesson_id": LESSON, "archetype": "izmišljeni tip"},
        {"lesson_id": "9-02-006", "archetype": "fraction_of_quantity"},
        {"lesson_id": LESSON, "archetype": ""},
        "nije rječnik",
    ]}
    assert esc.recent_target_attempts(session, LESSON, supported=SUPPORTED) == (
        "fraction_remainder",)


def test_attempt_projection_is_empty_for_a_fresh_session():
    assert esc.recent_target_attempts({}, LESSON, supported=SUPPORTED) == ()


def test_store_records_only_the_attempt_key():
    """Upis pokušaja ne smije prenijeti NIŠTA drugo iz palog turna."""
    store = SessionStore()
    session = store.load("s1", 6, LESSON, "T", "O", "practice")
    session["current_task"] = "objavljeni zadatak"
    store.save(session)

    mutated = store.peek("s1")
    mutated["current_task"] = "POLUPRIMIJENJENO STANJE"
    mutated["recent_task_signatures"] = [{"lesson_id": LESSON,
                                          "structured_signature": "{}"}]
    # Pozivalac NE prosljeđuje svoju sesiju — metoda radi nad pohranjenom.
    store.record_creative_target("s1", LESSON, "multi_fraction_remainder", 3)

    stored = store.peek("s1")
    assert stored["current_task"] == "objavljeni zadatak"      # nije procurilo
    assert stored["recent_task_signatures"] == []              # ni ovo
    assert stored["recent_creative_targets"] == [
        {"lesson_id": LESSON, "archetype": "multi_fraction_remainder"}]


def test_attempt_history_is_bounded():
    store = SessionStore()
    store.save(store.load("s2", 6, LESSON, "T", "O", "practice"))
    for index in range(10):
        store.record_creative_target("s2", LESSON, f"t{index}", 3)
    assert len(store.peek("s2")["recent_creative_targets"]) == 3


def test_recording_an_unknown_session_is_inert():
    store = SessionStore()
    store.record_creative_target("nema-me", LESSON, "fraction_remainder", 3)
    assert store.peek("nema-me") is None


def test_published_and_attempt_projections_never_share_storage():
    """Odbijen pokušaj ne smije nikad postati 'zadatak koji je učenik vidio'."""
    store = SessionStore()
    store.save(store.load("s3", 6, LESSON, "T", "O", "practice"))
    store.record_creative_target("s3", LESSON, "fraction_of_fraction", 3)
    session = store.peek("s3")
    assert esc.recent_archetypes(session, LESSON, supported=SUPPORTED) == ()
    assert esc.recent_target_attempts(session, LESSON, supported=SUPPORTED) == (
        "fraction_of_fraction",)


# ---------------------------------------------------------------------------
# §21 G/H — kroz STVARNI put: odbijeni pokušaji rotiraju cilj
# ---------------------------------------------------------------------------

def test_rejected_attempts_rotate_the_target_through_the_real_pipeline(
        monkeypatch):
    """Četiri uzastopna PADA ne smiju ciljati isti arhetip.

    Nacrt je namjerno neupotrebljiv (bez činjenica cilja), pa svaki turn pada
    na jednom pozivu — tačan oblik žive kampanje, samo bez modela."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    from matbot.practice import run_practice_turn
    from tests.conftest import (FakeLLM, make_difficulty_diagnostics,
                                make_task_payload, make_tutor_draft)

    def turn(session_id, message):
        return {"session_id": session_id, "grade": 6, "selected_topic": LESSON,
                "selected_oblast": "", "student_message": message, "intent": "",
                "difficulty_request": "", "interaction_phase": "",
                "last_tutor_task": "", "interaction_type": "student_question",
                "selected_option_id": "", "client_turn_id": ""}

    store, fake = SessionStore(), FakeLLM()
    for message in ("Daj mi zadatak.", "Daj mi teži zadatak.",
                    "Daj mi teži zadatak."):
        assert run_practice_turn(store, fake, turn("rot", message)
                                 )["status"] == "ready"
    assert fake.call_count == 0

    published_before = list(store.peek("rot")["recent_task_signatures"])
    targets = []
    for index in range(4):
        session = store.peek("rot")
        context = type("C", (), {
            "topic_id": LESSON,
            "semantic_contract": semantic_contracts.contract_for(LESSON)})()
        decision = esc.decide(
            context, session, "harder_task",
            type("T", (), {"boundary_reason": "at_maximum",
                           "target_level": 3})())
        assert decision is not None
        targets.append(decision.target_archetype)
        # Nacrt bez ciljnih činjenica → pada prije recenzenta; upravo to
        # produkcija bilježi kao POKUŠAJ.
        payload = make_task_payload(
            text=f"Neupotrebljiv nacrt {index}.",
            options=("$1$", "$2$", "$3$", "$4$"), correct_option_index=0,
            expected="$1$", solution="$1$", difficulty="hard")
        fake.queue(make_tutor_draft(
            intent="harder_task", reply="Evo zadatka.",
            lesson_focus="tekstualni zadaci s razlomcima",
            difficulty_diagnostics=make_difficulty_diagnostics(direction="higher"),
            new_task=payload))
        response = run_practice_turn(store, fake, turn("rot", "Daj mi teži zadatak."))
        assert "status" not in response                 # sigurna poruka

    assert len(set(targets)) == 4, targets
    session = store.peek("rot")
    # Historija OBJAVA netaknuta — učenik nijedan od tih nacrta nije vidio.
    assert session["recent_task_signatures"] == published_before
    # Historija POKUŠAJA je zabilježila (ograničeno).
    attempts = [record["archetype"]
                for record in session["recent_creative_targets"]]
    assert attempts == targets[-esc.RECENT_TARGET_ATTEMPTS:]


# ---------------------------------------------------------------------------
# ŽIVI NALAZ (finalna kampanja, turn 7): planer je iz „nepokušanih“ uzeo PRVI
# PO ENUMU i tako ponovo ciljao arhetip koji je učenik upravo vidio, pa je
# recenzent s pravom oborio raznolikost. Izbor sada nigdje ne zavisi od
# abecednog reda — samo od dvije postojeće historije.
# ---------------------------------------------------------------------------

LIVE_PUBLISHED = ("fraction_remainder", "fraction_of_fraction",
                  "multi_fraction_remainder")     # najstarije → najnovije
LIVE_ATTEMPTED = ("fraction_of_quantity",)


def test_live_turn7_picks_the_least_recently_published_unattempted():
    assert esc.select_target(SUPPORTED, LIVE_PUBLISHED, LIVE_ATTEMPTED) == \
        "fraction_remainder"


def test_live_turn7_does_not_repeat_the_just_seen_archetype():
    chosen = esc.select_target(SUPPORTED, LIVE_PUBLISHED, LIVE_ATTEMPTED)
    assert chosen != "fraction_of_fraction"
    assert chosen != LIVE_PUBLISHED[-1]


def test_selection_never_depends_on_enum_order():
    """Ista historija, PERMUTIRAN enum → isti cilj."""
    shuffled = tuple(reversed(SUPPORTED))
    assert (esc.select_target(SUPPORTED, LIVE_PUBLISHED, LIVE_ATTEMPTED)
            == esc.select_target(shuffled, LIVE_PUBLISHED, LIVE_ATTEMPTED))


def test_tier2_prefers_unattempted_over_least_recent_overall():
    """Nepokušani kandidat ima prednost i kad je viđen skorije od pokušanog."""
    published = ("fraction_of_quantity", "fraction_remainder",
                 "fraction_of_fraction")
    attempted = ("fraction_of_quantity",)
    chosen = esc.select_target(SUPPORTED, published, attempted)
    assert chosen not in attempted
    assert chosen == "multi_fraction_remainder"      # jedini potpuno svjež
