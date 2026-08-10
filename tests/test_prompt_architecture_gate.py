"""ARHITEKTONSKA KAPIJA NAD PROMPTOVIMA (Faza 0). ZERO poziva modela.

Dvije klase kvara koje nijedan postojeći test nije mogao vidjeti, obje
dokazane forenzikom nad `git log` i sastavljenim promptom:

  A) PRAVILO NAPISANO, ALI NIKAD POSLATO.
     `_REVIEWER_CHECK_SEMANTICS_RULE` je uveden commitom c7552b8 upravo da
     spriječi recenzentovu samokontradikciju (`correct` uz netačnu
     `checks.*`). Nikad nije uvezan ni u `build_reviewer_instructions` ni
     igdje drugdje — `grep` po cijelom repou vraća SAMO njegovu definiciju.
     Poruka tog commita ipak tvrdi da je „semantika sada ista na sva četiri
     mjesta: prompt (novo pravilo …)“. Klasa kvara koju je to propustilo je
     živa: FW-D04 (final40_c17538a) pao je kodom
     `odobreno uprkos oborenim provjerama: ['inside_lesson']`.

  B) NADVLADANO PRAVILO KOJE JE OSTALO U POŠILJCI.
     `_TARGET_LEVEL_RULE` istovremeno nosi „one condition, one operation, no
     representation change“ (00bbd45, 2026-08-04) i „Level 1 tolerates one
     change of representation and up to two connected operations“ (24d629f,
     2026-08-05). Druga rečenica je dodata DAN kasnije da popravi živi pad
     koji je izazvala prva; prva nikad nije uklonjena. Mjerodavan je
     `GLOBAL_LEVEL1_MAX` (operation_count <= 2, representation_change_count
     <= 1), koji `difficulty_evidence_errors` stvarno sprovodi — dakle model
     dobija prag STROŽI od presude, u istom pasusu s tačnim pragom.

ŠTA OVAJ FAJL RADI: zamrzava OBA nalaza. Inventarni testovi su ZELENI danas i
padaju čim se pojavi NOV mrtav blok ili NOVA kontradikcija. Sami invarijantni
testovi su `xfail(strict=True)` i označeni EXPECTED_PHASE1_FAILURE: kad Faza 1
popravi proizvod, oni XPASS-uju i suita pada — to je namjeran signal za
predaju, ne neobjašnjen crveni test.

FAZA 0 NE DIRA PROIZVOD. Nijedan prompt se ovdje ne mijenja.
"""
from __future__ import annotations

import ast
import itertools
import json
from pathlib import Path

import pytest

from matbot import prompts as legacy_prompts
from matbot import rules as shared_rules
from matbot.difficulty_target import shared_target_block
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.schema import GLOBAL_LEVEL1_MAX

ROOT = Path(__file__).resolve().parent.parent

# Prag „ovo je blok pravila, ne pomoćna konstanta“: višelinijski string na
# nivou modula koji je duži od 200 znakova. Kraći jednolinijski stringovi
# (kodovi, prefiksi, poruke) i brojevi se namjerno NE traže — kapija ne smije
# tražiti dosežnost od svake konstante, nego od onoga što očito postoji da bi
# bilo poslato modelu.
_RULE_BLOCK_MIN_CHARS = 200


def _rule_blocks(relative_path: str) -> dict:
    """Blokovi pravila jednog prompt modula: {ime konstante: tekst}."""
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    blocks = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue
        text = node.value.value
        if len(text) < _RULE_BLOCK_MIN_CHARS or "\n" not in text:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                blocks[target.id] = text
    return blocks


def _lesson_rows():
    """(grade, topic_id, title, oblast) za svih 534 lekcije."""
    topics = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    rows = []
    for grade in topics["grades"].values():
        for lesson in grade["lessons"]:
            rows.append((int(lesson["id"].split("-")[0]), lesson["id"],
                         lesson["title"], lesson.get("oblast", "")))
    return tuple(rows)


LESSON_ROWS = _lesson_rows()


def _tutor_prompt_surface() -> str:
    """Sve što `matbot/tutor/prompts.py` STVARNO pošalje, za svih 534 lekcije."""
    parts = []
    for grade, topic_id, _title, _oblast in LESSON_ROWS:
        context = lesson_context_module.build(grade, topic_id)
        if context is None:
            continue
        parts.append(tutor_prompts.build_tutor_instructions(context))
        parts.append(tutor_prompts.build_reviewer_instructions(context))
    return "\n".join(parts)


def _shared_rules_surface() -> str:
    parts = []
    for grade, _topic_id, title, oblast in LESSON_ROWS:
        for mode in ("practice", "explain", "quick"):
            parts.append(shared_rules.build_shared_math_rules(
                grade, title, oblast, mode=mode))
    return "\n".join(parts)


def _legacy_prompt_surface() -> str:
    parts = []
    # Explain/Quick grananje ovisi o zastavicama, ne o lekciji, pa je uzorak
    # lekcija dovoljan dok su sve zastavice pokrivene.
    for grade, _topic_id, title, oblast in LESSON_ROWS[:60]:
        parts.append(legacy_prompts.build_instructions(grade, title, oblast))
        for strong in (True, False):
            parts.append(legacy_prompts.build_explain_instructions(
                grade, title, oblast, lesson_context_strong=strong))
        for repair, image in itertools.product((False, True), (False, True)):
            parts.append(legacy_prompts.build_quick_instructions(
                grade, title, oblast, repair_intent=repair, image_present=image))
    return "\n".join(parts)


_SURFACES = {
    "matbot/tutor/prompts.py": _tutor_prompt_surface,
    "matbot/rules.py": _shared_rules_surface,
    "matbot/prompts.py": _legacy_prompt_surface,
}


def _unreachable_rule_blocks() -> dict:
    """{modul: (imena blokova koji se ne pojavljuju ni u jednom promptu)}."""
    unreachable = {}
    for relative_path, surface_builder in _SURFACES.items():
        surface = surface_builder()
        missing = tuple(sorted(
            name for name, text in _rule_blocks(relative_path).items()
            if text not in surface))
        if missing:
            unreachable[relative_path] = missing
    return unreachable


# Zatečeno stanje na kandidatu c17538a, dokazano gornjim mjeračem. Svaki NOV
# unos ovdje je nov mrtav prompt blok i mora oboriti suitu ODMAH.
KNOWN_UNSHIPPED_RULE_BLOCKS = {
    "matbot/tutor/prompts.py": ("_REVIEWER_CHECK_SEMANTICS_RULE",),
}


# ---------------------------------------------------------------------------
# A) DOSEŽNOST BLOKOVA PRAVILA
# ---------------------------------------------------------------------------

def test_rule_block_detector_sees_every_prompt_module():
    """Kapija je beskorisna ako ne vidi nijedan blok — zamrzni da ih vidi."""
    counts = {path: len(_rule_blocks(path)) for path in _SURFACES}
    assert counts["matbot/tutor/prompts.py"] >= 15, counts
    assert counts["matbot/rules.py"] >= 5, counts
    assert counts["matbot/prompts.py"] >= 3, counts


def test_no_new_unshipped_prompt_rule_block_appears():
    """ZELEN DANAS. Pada čim se pojavi NOV blok pravila koji se ne šalje.

    Pada i kad Faza 1 popravi zatečeni blok — tada je poruka uputa: ukloni
    ime iz `KNOWN_UNSHIPPED_RULE_BLOCKS` i obriši `xfail` ispod."""
    actual = _unreachable_rule_blocks()
    assert actual == KNOWN_UNSHIPPED_RULE_BLOCKS, (
        "Skup NEPOSLATIH blokova pravila se promijenio.\n"
        f"  izmjereno: {actual}\n"
        f"  zamrznuto: {KNOWN_UNSHIPPED_RULE_BLOCKS}\n"
        "Ako je Faza 1 uvezala _REVIEWER_CHECK_SEMANTICS_RULE: obriši taj unos "
        "iz KNOWN_UNSHIPPED_RULE_BLOCKS i ukloni xfail sa "
        "test_every_prompt_rule_block_is_reachable. Ako je dodat NOV blok koji "
        "se ne šalje: to je isti kvar kao c7552b8 — uveži ga ili obriši."
    )


def test_the_known_unshipped_block_is_exactly_the_reviewer_check_semantics_rule():
    """Provenijencija nalaza: ime, veličina i namjena mrtvog bloka."""
    blocks = _rule_blocks("matbot/tutor/prompts.py")
    dead = blocks["_REVIEWER_CHECK_SEMANTICS_RULE"]
    assert "WHAT `checks.*` DESCRIBE" in dead
    # Sadržaj je upravo pravilo koje bi FW-D04 klasu spriječilo.
    assert "that is the CORRECTED task, never the original draft" in dead
    assert dead not in tutor_prompts.build_reviewer_instructions(
        lesson_context_module.build(9, "9-02-006"))


@pytest.mark.xfail(strict=True, reason=(
    "EXPECTED_PHASE1_FAILURE — _REVIEWER_CHECK_SEMANTICS_RULE je definisan "
    "(c7552b8) a nikad uvezan u build_reviewer_instructions. Faza 0 ga samo "
    "mjeri; uvezivanje je Faza 1. Kad ovaj test XPASS-uje, Faza 1 je sletjela: "
    "ukloni xfail i ažuriraj KNOWN_UNSHIPPED_RULE_BLOCKS."))
def test_every_prompt_rule_block_is_reachable():
    assert _unreachable_rule_blocks() == {}


# ---------------------------------------------------------------------------
# B) KONTRADIKTORNE NUMERIČKE GRANICE NIVOA 1
# ---------------------------------------------------------------------------
# NAMJERNO NIJE opšte otkrivanje kontradikcija u prirodnom jeziku. Zatvorena
# tabela DOSLOVNIH fraza koje se stvarno šalju, svaka preslikana na polje
# dokaza težine i granicu koju ta fraza tvrdi. Mjerodavna vrijednost je
# `GLOBAL_LEVEL1_MAX` — ista konstanta iz koje se renderuje autoritativni blok
# i po kojoj `difficulty_evidence_errors` sudi.

LEVEL1_NUMERIC_CLAIMS = (
    ("one reasoning step", "reasoning_steps", 1),
    ("one condition", "condition_count", 1),
    ("one operation", "operation_count", 1),
    ("no representation change", "representation_change_count", 0),
    ("up to two connected operations", "operation_count", 2),
    ("one change of representation", "representation_change_count", 1),
)


def _contradictory_level1_claims() -> tuple:
    """Fraze prisutne u POŠILJCI koje tvrde granicu različitu od mjerodavne."""
    context = lesson_context_module.build(9, "9-02-006")
    shipped = (tutor_prompts.build_tutor_instructions(context)
               + "\n" + tutor_prompts.build_reviewer_instructions(context)).lower()
    return tuple(
        (phrase, field, claimed, GLOBAL_LEVEL1_MAX[field])
        for phrase, field, claimed in LEVEL1_NUMERIC_CLAIMS
        if phrase in shipped and claimed != GLOBAL_LEVEL1_MAX[field]
    )


# Zatečeno stanje na kandidatu c17538a. Obje kontradikcije žive u
# `_TARGET_LEVEL_RULE`, uz tačne rečenice u istom pasusu.
KNOWN_LEVEL1_CONTRADICTIONS = (
    ("one operation", "operation_count", 1, 2),
    ("no representation change", "representation_change_count", 0, 1),
)


def test_the_authoritative_difficulty_block_is_rendered_from_the_validator_constants():
    """Jedan izvor brojeva: blok cilja se renderuje iz ISTIH konstanti."""
    block = shared_target_block()
    for field, cap in GLOBAL_LEVEL1_MAX.items():
        assert f"{field} <= {cap}" in block, (field, cap, block)


def test_the_authoritative_difficulty_block_ships_verbatim_to_both_calls():
    context = lesson_context_module.build(9, "9-02-006")
    block = shared_target_block()
    assert block in tutor_prompts.build_tutor_instructions(context)
    assert block in tutor_prompts.build_reviewer_instructions(context)


def test_no_new_contradictory_level_one_numeric_claim_appears():
    """ZELEN DANAS. Pada na SVAKU novu kontradiktornu numeričku granicu."""
    actual = _contradictory_level1_claims()
    assert actual == KNOWN_LEVEL1_CONTRADICTIONS, (
        "Skup kontradiktornih numeričkih tvrdnji o nivou 1 se promijenio.\n"
        f"  izmjereno (fraza, polje, tvrdi, mjerodavno): {actual}\n"
        f"  zamrznuto: {KNOWN_LEVEL1_CONTRADICTIONS}\n"
        "Ako je Faza 1 uklonila nadvladanu rečenicu iz _TARGET_LEVEL_RULE: "
        "isprazni KNOWN_LEVEL1_CONTRADICTIONS i ukloni xfail sa "
        "test_no_shipped_rule_block_contradicts_the_authoritative_level_bounds."
    )


def test_both_known_contradictions_live_in_the_same_shipped_rule_block():
    """Provenijencija: 00bbd45 i 24d629f su u ISTOM pasusu, jedan uz drugi."""
    rule = tutor_prompts._TARGET_LEVEL_RULE.lower()
    assert "one operation, no representation change" in rule       # 00bbd45
    assert "up to two connected operations" in rule                # 24d629f
    assert "one change of representation" in rule                  # 24d629f


@pytest.mark.xfail(strict=True, reason=(
    "EXPECTED_PHASE1_FAILURE — _TARGET_LEVEL_RULE i dalje šalje nadvladanu "
    "rečenicu iz 00bbd45 („one operation, no representation change“) uz tačnu "
    "iz 24d629f. Mjerodavan je GLOBAL_LEVEL1_MAX (operation_count <= 2, "
    "representation_change_count <= 1). Uklanjanje nadvladane rečenice je Faza 1."))
def test_no_shipped_rule_block_contradicts_the_authoritative_level_bounds():
    assert _contradictory_level1_claims() == ()


# ---------------------------------------------------------------------------
# C) INVENTAR PONAVLJANJA — koliko puta se granica nivoa 1 uopšte iskazuje
# ---------------------------------------------------------------------------
# Ne tvrdi da je ponavljanje samo po sebi kvar. Zamrzava zatečen broj, pa
# SEDMO ponavljanje ne može ući nezapaženo.

LEVEL1_RESTATING_BLOCKS = ("_REVIEWER_PREFLIGHT_RULE", "_STRUCTURED_TASK_RULE",
                           "_TARGET_LEVEL_RULE")


def test_level_one_semantics_restatement_inventory_is_frozen():
    blocks = _rule_blocks("matbot/tutor/prompts.py")
    restating = tuple(sorted(
        name for name, text in blocks.items() if "level 1" in text.lower()))
    assert restating == tuple(sorted(LEVEL1_RESTATING_BLOCKS)), (
        "Broj blokova koji iznova iskazuju semantiku nivoa 1 se promijenio: "
        f"{restating}. Dodavanje još jednog iskaza istog pravila je upravo "
        "obrazac koji je proizveo kontradikciju 00bbd45/24d629f."
    )
