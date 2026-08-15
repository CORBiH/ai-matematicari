"""Metoda strelica za proporcionalnost (odluka F) — renderer, mathsafe, ruta.

Obnovljena školska metoda: smjer promjene veličina ($\\uparrow\\uparrow$ /
$\\uparrow\\downarrow$) određuje proporciju PRIJE računa. Prikaz je tekstualno
siguran (bez ASCII crteža i poravnanja) i ista formulacija ide u oba prompta i
u deterministički generator razmjere.
"""
from matbot import proportion_arrows as pa
from matbot.deterministic import ratio
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.rules import build_shared_math_rules


# ---------------------------------------------------------------------------
# 1) MATHSAFE — strelice su dozvoljene komande, nepoznate i dalje padaju
# ---------------------------------------------------------------------------

def test_arrow_commands_are_allowlisted_and_unknown_still_fails():
    cleaned, safe = sanitize_and_validate_math_text(
        "Smjer: $\\uparrow\\downarrow$ i $\\rightarrow$.")
    assert safe and "\\uparrow" in cleaned
    _, bad = sanitize_and_validate_math_text("Loše: $\\cancel{5}$.")
    assert not bad


# ---------------------------------------------------------------------------
# 2) PROMPT — blok oblasti proporcija nosi metodu u OBA smjera rutiranja
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
# 3) DETERMINISTIČKI GENERATOR — prepoznavanje koristi strelice
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
