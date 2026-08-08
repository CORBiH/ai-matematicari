"""Metoda strelica za proporcionalnost (odluka F) — renderer, mathsafe, ruta.

Obnovljena školska metoda: smjer promjene veličina ($\\uparrow\\uparrow$ /
$\\uparrow\\downarrow$) određuje proporciju PRIJE računa. Prikaz je tekstualno
siguran (bez ASCII crteža i poravnanja) i ista formulacija ide u oba prompta i
u deterministički generator razmjere.
"""
from fractions import Fraction

from matbot import proportion_arrows as pa
from matbot.deterministic import ratio
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.rules import build_shared_math_rules


def _lines_are_mathsafe(lines):
    for line in lines:
        cleaned, safe = sanitize_and_validate_math_text(line)
        assert safe, line
        assert cleaned == line, line


# ---------------------------------------------------------------------------
# 1) RENDERER — direktna, obrnuta, pravilo trojno (STEP 17.G)
# ---------------------------------------------------------------------------

def test_direct_proportion_rule_of_three_layout_and_orientation():
    """Direktna: 3 kg → 12 KM; 5 kg → x KM. x u donjem redu desno; obje
    strelice nagore; proporcija uz strelice: x : 12 = 5 : 3 → x = 20."""
    lines = pa.method_lines(3, 12, 5, pa.KIND_DIRECT, "kg", "KM")
    assert lines[0] == "$3$ kg $\\rightarrow$ $12$ KM"
    assert lines[1] == "$5$ kg $\\rightarrow$ $x$ KM"
    assert "ISTOM smjeru" in lines[2] and "\\uparrow\\uparrow" in lines[2]
    assert "x : 12 = 5 : 3" in lines[3]
    x = Fraction(12) * 5 / 3
    assert x == 20                       # metoda daje tačnu vrijednost
    _lines_are_mathsafe(lines)


def test_inverse_proportion_rule_of_three_layout_and_orientation():
    """Obrnuta: 4 radnika → 12 dana; 6 radnika → x dana. Lijeva razmjera se
    čita u suprotnom smjeru: x : 12 = 4 : 6 → x = 8."""
    lines = pa.method_lines(4, 12, 6, pa.KIND_INVERSE, "radnika", "dana")
    assert lines[1].endswith("$x$ dana")
    assert "SUPROTNIM smjerovima" in lines[2]
    assert "\\uparrow\\downarrow" in lines[2]
    assert "x : 12 = 4 : 6" in lines[3]
    x = Fraction(12) * 4 / 6
    assert x == 8
    assert 4 * 12 == 6 * x               # obrnuta: proizvod x·y je stalan
    _lines_are_mathsafe(lines)


def test_direct_orientation_preserves_the_quotient():
    x = Fraction(12) * 5 / 3             # x : 12 = 5 : 3
    assert Fraction(x, 5) == Fraction(12, 3)   # količnik y : x ostaje stalan


# ---------------------------------------------------------------------------
# 2) MATHSAFE — strelice su dozvoljene komande, nepoznate i dalje padaju
# ---------------------------------------------------------------------------

def test_arrow_commands_are_allowlisted_and_unknown_still_fails():
    cleaned, safe = sanitize_and_validate_math_text(
        "Smjer: $\\uparrow\\downarrow$ i $\\rightarrow$.")
    assert safe and "\\uparrow" in cleaned
    _, bad = sanitize_and_validate_math_text("Loše: $\\cancel{5}$.")
    assert not bad


# ---------------------------------------------------------------------------
# 3) PROMPT — blok oblasti proporcija nosi metodu u OBA smjera rutiranja
# ---------------------------------------------------------------------------

def test_prompt_block_mandates_the_arrow_method():
    text = pa.prompt_rule_text()
    assert "METODU STRELICA" in text
    assert "\\uparrow\\uparrow" in text and "\\uparrow\\downarrow" in text
    assert "NIKAD ASCII" in text


def test_proportionality_lessons_route_the_arrow_block():
    """Latentna rupa nađena pri obnovi: „proporcionalnost“ NE sadrži podstring
    „proporcij“, pa lekcije 8. razreda nisu dobijale blok oblasti."""
    for title in ("Prepoznavanje direktne proporcionalnosti",
                  "Prepoznavanje obrnute proporcionalnosti"):
        text = build_shared_math_rules(
            8, title, "Proporcionalnost, Talesova teorema i sličnost",
            "practice")
        assert "METODU STRELICA" in text, title


# ---------------------------------------------------------------------------
# 4) DETERMINISTIČKI GENERATOR — prepoznavanje koristi strelice
# ---------------------------------------------------------------------------

def test_recognition_packages_reason_with_arrows():
    import random
    for seed in range(20):
        package = ratio.generate_package(
            lesson_id="8-03-004", lesson_title="Prepoznavanje direktne "
            "proporcionalnosti",
            parameters={"concepts": ("proportionality_recognition",)},
            level=2, rng=random.Random(seed))
        blob = package.hints[0] + " " + package.solution
        assert "\\uparrow" in blob
        assert "smjer" in blob.lower()
        for line in (package.hints[0], package.solution):
            _, safe = sanitize_and_validate_math_text(line)
            assert safe, line
