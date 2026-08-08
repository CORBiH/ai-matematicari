"""Jedan server-vlasnički opis AKTIVNOG cilja težine za OBA prompta (F5J).

ZAŠTO POSTOJI (živa završna kapija na 9d52d0a, lekcija „Grafičko rješavanje
linearne jednačine“, svjež nivo 1): Tutor je napravio ispravan uvodni zadatak
($2x+1=5$) i iskreno prijavio steps=1/ops=1; Recenzent je ISTI zadatak
prebrojao kao steps=2/ops=2 pa ipak vratio `approve` — kontradikciju je server
ispravno odbio i turn je propao. Forenzika kroz sve kampanje broji 97 takvih
kontradikcija na nepovezanim lekcijama: recenzentu su pragovi i semantika
brojanja stizali samo kao proza, pa je „šta je jedan korak rezonovanja“
pogađao.

Zato od ove faze i Tutor i Recenzent dobijaju DOSLOVNO ISTI blok koji:
  1. navodi numeričke pragove aktivnog cilja — renderovan iz ISTIH konstanti
     koje čita deterministički validator (matbot/tutor/schema.py), pa se
     brojevi u promptu i brojevi u presudi ne mogu razići;
  2. definiše semantiku brojanja polja dokaza — izvedenu iz kalibrisanih
     živih testova (tests/test_difficulty_level_rubric.py): jedan direktan
     lanac „preuredi i izračunaj“ unutar jedne jednačine/formule je JEDAN
     korak rezonovanja i do dvije povezane operacije.

Server ostaje jedini autoritet: ovaj modul samo OPISUJE ono što validator
ionako deterministički sprovodi. Nijedan model ne bira ni profil, ni nivo,
ni pragove. Lekcijski-relativni profil (kad postoji) i dalje šalje svoj
data-autorski blok koji OVE globalne pragove izričito zamjenjuje.
"""
from matbot.tutor.schema import (GLOBAL_LEVEL1_MAX, GLOBAL_LEVEL2_FLOORS,
                                 GLOBAL_LEVEL2_MAX, GLOBAL_LEVEL3_FLOORS)

_FIELD_ORDER = ("reasoning_steps", "condition_count", "operation_count",
                "representation_change_count")


def _caps(values) -> str:
    return ", ".join(f"{name} <= {values[name]}" for name in _FIELD_ORDER)


def _floors(values) -> str:
    return " or ".join(f"{name} >= {values[name]}" for name in _FIELD_ORDER)


def evidence_semantics_block() -> str:
    """Autoritativna semantika brojanja — izvor: opisi polja šeme i
    kalibrisani živi testovi. IDENTIČAN tekst ide Tutoru i Recenzentu."""
    return (
        "HOW TO COUNT DIFFICULTY EVIDENCE (authoritative; the server judges "
        "with exactly these meanings):\n"
        "- reasoning_steps counts CONCEPTUAL SOLVING STAGES, not arithmetic "
        "moves. One direct application of a single rule, formula or equation "
        "— including rearranging it and computing the result — is ONE "
        "reasoning step even when it uses two elementary operations "
        "(calibrated example: solving $2x+1=5$ is steps=1, operations=2). A "
        "second step exists only when the student needs a NEW rule, a second "
        "relation, or an intermediate quantity derived from a previous "
        "result.\n"
        "- operation_count counts meaningful connected elementary "
        "operations inside those stages — never symbols, tokens, or checking "
        "the four options.\n"
        "- condition_count counts independent given conditions that must ALL "
        "be used; answer options are never conditions.\n"
        "- representation_change_count counts required changes of "
        "mathematical representation (text into an equation, fraction into a "
        "decimal, ...).\n"
        "- flags are true only when the STUDENT must explain, compare "
        "results, construct, prove, or combine distinct concepts — a worked "
        "solution the server stores is not a student explanation."
    )


def global_target_block() -> str:
    """Numerički pragovi globalne rubrike — iz ISTIH konstanti kao validator."""
    return (
        "ACTIVE DIFFICULTY TARGETS (server-owned; the server independently "
        "validates the returned difficulty evidence against the requested "
        "level and REJECTS the whole turn on violation):\n"
        f"- Level 1 caps: {_caps(GLOBAL_LEVEL1_MAX)}; no explanation, "
        "construction, proof, or combining of concepts. Comparison is "
        "allowed only while every numeric field stays at its minimum.\n"
        f"- Level 2 requires at least one of: {_floors(GLOBAL_LEVEL2_FLOORS)}, "
        "or a required explanation/comparison; caps: "
        f"{_caps(GLOBAL_LEVEL2_MAX)}; never construction or proof.\n"
        f"- Level 3 requires at least one of: {_floors(GLOBAL_LEVEL3_FLOORS)}, "
        "or required construction or proof.\n"
        "When a LESSON-RELATIVE DIFFICULTY PROFILE block is present for the "
        "selected lesson, ITS caps and floors replace these global ones — "
        "the counting semantics above stay the same."
    )


def shared_target_block() -> str:
    """Kompletan zajednički blok (pragovi + semantika brojanja)."""
    return global_target_block() + "\n\n" + evidence_semantics_block()
