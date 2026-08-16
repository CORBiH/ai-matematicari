# -*- coding: utf-8 -*-
"""POSTOJI LI TROUGAO KOJI ZADATAK OPISUJE.

ŽIVI NALAZ (post-deploy proba izdanja `3128968`, kontrolni, 7. razred, oblast
„Ugao i trougao“, q5) — objavljen je zadatak koji traži obim trougla, a njegovi
ZADATI podaci ne mogu opisati nijedan trougao:

    $\\alpha=50^\\circ$, $\\beta=60^\\circ$, $a=6\\,\\text{cm}$, $b=7\\,\\text{cm}$
    $6/\\sin 50^\\circ = 7{,}832 \\neq 8{,}083 = 7/\\sin 60^\\circ$   (3,2 %)

Svi postojeći slojevi su ĆUTALI ISPRAVNO: notaciju sudi `geometrycheck`,
vrijednosti sude uski orakli, a modelovo rješenje ($6+7+8=21$) je aritmetički
tačno. Nijedan nije pitao postoji li sam objekat.

Ovi testovi drže dvije stvari istovremeno: nemoguć zadatak NE SMIJE proći, a
ispravan trougao NE SMIJE biti oboren.
"""
import pytest

from matbot import triangle_consistency

CODE = triangle_consistency.INCONSISTENT_CODE
fail = triangle_consistency.publication_failure


# ---------------------------------------------------------------------------
# A) ISTORIJSKI SLUČAJ
# ---------------------------------------------------------------------------

HISTORICAL = (
    "U trouglu $ABC$ dati su uglovi $\\alpha=50^\\circ$ i $\\beta=60^\\circ$, "
    "stranice $a=6\\,\\text{cm}$ i $b=7\\,\\text{cm}$. U trouglu $DEF$ dati su "
    "uglovi $\\delta=50^\\circ$ i $\\varepsilon=60^\\circ$, a stranica "
    "$f=8\\,\\text{cm}$ nalazi se između tih uglova. Koliki je obim trougla $DEF$?")


def test_a_historical_kk1_q5_is_rejected():
    assert fail(HISTORICAL) == CODE


def test_a_second_triangle_alone_is_not_blamed():
    """DEF ($\\delta$, $\\varepsilon$, $f$) NEMA nijedan naspramni par — sam za
    sebe je potpuno saglasan. Odbijanje dolazi isključivo od $ABC$."""
    only_def = ("U trouglu $DEF$ dati su uglovi $\\delta=50^\\circ$ i "
                "$\\varepsilon=60^\\circ$, a stranica $f=8\\,\\text{cm}$ nalazi se "
                "između tih uglova. Koliki je obim trougla $DEF$?")
    assert fail(only_def) == ""


# ---------------------------------------------------------------------------
# B–D) NASPRAMNI PAROVI (sinusna teorema, interno)
# ---------------------------------------------------------------------------

def test_b_thirty_sixty_with_root_three_is_consistent():
    """$a=5$, $b=5\\sqrt3$ uz $30^\\circ/60^\\circ$ je EGZAKTNO saglasno —
    i vrijednost s korijenom se mora pročitati tačno, ne kao $5$."""
    assert fail("U trouglu $ABC$ je $\\alpha=30^\\circ$, $\\beta=60^\\circ$, "
                "$a=5\\,\\text{cm}$ i $b=5\\sqrt{3}\\,\\text{cm}$. Kolika je "
                "stranica $c$?") == ""


def test_c_isosceles_right_angles_are_consistent():
    assert fail("U trouglu $ABC$ je $\\alpha=45^\\circ$, $\\beta=45^\\circ$, "
                "$a=6\\,\\text{cm}$ i $b=6\\,\\text{cm}$. Koliki je obim?") == ""


def test_d_equal_angles_with_unequal_sides_are_impossible():
    assert fail("U trouglu $ABC$ je $\\alpha=30^\\circ$, $\\beta=30^\\circ$, "
                "$a=5\\,\\text{cm}$ i $b=6\\,\\text{cm}$. Koliki je obim?") == CODE


# ---------------------------------------------------------------------------
# E–F) ZBIR UGLOVA
# ---------------------------------------------------------------------------

def test_e_angle_sum_180_is_consistent():
    assert fail("U trouglu $ABC$ su uglovi $\\alpha=50^\\circ$, "
                "$\\beta=60^\\circ$ i $\\gamma=70^\\circ$. Koji je to trougao?") == ""


def test_f_angle_sum_190_is_rejected():
    assert fail("U trouglu $ABC$ su uglovi $\\alpha=50^\\circ$, "
                "$\\beta=60^\\circ$ i $\\gamma=80^\\circ$. Koji je to trougao?") == CODE


@pytest.mark.parametrize("alpha", ["0", "180", "200"])
def test_f_degenerate_angle_is_rejected(alpha):
    assert fail(f"U trouglu $ABC$ je $\\alpha={alpha}^\\circ$, "
                "$\\beta=60^\\circ$ i $\\gamma=70^\\circ$.") == CODE


def test_f_two_angles_already_reaching_180_are_rejected():
    """Treći ugao bi morao biti $\\le 0$ — nemoguće i bez trećeg podatka."""
    assert fail("U trouglu $ABC$ je $\\alpha=120^\\circ$ i "
                "$\\beta=70^\\circ$. Kolika je mjera trećeg ugla?") == CODE


# ---------------------------------------------------------------------------
# G–H) NEJEDNAKOST TROUGLA
# ---------------------------------------------------------------------------

def test_g_sides_3_4_5_are_consistent():
    assert fail("Trougao ima stranice $a=3\\,\\text{cm}$, $b=4\\,\\text{cm}$ i "
                "$c=5\\,\\text{cm}$. Koliki je obim?") == ""


def test_h_sides_2_3_6_are_rejected():
    assert fail("Trougao ima stranice $a=2\\,\\text{cm}$, $b=3\\,\\text{cm}$ i "
                "$c=6\\,\\text{cm}$. Koliki je obim?") == CODE


def test_h_degenerate_sides_are_rejected():
    """$2+3=5$ daje duž, ne trougao."""
    assert fail("Trougao ima stranice $a=2\\,\\text{cm}$, $b=3\\,\\text{cm}$ i "
                "$c=5\\,\\text{cm}$.") == CODE


# ---------------------------------------------------------------------------
# I–J) NEPOTPUNO / NEDOKAZIVO → ĆUTANJE, NIKAD IZMIŠLJANJE
# ---------------------------------------------------------------------------

def test_i_single_pair_proves_nothing():
    assert fail("U trouglu $ABC$ je $\\alpha=50^\\circ$ i "
                "$a=6\\,\\text{cm}$. Kolika je stranica $b$?") == ""


def test_i_two_sides_only_proves_nothing():
    assert fail("U trouglu $ABC$ je $a=2\\,\\text{cm}$ i "
                "$b=3\\,\\text{cm}$. Kolika je stranica $c$?") == ""


def test_j_unreadable_value_abstains_instead_of_misreading():
    assert fail("U trouglu $ABC$ je $\\alpha=30^\\circ$, $\\beta=60^\\circ$, "
                "$a=x+1$ i $b=2x$.") == ""


def test_j_mixed_units_abstain():
    """$a=6\\,\\text{cm}$ i $b=7\\,\\text{m}$ nisu uporedivi bez pretvaranja."""
    assert fail("U trouglu $ABC$ je $\\alpha=50^\\circ$, $\\beta=60^\\circ$, "
                "$a=6\\,\\text{cm}$ i $b=7\\,\\text{m}$.") == ""


def test_j_subscripted_angle_is_not_the_same_angle():
    assert fail("U trouglu $ABC$ je $\\alpha_1=50^\\circ$, $\\beta_1=60^\\circ$, "
                "$a=6\\,\\text{cm}$ i $b=7\\,\\text{cm}$.") == ""


def test_j_exterior_angle_wording_abstains():
    assert fail("U trouglu $ABC$ vanjski ugao $\\alpha=50^\\circ$, "
                "$\\beta=60^\\circ$, $a=6\\,\\text{cm}$, $b=7\\,\\text{cm}$.") == ""


def test_j_approximate_data_abstains():
    assert fail("U trouglu $ABC$ je približno $\\alpha=50^\\circ$, "
                "$\\beta=60^\\circ$, $a=6\\,\\text{cm}$, $b=7\\,\\text{cm}$.") == ""


def test_j_non_triangle_text_never_engages():
    assert fail("Romb ima dijagonale $6\\,\\text{cm}$ i $8\\,\\text{cm}$. "
                "Koliki je obim?") == ""
    assert fail("Koliko je $\\frac{3}{8}+\\frac{2}{8}$?") == ""


def test_j_two_triangles_are_never_mixed():
    """Prvi trougao daje $(\\alpha,a)$, drugi $(\\beta,b)$ — poređenje preko
    granice bi bilo izmišljena korespondencija, pa se ne radi."""
    assert fail("U trouglu $ABC$ je $\\alpha=30^\\circ$ i $a=5\\,\\text{cm}$. "
                "U trouglu $KLM$ je $\\beta=60^\\circ$ i "
                "$b=50\\,\\text{cm}$.") == ""


# ---------------------------------------------------------------------------
# K–M) POSTOJEĆI ISPRAVNI SADRŽAJ OSTAJE NETAKNUT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stem", [
    "U trouglu $ABC$ tačke $M$ i $N$ su sredine stranica $AB$ i $AC$. Ako je "
    "$BC=14\\,\\text{cm}$, kolika je dužina srednje linije $MN$?",
    "Centar opisane kružnice trougla $ABC$ je tačka $O$. Ako je "
    "$OA=6\\,\\text{cm}$, kolika je dužina $OC$?",
    "Pravougli trougao ima katete dužina $3\\,\\text{cm}$ i $4\\,\\text{cm}$. "
    "Kolika je dužina visine spuštene iz pravog ugla na hipotenuzu?",
    "U trouglu se sve tri težišnice sijeku u jednoj tački. Koliko tačaka "
    "presjeka imaju težišnice tog trougla?",
    "Jednakokraki trougao ima krakove dužine $13\\,\\text{cm}$ i osnovicu "
    "dužine $10\\,\\text{cm}$. Kolika je visina na osnovicu?",
    "Trougao ima stranice dužine $6\\,\\text{cm}$, $8\\,\\text{cm}$ i "
    "$10\\,\\text{cm}$. Kolika je mjera njegovog najvećeg ugla?",
])
def test_k_existing_valid_triangle_questions_are_untouched(stem):
    assert fail(stem) == ""


def test_l_congruence_question_with_valid_redundant_data_is_untouched():
    assert fail("Trouglovi $ABC$ i $DEF$ podudarni su prema pravilu SSU tako da "
                "tjemenu $B$ odgovara tjeme $E$. Ako je $BC=8\\,\\text{cm}$, "
                "kolika je dužina odgovarajuće stranice $EF$?") == ""


def test_m_similarity_question_is_untouched_when_consistent():
    assert fail("Trouglovi $ABC$ i $DEF$ su slični. U trouglu $ABC$ je "
                "$\\alpha=50^\\circ$, $\\beta=60^\\circ$ i $\\gamma=70^\\circ$. "
                "Koliki je ugao $\\delta$?") == ""


# ---------------------------------------------------------------------------
# TOLERANCIJA: računska greška prolazi, 3,2 % ne
# ---------------------------------------------------------------------------

def test_floating_point_noise_does_not_reject():
    """Egzaktno saglasna vrijednost, zapisana punom preciznošću, mora proći:
    tolerancija pokriva RAČUNSKU grešku, a ne „dovoljno blizu geometriju“."""
    import math
    b = 6 * math.sin(math.radians(60)) / math.sin(math.radians(50))
    stem = ("U trouglu $ABC$ je $\\alpha=50^\\circ$, $\\beta=60^\\circ$, "
            f"$a=6\\,\\text{{cm}}$ i $b={b!r}\\,\\text{{cm}}$.")
    assert fail(stem) == ""


def test_materially_inconsistent_data_is_not_forgiven():
    assert fail("U trouglu $ABC$ je $\\alpha=50^\\circ$, $\\beta=60^\\circ$, "
                "$a=6\\,\\text{cm}$ i $b=7\\,\\text{cm}$.") == CODE
