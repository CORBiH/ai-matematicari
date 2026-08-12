"""Arhetipi razlomačkih tekstualnih zadataka moraju poštovati UGOVOR O TEŽINI.

ZAŠTO POSTOJI: proširenje enuma (`fraction_of_fraction`,
`multi_fraction_remainder`) riješilo je dostupnost na kreativnoj eskalaciji, ali
je deterministički generator birao arhetip RAVNOMJERNO iz cijelog enuma, bez
obzira na nivo. Mjereno nad 5000 sesija po nivou kroz stvarni put: 81,1%
zadataka NIVOA 1 bilo je višekoračno, a nivo 1 ugovorom znači „jedna direktna
relacija“.

Nijedan zatečeni test to nije uhvatio jer su se dokaz težine i nivo MEĐUSOBNO
slagali — deterministički dokaz se čitao iz tabele po nivou, ne iz stvarnog
grafa zadatka. Zato ovdje mjerimo STRUKTURU objavljenog potpisa
(`required_conditions`), a ne samo `difficulty_level`.
"""
import dataclasses
import json
from types import MappingProxyType, SimpleNamespace

import pytest

from matbot.deterministic import wordproblems
from matbot.practice import run_practice_turn
from matbot.semantics import contracts as semantic_contracts
from matbot.session_store import SessionStore
from matbot.tutor import creative_escalation
from tests.conftest import FakeLLM

LESSON = "6-04-015"
GRADE = 6
SESSIONS = 300

# Ugovorna granica dubine grafa po nivou, izvedena iz compiled `level_bounds`
# ove lekcije:
#   1 „jedna direktna relacija, mali brojevi“        → 1
#   2 „jedan dodatni korak (ostatak, kusur, …)“      → 2
#   3 „veće vrijednosti ili više povezanih relacija“ → bez gornje granice
MAX_DEPTH = {1: 1, 2: 2, 3: 99}


def turn(session_id, message):
    return {"session_id": session_id, "grade": GRADE, "selected_topic": LESSON,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": "", "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}


def contract():
    return semantic_contracts.contract_for(LESSON)


def published_structure(session):
    """(arhetip, dubina grafa) iz OBJAVLJENOG potpisa."""
    raw = (session.get("current_task_signature") or {}).get(
        "structured_signature")
    assert raw, "objavljen zadatak nema strukturisan potpis"
    data = json.loads(raw)
    return (data.get("operation_or_relation"),
            len(data.get("required_conditions") or ()))


def climb(store, fake, session_id, level):
    """Svježa sesija do traženog nivoa, isključivo kroz obične poruke."""
    assert run_practice_turn(store, fake, turn(session_id, "Daj mi zadatak.")
                             )["status"] == "ready"
    for _ in range(level - 1):
        assert run_practice_turn(
            store, fake, turn(session_id, "Daj mi teži zadatak.")
        )["status"] == "ready"
    session = store.peek(session_id)
    assert session.get("difficulty_level") == level
    return session


def sample(level, sessions=SESSIONS):
    store, fake = SessionStore(), FakeLLM()
    found = []
    for index in range(sessions):
        session = climb(store, fake, f"{level}-{index}", level)
        found.append(published_structure(session))
    assert fake.call_count == 0, "obična progresija mora biti nula-pozivna"
    return found


@pytest.fixture(autouse=True)
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


# ---------------------------------------------------------------------------
# §8/§9/§10 — obična deterministička izrada po nivou
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", (1, 2, 3))
def test_ordinary_generation_respects_the_level_depth_bound(level):
    """Nijedan nivo ne smije objaviti graf dublji od svoje granice."""
    breaches = [(archetype, depth) for archetype, depth in sample(level)
                if depth > MAX_DEPTH[level]]
    assert breaches == [], f"nivo {level} probio granicu: {breaches[:5]}"


def test_level_1_is_exactly_one_direct_relation():
    """Nivo 1 ne smije nositi VIŠEKORAČNI arhetip — ni novi ni zatečeni.

    Ovo je pao i prije proširenja enuma: `fraction_remainder` (dubina 2) činio
    je 60,1% zadataka nivoa 1. Granica je dakle popravljena, ne samo očuvana.
    """
    depths = {depth for _, depth in sample(1)}
    assert depths == {1}, depths


def test_level_2_allows_the_named_extra_step_but_not_more():
    """Nivo 2 nosi „jedan dodatni korak“ — ostatak nad ISTOM cjelinom."""
    found = sample(2)
    assert {archetype for archetype, _ in found} == {
        "fraction_of_quantity", "fraction_remainder"}
    assert max(depth for _, depth in found) == 2


def test_level_3_exposes_the_richer_new_archetypes():
    """Nivo 3 mora stvarno moći ponuditi obje nove strukture."""
    seen = {archetype for archetype, _ in sample(3)}
    assert {"fraction_of_fraction", "multi_fraction_remainder"} <= seen
    assert max(depth for _, depth in sample(3)) >= 3


# ---------------------------------------------------------------------------
# §15 — dokaz težine mora biti ISTINIT o strukturi, ne samo o nivou
# ---------------------------------------------------------------------------

def test_level_1_evidence_never_describes_a_multi_step_task():
    """Prije popravke je paket dubine 3 nosio dokaz „1 korak, 1 operacija“.

    Dokaz i nivo su se slagali jedno s drugim, a oba su bila u neskladu sa
    STVARNIM grafom — zbog toga curenje nije oborilo nijednu provjeru."""
    store, fake = SessionStore(), FakeLLM()
    for index in range(SESSIONS):
        session = climb(store, fake, f"ev-{index}", 1)
        _, depth = published_structure(session)
        evidence = session.get("current_task_difficulty_evidence") or {}
        assert evidence.get("reasoning_steps") == 1
        assert depth == 1, (
            "dokaz tvrdi jedan korak, a graf nosi "
            f"{depth} relacije")


# ---------------------------------------------------------------------------
# §5 — tri POJMA se ne smiju voditi jednim enumom
# ---------------------------------------------------------------------------

def test_level_pools_are_subsets_of_the_lesson_enum():
    parameters = contract().parameters
    supported = set(parameters["problem_types"])
    by_level = parameters["problem_types_by_level"]
    assert set(by_level) == {"1", "2", "3"}
    for level, pool in by_level.items():
        assert set(pool) <= supported, (level, pool)
        assert pool, level


def test_creative_pool_is_separate_from_the_level_pools():
    """Kreativni pool je NAMJERNO širi od nivoa 1 — inače bi popravka težine
    ponovo izgladnjela eskalaciju, tačno stanje zbog kojeg je enum širen."""
    parameters = contract().parameters
    creative = set(parameters["creative_problem_types"])
    assert creative <= set(parameters["problem_types"])
    assert {"fraction_of_fraction", "multi_fraction_remainder"} <= creative
    assert creative > set(parameters["problem_types_by_level"]["1"])


def test_creative_target_pool_ignores_the_level_pool():
    """Kroz UGOVOR u kojem se dva pool-a RAZLIKUJU.

    Nad stvarnim podacima nivo 3 i kreativni pool su danas jednaki, pa bi
    zamjena jednog drugim prošla nezapaženo. Ovdje se mjeri MEHANIZAM: kad
    ugovor suzi nivo 3, kreativni cilj to NE smije naslijediti — inače bi
    ispravka težine ponovo izgladnjela eskalaciju."""
    real = contract()
    narrowed = dataclasses.replace(real, parameters=MappingProxyType({
        **dict(real.parameters),
        "problem_types_by_level": MappingProxyType(
            {"1": ("fraction_of_quantity",), "2": ("fraction_of_quantity",),
             "3": ("fraction_of_quantity",)}),
    }))
    with semantic_contracts.override_contracts({LESSON: narrowed}):
        context = SimpleNamespace(topic_id=LESSON, semantic_contract=narrowed)
        offered = creative_escalation._contract_archetypes(context)
    assert set(offered) == set(real.parameters["creative_problem_types"])
    assert {"fraction_of_fraction", "multi_fraction_remainder"} <= set(offered)


def test_generator_pool_falls_back_to_the_full_enum():
    """Lekcija BEZ `problem_types_by_level` radi bajt za bajt kao ranije."""
    parameters = {"problem_types": ["fraction_of_quantity", "money_total"]}
    for level in (1, 2, 3):
        assert wordproblems.types_for_level(parameters, level) == (
            "fraction_of_quantity", "money_total")


def test_generator_pool_uses_the_level_entry_when_present():
    parameters = {
        "problem_types": ["fraction_of_quantity", "fraction_remainder"],
        "problem_types_by_level": {"1": ["fraction_of_quantity"],
                                   "2": ["fraction_remainder"]}}
    assert wordproblems.types_for_level(parameters, 1) == ("fraction_of_quantity",)
    assert wordproblems.types_for_level(parameters, 2) == ("fraction_remainder",)
    # Nivo bez unosa pada nazad na puni enum — nikad na prazan izbor.
    assert wordproblems.types_for_level(parameters, 3) == (
        "fraction_of_quantity", "fraction_remainder")


def test_other_lessons_of_the_family_keep_the_unconstrained_pool():
    """§19: nijedna druga lekcija porodice ne mijenja ponašanje."""
    changed = []
    family = []
    for lesson_id, entry in semantic_contracts.all_contracts().items():
        if entry.family_id != "structured_word_problem":
            continue
        family.append(lesson_id)
        if "problem_types_by_level" in entry.parameters and lesson_id != LESSON:
            changed.append(lesson_id)
    assert len(family) > 1, "porodica mora imati i druge lekcije da provjera znači nešto"
    assert changed == []
