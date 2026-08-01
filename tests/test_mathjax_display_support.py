"""Faza A1 (docs/CURRENT_STATE.md C-3/C-5): matbot/mathsegments.py — zajednički
tokenizator koji ispravno razlikuje $...$ (inline) od $$...$$ (display), umjesto
naivnog alternating-split-a koji je par susjednih '$$' razbijao na dva odvojena
para i ostavljao sadržaj unutar $$...$$ neprovjeren."""
from matbot.mathcheck import find_numeric_inconsistencies, math_segments
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.mathsegments import join_segments, tokenize_math


# ---------------------------------------------------------------------------
# 1-2: validan inline/display ostaje bajt-identičan
# ---------------------------------------------------------------------------

def test_case1_valid_inline_math_unchanged():
    text = "Rezultat je $\\frac{16}{60}$ i to je konačno."
    out, safe = sanitize_and_validate_math_text(text)
    assert safe is True
    assert out == text


def test_case2_valid_display_math_unchanged():
    text = "Formula: $$P=\\frac{a\\cdot h}{2}$$ je površina."
    out, safe = sanitize_and_validate_math_text(text)
    assert safe is True
    assert out == text


# ---------------------------------------------------------------------------
# 3-5: mathcheck sada vidi I inspektuje sadržaj $$...$$
# ---------------------------------------------------------------------------

def test_case3_display_math_content_is_inspected_by_mathcheck():
    segments = math_segments("Rezultat: $$60:15=4$$ je tačan.")
    assert segments == ["60:15=4"]


def test_case4_wrong_equality_inside_display_math_rejects():
    text = "Računamo: $$60:15=5$$ pa je rezultat 5."
    out, safe = sanitize_and_validate_math_text(text)
    assert safe is True  # mathsafe samo o MathJax-u, ne o aritmetici
    issues = find_numeric_inconsistencies(out)
    assert issues
    assert "60:15" in issues[0]


def test_case5_correct_equality_inside_display_math_passes():
    text = "Računamo: $$60:15=4$$ pa je rezultat 4."
    out, safe = sanitize_and_validate_math_text(text)
    assert safe is True
    assert find_numeric_inconsistencies(out) == []


# ---------------------------------------------------------------------------
# 6-7: ugniježđeni/nezatvoreni delimiteri se bezbjedno odbijaju
# ---------------------------------------------------------------------------

def test_case6_nested_inline_delimiter_inside_display_math_rejects_safely():
    # usamljen "$" UNUTAR $$...$$ ne otvara/zatvara ništa (tokenize_math traži
    # tačno "$$" da zatvori display) — ostaje kao doslovan sadržaj. Isti
    # princip kao nebalansirane zagrade (test_unbalanced_braces_...): segment
    # se NE prihvata kao valjana matematika — delimiteri se uklone i ostatak
    # ide kao obični, čitljiv tekst (repair, ne odbijanje CIJELOG odgovora),
    # a sigurnosna mreža na kraju garantuje da NIJEDAN usamljen '$' ne
    # procuri u MathJax kao slomljen delimiter.
    text = "$$P=1$ nekompletno$$"
    out, safe = sanitize_and_validate_math_text(text)
    assert safe is True
    assert "$" not in out
    assert "P=1" in out and "nekompletno" in out


def test_nested_dollar_is_caught_by_find_unsafe_math_issues_directly():
    """find_unsafe_math_issues sam po sebi (bez prethodnog sanitize_math_text
    prolaza) MORA prepoznati usamljen '$' unutar već izdvojenog matematičkog
    segmenta kao nebezbjedan — odbrana u dubini za pozivaoce koji ovu funkciju
    koriste direktno nad tekstom koji nije prošao kroz sanitize_math_text."""
    from matbot.mathsafe import find_unsafe_math_issues

    issues = find_unsafe_math_issues("$$P=1$ nekompletno$$")
    assert "nested_dollar_in_math_segment" in issues


def test_case7_dangling_display_delimiter_rejects():
    text = "Formula: $$P=\\frac{a\\cdot h}{2} bez zatvaranja."
    out, safe = sanitize_and_validate_math_text(text)
    # nezatvoren "$$" se otpisuje (otvarajući delimiter nestaje), a ostatak
    # ide kao obični tekst — nikad ne smije ostaviti neparan broj '$' u izlazu
    assert out.count("$") % 2 == 0
    assert safe is True  # čitljiv obični tekst, ne polomljen MathJax


# ---------------------------------------------------------------------------
# 8-10: mješoviti blokovi, zagrade/razlomci/korijeni/komande ostaju netaknuti,
# izlaz je uvijek balansiran (MathJax-renderabilan)
# ---------------------------------------------------------------------------

def test_case8_mixed_inline_and_display_blocks_pass():
    text = "Prvo $x=5$, zatim $$y=\\sqrt{25}$$ i na kraju $z=2x$."
    out, safe = sanitize_and_validate_math_text(text)
    assert safe is True
    assert out == text


def test_case9_braces_fractions_roots_commands_remain_intact():
    text = "Provjera: $$P=\\frac{a\\cdot h}{2}$$, $\\sqrt{20}=2\\sqrt{5}$, $x^{2}=9$."
    out, safe = sanitize_and_validate_math_text(text)
    assert safe is True
    assert "\\frac{a\\cdot h}{2}" in out
    assert "\\sqrt{20}" in out
    assert "2\\sqrt{5}" in out
    assert "x^{2}" in out


def test_case10_output_always_mathjax_renderable_dollar_balance():
    cases = [
        "$$$$$$",
        "{{{{$$}}}}",
        "$$nezatvoreno",
        "$ $ $$ $ $$",
        "Formula: $$P=\\frac{a\\cdot h}{2}$$ tekst $x=5$ kraj.",
    ]
    for text in cases:
        out, _ = sanitize_and_validate_math_text(text)
        assert out.count("$") % 2 == 0, text


# ---------------------------------------------------------------------------
# Tokenizator direktno (Layer 2 jedinični testovi)
# ---------------------------------------------------------------------------

def test_tokenizer_roundtrips_well_formed_text():
    for text in [
        "Skrati razlomak $\\frac{20}{32}$ i provjeri $5 \\cdot 4 = 20$.",
        "Formula: $$P=\\frac{a\\cdot h}{2}$$ je površina.",
        "Nema nikakve matematike ovdje.",
        "",
    ]:
        assert join_segments(tokenize_math(text)) == text


def test_tokenizer_classifies_display_vs_inline():
    segs = tokenize_math("Tekst $x=1$ i $$y=2$$ kraj.")
    kinds = [k for k, _ in segs]
    assert kinds == ["text", "inline", "text", "display", "text"]
    contents = [c for _, c in segs]
    assert contents[1] == "x=1"
    assert contents[3] == "y=2"
