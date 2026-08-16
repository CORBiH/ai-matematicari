# -*- coding: utf-8 -*-
"""N-5: gola proza kao matematički atom ne smije se objaviti.

ŽIVI NALAZ (kontrolni, 7. razred, oblast 7-05, pitanje q4):

    „Četverougao ima stranice dužina $7$, $5$, $9$ i $6$ cm. Koliki je obim?“
    opcija a) `$ treinta\\,\\text{cm}$`   („treinta“ = španski „trideset“)

Ključ (`27 cm`) je bio tačan, ali je učeniku prikazan nesuvisao distraktor.
N-1 pravilo gleda samo ARGUMENTE strukturnih komandi, pa je `treinta` prošlo:
`\\,` i `\\text{cm}` su uredni, a riječ nije bila ničiji argument.
"""
import pytest

from matbot import kontrolni, mathsafe
from matbot.schema import KontrolniQuestionOutput

BAD_OPTION = "$ treinta\\,\\text{cm}$"
STEM = ("Četverougao ima stranice dužina $7\\,\\text{cm}$, $5\\,\\text{cm}$, "
        "$9\\,\\text{cm}$ i $6\\,\\text{cm}$. Koliki je njegov obim?")


class _Ctx:
    geometry_scope = "planimetrija"
    geometry_figures = ("cetverougao",)


def _validate(text, options, marked, solution="Obim je $7+5+9+6=27$ cm."):
    slot = {"slot": 4, "lesson_id": "7-05-001", "lesson_title": "Obim četverougla",
            "difficulty": "medium"}
    parsed = KontrolniQuestionOutput(
        slot=4, lesson_id="7-05-001", text=text, options=options,
        correct_option_index=marked, expected_answer=options[marked],
        solution=solution, difficulty="medium")
    return kontrolni.validate_generated_question(parsed, slot, _Ctx(), set())


def test_historical_n5_option_is_rejected_before_publication():
    clean, code = _validate(
        STEM, ["$27\\,\\text{cm}$", BAD_OPTION, "$21\\,\\text{cm}$",
               "$26\\,\\text{cm}$"], 0)
    assert clean is None
    assert code == "unsafe_or_long_option"


def test_historical_n5_carries_a_precise_reason():
    assert any(i.startswith("prose_atom_in_math:treinta")
               for i in mathsafe.find_unsafe_math_issues(BAD_OPTION))


def test_same_question_without_the_garbage_option_publishes():
    clean, code = _validate(
        STEM, ["$27\\,\\text{cm}$", "$30\\,\\text{cm}$", "$21\\,\\text{cm}$",
               "$26\\,\\text{cm}$"], 0)
    assert clean is not None, code


# ---------------------------------------------------------------------------
# STRUKTURNE VARIJANTE SMEĆA — sve padaju
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$treinta\\,\\text{cm}$",
    "$ treinta $",
    "$odgovor\\,\\text{m}$",
    "$nepoznato nesto$",
    "$rezultat je 5$",
    "Vrijednost je $priblizno pet$.",
    "$dvadeset$",
    "$x = nepoznato$",
    "$2^xcdot2^3$",                       # izgubljena kosa crta
])
def test_bare_prose_in_math_is_rejected(text):
    assert not mathsafe.sanitize_and_validate_math_text(text)[1], text


# ---------------------------------------------------------------------------
# VALIDNA MATEMATIKA — ništa od ovoga ne smije pasti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$x$", "$y$", "$a$", "$b$", "$n$", "$k$", "$r$", "$s$",
    "$AB$", "$ABC$", "$ABCD$", "$KM$", "$NZD$",
    "$cm$", "$\\text{cm}$", "$\\text{KM}$", "$12\\,\\text{cm}^2$",
    "$\\alpha$", "$\\beta$", "$\\pi$", "$\\varepsilon$",
    "$\\sin x$", "$\\cos \\alpha$", "$\\tan\\beta$", "$\\log 100$",
    "$x^2$", "$x_1$", "$h_a$", "$d_1$", "$V_{kupe}$", "$V_{valjka}$",
    "$P_{osnove}$", "$S_{baze}$",
    "$6\\sqrt{2}$", "$\\frac{3}{4}$", "$\\sqrt{194}$", "$16\\sqrt{3}$",
    "$A\\cap B$", "$A\\cup B$", "$x\\in A$", "$A\\subseteq B$",
    "$45^\\circ$", "$\\emptyset$", "$\\mathbb{Z}$",
    "$AB \\parallel CD$", "$AB \\perp CD$",
    "$2x+5=13$", "$18,75\\ \\text{m/s}$", "$1\\frac{3}{4}$",
    "$\\text{prizma}: 2n \\text{ tjemena}, 3n \\text{ ivica}, n+2 \\text{ strane}$",
])
def test_valid_math_is_preserved(text):
    assert mathsafe.sanitize_and_validate_math_text(text)[1], (
        text, mathsafe.find_unsafe_math_issues(text))


# ---------------------------------------------------------------------------
# N-1 I N-3 OSTAJU ZATVORENI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$\\sqrt{ posibilidades }\\,\\text{cm}$",     # N-1
    "$\\frac{ tekst }{2}$",                        # N-1
    "$AB\\beta CD$",                               # N-3
    "$\\overline{AB}\\beta\\overline{CD}$",        # N-3
])
def test_n1_and_n3_remain_closed(text):
    assert not mathsafe.sanitize_and_validate_math_text(text)[1], text


def test_no_silent_deletion_of_the_garbage():
    """Smeće se ODBIJA, ne briše tiho."""
    cleaned, safe = mathsafe.sanitize_and_validate_math_text(BAD_OPTION)
    assert not safe
    assert "treinta" in cleaned


def test_deterministic_polyhedron_mnemonic_uses_text_wrapping():
    """Generator zapremina piše riječi u `\\text{…}`, pa ostaje objavljiv."""
    import pathlib
    source = (pathlib.Path(__file__).resolve().parent.parent
              / "matbot" / "deterministic" / "geometry.py").read_text(encoding="utf-8")
    assert "\\\\text{prizma}" in source or "\\text{prizma}" in source
    assert "prizma: 2n tjemena" not in source
