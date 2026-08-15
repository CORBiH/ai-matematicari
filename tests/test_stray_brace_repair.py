"""Narrow repair of a stray terminal '}' outside $...$ (matbot/mathsafe.py).

Živi nalaz: prvi-pogrešan hint uživo završio se doslovno s „...$y=3$.}“ — jedna
zalutala zatvarajuća vitičasta zagrada bez para, izvan MathJax-a. Nije lomila
MathJax (nije problem balansa $), ali je vidljiv kozmetički defekt. Popravka
je NAMJERNO uska: briše TAČNO jednu terminalnu „}“ samo kad je dokazivo
zalutala (izvan $...$, na samom kraju, bez para u ostatku teksta izvan $...$).
"""
from matbot import config, feedback
from matbot.mathsafe import repair_stray_terminal_brace, sanitize_and_validate_math_text
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_options, make_output, make_task_for_family

LIVE_HINT = ("Provjeri prvo da li uređeni par $(7,3)$ zadovoljava prvu jednačinu "
            "tako što ćeš u nju zamijeniti $x=7$ i $y=3$.}")
LIVE_HINT_REPAIRED = ("Provjeri prvo da li uređeni par $(7,3)$ zadovoljava prvu jednačinu "
                     "tako što ćeš u nju zamijeniti $x=7$ i $y=3$.")


# ---------------------------------------------------------------------------
# 1-2. Exact live case
# ---------------------------------------------------------------------------

def test_exact_live_hint_stray_brace_is_repaired():
    assert repair_stray_terminal_brace(LIVE_HINT) == LIVE_HINT_REPAIRED


def test_exact_live_hint_repaired_via_full_sanitize_pipeline():
    cleaned, is_safe = sanitize_and_validate_math_text(LIVE_HINT)
    assert cleaned == LIVE_HINT_REPAIRED
    assert is_safe


def test_simple_example_from_the_spec():
    assert repair_stray_terminal_brace("Provjeri $x=7$.}") == "Provjeri $x=7$."


# ---------------------------------------------------------------------------
# 3-5. Valid LaTeX braces inside $...$ are byte-identical
# ---------------------------------------------------------------------------

def test_frac_remains_byte_identical():
    text = "$\\frac{3}{4}$"
    assert repair_stray_terminal_brace(text) == text


def test_exponent_remains_byte_identical():
    text = "$x^{2}$"
    assert repair_stray_terminal_brace(text) == text


def test_pdp_pop_ob_ru_ro_ha_remain_byte_identical():
    for symbol in ("$P_{DP}$", "$P_{OP}$", "$O_B$", "$r_u$", "$r_o$", "$h_a$"):
        assert repair_stray_terminal_brace(symbol) == symbol, symbol


def test_geometry_formula_sentence_remains_byte_identical():
    text = "Površina dijagonalnog presjeka je $P_{DP} = a^2\\sqrt{2}$."
    assert repair_stray_terminal_brace(text) == text


def test_frac_at_end_of_sentence_remains_byte_identical():
    text = "Rezultat je $\\frac{9}{24}$."
    assert repair_stray_terminal_brace(text) == text


def test_multiple_subscripts_in_one_expression_remain_byte_identical():
    text = "Uspoređujemo $r_u$ i $r_o$: $r_o > r_u$."
    assert repair_stray_terminal_brace(text) == text


# ---------------------------------------------------------------------------
# 6. Balanced plain-text braces remain unchanged
# ---------------------------------------------------------------------------

def test_balanced_plain_text_braces_unchanged():
    text = "Tekst {primjer}"
    assert repair_stray_terminal_brace(text) == text


def test_balanced_plain_text_braces_mid_sentence_unchanged():
    text = "Tekst {primjer} nastavlja se dalje."
    assert repair_stray_terminal_brace(text) == text


def test_set_notation_inside_math_unchanged():
    text = "Skup je $\\{1,2,3\\}$."
    assert repair_stray_terminal_brace(text) == text


# ---------------------------------------------------------------------------
# 7. Unmatched brace INSIDE $...$ is not repaired here (existing balance
#    check in sanitize_math_text handles that segment separately)
# ---------------------------------------------------------------------------

def test_unmatched_brace_inside_math_is_not_touched_by_this_repair():
    text = "$\\frac{3}{4$"  # nebalansirano UNUTAR $...$
    # repair_stray_terminal_brace ne dira sadržaj unutar $...$ uopšte —
    # ne postoji terminalna '}' IZVAN $...$ ovdje, pa tekst ostaje isti.
    assert repair_stray_terminal_brace(text) == text


def test_unmatched_open_brace_inside_math_is_rejected_by_existing_balance_check():
    """Postojeća provjera unutar sanitize_math_text i dalje uklanja $
    delimitere oko nebalansiranog segmenta — ovaj repair to ne zamjenjuje."""
    from matbot.mathsafe import sanitize_math_text
    text = "$\\frac{3}{4$ ostatak teksta"
    cleaned = sanitize_math_text(text)
    assert "$" not in cleaned  # delimiteri uklonjeni jer segment nije balansiran


# ---------------------------------------------------------------------------
# 8. Multiple / internal unmatched braces are not broadly deleted
# ---------------------------------------------------------------------------

def test_double_trailing_brace_is_not_touched():
    text = "Tekst koji završava sa}}"
    assert repair_stray_terminal_brace(text) == text


def test_unmatched_open_brace_elsewhere_prevents_repair():
    """Ako ostatak teksta IZVAN $...$ već ima svoj neuparen '{', ne pogađamo
    koja je 'prava' višak zagrada — ništa se ne dira."""
    text = "Tekst {nezavršen i onda kraj.}"
    assert repair_stray_terminal_brace(text) == text


def test_brace_not_at_the_very_end_is_not_touched():
    text = "Tekst sa } zagradom usred rečenice, pa nastavak."
    assert repair_stray_terminal_brace(text) == text


def test_removal_that_would_empty_the_text_is_not_applied():
    assert repair_stray_terminal_brace("}") == "}"
    assert repair_stray_terminal_brace("   }") == "   }"


def test_trailing_whitespace_after_stray_brace_is_preserved():
    text = "Provjeri $x=7$.}   "
    assert repair_stray_terminal_brace(text) == "Provjeri $x=7$.   "


def test_empty_and_none_are_safe():
    assert repair_stray_terminal_brace("") == ""
    assert repair_stray_terminal_brace(None) == ""


def test_text_without_brace_is_returned_identical():
    text = "Sasvim običan tekst bez ijedne zagrade."
    assert repair_stray_terminal_brace(text) is text


# ---------------------------------------------------------------------------
# 12. Existing Practice/Explain/Quick sanitation tests remain green — proven
# by running the full suite (see final report); a few targeted spot-checks:
# ---------------------------------------------------------------------------

def test_existing_mathsafe_suite_symbols_still_pass_safety_check():
    for text in ["$\\frac{3}{4}$", "$\\sqrt{5}$", "$x^2$", "Rezultat: $\\frac{1}{2}$."]:
        cleaned, is_safe = sanitize_and_validate_math_text(text)
        assert is_safe, text
