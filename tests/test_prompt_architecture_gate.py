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

STANJE POSLIJE FAZE 1 — OBA NALAZA SU ZATVORENA U PROIZVODU:

  A) `_REVIEWER_CHECK_SEMANTICS_RULE` je uvezan TAČNO JEDNOM u
     `build_reviewer_instructions`, odmah uz `_REVIEWER_DECISION_RULE`.
     `KNOWN_UNSHIPPED_RULE_BLOCKS` je prazan, a `xfail` je uklonjen.

  B) Nadvladana rečenica iz 00bbd45 je uklonjena iz `_TARGET_LEVEL_RULE`;
     ostala su samo ograničenja koja se poklapaju s `GLOBAL_LEVEL1_MAX`.
     `KNOWN_LEVEL1_CONTRADICTIONS` je prazan, a `xfail` je uklonjen.

DETEKTORI SE NE SLABE. Oba mjerača i dalje rade nad STVARNO sastavljenim
promptom kroz svih 534 lekcije, pa svaki NOV mrtav blok ili NOVA kontradiktorna
numerička granica obara suitu odmah. Prazan zamrznut skup je jača tvrdnja od
ranijeg, ne slabija.

Faza 1 je uz to dodala zaštitu PORODICA PRAVILA (dno fajla): ne snima prompt
bajt za bajt — štiti samo to da važne semantičke porodice ne nestanu tiho.
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
    """Sve što `matbot/tutor/prompts.py` STVARNO pošalje, za svih 534 lekcije.

    ARHITEKTONSKA FAZA 2: modul od tada gradi TRI sistemska prompta, pa površina
    mora sadržavati i onaj TREĆI (`build_help_instructions`). Bez ovog reda bi
    kapija za blokove pravila POMOĆI tvrdila da su nedosežni — dakle mjerila bi
    lažni kvar — a što je gore, novi mrtav blok POMOĆI bi joj bio nevidljiv.
    Detektor se ovim ne slabi ni za jedan blok: skup mjerenih blokova je isti,
    samo je pošiljka izmjerena u cijelosti."""
    parts = []
    for grade, topic_id, _title, _oblast in LESSON_ROWS:
        context = lesson_context_module.build(grade, topic_id)
        if context is None:
            continue
        parts.append(tutor_prompts.build_tutor_instructions(context))
        parts.append(tutor_prompts.build_reviewer_instructions(context))
        parts.append(tutor_prompts.build_help_instructions(context))
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


# Poslije Faze 1: NIJEDAN blok pravila nije nedosežan. Prazan skup je najjača
# moguća tvrdnja — svaki NOV mrtav prompt blok obara suitu ODMAH.
KNOWN_UNSHIPPED_RULE_BLOCKS = {}


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
    """Pada čim se pojavi NOV blok pravila koji se ne šalje."""
    actual = _unreachable_rule_blocks()
    assert actual == KNOWN_UNSHIPPED_RULE_BLOCKS, (
        "Skup NEPOSLATIH blokova pravila se promijenio.\n"
        f"  izmjereno: {actual}\n"
        f"  zamrznuto: {KNOWN_UNSHIPPED_RULE_BLOCKS}\n"
        "Nov blok koji se ne šalje je isti kvar kao c7552b8 — uveži ga ili "
        "obriši; nikad ga ne ostavljaj napisanog a neposlatog."
    )


def test_every_prompt_rule_block_is_reachable():
    """Faza 1: bivši EXPECTED_PHASE1_FAILURE, sada obična invarijanta."""
    assert _unreachable_rule_blocks() == {}


def test_the_reviewer_check_semantics_rule_now_ships_exactly_once():
    """Provenijencija zatvorenog nalaza: gdje je i koliko puta blok stiže.

    Uvezan je JEDNOM, u recenzentov prompt — Tutor ga ne dobija, jer on ne
    donosi odluku niti popunjava `checks.*`."""
    block = tutor_prompts._REVIEWER_CHECK_SEMANTICS_RULE
    context = lesson_context_module.build(9, "9-02-006")
    reviewer = tutor_prompts.build_reviewer_instructions(context)
    assert "WHAT `checks.*` DESCRIBE" in block
    assert reviewer.count(block) == 1
    assert block not in tutor_prompts.build_tutor_instructions(context)


def test_the_reviewer_check_semantics_rule_ships_for_every_lesson():
    """Blok stoji u STABILNOM prefiksu, pa ne smije ovisiti o lekciji."""
    for grade, topic_id, _title, _oblast in LESSON_ROWS[::37]:
        context = lesson_context_module.build(grade, topic_id)
        if context is None:
            continue
        assert tutor_prompts._REVIEWER_CHECK_SEMANTICS_RULE in \
            tutor_prompts.build_reviewer_instructions(context), topic_id


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
               + "\n" + tutor_prompts.build_reviewer_instructions(context)
               + "\n" + tutor_prompts.build_help_instructions(context)).lower()
    return tuple(
        (phrase, field, claimed, GLOBAL_LEVEL1_MAX[field])
        for phrase, field, claimed in LEVEL1_NUMERIC_CLAIMS
        if phrase in shipped and claimed != GLOBAL_LEVEL1_MAX[field]
    )


# Poslije Faze 1: nijedna poslata fraza ne tvrdi granicu različitu od
# mjerodavne. Tabela fraza iznad se NE SMANJUJE — i dalje traži sve tri
# nadvladane formulacije, pa njihov povratak odmah obara suitu.
KNOWN_LEVEL1_CONTRADICTIONS = ()


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
    """Pada na SVAKU novu kontradiktornu numeričku granicu."""
    actual = _contradictory_level1_claims()
    assert actual == KNOWN_LEVEL1_CONTRADICTIONS, (
        "Skup kontradiktornih numeričkih tvrdnji o nivou 1 se promijenio.\n"
        f"  izmjereno (fraza, polje, tvrdi, mjerodavno): {actual}\n"
        f"  zamrznuto: {KNOWN_LEVEL1_CONTRADICTIONS}\n"
        "Nijedan poslati blok ne smije tvrditi granicu različitu od "
        "GLOBAL_LEVEL1_MAX — mjerodavan je server-renderovan blok cilja."
    )


def test_no_shipped_rule_block_contradicts_the_authoritative_level_bounds():
    """Faza 1: bivši EXPECTED_PHASE1_FAILURE, sada obična invarijanta."""
    assert _contradictory_level1_claims() == ()


def test_the_superseded_level_one_sentence_is_gone_and_the_correct_one_remains():
    """Provenijencija zatvorenog nalaza (Faza 1).

    Uklonjena su ISKLJUČIVO dva nadvladana ograničenja iz 00bbd45. Rečenica iz
    24d629f, koja se poklapa s `GLOBAL_LEVEL1_MAX`, ostaje netaknuta, kao i
    dva ograničenja iz 00bbd45 koja se s njim poklapaju."""
    rule = tutor_prompts._TARGET_LEVEL_RULE.lower()
    # uklonjeno (00bbd45, nadvladano)
    assert "one operation" not in rule
    assert "no representation change" not in rule
    # zadržano iz iste rečenice — poklapa se s mjerodavnim granicama
    assert "one reasoning step and one condition" in rule
    # zadržano (24d629f) — mjerodavna kvalitativna dozvola
    assert "up to two connected operations" in rule
    assert "one change of representation" in rule


def test_removing_the_superseded_sentence_kept_every_other_target_level_duty():
    """Ostatak bloka nosi jedinstvene odgovornosti koje se NE smiju izgubiti."""
    rule = tutor_prompts._TARGET_LEVEL_RULE
    assert "Never derive a multi-step result and label it Level 1" in rule
    assert "A lesson whose title is conceptual is still introduced directly" in rule
    assert "Level 2 is a bounded combination" in rule
    assert "Level 3 requires construction, proof or justification" in rule
    assert "A harder request moves ONE bounded step up" in rule
    assert "Overshooting the requested level is\nrejected exactly like undershooting it" in rule
    assert "`difficulty_evidence` must honestly describe the task you actually wrote" in rule


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


# ---------------------------------------------------------------------------
# D) OČUVANJE PORODICA PRAVILA (Faza 1)
# ---------------------------------------------------------------------------
# NIJE snimak prompta bajt za bajt: takav snimak bi zabranio i običnu popravku
# formulacije, pa bi ga prvo čišćenje obrisalo. Ovdje se štiti samo to da
# POJEDINA SEMANTIČKA PORODICA ne nestane tiho iz pošiljke.
#
# Svaki unos je (ime porodice, dokazna fraza, kome mora stići). Fraza je
# namjerno KRATKA i nosi pojam, ne stil — preformulisanje rečenice oko nje ne
# obara test, a brisanje cijele porodice obara.
#
# ZAŠTO POSTOJI: Faza 0 je izmjerila da je jedno pravilo (`c7552b8`) bilo
# napisano a nikad poslato ČETIRI DANA i kroz jednu kampanju, a da ga nijedan
# test nije tražio. Doseg (kapija A) dokazuje da blok POSTOJI u nekom promptu;
# ovo dokazuje da postoji u PRAVOM promptu.

TUTOR = "tutor"
REVIEWER = "reviewer"
BOTH = "both"
# Arhitektonska Faza 2: treći sistemski prompt (`build_help_instructions`) ide
# ISKLJUČIVO na turn pomoći koji je server deterministički prepoznao.
HELP = "help"

PROTECTED_RULE_FAMILIES = (
    # (porodica, dokazna fraza, primalac)
    ("reviewer_decision_consistency", "DECISION CONSISTENCY RULE", REVIEWER),
    ("reviewer_check_semantics", "WHAT `checks.*` DESCRIBE", REVIEWER),
    ("reviewer_lesson_fidelity", "WHAT `inside_lesson` MEANS", REVIEWER),
    ("reviewer_target_level", "TARGET LEVEL DECISION RULE", REVIEWER),
    ("reviewer_preflight_authority", "SERVER-DETECTED DRAFT ISSUES", REVIEWER),
    ("reviewer_independent_solve", "checks.independent_answer", REVIEWER),
    ("authoritative_difficulty_target", "ACTIVE DIFFICULTY TARGETS", BOTH),
    ("difficulty_counting_semantics", "HOW TO COUNT DIFFICULTY EVIDENCE", BOTH),
    ("solve_task_authoring", "KAD ZADATAK TRAŽI SKUP RJEŠENJA", BOTH),
    ("scaled_division_notation", "WHEN YOU SCALE BOTH SIDES OF A DIVISION", BOTH),
    ("intent_vocabulary", "ODREDI NAMJERU", TUTOR),
    ("field_contract", "PRAVILO POLJA", TUTOR),
    ("mcq_authoring", "KAD PRAVIŠ ZADATAK", TUTOR),
    ("structured_task_package", "STRUCTURED TASK PACKAGE", TUTOR),
    ("target_level_authoring", "TARGET DIFFICULTY LEVEL", TUTOR),
    ("starting_complexity", "POLAZNA SLOŽENOST", TUTOR),
    ("difficulty_direction", "KAD MIJENJAŠ TEŽINU", TUTOR),
    ("help_notation", "ZAPIS U `hint` I `worked_solution`", TUTOR),
    ("hint_ladder_guidance", "SLJEDEĆI HINT JE NIVO 1", TUTOR),
    ("lesson_identity", "KANONSKA LEKCIJA", TUTOR),
    ("shared_domain_safety", "DOMEN I SIGURNOST", BOTH),
    ("shared_language_terminology", "PRAVILA JEZIKA I TERMINOLOGIJE", BOTH),
    ("shared_math_notation", "PRAVILA MATEMATIČKOG ZAPISA", BOTH),
    ("shared_arithmetic_selfcheck", "OBAVEZNA SAMOPROVJERA RAČUNA", BOTH),
    # --- arhitektonska Faza 2: porodice prompta POMOĆI ---
    ("help_field_contract", "PRAVILO POLJA ZA TURN POMOĆI", HELP),
    ("help_ladder_computational", "LJESTVICA POMOĆI — RAČUNSKI ZADATAK", HELP),
    ("help_ladder_propositional", "LJESTVICA POMOĆI — PREPOZNAVANJE TVRDNJE", HELP),
    ("help_notation_in_help_prompt", "ZAPIS U `hint` I `worked_solution`", HELP),
    ("help_scaled_division", "WHEN YOU SCALE BOTH SIDES OF A DIVISION", HELP),
    ("help_lesson_identity", "KANONSKA LEKCIJA", HELP),
    ("help_ladder_level_guidance", "SLJEDEĆI HINT JE NIVO 1", HELP),
    ("help_language_terminology", "PRAVILA JEZIKA I TERMINOLOGIJE", HELP),
)


def _phase1_surfaces():
    """Stvarno sastavljen Tutor, Reviewer i Help prompt, uključujući ulazni blok.

    `_lesson_block`/`_state_block` idu u INPUT, ne u sistemske instrukcije, pa
    se porodice poput ljestvice nagovještaja moraju tražiti i tamo."""
    context = lesson_context_module.build(9, "9-02-006")
    session = {
        "current_task": "Neka prava $p$ siječe ravan $\\alpha$ u tački $A$.",
        "current_options": [{"id": "a", "text": "$1$"}, {"id": "b", "text": "$2$"}],
        "correct_option_id": "a",
        "expected_answer_summary": "$1$", "difficulty": "easy",
        "difficulty_level": 1, "hint_level": 0, "recent_tasks": [], "recent_turns": [],
    }
    tutor = (tutor_prompts.build_tutor_instructions(context) + "\n"
             + tutor_prompts.build_tutor_input(context, session, "Daj mi hint."))
    reviewer = (tutor_prompts.build_reviewer_instructions(context) + "\n"
                + tutor_prompts.build_reviewer_input(context, session, "Daj mi zadatak.",
                                                     "{}"))
    help_prompt = (tutor_prompts.build_help_instructions(context) + "\n"
                   + tutor_prompts.build_help_input(context, session, "Ne znam.",
                                                    "hint_request"))
    return tutor, reviewer, help_prompt


def test_every_protected_rule_family_still_reaches_its_intended_call():
    tutor, reviewer, help_prompt = _phase1_surfaces()
    missing = []
    for family, phrase, audience in PROTECTED_RULE_FAMILIES:
        if audience in (TUTOR, BOTH) and phrase not in tutor:
            missing.append(f"{family} → Tutor")
        if audience in (REVIEWER, BOTH) and phrase not in reviewer:
            missing.append(f"{family} → Reviewer")
        if audience == HELP and phrase not in help_prompt:
            missing.append(f"{family} → Help")
    assert not missing, (
        "Porodica pravila je nestala iz pošiljke: " + ", ".join(missing) +
        ". Preformulisanje je dozvoljeno; tiho brisanje cijele porodice nije. "
        "Ako je porodica namjerno uklonjena, obriši njen red i u izvještaju "
        "dokaži gdje njeno ponašanje sada živi."
    )


def test_the_protected_family_inventory_is_not_silently_shrunk():
    """Skidanje reda iz tabele mora biti svjesna izmjena, ne slučajna.

    Faza 2 dodaje OSAM porodica prompta pomoći (24 → 32). Nijedan zatečeni red
    nije uklonjen: pomoć je dobila vlastitu pošiljku, a ugovor izrade zadatka je
    ostao gdje je bio."""
    assert len(PROTECTED_RULE_FAMILIES) == 32
    assert len({family for family, _phrase, _audience in PROTECTED_RULE_FAMILIES}) == 32
    assert sum(1 for _f, _p, audience in PROTECTED_RULE_FAMILIES
               if audience == HELP) == 8


def test_protected_families_are_not_asserted_against_an_empty_surface():
    """Kapija je beskorisna ako su površine prazne — dokaži da nisu."""
    tutor, reviewer, help_prompt = _phase1_surfaces()
    assert len(tutor) > 15000 and len(reviewer) > 15000
    assert len(help_prompt) > 4000


def test_the_help_prompt_drops_the_task_authoring_contract():
    """FAZA 2 — MJERENJE OBLIKOVANJA PROMPTA.

    Turn pomoći je do sada dobijao DOSLOVNO ugovor izrade zadatka. Ovdje se
    zaključava da ga prompt pomoći više ne nosi, a da ga prompt izrade i dalje
    nosi u cijelosti — oblikovanje ne smije nikome ništa oduzeti."""
    context = lesson_context_module.build(9, "9-02-006")
    tutor = tutor_prompts.build_tutor_instructions(context)
    help_prompt = tutor_prompts.build_help_instructions(context)
    generation_only = (
        "STRUCTURED TASK PACKAGE",
        "TARGET DIFFICULTY LEVEL",
        "ACTIVE DIFFICULTY TARGETS",
        "POLAZNA SLOŽENOST",
        "KAD MIJENJAŠ TEŽINU",
        "KAD PRAVIŠ ZADATAK",
        "KAD ZADATAK TRAŽI SKUP RJEŠENJA",
        "ODREDI NAMJERU",
    )
    for phrase in generation_only:
        assert phrase in tutor, phrase
        assert phrase not in help_prompt, phrase


def test_the_help_prompt_is_materially_smaller_than_the_generation_prompt():
    """Oblikovanje mora biti MJERLJIVO, ne samo namjera.

    Izmjereno na lekciji 9-02-006: 19 634 → 11 024 znaka sistemskih instrukcija,
    dakle preko 8 500 znaka manje. Ostatak je dijeljeni blok pravila
    (`rules.build_shared_math_rules`), koji pomoć STVARNO treba — on nosi razred,
    domen, terminologiju i notaciju."""
    context = lesson_context_module.build(9, "9-02-006")
    tutor = tutor_prompts.build_tutor_instructions(context)
    help_prompt = tutor_prompts.build_help_instructions(context)
    assert len(tutor) - len(help_prompt) > 6000, (len(help_prompt), len(tutor))
    assert len(help_prompt) < len(tutor) * 0.60, (len(help_prompt), len(tutor))


def test_the_help_prompt_prefix_is_identical_for_every_lesson():
    """Stabilan prefiks (Workstream K): sadržaj po lekciji dolazi TEK na kraju."""
    marker = "TON: obraćaj se učeniku direktno"
    prefixes = set()
    for grade, topic_id, _title, _oblast in LESSON_ROWS[::37]:
        context = lesson_context_module.build(grade, topic_id)
        if context is None:
            continue
        shipped = tutor_prompts.build_help_instructions(context)
        assert marker in shipped, topic_id
        prefixes.add(shipped[:shipped.index(marker)])
    assert len(prefixes) == 1
