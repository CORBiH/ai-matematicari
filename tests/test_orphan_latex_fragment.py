# -*- coding: utf-8 -*-
"""Osirotjeli fragment LaTeX komande — izgubljen backslash (matbot/mathsafe.py).

ŽIVI NALAZ (velika Explain evaluacija, slučaj g6-10): učeniku je objavljeno
`$rac{2}{3}$` umjesto `$\\frac{2}{3}$`, osam puta u istom odgovoru. Notacija je
bila vidljivo pokvarena, a nijedan sloj je nije odbio.

MEHANIZAM JE DOKAZAN, NE PRETPOSTAVLJEN. Explain koristi strukturirani izlaz,
pa model piše JSON. U JSON-u je `\\f` VAŽEĆA escape sekvenca za form-feed, pa
`"\\frac"` s jednim backslashom dekodira u U+000C + „rac". Isto vrijedi za
`\\b`, `\\n`, `\\r` i `\\t`; komande čije prvo slovo nije u {b,f,n,r,t}
(`\\sqrt`, `\\alpha`, `\\circ`, `\\approx`, `\\angle`) NISU važeći JSON escape
i ne mogu nastati tim putem.

Kad kontrolni bajt preživi, postojeći skeneri ga već rješavaju (popravak ili
pad zatvoreno) — ti testovi to čuvaju. Rupa je bila slučaj kad bajta više nema.
"""
import pytest

from matbot import mathsafe

FF = chr(12)     # \f  — \frac
BS = chr(8)      # \b  — \beta
TAB = chr(9)     # \t  — \times, \text
NUL = chr(0)
ENQ = chr(5)


def published(text):
    _clean, safe = mathsafe.sanitize_and_validate_math_text(text)
    return safe


def issues(text):
    return mathsafe.find_unsafe_math_issues(text) or []


def wrap(inner):
    return "Rezultat je $%s$ ovdje." % inner


# ---------------------------------------------------------------------------
# RUPA KOJA SE ZATVARA — bajt je nestao, ostala gola riječ ispred `{`
# ---------------------------------------------------------------------------

def test_live_finding_g6_10_is_rejected():
    """Tačan oblik koji je stigao do učenika."""
    text = ("Zajednički nazivnik je $12$.\n\n$rac{2}{3}=rac{8}{12}$\n\n"
            "$rac{3}{4}=rac{9}{12}$")
    assert not published(text)
    assert any(i.startswith("orphan_latex_fragment_in_math:rac") for i in issues(text))


@pytest.mark.parametrize("inner,origin", [
    ("rac{2}{3}", "\\frac"),
    ("ext{cm}", "\\text"),
    ("extbf{a}", "\\textbf"),
    ("inom{5}{2}", "\\binom"),
    ("ar{x}", "\\bar"),
    ("riangle{ABC}", "\\triangle"),
])
def test_orphan_fragment_from_json_escape_is_rejected(inner, origin):
    """Svaki oblik ovdje nastaje gubitkom `\\X` gdje je X važeći JSON escape."""
    assert not published(wrap(inner))


def test_orphan_is_reported_with_its_own_code():
    assert "orphan_latex_fragment_in_math:rac" in issues(wrap("rac{2}{3}"))


def test_server_never_guesses_the_lost_command():
    """Ne prepravlja se u `\\frac` — pogađanje komande je zabranjeno."""
    text = wrap("rac{2}{3}")
    clean, safe = mathsafe.sanitize_and_validate_math_text(text)
    assert not safe
    assert "\\frac" not in clean


# ---------------------------------------------------------------------------
# KONTROLNI BAJT PREŽIVIO — postojeća zaštita se NE SMIJE promijeniti
# ---------------------------------------------------------------------------

def test_surviving_control_byte_is_still_repaired():
    """`\\frac` → FF+„rac" se i dalje popravlja u ispravan `\\frac`."""
    clean, safe = mathsafe.sanitize_and_validate_math_text(wrap(FF + "rac{2}{3}"))
    assert safe
    assert "\\frac{2}{3}" in clean


@pytest.mark.parametrize("inner", [BS + "eta", TAB + "imes", ENQ + "sqrt{2}"])
def test_other_surviving_control_bytes_still_publish_repaired(inner):
    assert published(wrap(inner))


def test_control_byte_that_cannot_be_repaired_still_fails_closed():
    assert not published(wrap(NUL + "angle ABC"))


def test_control_character_detection_is_unchanged():
    assert "control_character_in_math" in issues(wrap(FF + "rac{2}{3}"))


# ---------------------------------------------------------------------------
# LAŽNI POZITIVI — kanonski LaTeX mora ostati dozvoljen
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("inner", [
    "\\frac{2}{3}", "\\sqrt{2}", "\\sqrt{\\frac{a}{b}}", "\\text{cm}",
    "\\mathrm{cm}^2", "\\alpha", "\\beta", "\\gamma", "90^\\circ",
    "\\pi\\approx3,14", "P=a\\cdot b", "x^{2}", "a_{1}", "x", "a", "abc",
    "\\frac{\\text{put}}{\\text{vrijeme}}", "\\overrightarrow{AB}",
    "12,5\\cdot0,4=5", "\\frac{1}{2}+\\frac{1}{3}=\\frac{5}{6}",
])
def test_canonical_latex_still_publishes(inner):
    assert published(wrap(inner))


@pytest.mark.parametrize("text", [
    "Aproksimacija je približna vrijednost.",
    "Beta i gama su grčka slova.",
    "Frakcija i cirkularno kretanje.",
    "U tekstu se pojavljuje riječ tekstualni zadatak.",
    "Račun je tačan.",
    "Zapisujemo $\\text{aproksimacija}$ kao oznaku.",
    "Trougao ima tri stranice.",
])
def test_prose_with_command_like_words_is_not_flagged(text):
    assert published(text)
    assert not [i for i in issues(text) if i.startswith("orphan_latex_fragment")]


def test_prose_inside_text_command_is_never_scanned():
    """`\\text{…}` je proza po definiciji — postojeća maska mora ostati."""
    text = "Mjera je $\\text{brzina puta vrijeme}$ ovdje."
    assert not [i for i in issues(text) if i.startswith("orphan_latex_fragment")]


def test_single_letter_before_brace_is_not_flagged():
    """Grupisanje iza jednog slova nije dokaz izgubljene komande."""
    assert not [i for i in issues(wrap("f{x}")) if i.startswith("orphan_latex_fragment")]


def test_full_command_name_keeps_its_existing_code():
    """`frac{` bez backslasha je već pokriven starijim skenerom — bez duplikata."""
    found = issues(wrap("frac{2}{3}"))
    assert any(i.startswith("bare_command_in_math:frac") for i in found)
    assert not [i for i in found if i.startswith("orphan_latex_fragment")]


# ---------------------------------------------------------------------------
# DIJELJENI SLOJ — zaštita mora vrijediti u svim modovima, bez izuzetka
# ---------------------------------------------------------------------------

def test_guard_is_in_the_shared_publication_boundary():
    """mathsafe je zajednički za Explain, Quick, Practice i Kontrolni."""
    assert not published("Zadatak: koliko je $rac{1}{2}$ od 10?")


def test_guard_makes_no_model_call():
    assert not hasattr(mathsafe, "llm")
    assert not published(wrap("rac{2}{3}"))


def test_clean_text_is_never_mutated_by_the_guard():
    text = "Zbir je $\\frac{5}{6}$."
    clean, safe = mathsafe.sanitize_and_validate_math_text(text)
    assert safe
    assert clean == text
