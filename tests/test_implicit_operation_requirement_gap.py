# -*- coding: utf-8 -*-
"""IMPLICITNI ZAHTJEV ZA ZABRANJENU OPERACIJU — zakljucana MJERENA praznina.

ZIVI NALAZ L5 (kampanja poslije izdanja 0a2f087): sestas je pitao „Pravougli
trougao ima katete 3 cm i 4 cm. Kolika je hipotenuza?" i dobio tacan odgovor
„hipotenuza iznosi 5 cm" uz urednu recenicu da se Pitagorina teorema uci
kasnije. Formula NIJE upotrijebljena, ali REZULTAT koji se bez nje ne moze
dobiti jeste objavljen — a to je treci nivo politike (pojam smije, operacija
ne smije, rezultat te operacije ne smije).

ZASTO OVDJE NEMA POPRAVKE, NEGO TESTOVA: da bi server ovo zaustavio PRIJE
poziva modela, mora dokazati tvrdnju „trazeni rezultat zahtijeva operaciju X".
Revizija je izmjerila da taj signal danas ne postoji ni u jednom
server-vlasnickom izvoru, i da bi svaka zamjena bila prozni mini-parser
(„ako pise kateta i hipotenuza — blokiraj"), sto je izricito odbijeno.

Ovi testovi zato ZAKLJUCAVAJU DOKAZE revizije. Ako neko kasnije doda
strukturni signal, ovi testovi su lista uslova koje mora ispuniti; ako neko
pokusa precicu heuristikom, testovi o prekomjernoj blokadi ce ga zaustaviti.
"""
import json
import re

import pytest

from matbot import capability_requests, practice_policy
from matbot.semantics import contracts as semantic_contracts
from matbot.semantics import detectors
from matbot.topics import lesson_info

ROOT_DATA = "data/topics.json"

L5_MESSAGE = "Pravougli trougao ima katete $3$ cm i $4$ cm. Kolika je hipotenuza?"
L4_MESSAGE = "Kvadrat ima povrsinu $20\\,\\text{cm}^2$. Kolika mu je stranica?"


def _policy(grade, lesson_id):
    info = lesson_info(grade, lesson_id)
    return practice_policy.resolve(
        grade=grade, lesson_id=lesson_id,
        lesson_title=info["title"], oblast=info["oblast"])


# ---------------------------------------------------------------------------
# DOKAZ 1 — semanticka porodica prati IZABRANU LEKCIJU, ne ZAHTJEV
# ---------------------------------------------------------------------------

def test_semantic_family_follows_the_lesson_not_the_request():
    """Ista poruka, tri razreda, tri razlicite porodice.

    Zato porodica NE MOZE biti signal o zahtjevu: kad bi se koristila, 8.
    razred (`pythagoras_direct`) bi se blokirao, a 6. razred
    (`angle_relationships_direct`) propustio — tacno obrnuto od potrebnog."""
    families = {}
    for grade, lesson_id in ((6, "6-09-003"), (7, "7-04-019"), (8, "8-04-001")):
        contract = semantic_contracts.contract_for(lesson_id)
        families[grade] = getattr(contract, "family_id", None)
    assert families[8] == "pythagoras_direct"
    assert families[6] != "pythagoras_direct"
    assert families[7] != "pythagoras_direct"
    # Razred koji SMIJE racunati je jedini kojem porodica imenuje operaciju.
    assert families[6] == families[7] == "angle_relationships_direct"


def test_no_registered_detector_can_recognise_a_pythagoras_request():
    """`kinds` iz `semantic_families.json` je ograniÄenje GENERISANJA
    („vidljivi zadatak mora pripadati vrstama: hypotenuse, ..."), a ne
    prepoznavac teksta. Registrovanog detektora nema."""
    assert "pythagoras_direct" not in detectors.DETECTORS
    assert "square_root" not in detectors.DETECTORS
    # Ono sto JESTE registrovano ne govori nista o zahtijevanoj operaciji.
    assert set(detectors.DETECTORS) == {
        "fraction_arithmetic", "polynomial_basic", "geometry_formula_2d",
        "solid_geometry_direct", "common_divisors_multiples",
    }


# ---------------------------------------------------------------------------
# DOKAZ 2 — kurikularni dokaz: 6. i 7. razred NEMAJU ovlascen put
# ---------------------------------------------------------------------------

def test_lower_grades_have_no_authorised_route_to_a_hypotenuse():
    """Kljucno pitanje nije „moze li odrastao rijesiti bez formule" nego
    „ima li razred OVLASCEN put do rezultata". Nema ga: nijedna lekcija 6. ni
    7. razreda ne imenuje Pitagorinu teoremu, katetu ni hipotenuzu."""
    payload = json.loads(open(ROOT_DATA, encoding="utf-8").read())
    lessons = {}

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("id"), str) and re.match(r"^\d-\d\d-\d\d\d$", node["id"]):
                lessons.setdefault(node["id"], node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    pattern = re.compile(r"pitagor|hipotenuz|kateta", re.IGNORECASE)
    for grade in ("6", "7"):
        hits = [lid for lid, lesson in lessons.items()
                if lid.startswith(grade + "-")
                and pattern.search(lesson.get("title", "") + " " + lesson.get("oblast", ""))]
        assert hits == [], (grade, hits)
    # 8. razred ih ima — granica je stvarna, ne pretpostavljena. Mjereno:
    # 16 lekcija po naslovu+oblasti (vlastita oblast „Pitagorina teorema i
    # primjene u ravni"), od toga 2 imenuju teoremu u samom naslovu.
    eight = [lid for lid, lesson in lessons.items()
             if lid.startswith("8-")
             and pattern.search(lesson.get("title", "") + " " + lesson.get("oblast", ""))]
    assert len(eight) == 16, len(eight)


# ---------------------------------------------------------------------------
# DOKAZ 3 — mjerena praznina: implicitni zahtjev NIJE presretnut
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("grade,lesson_id", ((6, "6-09-003"), (7, "7-04-019")))
def test_implicit_pythagoras_request_is_not_intercepted_today(grade, lesson_id):
    """NAMJERNO ZAKLJUCANA PRAZNINA (klasa `IMPLICIT_FORBIDDEN_OPERATION`).

    Ovo NIJE zeljeno ponasanje — to je izmjereno stanje. Kad stigne
    strukturni signal, ovaj test se mijenja u tvrdnju da JESTE presretnut, i
    tada mora pasti zajedno sa svojim parom ispod."""
    policy = _policy(grade, lesson_id)
    assert policy.pythagoras_operation_allowed is False
    assert capability_requests.named_capabilities(L5_MESSAGE) == ()
    assert capability_requests.forbidden_operation_requests(L5_MESSAGE, policy) == ()


def test_implicit_radical_request_is_not_intercepted_today():
    policy = _policy(6, "6-13-004")
    assert policy.radical_operation_allowed is False
    assert capability_requests.forbidden_operation_requests(L4_MESSAGE, policy) == ()


# ---------------------------------------------------------------------------
# DOKAZ 4 — zasto precica heuristikom nije dozvoljena (prekomjerna blokada)
# ---------------------------------------------------------------------------

def test_a_shape_based_radical_block_would_break_legitimate_grade_six_work():
    """„Kvadrat povrsine P -> stranica" NIJE po obliku zabranjen u 6. razredu.

    P=16 -> a=4 je MNOZENJE („koji broj pomnozen sam sa sobom daje 16"), puno
    gradivo 6. razreda; tek P=20 trazi korjenovanje. Zahtjev dakle ovisi o
    VRIJEDNOSTI, ne o obliku, pa bi blokada po obliku oborila legitiman
    zadatak. Zato korijen NIJE simetrican s Pitagorom i zato se ovdje ne
    uvodi zajednicka „oblik -> operacija" precica."""
    policy = _policy(6, "6-13-004")
    legitimate = "Kvadrat ima povrsinu $16\\,\\text{cm}^2$. Kolika mu je stranica?"
    assert capability_requests.forbidden_operation_requests(legitimate, policy) == ()
    # Isti OBLIK kao zabranjeni slucaj — razlikuje ih samo vrijednost.
    assert legitimate.replace("16", "20") == L4_MESSAGE.replace("povrsinu", "povrsinu")


def test_pythagoras_requirement_is_a_property_of_shape_not_value():
    """Suprotno korijenu: kod Pitagore ni „lijepa" trojka ne daje razredu put.

    3-4-5 je egzaktno, ali 6. razred do te 5 nema nijednu ovlascenu metodu —
    odrasla „prepoznata trojka" je pamcenje posljedice teoreme. Zato bi za
    Pitagoru blokada po obliku BILA ispravna; nedostaje samo prepoznavac."""
    assert _policy(6, "6-09-003").pythagoras_operation_allowed is False
    assert _policy(8, "8-04-001").pythagoras_operation_allowed is True


# ---------------------------------------------------------------------------
# DOKAZ 5 — postojeca zastita ostaje netaknuta
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", (
    "Koliki je kvadratni korijen broja 49?",
    "Izracunaj $\\sqrt{25}$.",
    "Koliko je $\\sqrt{36}$?",
))
def test_explicit_preflight_is_untouched_by_this_audit(message):
    policy = _policy(6, "6-08-004")
    assert capability_requests.forbidden_operation_requests(message, policy) == \
        (capability_requests.CAPABILITY_RADICAL,)


@pytest.mark.parametrize("message", (
    "Koja stranica je hipotenuza?",
    "Sta je pravougli trougao?",
    "Kada se uci Pitagorina teorema?",
))
def test_concept_questions_stay_answerable_at_every_grade(message):
    """Pojam SMIJE biti razgovaran — prvi nivo politike ostaje otvoren."""
    for grade, lesson_id in ((6, "6-09-003"), (7, "7-04-019")):
        policy = _policy(grade, lesson_id)
        assert capability_requests.forbidden_operation_requests(message, policy) == (), \
            (grade, message)


def test_grade_eight_is_never_intercepted_for_either_operation():
    policy = _policy(8, "8-04-001")
    for message in (L5_MESSAGE, "Izracunaj $\\sqrt{169}$.", "Pojednostavi $\\sqrt{20}$."):
        assert capability_requests.forbidden_operation_requests(message, policy) == (), message
