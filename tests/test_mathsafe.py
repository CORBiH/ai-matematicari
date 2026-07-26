"""Unit testovi za matbot/mathsafe.py — deterministička MathJax zaštita."""
from matbot.mathsafe import sanitize_math_text


def _dollar_count(s):
    # broji SVE '$' znakove (dovoljno za test — sanitizer ne unosi escape-ovane $)
    return s.count("$")


def test_well_formed_math_is_unchanged():
    text = "Skrati razlomak $\\frac{20}{32}$ i provjeri $5 \\cdot 4 = 20$."
    assert sanitize_math_text(text) == text


def test_plain_text_without_dollars_is_unchanged():
    text = "Nema nikakve matematike ovdje."
    assert sanitize_math_text(text) == text


def test_empty_and_none_input():
    assert sanitize_math_text("") == ""
    assert sanitize_math_text(None) == ""


def test_odd_number_of_dollars_handled_safely():
    # nezatvoren $ na kraju teksta — čest slučaj kad model "zaboravi" zatvoriti
    broken = "Rezultat je $5/8 i to je tačno."
    out = sanitize_math_text(broken)
    assert _dollar_count(out) % 2 == 0
    # sadržaj ostaje čitljiv (nije izgubljen, samo bez $ oko njega)
    assert "5/8" in out
    assert "tačno" in out


def test_unbalanced_braces_in_frac_strip_delimiters_not_crash():
    # nedostaje zatvarajuća vitičasta zagrada u \frac{16}{60
    broken = "Prošireni razlomak je $\\frac{16}{60$ tačno."
    out = sanitize_math_text(broken)
    assert _dollar_count(out) % 2 == 0
    # taj segment se ne smije pojaviti UMOTAN u $ (jer bi MathJax pao na njemu)
    assert "$\\frac{16}{60$" not in broken.replace(broken, out)  # sanity no-op guard
    assert "\\frac{16}{60" in out  # sadržaj i dalje čitljiv kao obični tekst
    assert not out.startswith("$\\frac{16}{60$")


def test_multiple_segments_only_broken_one_is_stripped():
    text = "Prvo $\\frac{1}{2}$, a zatim $\\frac{3}{4$ (greška) i na kraju $6$."
    out = sanitize_math_text(text)
    assert _dollar_count(out) % 2 == 0
    assert "$\\frac{1}{2}$" in out       # ispravan segment ostaje netaknut
    assert "$6$" in out                 # ispravan segment ostaje netaknut
    assert "$\\frac{3}{4$" not in out   # pokvareni segment izgubio je $ omot


def test_escaped_dollar_sign_not_treated_as_delimiter():
    text = r"Cijena je \$5, a razlomak je $\frac{1}{2}$."
    out = sanitize_math_text(text)
    assert "$\\frac{1}{2}$" in out
    assert r"\$5" in out


def test_sanitizer_never_raises_on_garbage_input():
    garbage_inputs = [
        "$$$$$$",
        "{{{{$$}}}}",
        "$" * 51,
        "\\frac{{{{",
        "normalan tekst $ $ $ tekst $",
    ]
    for g in garbage_inputs:
        out = sanitize_math_text(g)
        assert isinstance(out, str)
        assert _dollar_count(out) % 2 == 0
