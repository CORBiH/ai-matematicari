# -*- coding: utf-8 -*-
"""Deterministička provjera geometrijske notacije (matbot/geometrycheck.py).

Živi nalaz koji je iznudio ovaj modul (poziv 41 velike kampanje, 6. razred,
lekcija „Centar, poluprečnik/polumjer i prečnik/promjer“):

    „Krug ima prečnik $D=10\\,\\text{cm}$. Izračunaj obim kruga.“
    expected_answer: „$O=\\pi D=3,14\\cdot10=31,4\\,\\text{cm}$“

Račun je tačan, MathJax ispravan, opcije različite, porodica ispravna — svaki
postojeći validator ga propušta. Ali $R$ je prečnik, a $D$ je prostorna
dijagonala (matbot/geometry_rules.py:42-45). Zadatak je stigao do browsera.
"""
import pytest

from matbot import geometrycheck as gc
from matbot.geometry_rules import route_geometry_topic
from matbot.topics import lesson_info

CIRC = ("plane", ["krug"])
TRI = ("plane", ["trougao"])
RECT = ("plane", ["pravougaonik"])
SQ = ("plane", ["kvadrat"])
POLY = ("plane", ["mnogougao"])
KOCKA = ("solid", ["kocka"])
KVADAR = ("solid", ["kvadar"])
PIR = ("solid", ["piramida_4"])
PRIZ = ("solid", ["prizma"])
KUPA = ("solid", ["kupa"])
VALJAK = ("solid", ["valjak"])
LOPTA = ("solid", ["lopta"])


def issues(text, ctx, **kw):
    return gc.find_geometry_issues(text, ctx[0], ctx[1], **kw)


# ---------------------------------------------------------------------------
# 1-8: tačne regresije kritičnog zastoja (krug: prečnik/poluprečnik)
# ---------------------------------------------------------------------------

CRITICAL_TASK = "Krug ima prečnik $D=10\\,\\text{cm}$. Izračunaj obim kruga."
CRITICAL_EXPECTED = "$O=\\pi D=3,14\\cdot10=31,4\\,\\text{cm}$"


def test_1_exact_call41_task_rejected():
    assert gc.CIRCLE_DIAMETER_USES_D in issues(CRITICAL_TASK, CIRC)


def test_2_exact_call41_expected_answer_rejected():
    assert gc.CIRCLE_DIAMETER_USES_D in issues(CRITICAL_EXPECTED, CIRC)


def test_3_canonical_R_diameter_accepted():
    assert issues("Krug ima prečnik $R=10\\,\\text{cm}$.", CIRC) == []
    assert issues("$O=\\pi R=3,14\\cdot10=31,4\\,\\text{cm}$", CIRC) == []
    assert issues("$R=2r$", CIRC) == []
    assert issues("$O=2\\pi r$", CIRC) == []
    assert issues("$P=\\pi r^2$", CIRC) == []


def test_4_lowercase_d_as_diameter_rejected():
    assert gc.CIRCLE_DIAMETER_USES_LOWER_D in issues("prečnik je označen sa $d$", CIRC)
    assert gc.CIRCLE_DIAMETER_USES_LOWER_D in issues("$O=\\pi d$", CIRC)


def test_5_R_as_radius_rejected():
    assert gc.CIRCLE_RADIUS_USES_R in issues("poluprečnik $R=5\\,\\text{cm}$", CIRC)


def test_6_R_as_circumradius_rejected():
    assert gc.CIRCUMRADIUS_USES_R in issues("poluprečnik opisane kružnice je $R$", TRI)


def test_7_r_o_accepted():
    assert issues("$r_o$ je poluprečnik opisane kružnice", TRI) == []
    assert issues("$r_o=\\frac{a\\sqrt{3}}{3}$", TRI) == []


def test_8_r_u_accepted():
    assert issues("$r_u=\\frac{a}{2}$ je poluprečnik upisane kružnice", SQ) == []


def test_D_equals_2r_rejected_in_circle_context():
    assert gc.CIRCLE_DIAMETER_USES_D in issues("$D=2r$", CIRC)


# ---------------------------------------------------------------------------
# 9-13: regresija oznake površine ($S$ umjesto $P$)
# ---------------------------------------------------------------------------

def test_9_triangle_area_with_S_rejected():
    assert gc.PLANE_AREA_USES_S in issues("$S=\\frac{ah_a}{2}$", TRI)
    assert gc.PLANE_AREA_USES_S in issues("Površina trougla je $S=30\\,\\text{cm}^2$.", TRI)


def test_10_triangle_area_with_P_accepted():
    assert issues("$P=\\frac{ah_a}{2}$", TRI) == []


def test_11_rectangle_area_with_S_rejected():
    assert gc.PLANE_AREA_USES_S in issues("$S=ab$", RECT)


def test_12_S_as_point_or_set_label_not_rejected():
    assert issues("Tačka S je centar kružnice.", CIRC) == []
    assert issues("Neka je $S$ presjek dijagonala.", SQ) == []
    assert issues("$S_n=(n-2)\\cdot 180^\\circ$", POLY) == []
    assert issues("$S_v=360^\\circ$", POLY) == []


def test_13_lowercase_s_semiperimeter_valid():
    assert issues("$s=\\frac{a+b+c}{2}$, pa $P=\\sqrt{s(s-a)(s-b)(s-c)}$", TRI) == []


def test_perimeter_area_symbol_swap_rejected():
    assert gc.PLANE_PERIMETER_AREA_SYMBOL_SWAP in issues("Obim kvadrata je $P=4a$.", SQ)
    assert gc.PLANE_PERIMETER_AREA_SYMBOL_SWAP in issues("Površina kvadrata je $O=a^2$.", SQ)


def test_perimeter_and_area_in_one_sentence_not_falsely_rejected():
    assert issues("Površina je $P=ab$, a obim $O=2(a+b)$.", RECT) == []


# ---------------------------------------------------------------------------
# 14-21: dijagonale i tijela
# ---------------------------------------------------------------------------

def test_14_square_diagonal_d_passes():
    assert issues("$d=a\\sqrt{2}$", SQ) == []


def test_15_cube_face_diagonal_d_passes():
    assert issues("$d=a\\sqrt{2}$", KOCKA) == []


def test_16_cube_space_diagonal_D_passes():
    assert issues("$D=a\\sqrt{3}$", KOCKA) == []


def test_17_solid_using_d_as_space_diagonal_rejected():
    assert gc.SOLID_SPACE_DIAGONAL_USES_D in issues("$d=a\\sqrt{3}$", KOCKA)
    assert gc.SOLID_SPACE_DIAGONAL_USES_D in issues("prostorna dijagonala je $d$", KVADAR)


def test_18_prism_cuboid_d_and_D_remain_distinct():
    assert issues("$d=\\sqrt{a^2+b^2}$; $D=\\sqrt{a^2+b^2+c^2}$", KVADAR) == []
    assert gc.SOLID_FACE_DIAGONAL_USES_D in issues("dijagonala strane je $D$", KVADAR)
    assert gc.SOLID_FACE_DIAGONAL_USES_D in issues("$D=a\\sqrt{2}$", KOCKA)


def test_19_canonical_solid_symbols_unchanged_and_valid():
    text = "$P_{DP}=a^2\\sqrt{2}$, $P_{OP}=RH$, $O_B=4a$, $h_a$, $M=O_BH$, $V=BH$, $P=2B+M$"
    assert issues(text, PRIZ) == []
    # flatten ne smije oštetiti kanonske indekse
    flat = gc.flatten(text)
    for token in ("P_{DP}", "P_{OP}", "O_B", "h_a"):
        assert token in flat


def test_20_pyramid_apothem_and_lateral_edge_not_confused():
    assert issues("$h_a$ je apotema, a $s$ bočna ivica", PIR) == []
    assert gc.PYRAMID_APOTHEM_EDGE_CONFUSION in issues("apotema je $s$", PIR)
    assert gc.PYRAMID_APOTHEM_EDGE_CONFUSION in issues("bočna ivica je $h_a$", PIR)


def test_21_cone_s_remains_slant_height():
    assert issues("izvodnica kupe je $s$", KUPA) == []
    assert issues("$s^2=r^2+H^2$, pa $M=\\pi rs$", KUPA) == []


def test_sphere_pi_R_squared_valid_but_plane_circle_area_not():
    # $P=\pi R^2$ je ISPRAVNO za sferu ($R$ = prečnik), a POGREŠNO za krug.
    assert issues("$P=4\\pi r^2=\\pi R^2$", LOPTA) == []
    assert gc.GEOMETRY_FORMULA_SYMBOL_CONFLICT in issues("$P=\\pi R^2$", CIRC)
    assert gc.GEOMETRY_FORMULA_SYMBOL_CONFLICT in issues("$O=2\\pi R$", CIRC)


def test_solid_base_area_symbol_mismatch():
    assert gc.SOLID_BASE_AREA_SYMBOL_MISMATCH in issues("površina baze je $P=a^2$", PRIZ)
    assert issues("površina baze je $B=a^2$", PRIZ) == []


def test_cylinder_canonical_block_valid():
    assert issues("$R=2r$; $B=\\pi r^2$; $O_B=2\\pi r=\\pi R$; $V=\\pi r^2H$", VALJAK) == []


# ---------------------------------------------------------------------------
# Phase 4: zaštita od lažnih pozitiva
# ---------------------------------------------------------------------------

def test_point_labels_never_rejected():
    assert issues("Tačka D pripada kružnici.", CIRC) == []
    assert issues("Duž CD je tetiva kružnice.", CIRC) == []
    assert issues("Prečnik je duž $AD$.", CIRC) == []
    assert issues("Trougao $ABD$ je pravougli.", TRI) == []


def test_genitive_noun_between_quantity_and_symbol_is_caught():
    """Dry-run nalaz: „Prečnik baze je $D=6$“ je PROLAZIO jer je između riječi
    i simbola stajala imenica („baze“), a ne samo kopula."""
    assert gc.CIRCLE_DIAMETER_USES_D in issues("Prečnik baze je $D=6\\,\\text{cm}$.", VALJAK)
    assert gc.CIRCLE_DIAMETER_USES_D in issues("Prečnik kruga je $D=10$.", CIRC)
    assert gc.CIRCLE_RADIUS_USES_R in issues("Poluprečnik baze je $R=3$.", VALJAK)
    assert gc.PYRAMID_APOTHEM_EDGE_CONFUSION in issues("apotema piramide je $s$", PIR)
    assert issues("Prečnik baze je $R=6\\,\\text{cm}$.", VALJAK) == []


def test_symbol_as_point_label_after_quantity_word_not_falsely_rejected():
    """Granica prethodne popravke: veznici su ZATVOREN spisak, pa slobodna
    rečenica s tačkom $D$ ostaje ispravno neprijavljena."""
    assert issues("Izračunaj prečnik ako je tačka $D$ na kružnici.", CIRC) == []
    assert issues("Prečnik je najduža tetiva. Tačka D je na kružnici.", CIRC) == []


def test_empty_scope_never_checked():
    # statistika / algebra / učenikov tekst — routing daje prazan opseg
    assert issues("Kružni dijagram prikazuje podatke. $S=30$", ("", [])) == []
    assert issues("Krug ima prečnik $D=10$.", ("", [])) == []


def test_case_sensitivity_is_preserved():
    """Najvažnija Phase 4 garancija: $d$/$D$ i $S$/$s$ se NIKAD ne izjednačavaju."""
    assert issues("$d=a\\sqrt{2}$", KOCKA) == []       # dijagonala strane
    assert issues("$D=a\\sqrt{3}$", KOCKA) == []       # prostorna dijagonala
    assert issues("$d=a\\sqrt{3}$", KOCKA) != []       # zamijenjeno
    assert issues("$D=a\\sqrt{2}$", KOCKA) != []       # zamijenjeno


def test_distractor_role_is_never_checked():
    assert issues("$S=ab$", RECT, role=gc.ROLE_DISTRACTOR) == []
    assert issues(CRITICAL_TASK, CIRC, role=gc.ROLE_DISTRACTOR) == []


def test_allow_intentional_violation_policy():
    assert issues(CRITICAL_TASK, CIRC, policy=gc.POLICY_ALLOW_INTENTIONAL) == []
    assert issues(CRITICAL_TASK, CIRC, policy=gc.POLICY_CHECK) != []


# ---------------------------------------------------------------------------
# 36-40: routing kurikuluma
# ---------------------------------------------------------------------------

def test_36_topic_6_08_006_routes_to_circle_checks():
    info = lesson_info(6, "6-08-006")
    scope, figures = route_geometry_topic(info["oblast"], info["title"])
    assert scope == "plane" and "krug" in figures
    assert gc.CIRCLE_DIAMETER_USES_D in gc.find_geometry_issues(CRITICAL_TASK, scope, figures)


def test_37_square_rectangle_topics_route_to_diagonal_checks():
    for grade, topic in ((8, "8-04-004"), (8, "8-04-005")):
        info = lesson_info(grade, topic)
        scope, figures = route_geometry_topic(info["oblast"], info["title"])
        assert scope == "plane"
        assert gc.find_geometry_issues("$d=a\\sqrt{2}$", scope, figures) == []


def test_38_prism_cube_cuboid_topics_route_to_solid_checks():
    info = lesson_info(8, "8-05-016")  # Prostorna dijagonala pravilne četverostrane prizme
    scope, figures = route_geometry_topic(info["oblast"], info["title"])
    assert scope == "solid"
    assert gc.SOLID_SPACE_DIAGONAL_USES_D in gc.find_geometry_issues(
        "prostorna dijagonala je $d$", scope, figures)


def test_39_statistics_circular_diagram_excluded():
    for oblast, title in (("Podaci i vjerovatnoća", "Kružni dijagram"),
                          ("Mjerenje, mjerne jedinice i podaci",
                           "Čitanje podataka iz tabela i dijagrama")):
        scope, figures = route_geometry_topic(oblast, title)
        assert "krug" not in figures
        assert gc.find_geometry_issues("Prečnik $D=10$.", scope, figures) == []


def test_40_audit_all_534_lessons_for_unexpected_geometry_routing():
    """Nijedna lekcija ne smije dobiti provjere kruga/tijela bez razloga, i
    nijedna negeometrijska lekcija ne smije uopšte ući u geometrijski opseg."""
    from matbot.topics import _load

    circle_lessons, solid_lessons, checked = [], [], 0
    for grade, gd in _load()["grades"].items():
        for lesson in gd["lessons"]:
            checked += 1
            scope, figures = route_geometry_topic(lesson["oblast"], lesson["title"])
            if scope == "" and figures:
                pytest.fail(f"{lesson['id']}: figure bez opsega")
            if "krug" in figures:
                circle_lessons.append((lesson["id"], lesson["title"]))
            if scope == "solid":
                solid_lessons.append((lesson["id"], lesson["title"]))
    assert checked == 536
    # Nijedna statistička/dijagramska lekcija ne smije biti u krug skupu.
    for _id, title in circle_lessons:
        assert "dijagram" not in title.lower(), f"{_id} pogrešno rutiran na krug"
    # Nijedna algebarska „kvadrat …“ lekcija ne smije biti u solid skupu.
    for _id, title in solid_lessons:
        low = title.lower()
        assert not low.startswith("kvadrat racionalnog"), _id
    assert circle_lessons, "očekivane su lekcije o krugu"
    assert solid_lessons, "očekivane su lekcije o tijelima"


# ---------------------------------------------------------------------------
# Server-owned politika porodica (Phase 3)
# ---------------------------------------------------------------------------

INTENTIONAL_FAMILIES = (
    "detect_student_error", "detect_formula_error", "recognize_correct_statement",
    "verify_solution", "choose_correct_formula", "identify_next_step",
)


def test_issue_codes_are_internal_only():
    """Kodovi su interni — ne smiju se pojaviti u poruci koju vidi učenik."""
    from matbot.practice import SAFE_ERROR_MESSAGE

    for code in gc.ALL_ISSUE_CODES:
        assert code not in SAFE_ERROR_MESSAGE
