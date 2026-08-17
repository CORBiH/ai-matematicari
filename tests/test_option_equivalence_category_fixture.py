"""KATEGORIJSKA FIKSTURA ZA `option_equivalence` (Faza 0 → RIJEŠENO).

STATUS 2026-08-17: nalaz je RIJEŠEN. Sve ispod ostaje zapis kako je otkriven i
zašto Faza 0 nije smjela pogađati; testovi mehanizma sada tvrde ISPRAVLJENO
ponašanje. Okidač za rješavanje je bio drugi živi release gate
(`release_gate_grade7_rotating`, lekcija o podudarnosti trouglova — SSU), gdje
je isti mehanizam proglasio duplikatima kriterije `SUS` i `SSU`: tu dvosmislice
NEMA — to su dva različita kriterija — pa je mehanizam dokazano pogrešan, a ne
samo nedokaziv. Vidi tests/test_uppercase_label_option_equivalence.py.

Razrješenje ide u smjeru koji ovaj isti fajl već dokumentuje kao projektno
pravilo (`test_overline_segment_is_left_unproven_rather_than_guessed`,
`test_angle_names_are_not_collapsed_either`): kad se ne može DOKAZATI, kapija
ne tvrdi ekvivalenciju. Goli `$AB$`/`$BA$` je bio jedino mjesto koje je to
pravilo kršilo; sada se ponaša isto kao `\\overline{AB}` i `\\angle BAC`.


ZAŠTO POSTOJI. Živi FW-G02 (final40_c17538a, lekcija 6-09-001 „Uglovi“, poruka
učenika traži da prepozna KOJI KRAK polazi iz tjemena ugla $\\angle BAC$) pao
je zatvoreno na `semantically_duplicate_options … (symbolic_commutative)`:

    preflight nacrta : option IDs a and d (symbolic_commutative)
    konačan paket    : option IDs c and d (symbolic_commutative)

Recenzent je popravio prvi par i proizveo drugi. Arhitektonska dijagnostika je
offline reprodukovala mehanizam: `canonicalize_expression` bare token `AB` čita
kao implicitni proizvod $A\\cdot B$ i kanonikalizuje ga komutativno, pa je
`AB` = `BA`.

ZA ŠTA JE TO TAČNO, A ZA ŠTA NIJE:
  • DUŽ i PRAVA: $AB$ i $BA$ jesu isti objekat — komutativnost je ISPRAVNA;
  • POLUPRAVA (krak) i VEKTOR: $AB$ i $BA$ NISU isti objekat — komutativnost
    bi bila pogrešna, a razlikovanje ta dva zapisa je upravo ono što takav
    zadatak ispituje.

Goli zapis `$AB$` ne kaže KOJI je od ta dva objekta u pitanju, pa se pitanje ne
može riješiti gledanjem same opcije.

ŠTO SE OVDJE DOKAZUJE OFFLINE:
  1. projekat IMA jednoznačan zapis poluprave — `\\overrightarrow{AB}`, koji
     `geometrycheck._RAY_TOKEN` već koristi i o čijem SMJERU već rasuđuje
     (živi nalaz DISC-D005);
  2. `option_equivalence` taj zapis POŠTUJE: $\\overrightarrow{AB}$ i
     $\\overrightarrow{BA}$ nisu proglašeni istima;
  3. sudar se javlja ISKLJUČIVO kod gole dvoslovne matematičke opcije bez
     proze i bez usmjerenog zapisa.

KLASIFIKACIJA U FAZI 0 JE BILA: `UNRESOLVED_PENDING_PHASE4`.
Tačni tekstovi opcija iz FW-G02 nisu povratljivi (CLAUDE.md, pravilo 7:
artefakt nosi samo kod i ID-jeve opcija), pa se nije moglo dokazati koji je od
dva objekta Tutor mislio, a Faza 0 nije smjela pogađati.

RAZRJEŠENJE (`RESOLVED_UPPERCASE_LABEL_IS_ATOMIC`): ne odlučuje se KOJI je
objekat u pitanju — odlučuje se da tvrdnja nije dokaziva, pa se i ne iznosi.
Niz od dva ili više velikih slova je jedan atomski simbol (množenje se u ovom
projektu uvijek piše sa `\\cdot`), pa `AB` i `BA` više nisu „dokazano isti“.
Smjer promašaja je konzervativan i identičan onome koji ovaj fajl već brani za
`\\overline{AB}`: propušten duplikat, nikad lažna optužba. Doslovno identična
opcija i dalje pada na `duplicate_option_text`.
"""
from __future__ import annotations

import pytest

from matbot import geometrycheck, option_equivalence

# --- klasifikacija nalaza (rječnik zadatka Faze 0) -------------------------
CONFIRMED_DEFECT = "CONFIRMED_DEFECT"
EXPECTED_REPRESENTATION_RULE = "EXPECTED_REPRESENTATION_RULE"
UNRESOLVED_PENDING_PHASE4 = "UNRESOLVED_PENDING_PHASE4"
RESOLVED_UPPERCASE_LABEL_IS_ATOMIC = "RESOLVED_UPPERCASE_LABEL_IS_ATOMIC"

FW_G02_CLASSIFICATION = RESOLVED_UPPERCASE_LABEL_IS_ATOMIC

# Dijagnostika iz kampanje — SAMO kodovi i ID-jevi opcija, nikad sadržaj.
FW_G02_DRAFT_FINDING = "semantically_duplicate_options: option IDs a and d (symbolic_commutative)"
FW_G02_FINAL_FINDING = "semantically_duplicate_options: option IDs c and d (symbolic_commutative)"
FW_G02_TOPIC_ID = "6-09-001"


# ---------------------------------------------------------------------------
# 1. MEHANIZAM — goli niz velikih slova više NIJE implicitni proizvod
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first,second", (
    ("$AB$", "$BA$"),
    ("$AC$", "$CA$"),
    ("$BC$", "$CB$"),
))
def test_bare_two_letter_labels_are_no_longer_collapsed(first, second):
    """Bio je `symbolic_commutative` (mehanizam FW-G02); sada je nedokazano."""
    assert option_equivalence.options_are_equivalent(first, second) is False
    assert option_equivalence.classify_equivalence(first, second) is None


def test_distinct_bare_labels_are_not_confused():
    """Kapija nije „sve dvoslovno je isto“ — razlikuje stvarno različite parove."""
    assert option_equivalence.options_are_equivalent("$AB$", "$AC$") is False


# ---------------------------------------------------------------------------
# 2. PROJEKAT IMA JEDNOZNAČAN ZAPIS POLUPRAVE, I ON SE POŠTUJE
# ---------------------------------------------------------------------------

def test_the_repository_already_owns_a_directed_ray_notation():
    """`geometrycheck` čita polupravu isključivo kao \\overrightarrow{XY} i
    o njenom smjeru već sudi (tjeme prvo) — dakle zapis postoji i nosi smjer."""
    assert geometrycheck._RAY_TOKEN == r"\\overrightarrow\s*\{\s*([A-Z])\s*([A-Z])\s*\}"


def test_directed_ray_notation_is_never_collapsed_by_commutativity():
    assert option_equivalence.options_are_equivalent(
        "$\\overrightarrow{AB}$", "$\\overrightarrow{BA}$") is False
    assert option_equivalence.options_are_equivalent(
        "$\\overrightarrow{AB}$", "$\\overrightarrow{AC}$") is False


@pytest.mark.parametrize("first,second", (
    ("krak $AB$", "krak $BA$"),
    ("Poluprava $AB$", "Poluprava $BA$"),
))
def test_any_prose_around_the_label_also_stops_the_collapse(first, second):
    """Sudar je uzak: traži GOLU matematičku opciju bez ijedne riječi."""
    assert option_equivalence.options_are_equivalent(first, second) is False


def test_angle_names_are_not_collapsed_either():
    """$\\angle BAC$ i $\\angle CAB$ jesu isti ugao, ali kapija to ne TVRDI —
    „nepoznato“ nikad ne postaje „dokazano isto“ (ni „dokazano različito“)."""
    assert option_equivalence.options_are_equivalent(
        "$\\angle BAC$", "$\\angle CAB$") is False


def test_overline_segment_is_left_unproven_rather_than_guessed():
    """Za DUŽ bi $\\overline{AB}$ = $\\overline{BA}$ bilo tačno; kapija ipak ne
    tvrdi ekvivalenciju. To je konzervativan smjer promašaja (propušten
    duplikat), ne lažna optužba — isto pravilo kao svuda u `matbot/`."""
    assert option_equivalence.options_are_equivalent(
        "$\\overline{AB}$", "$\\overline{BA}$") is False


# ---------------------------------------------------------------------------
# 3. KLASIFIKACIJA NALAZA — izričita, bez pogađanja
# ---------------------------------------------------------------------------

def test_fw_g02_classification_is_resolved_without_guessing_the_object():
    """Riješeno bez odlučivanja KOJI je objekat: tvrdnja koja se ne može
    dokazati se više ne iznosi (isto pravilo kao za `\\overline{AB}`)."""
    assert FW_G02_CLASSIFICATION == RESOLVED_UPPERCASE_LABEL_IS_ATOMIC
    assert option_equivalence.options_are_equivalent("$AB$", "$BA$") is False


def test_the_campaign_diagnostics_carry_only_codes_and_option_ids():
    """Zašto se ne može razriješiti: artefakt po pravilu 7 ne nosi tekst opcija."""
    for finding in (FW_G02_DRAFT_FINDING, FW_G02_FINAL_FINDING):
        assert "symbolic_commutative" in finding
        assert "option IDs" in finding
        # Nijedna riječ zadatka/opcije nije u dijagnostici.
        for content_word in ("krak", "tjeme", "ugao", "prava", "$"):
            assert content_word not in finding


def test_the_reviewer_repair_moved_the_pair_instead_of_removing_it():
    """Nacrt je imao par (a, d); recenzentov konačan paket par (c, d)."""
    assert "a and d" in FW_G02_DRAFT_FINDING
    assert "c and d" in FW_G02_FINAL_FINDING
    assert FW_G02_DRAFT_FINDING != FW_G02_FINAL_FINDING
