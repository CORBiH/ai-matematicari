"""Testovi kanonskih geometrijskih oznaka i formula (matbot/geometry_rules.py).

Izvor istine su dva referentna dokumenta u reference/curriculum/geometry/.
Najvažnija konvencija koju ovi testovi zaključavaju:
    R = PREČNIK (R = 2r), r = poluprečnik,
    d/d_1/d_2 = dijagonale (NIKAD prečnik),
    r_u/r_o = poluprečnici upisane/opisane kružnice (NIKAD R).
"""
import json
import re
from pathlib import Path

from matbot import geometry_rules as gr, prompts
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.rules import build_shared_math_rules

ROOT = Path(__file__).resolve().parent.parent


def rules_for(oblast, lesson_title, mode="practice"):
    return gr.build_geometry_rules(oblast, lesson_title, mode=mode)


# ---------------------------------------------------------------------------
# Konvencija oznaka — R je prečnik, d je dijagonala
# ---------------------------------------------------------------------------

def test_R_is_defined_as_diameter_with_R_equals_2r():
    text = rules_for("Geometrijska tijela", "Zapremina valjka")
    assert "$R=2r$" in text.replace(" ", "")
    assert "PREČNIK" in text


def test_d_is_defined_as_diagonal_not_diameter():
    for oblast, lesson in [("Četverougao", "Dijagonale romba"),
                            ("Geometrijska tijela", "Dijagonala kvadra")]:
        text = rules_for(oblast, lesson)
        assert "dijagonal" in text.lower()
        assert "$d$ NIKAD ne znači prečnik" in text or "oznaka $d$ NIKAD ne znači prečnik" in text


def test_inscribed_and_circumscribed_radii_use_ru_and_ro():
    text = rules_for("Mnogougao", "Pravilan šestougao")
    assert "$r_u$" in text
    assert "$r_o$" in text
    assert "UPISANE" in text.upper()
    assert "OPISANE" in text.upper()


def test_R_is_never_defined_as_circumscribed_circle_radius():
    """Stara konvencija (R = poluprečnik opisane kružnice) ne smije postojati.

    Namjerno BEZ re.IGNORECASE: veliko $R$ i malo $r$ su različite veličine —
    „$r$ = poluprečnik“ je ispravno i ne smije oboriti ovu provjeru."""
    forbidden = re.compile(r"\$?R\$?\s*(?:=|je|označava)\s*poluprečnik")
    for figure_text in gr._FIGURE_RULES.values():
        assert not forbidden.search(figure_text)
    for symbols in (gr._PLANE_SYMBOLS, gr._SOLID_SYMBOLS):
        assert not forbidden.search(symbols)
        assert "$R$ NIKAD ne znači poluprečnik opisane kružnice" in symbols


def test_d_is_never_defined_as_diameter_anywhere():
    # Bez re.IGNORECASE iz istog razloga kao gore: $d$ i $D$ su različite oznake.
    forbidden = re.compile(r"\$?d\$?\s*(?:=|je|označava)\s*prečnik")
    for figure_text in gr._FIGURE_RULES.values():
        assert not forbidden.search(figure_text)
    for symbols in (gr._PLANE_SYMBOLS, gr._SOLID_SYMBOLS):
        assert not forbidden.search(symbols)


# ---------------------------------------------------------------------------
# Ravne figure: O = obim, P = površina
# ---------------------------------------------------------------------------

def test_plane_symbols_define_O_as_perimeter_and_P_as_area():
    text = rules_for("Trougao", "Obim i površina trougla")
    assert "$O$ = obim figure" in text
    assert "$P$ = površina figure" in text


def test_triangle_formulas_match_reference():
    text = rules_for("Trougao", "Površina trougla")
    assert "$O = a+b+c$" in text
    assert "$\\alpha+\\beta+\\gamma = 180^\\circ$" in text
    assert "\\frac{a \\cdot h_a}{2}" in text


def test_heron_formula_and_semiperimeter():
    text = rules_for("Trougao", "Heronova formula i površina trougla")
    assert "$s = \\frac{a+b+c}{2}$" in text
    assert "P = \\sqrt{s(s-a)(s-b)(s-c)}" in text
    assert "$s=\\frac{O}{2}$" in text  # poluobim iz zajedničkih oznaka


def test_right_triangle_pythagoras_and_euclid():
    text = rules_for("Pitagorina teorema", "Pravougli trougao i Pitagorina teorema")
    assert "$c^2 = a^2+b^2$" in text
    assert "$P = \\frac{ab}{2}$" in text
    assert "$h_c = \\frac{ab}{c}$" in text
    assert "$a^2 = cp$" in text
    assert "$r_o = \\frac{c}{2}$" in text


def test_equilateral_triangle_formulas():
    text = rules_for("Trougao", "Jednakostranični trougao")
    assert "$O = 3a$" in text
    assert "$P = \\frac{a^2\\sqrt{3}}{4}$" in text
    assert "$r_u = \\frac{a\\sqrt{3}}{6}$" in text
    assert "$r_o = \\frac{a\\sqrt{3}}{3}$" in text


def test_isosceles_triangle_formulas():
    text = rules_for("Trougao", "Jednakokraki trougao")
    assert "$O = a+2b$" in text
    assert "h_a = \\sqrt{b^2-\\left(\\frac{a}{2}\\right)^2}" in text


def test_rectangle_and_square_formulas():
    text = rules_for("Četverougao", "Pravougaonik i kvadrat")
    assert "$O = 2(a+b)$" in text and "$P = ab$" in text
    assert "$O = 4a$" in text
    assert "$P = a^2 = \\frac{d^2}{2}$" in text
    assert "$d = a\\sqrt{2}$" in text


def test_rhombus_formulas_use_diagonals():
    text = rules_for("Četverougao", "Površina romba")
    assert "$O = 4a$" in text
    assert "\\frac{d_1 d_2}{2}" in text


def test_trapezoid_formulas():
    text = rules_for("Četverougao", "Površina trapeza")
    assert "$m = \\frac{a+c}{2}$" in text
    assert "P = \\frac{(a+c)h}{2} = mh" in text


def test_deltoid_formulas():
    text = rules_for("Četverougao", "Deltoid")
    assert "$O = 2(a+b)$" in text
    assert "$P = \\frac{d_1 d_2}{2}$" in text


def test_parallelogram_formulas():
    text = rules_for("Četverougao", "Paralelogram")
    assert "$P = a \\cdot h_a = b \\cdot h_b$" in text
    assert "$d_1^2 + d_2^2 = 2(a^2+b^2)$" in text


def test_regular_polygon_formulas_and_angle_sums():
    text = rules_for("Mnogougao", "Pravilan mnogougao — obim i površina")
    assert "$O = na$" in text
    assert "P = \\frac{O \\cdot r_u}{2}" in text
    assert "S_n = (n-2) \\cdot 180^\\circ" in text
    assert "\\frac{n(n-3)}{2}" in text


def test_similarity_ratio_rules():
    text = rules_for("Sličnost", "Sličnost trouglova i koeficijent sličnosti")
    assert "\\frac{O_2}{O_1} = k" in text
    assert "\\frac{P_2}{P_1} = k^2" in text


# ---------------------------------------------------------------------------
# Tijela: B, O_B, M, P, V, H
# ---------------------------------------------------------------------------

def test_solid_symbols_define_all_required_letters():
    text = rules_for("Geometrijska tijela", "Zapremina prizme")
    assert "$B$ = površina jedne baze" in text
    assert "$O_B$ = obim baze" in text
    assert "$M$ = površina omotača" in text
    assert "$P$ = ukupna površina tijela" in text
    assert "$V$ = zapremina tijela" in text
    assert "$H$ = visina geometrijskog tijela" in text


def test_prism_general_formulas():
    text = rules_for("Geometrijska tijela", "Prizma — površina i zapremina")
    assert "$M = O_B \\cdot H$" in text
    assert "$P = 2B + M = 2B + O_B \\cdot H$" in text
    assert "$V = B \\cdot H$" in text
    assert "$M = O_BH$" in text and "$V = BH$" in text  # brzo pamćenje


def test_pyramid_general_formulas():
    text = rules_for("Geometrijska tijela", "Piramida — površina i zapremina")
    assert "M = \\frac{O_B \\cdot h_a}{2}" in text
    assert "V = \\frac{B \\cdot H}{3}" in text
    assert "$M = \\frac{O_Bh_a}{2}$" in text and "$V = \\frac{BH}{3}$" in text


def test_pyramid_distinguishes_apothem_from_lateral_edge():
    text = rules_for("Geometrijska tijela", "Piramida — apotema")
    assert "$h_a$" in text and "$s$" in text
    assert "ne miješaj" in text.lower()


def test_cube_formulas():
    text = rules_for("Geometrijska tijela", "Kocka")
    assert "$P = 6a^2$" in text
    assert "$V = a^3$" in text
    assert "$d = a\\sqrt{2}$" in text
    assert "$D = a\\sqrt{3}$" in text


def test_cuboid_formulas():
    text = rules_for("Geometrijska tijela", "Kvadar")
    assert "$P = 2(ab+ac+bc)$" in text
    assert "$V = abc$" in text
    assert "$D = \\sqrt{a^2+b^2+c^2}$" in text


def test_regular_quadrilateral_prism_formulas():
    text = rules_for("Geometrijska tijela", "Pravilna četvorostrana prizma")
    assert "$B = a^2$" in text and "$O_B = 4a$" in text
    assert "$V = a^2H$" in text


def test_regular_triangular_prism_formulas():
    text = rules_for("Geometrijska tijela", "Pravilna trostrana prizma")
    assert "$B = \\frac{a^2\\sqrt{3}}{4}$" in text
    assert "$O_B = 3a$" in text


def test_regular_hexagonal_prism_formulas():
    text = rules_for("Geometrijska tijela", "Pravilna šestostrana prizma")
    assert "$B = \\frac{3a^2\\sqrt{3}}{2}$" in text
    assert "$O_B = 6a$" in text
    assert "$r_o = a$" in text


def test_regular_quadrilateral_pyramid_formulas():
    text = rules_for("Geometrijska tijela", "Pravilna četvorostrana piramida")
    assert "$M = 2ah_a$" in text
    assert "$V = \\frac{a^2H}{3}$" in text
    assert "h_a^2 = H^2+\\left(\\frac{a}{2}\\right)^2" in text


def test_regular_triangular_pyramid_formulas():
    text = rules_for("Geometrijska tijela", "Pravilna trostrana piramida")
    assert "$V = \\frac{a^2\\sqrt{3} \\cdot H}{12}$" in text
    assert "$P_{OP} = \\frac{hH}{2}$" in text


def test_cylinder_formulas():
    text = rules_for("Geometrijska tijela", "Valjak")
    assert "$B = \\pi r^2$" in text
    assert "$M = 2\\pi rH = \\pi RH$" in text
    assert "$V = \\pi r^2H$" in text
    assert "$P_{OP} = RH$" in text


def test_cone_formulas():
    text = rules_for("Geometrijska tijela", "Kupa")
    assert "$M = \\pi rs$" in text
    assert "V = \\frac{\\pi r^2H}{3}" in text
    assert "$s^2 = r^2+H^2$" in text
    assert "P_{OP} = \\frac{RH}{2} = rH" in text


def test_sphere_formulas():
    text = rules_for("Geometrijska tijela", "Lopta i sfera")
    assert "$P = 4\\pi r^2 = \\pi R^2$" in text
    assert "V = \\frac{4}{3}\\pi r^3" in text


def test_diagonal_and_axial_section_symbols():
    text = rules_for("Geometrijska tijela", "Dijagonalni presjek prizme")
    assert "$P_{DP}$" in text
    assert "$P_{OP}$" in text
    assert "$D$, $D_1$ = prostorne dijagonale" in text


# ---------------------------------------------------------------------------
# Jedinice
# ---------------------------------------------------------------------------

def test_solid_units_include_volume_units():
    text = rules_for("Geometrijska tijela", "Zapremina kocke")
    assert "mm³, cm³, dm³, m³" in text
    assert "litar" in text


def test_plane_units_are_length_and_square_only():
    text = rules_for("Trougao", "Površina trougla")
    assert "mm², cm², dm², m²" in text
    assert "mm³" not in text


# ---------------------------------------------------------------------------
# Routing: ništa ne curi u nepovezane lekcije
# ---------------------------------------------------------------------------

def test_non_geometry_lesson_gets_no_geometry_block():
    for oblast, lesson in [
        ("Razlomci", "Proširivanje razlomaka"),
        ("Skupovi i skupovne operacije", "Unija skupova"),
        ("Cijeli brojevi", "Sabiranje cijelih brojeva"),
        ("Sistemi linearnih jednačina", "Metoda supstitucije"),
    ]:
        assert rules_for(oblast, lesson) == "", f"{oblast} / {lesson}"


def test_algebraic_square_is_not_the_square_figure():
    """Živi nalaz iz kurikuluma: „Kvadrat binoma“, „Razlika kvadrata“ i
    „Kvadrat racionalnog broja“ su algebra, ne četverougao."""
    for lesson in ["Kvadrat binoma", "Razlika kvadrata", "Kvadrat zbira i razlike",
                    "Kvadrat racionalnog broja", "Savršeni kvadrati i procjena"]:
        _, figures = gr.route_geometry_topic("Polinomi", lesson)
        assert "kvadrat" not in figures, lesson


def test_real_square_figure_lessons_still_route():
    for oblast, lesson in [("Četverougao, obim i površina", "Kvadrat - svojstva"),
                            ("Pitagorina teorema i primjene u ravni", "Dijagonala kvadrata")]:
        _, figures = gr.route_geometry_topic(oblast, lesson)
        assert "kvadrat" in figures, lesson


def test_quadratic_equation_lesson_is_not_geometry():
    assert rules_for("Kvadratne jednačine", "Rješavanje kvadratne jednačine") == ""


def test_prism_lesson_does_not_receive_pyramid_formulas():
    text = rules_for("Geometrijska tijela", "Zapremina prizme")
    assert "PIRAMIDA" not in text
    assert "KUPA" not in text


def test_triangle_lesson_does_not_receive_solid_formulas():
    text = rules_for("Trougao", "Površina trougla")
    assert "zapremina" not in text.lower()
    assert "$V$" not in text


def test_scope_is_never_mixed_between_plane_and_solid():
    """Lekcija o prizmi s kvadratnom osnovom mora ostati u konvenciji tijela —
    miješanje oznaka ravnih figura i tijela je izvor zabune oko $d$/$R$."""
    scope, figures = gr.route_geometry_topic("Geometrijska tijela", "Prizma sa kvadratnom osnovom")
    assert scope == "solid"
    assert "kvadrat" not in figures


def test_figure_blocks_are_capped():
    scope, figures = gr.route_geometry_topic(
        "Geometrija", "Trougao, kvadrat, romb, trapez, deltoid i paralelogram")
    assert len(figures) <= gr.MAX_FIGURE_BLOCKS


def test_geometry_lesson_without_named_figure_still_gets_symbols():
    text = rules_for("Geometrijska tijela", "Mjerne jedinice površine i zapremine")
    assert text
    assert "$V$ = zapremina tijela" in text


# ---------------------------------------------------------------------------
# Integracija u zajednička pravila i MathJax granica
# ---------------------------------------------------------------------------

def test_geometry_rules_reach_shared_math_rules():
    text = build_shared_math_rules(9, "Zapremina prizme", "Geometrijska tijela", mode="practice")
    assert "$M = O_B \\cdot H$" in text


def test_shared_rules_for_non_geometry_lesson_have_no_geometry():
    text = build_shared_math_rules(6, "Proširivanje razlomaka", "Razlomci", mode="practice")
    assert "GEOMETRIJSKE OZNAKE" not in text


def test_every_figure_block_survives_the_mathjax_safety_boundary():
    """Sve formule moraju proći postojeću granicu sigurnosti — nijedan blok ne
    smije sadržavati sirovi LaTeX izvan $...$ niti nebalansirane delimitere."""
    for figure_id, block in gr._FIGURE_RULES.items():
        cleaned, is_safe = sanitize_and_validate_math_text(block)
        assert is_safe, f"Blok '{figure_id}' ne prolazi math-safety granicu"
        assert cleaned.count("$") % 2 == 0, figure_id


def test_symbol_blocks_survive_the_mathjax_safety_boundary():
    for name, block in (("plane", gr._PLANE_SYMBOLS), ("solid", gr._SOLID_SYMBOLS)):
        _, is_safe = sanitize_and_validate_math_text(block)
        assert is_safe, f"Blok oznaka '{name}' ne prolazi math-safety granicu"


def test_no_double_dollar_display_math_anywhere():
    for block in list(gr._FIGURE_RULES.values()) + [gr._PLANE_SYMBOLS, gr._SOLID_SYMBOLS]:
        assert "$$" not in block


def test_no_visible_literal_newline_escape_in_blocks():
    for block in list(gr._FIGURE_RULES.values()) + [gr._PLANE_SYMBOLS, gr._SOLID_SYMBOLS]:
        assert "\\n" not in block.replace("\n", "")


def test_subscripts_are_always_inside_math_delimiters():
    """Sirovi underscore izvan $...$ bi se prikazao kao obični tekst."""
    from matbot.mathsafe import _outside_math_parts
    for block in list(gr._FIGURE_RULES.values()) + [gr._PLANE_SYMBOLS, gr._SOLID_SYMBOLS]:
        for part in _outside_math_parts(block):
            assert "_" not in part, f"Sirov underscore izvan matematike: {part[:80]!r}"


# ---------------------------------------------------------------------------
# Provjera nad stvarnim kurikulumom
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# A. Repository-wide audit: the FINAL COMBINED prompt (not just the isolated
# geometry_rules block) must never state both conventions at once. This is
# what actually reaches the model — build_instructions/build_explain_instructions/
# build_quick_instructions all splice in build_shared_math_rules().
# ---------------------------------------------------------------------------

# Representative topics spanning both scopes and multiple grades/modes.
_REPRESENTATIVE_GEOMETRY_TOPICS = [
    (6, "Skupovi tačaka, kružnica i krug", "Poluprečnik, prečnik i obim kruga"),
    (7, "Trougao", "Pravougli trougao i Pitagorina teorema"),
    (8, "Četverougao, obim i površina", "Površina romba"),
    (8, "Geometrijska tijela", "Pravilna četvorostrana piramida"),
    (9, "Geometrijska tijela", "Valjak — površina i zapremina"),
    (9, "Mnogougao", "Pravilan mnogougao — obim i površina"),
]

_OLD_CONVENTION_D_AS_DIAMETER = re.compile(r"\$?d\$?\s*(?:=|je|označava)\s*prečnik")
_OLD_CONVENTION_R_AS_CIRCUMSCRIBED = re.compile(r"\$?R\$?\s*(?:=|je|označava)\s*poluprečnik")


def test_full_practice_instructions_have_no_contradictory_geometry_symbols():
    for grade, oblast, lesson in _REPRESENTATIVE_GEOMETRY_TOPICS:
        # Stari Practice graditelj je povucen; zajednicki blok je isti izvor
        # koji aktivni tutor prompt splajsa.
        from matbot.rules import build_shared_math_rules
        full = build_shared_math_rules(grade, lesson, oblast, mode="practice")
        assert not _OLD_CONVENTION_D_AS_DIAMETER.search(full), (oblast, lesson)
        assert not _OLD_CONVENTION_R_AS_CIRCUMSCRIBED.search(full), (oblast, lesson)
        assert "$R=2r$" in full.replace(" ", "")


def test_full_explain_instructions_have_no_contradictory_geometry_symbols():
    for grade, oblast, lesson in _REPRESENTATIVE_GEOMETRY_TOPICS:
        full = prompts.build_explain_instructions(grade, lesson_title=lesson, oblast=oblast)
        assert not _OLD_CONVENTION_D_AS_DIAMETER.search(full), (oblast, lesson)
        assert not _OLD_CONVENTION_R_AS_CIRCUMSCRIBED.search(full), (oblast, lesson)


def test_full_quick_instructions_have_no_contradictory_geometry_symbols():
    for grade, oblast, lesson in _REPRESENTATIVE_GEOMETRY_TOPICS:
        full = prompts.build_quick_instructions(grade, lesson_title=lesson, oblast=oblast)
        assert not _OLD_CONVENTION_D_AS_DIAMETER.search(full), (oblast, lesson)
        assert not _OLD_CONVENTION_R_AS_CIRCUMSCRIBED.search(full), (oblast, lesson)


def test_combined_prompt_defines_R_as_diameter_exactly_once_per_scope():
    """The full prompt must state R's meaning consistently — not once as
    diameter and again elsewhere as circumscribed radius."""
    for grade, oblast, lesson in _REPRESENTATIVE_GEOMETRY_TOPICS:
        # Stari Practice graditelj je povucen; zajednicki blok je isti izvor
        # koji aktivni tutor prompt splajsa.
        from matbot.rules import build_shared_math_rules
        full = build_shared_math_rules(grade, lesson, oblast, mode="practice")
        # r_o (circumscribed radius) must be the ONLY place that mentions
        # "opisane kružnice" together with a poluprečnik definition — and it
        # must be attributed to r_o, never to R.
        for match in re.finditer(r"poluprečnik\s+(?:UPISANE|OPISANE)\s+kružnice", full):
            window = full[max(0, match.start() - 20):match.start()]
            assert "$R$" not in window, (oblast, lesson, window)


def test_all_four_grades_and_both_scopes_are_free_of_old_convention():
    """Sweep beyond the representative sample: every grade x every routed
    scope, using a lesson name synthesized to hit that scope."""
    probes = [
        (g, "Geometrijska tijela", "Zapremina prizme") for g in (6, 7, 8, 9)
    ] + [
        (g, "Četverougao", "Površina pravougaonika") for g in (6, 7, 8, 9)
    ]
    for grade, oblast, lesson in probes:
        # Stari Practice graditelj je povucen; zajednicki blok je isti izvor
        # koji aktivni tutor prompt splajsa.
        from matbot.rules import build_shared_math_rules
        full = build_shared_math_rules(grade, lesson, oblast, mode="practice")
        assert not _OLD_CONVENTION_D_AS_DIAMETER.search(full)
        assert not _OLD_CONVENTION_R_AS_CIRCUMSCRIBED.search(full)


def test_no_geometry_leaks_into_non_geometry_curriculum_lessons():
    """Nijedna lekcija čiji naziv/oblast nemaju veze s geometrijom ne smije
    dobiti geometrijske formule."""
    data = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    algebra_marker = re.compile(
        r"razlomak|razlomc|skup|cijeli broj|procenat|postotak|vjerovatno|"
        r"statistik|polinom",
        re.IGNORECASE,
    )
    geometry_marker = re.compile(
        r"trougao|trougl|četver|kvadrat|romb|trapez|krug|kružnic|mnogougao|"
        r"prizma|piramid|valjak|kupa|lopta|površin|obim|zapremin|dijagonal|geometrij",
        re.IGNORECASE,
    )
    for grade_data in data["grades"].values():
        for lesson in grade_data["lessons"]:
            haystack = f"{lesson['oblast']} {lesson['title']}"
            if algebra_marker.search(haystack) and not geometry_marker.search(haystack):
                assert rules_for(lesson["oblast"], lesson["title"]) == "", haystack
