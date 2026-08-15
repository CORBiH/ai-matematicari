"""Unit testovi za matbot/mathsafe.py — deterministička MathJax zaštita."""
import json

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


def _has_control_chars(s):
    return any(ord(ch) < 0x20 and ch not in ("\n", "\t") for ch in s)


# ---------------------------------------------------------------------------
# JSON dvostruko-escape bug: model piše "\frac" u JSON stringu bez ispravnog
# escape-a backslasha; JSON parser to dekodira kao poznat escape (\f, \b, \t,
# \r, \n) i rezultat je STVARAN kontrolni znak umjesto backslasha, praćen
# ostatkom LaTeX komande kao običnim slovima. Ovi testovi simuliraju TAČNO to
# stanje (kao da je Python string već prošao kroz json.loads/pydantic parsing).
# ---------------------------------------------------------------------------

def test_form_feed_reconstructs_frac():
    broken = "Izračunaj $\x0crac{3}{5} : 2$."
    out = sanitize_math_text(broken)
    assert "$\\frac{3}{5} : 2$" in out
    assert not _has_control_chars(out)


def test_tab_reconstructs_times():
    broken = "Rezultat je $4\t" + "imes 5$."  # \t je STVARNI tab znak + "imes"
    out = sanitize_math_text(broken)
    assert "$4\\times 5$" in out
    assert not _has_control_chars(out)


def test_newline_reconstructs_neq():
    broken = "Provjeri: $a\n" + "eq b$."  # \n je stvarni newline + "eq"
    out = sanitize_math_text(broken)
    assert "$a\\neq b$" in out
    assert not _has_control_chars(out)


def test_carriage_return_reconstructs_right():
    broken = "Zagrada: $\\left(3\r" + "ight)$."  # \r je stvarni CR + "ight"
    out = sanitize_math_text(broken)
    assert "$\\left(3\\right)$" in out
    assert not _has_control_chars(out)


def test_backspace_reconstructs_begin():
    broken = "Sistem: $\x08egin{cases}x=1\\end{cases}$."  # \b je stvarni backspace + "egin"
    out = sanitize_math_text(broken)
    assert "$\\begin{cases}x=1\\end{cases}$" in out
    assert not _has_control_chars(out)


def test_normal_newlines_outside_math_are_untouched():
    text = "Prvi red.\nDrugi red sa formulom $\\frac{1}{2}$.\nTreći red.\tSa tabom."
    out = sanitize_math_text(text)
    assert out == text  # van $...$ se ništa ne dira, uključujući normalne \n i \t


def test_control_char_repair_runs_before_brace_balance_check():
    # nakon popravke kontrolnog znaka, zagrade MORAJU ostati balansirane i
    # segment se MORA zadržati kao ispravan matematički izraz (ne stripovan)
    broken = "Izračunaj $\x0crac{3}{5} : 2$."
    out = sanitize_math_text(broken)
    assert out == "Izračunaj $\\frac{3}{5} : 2$."


# ---------------------------------------------------------------------------
# Regresija za konkretno prijavljen live bug: rekonstrukcija je NEKAD (u ranijoj
# hipotezi) mogla proizvesti DVA backslasha ("$\\frac{3}{4}\\cdot 8$" u JSON
# wire formatu → 2 stvarna backslasha u Python stringu), zbog čega MathJax
# prikaže "frac34cdot8" umjesto formule. Ovi testovi eksplicitno broje
# backslash znakove i provjeravaju json.dumps() wire format da razlika između
# JEDAN backslash (ispravno) i DVA backslasha (bug) nikad više ne prođe neopaženo.
# ---------------------------------------------------------------------------

def _backslash_count_before(s, marker):
    """Broj UZASTOPNIH '\\' znakova neposredno prije prve pojave markera."""
    idx = s.index(marker)
    count = 0
    while idx - count - 1 >= 0 and s[idx - count - 1] == "\\":
        count += 1
    return count


def test_repr_of_repaired_frac_has_exactly_one_backslash():
    out = sanitize_math_text("$\x0crac{3}{4}$")
    assert repr(out) == r"'$\\frac{3}{4}$'"   # repr() sam duplira backslash za PRIKAZ
    assert out.count("\\") == 1               # ali STVARNI string ima tačno jedan
    assert _backslash_count_before(out, "frac") == 1


def test_json_dumps_of_repaired_frac_has_single_escaped_backslash():
    out = sanitize_math_text("$\x0crac{3}{4}$")
    wire = json.dumps(out)
    assert r"\\frac" in wire       # JSON ispravno escapuje JEDAN backslash kao \\
    assert r"\\\\frac" not in wire  # NIKAD dva stvarna backslasha (bug scenario)


def test_json_dumps_of_repaired_times_has_single_escaped_backslash():
    out = sanitize_math_text("$5\t" + "imes 6$")
    wire = json.dumps(out)
    assert r"\\times" in wire
    assert r"\\\\times" not in wire


def test_json_dumps_of_repaired_neq_has_single_escaped_backslash():
    out = sanitize_math_text("$a\n" + "eq b$")
    wire = json.dumps(out)
    assert r"\\neq" in wire
    assert r"\\\\neq" not in wire


def test_json_dumps_of_repaired_right_has_single_escaped_backslash():
    out = sanitize_math_text("$\\left(3\r" + "ight)$")
    wire = json.dumps(out)
    assert r"\\right" in wire
    assert r"\\\\right" not in wire


def test_json_dumps_of_repaired_begin_has_single_escaped_backslash():
    out = sanitize_math_text("$\x08egin{cases}x=1\\end{cases}$")
    wire = json.dumps(out)
    assert r"\\begin" in wire
    assert r"\\\\begin" not in wire


# ---------------------------------------------------------------------------
# PROŠIRENJE (live produkcijski nalaz — 3 stvarna bagova): sanitize_math_text
# gore rješava SAMO ono što je VEĆ unutar $...$. Ovi testovi pokrivaju novi
# sanitize_and_validate_math_text + pomoćne funkcije za sadržaj koji stigne
# BEZ (ili s djelimičnim) $...$ omotom — svaki od tačno tri prijavljena
# live slučaja ima svoj test niže.
# ---------------------------------------------------------------------------

from matbot.mathsafe import (  # noqa: E402
    find_unsafe_math_issues,
    replace_literal_newline_escapes,
    sanitize_and_validate_math_text,
    wrap_isolated_frac_tokens,
)


# --- Failure 1: sirov \frac u feedbacku (bez ijednog $) -------------------

def test_failure1_raw_frac_in_feedback_gets_wrapped_safely():
    text, is_safe = sanitize_and_validate_math_text("Izabrao si \\frac{3}{24}.")
    assert is_safe
    assert text == "Izabrao si $\\frac{3}{24}$."


def test_isolated_frac_token_wrap_does_not_touch_surrounding_prose():
    out = wrap_isolated_frac_tokens("Izabrao si \\frac{3}{24}. Tačno!")
    assert out == "Izabrao si $\\frac{3}{24}$. Tačno!"


def test_frac_already_inside_dollar_is_not_double_wrapped():
    out = wrap_isolated_frac_tokens("Rezultat je $\\frac{3}{24}$.")
    assert out == "Rezultat je $\\frac{3}{24}$."


# --- Failure 2: literalni "\n" + neomotan uređeni par u opciji -------------

def test_failure2_literal_newline_escape_becomes_real_newline():
    text, is_safe = sanitize_and_validate_math_text(
        "\\nKoji je tačan uređeni par $(x,y)$?"
    )
    assert is_safe
    assert "\\n" not in text
    assert text.startswith("\n")
    assert "$(x,y)$" in text


def test_literal_newline_inside_math_is_not_touched_neq_stays_intact():
    # "\n" na početku \neq NE smije postati stvaran newline usred formule
    out = replace_literal_newline_escapes("Provjeri: $a\\neq b$ i onda\\nnastavi.")
    assert "$a\\neq b$" in out
    assert "\\neq" in out  # \neq unutar $...$ netaknut
    assert out.endswith("\nnastavi.")  # \n IZVAN $...$ postaje stvaran newline


def test_failure2_unwrapped_ordered_pair_option_gets_whole_wrapped():
    text, is_safe = sanitize_and_validate_math_text(
        "(0,\\frac{8}{3})", allow_whole_expression_wrap=True
    )
    assert is_safe
    assert text == "$(0,\\frac{8}{3})$"


def test_plain_numeric_option_without_latex_is_unaffected_by_whole_wrap():
    """Regresija: obična opcija '5/8' (bez ijedne LaTeX komande) mora ostati
    netaknuta kao i prije — whole-wrap se aktivira SAMO uz prisustvo poznate
    komande (frac/sqrt/text/cdot/begin/end), ne bilo kojeg broja/razlomka."""
    text, is_safe = sanitize_and_validate_math_text("5/8", allow_whole_expression_wrap=True)
    assert is_safe
    assert text == "5/8"


def test_prose_option_without_math_is_unaffected_by_whole_wrap():
    text, is_safe = sanitize_and_validate_math_text(
        "Povučem paralelu s prvim krakom.", allow_whole_expression_wrap=True
    )
    assert is_safe
    assert text == "Povučem paralelu s prvim krakom."


# --- Failure 3: oštećen sqrt/text (izgubljeni backslash/zagrade) -----------

def test_failure3_damaged_sqrt_and_units_form_is_repaired_when_unambiguous():
    # Živi produkcijski nalaz (Defekt 1): "54sqrt3,textcm^3" ima NEDVOSMISLEN
    # radikand ("3") i POZNATU jedinicu ("cm") s eksponentom — sada se
    # deterministički popravlja umjesto da se cio zadatak odbaci.
    text, is_safe = sanitize_and_validate_math_text(
        "54sqrt3,textcm^3", allow_whole_expression_wrap=True
    )
    assert is_safe
    assert text == "$54\\sqrt{3},\\text{cm}^3$"


def test_ambiguous_sqrt_radicand_is_still_rejected_not_guessed():
    # Kontrolni slučaj: "sqrtx" nema brojčani/braced/parenthesized radikand —
    # NEJEDNOZNAČNO, pa se i dalje odbija umjesto da se pogodi.
    text, is_safe = sanitize_and_validate_math_text("$d=sqrtx$")
    assert not is_safe


def test_valid_sqrt_and_units_expression_survives_unchanged():
    original = "$54\\sqrt{3}\\,\\text{cm}^3$"
    text, is_safe = sanitize_and_validate_math_text(original, allow_whole_expression_wrap=True)
    assert is_safe
    assert text == original


def test_raw_sqrt_outside_math_without_frac_is_rejected():
    """\\sqrt (za razliku od \\frac) nema uzak siguran repair — mora biti odbijen."""
    text, is_safe = sanitize_and_validate_math_text("Rezultat je \\sqrt{20}.")
    assert not is_safe


def test_raw_text_command_outside_math_is_rejected():
    text, is_safe = sanitize_and_validate_math_text("Jedinica je \\text{cm}.")
    assert not is_safe


def test_raw_begin_end_outside_math_is_rejected():
    text, is_safe = sanitize_and_validate_math_text("\\begin{cases}x=1\\end{cases}")
    assert not is_safe


# --- find_unsafe_math_issues direktne provjere -----------------------------

def test_find_unsafe_math_issues_empty_for_clean_text():
    assert find_unsafe_math_issues("Tačno! Rezultat je $\\frac{1}{2}$.") == []


def test_find_unsafe_math_issues_detects_raw_command_outside_math():
    issues = find_unsafe_math_issues("Rezultat: \\sqrt{9} je 3.")
    assert "raw_latex_command_outside_math" in issues


def test_find_unsafe_math_issues_detects_damaged_form():
    issues = find_unsafe_math_issues("180sqrt3,textcm^3")
    assert "damaged_latex_form" in issues


def test_find_unsafe_math_issues_ignores_commands_inside_valid_math():
    assert find_unsafe_math_issues("$\\sqrt{9}\\,\\text{cm}$") == []


def test_damaged_form_detects_digit_before_sqrt_too():
    """'2sqrt5' (cifra ODMAH prije 'sqrt', bez word-boundary-a) mora se
    prepoznati kao oštećen oblik, ne samo 'sqrt3' (cifra poslije)."""
    issues = find_unsafe_math_issues("Rezultat je 2sqrt5 otprilike.")
    assert "damaged_latex_form" in issues


# ---------------------------------------------------------------------------
# Pooštravanje whole-expression whitelist-a (konsolidacijski nalaz): prazan
# whitelist charset (cifre/slova/backslash/zagrade) SAM PO SEBI ne razlikuje
# proznu bosansku rečenicu BEZ dijakritika od pravog matematičkog izraza —
# obje koriste isti skup znakova. Ove rečenice NIKAD ne smiju biti umotane
# kao jedan veliki matematički izraz.
# ---------------------------------------------------------------------------

def test_prose_with_frac_is_not_whole_wrapped():
    text, is_safe = sanitize_and_validate_math_text(
        "Rezultat je \\frac{3}{4}", allow_whole_expression_wrap=True
    )
    assert text != "$Rezultat je \\frac{3}{4}$"
    # usko umotavanje ipak popravlja izolovan \frac token (ostaje bezbjedno)
    assert text == "Rezultat je $\\frac{3}{4}$"
    assert is_safe


def test_prose_with_sqrt_is_not_whole_wrapped():
    text, is_safe = sanitize_and_validate_math_text(
        "Izaberi \\sqrt{5}", allow_whole_expression_wrap=True
    )
    assert text != "$Izaberi \\sqrt{5}$"
    # \sqrt izvan $...$ nema uzak siguran repair (samo \frac ima) → odbijeno
    assert not is_safe


def test_prose_with_text_and_diacritics_is_not_whole_wrapped():
    text, is_safe = sanitize_and_validate_math_text(
        "Površina je 20 \\text{ cm}^2", allow_whole_expression_wrap=True
    )
    assert text != "$Površina je 20 \\text{ cm}^2$"
    assert not is_safe


def test_pure_ordered_pair_with_frac_is_still_whole_wrapped():
    """Regresija: pooštravanje ne smije pokvariti stvarni Failure-2 slučaj."""
    text, is_safe = sanitize_and_validate_math_text(
        "(0,\\frac{8}{3})", allow_whole_expression_wrap=True
    )
    assert is_safe
    assert text == "$(0,\\frac{8}{3})$"


def test_pure_sqrt_units_expression_still_whole_wrapped():
    text, is_safe = sanitize_and_validate_math_text(
        "54\\sqrt{3}\\,\\text{cm}^3", allow_whole_expression_wrap=True
    )
    assert is_safe
    assert text == "$54\\sqrt{3}\\,\\text{cm}^3$"


def test_raw_commands_never_survive_outside_math_in_default_mode():
    for command in ("\\frac{1}{2}", "\\sqrt{5}", "\\text{cm}", "\\begin{cases}x=1\\end{cases}"):
        text, is_safe = sanitize_and_validate_math_text(f"Prefix {command} suffix")
        if is_safe:
            assert not _RAW_LATEX_COMMAND_RE_TEST_HELPER(text)


def _RAW_LATEX_COMMAND_RE_TEST_HELPER(text):
    """Pomoćna provjera za test iznad: da li IZVAN $...$ i dalje postoji
    sirova zabranjena komanda (koristi isti regex kao produkcijski kod)."""
    from matbot.mathsafe import _outside_math_parts, _RAW_LATEX_COMMAND_RE

    return any(_RAW_LATEX_COMMAND_RE.search(part) for part in _outside_math_parts(text))


def test_literal_newline_never_reaches_safe_output_in_any_scenario():
    scenarios = [
        "Prvi dio.\\nDrugi dio.",
        "$a=1$\\nNovi red.",
        "\\nPočetak teksta $x=1$.",
    ]
    for scenario in scenarios:
        text, is_safe = sanitize_and_validate_math_text(scenario)
        if is_safe:
            assert "\\n" not in text


# ---------------------------------------------------------------------------
# PHASE 6 — Defekti 1 i 2 (živi nalaz, "Dijagonala kvadrata"): bare sqrt/text
# UNUTAR $...$ i doslovan "\n" UNUTAR $...$. Kategorije 1-15 iz zahtjeva.
# ---------------------------------------------------------------------------

# --- Kategorija 1-8: tačan screenshot-formatting -----------------------------

def test_defect1_case01_bare_sqrt_and_text_no_dollar_at_all():
    text, is_safe = sanitize_and_validate_math_text(
        "4sqrt2,textcm", allow_whole_expression_wrap=True
    )
    assert is_safe
    assert text == "$4\\sqrt{2},\\text{cm}$"


def test_defect1_case02_bare_sqrt_and_text_inside_dollar():
    text, is_safe = sanitize_and_validate_math_text("$8sqrt2\\,textcm$")
    assert is_safe
    assert text == "$8\\sqrt{2}\\,\\text{cm}$"


def test_defect1_case03_bare_text_unit_only():
    text, is_safe = sanitize_and_validate_math_text("$16,textcm$")
    assert is_safe
    assert text == "$16,\\text{cm}$"


def test_defect1_case04_formula_option_a_times_sqrt2():
    text, is_safe = sanitize_and_validate_math_text("$d=asqrt2$")
    assert is_safe
    assert text == "$d=a\\sqrt{2}$"


def test_defect1_case05_formula_option_sqrt2_times_a():
    text, is_safe = sanitize_and_validate_math_text("$d=sqrt2a$")
    assert is_safe
    assert text == "$d=\\sqrt{2}a$"


def test_defect1_case06_ambiguous_radicand_rejected_not_guessed():
    text, is_safe = sanitize_and_validate_math_text("$sqrtx$")
    assert not is_safe


def test_defect1_case07_bare_sqrt_and_unit_with_exponent():
    text, is_safe = sanitize_and_validate_math_text(
        "54sqrt3,textcm^3", allow_whole_expression_wrap=True
    )
    assert is_safe
    assert text == "$54\\sqrt{3},\\text{cm}^3$"


def test_defect1_case08_already_correct_latex_unaffected():
    original = "$54\\sqrt{3}\\,\\text{cm}^3$"
    text, is_safe = sanitize_and_validate_math_text(original, allow_whole_expression_wrap=True)
    assert is_safe
    assert text == original


# --- Kategorija 9-15: doslovan "\n" -----------------------------------------

def test_defect2_case09_literal_n_inside_math_removed():
    text, is_safe = sanitize_and_validate_math_text(
        "$d = \\n\\sqrt{128}=8\\sqrt{2}$"
    )
    assert is_safe
    assert text == "$d = \\sqrt{128}=8\\sqrt{2}$"
    assert "\\n" not in text.replace("\\neq", "").replace("\\nabla", "")


def test_defect2_case10_neq_preserved_inside_math():
    text, is_safe = sanitize_and_validate_math_text("$a\\neq b$")
    assert is_safe
    assert text == "$a\\neq b$"


def test_defect2_case11_ne_preserved_inside_math():
    text, is_safe = sanitize_and_validate_math_text("$a \\ne b$")
    assert is_safe
    assert "\\ne " in text or text.endswith("\\ne")


def test_defect2_case12_not_preserved_inside_math():
    text, is_safe = sanitize_and_validate_math_text("$A \\not\\subset B$")
    assert is_safe
    assert "\\not" in text


def test_defect2_case13_nu_preserved_inside_math():
    text, is_safe = sanitize_and_validate_math_text("$\\nu = 5$")
    assert is_safe
    assert "\\nu" in text


def test_defect2_case14_literal_n_outside_math_becomes_real_newline():
    text, is_safe = sanitize_and_validate_math_text("Prvi dio.\\nDrugi dio.")
    assert is_safe
    assert text == "Prvi dio.\nDrugi dio."


def test_phase7_live_finding_doubled_backslash_before_sqrt_is_repaired():
    """Živi nalaz (Phase 7 live test, 8-04-004 "Dijagonala kvadrata"): model je
    stvarno vratio DVA backslash znaka neposredno ispred sqrt/text/zarez
    (npr. doslovno "8\\\\sqrt{2}\\\\,\\\\text{cm}" — DVA backslasha, ne jedan).
    _RAW_LATEX_COMMAND_RE/_BARE_COMMAND_RESIDUE_RE ovo ranije NISU hvatali
    (znak neposredno ispred "sqrt" JESTE backslash), a MathJax "\\\\" tumači
    kao prelom reda pa "sqrt{2}" ostaje neprevedeno — identičan vizuelni bag
    kao bare sqrt. Otkriveno TEK u Phase 7 live pozivu, popravljeno lokalno."""
    raw = "$8" + "\\\\" + "sqrt{2}" + "\\\\" + "," + "\\\\" + "text{cm}$"
    text, is_safe = sanitize_and_validate_math_text(raw)
    assert is_safe
    assert text == "$8\\sqrt{2}\\,\\text{cm}$"


def test_phase7_live_finding_doubled_backslash_formula_option_is_repaired():
    raw = "$d=" + "\\\\" + "frac{a" + "\\\\" + "sqrt{2}}{2}$"
    text, is_safe = sanitize_and_validate_math_text(raw)
    assert is_safe
    assert text == "$d=\\frac{a\\sqrt{2}}{2}$"


def test_defect2_case15_literal_n_before_new_sentence_still_converted():
    """Kontrolni slučaj za usko ograničenu zaštitu (eq/e/ot/u/abla): "\\n" ispred
    OBIČNE riječi (npr. početak nove rečenice) NIJE \\neq/\\ne/\\not/\\nu/\\nabla
    i mora se ispraviti u stvaran prelom reda, ne ostati doslovno "\\n"."""
    text, is_safe = sanitize_and_validate_math_text("$a=1$\\nNovi red.")
    assert is_safe
    assert text == "$a=1$\nNovi red."


def test_f5l_live_finding_displaystyle_and_boxed_are_allowed():
    """ŽIVI F5L NALAZ (I03; ranija ponavljanja M06/M18): model u punim
    rješenjima uobičajeno piše "$\\displaystyle \\frac{...}$" i
    "\\boxed{...}". Obje su standardne, bezbjedne komande samo za prikaz;
    dok ih allowlist nije znao, SVAKO takvo rješenje padalo je u objavi kao
    „nebezbjedan matematički zapis“ bez dijagnoze. Kodovi
    unknown_mathjax_command:displaystyle/boxed uhvaćeni su živo novim
    logovanjem defekt-kodova u _safe_text."""
    text, is_safe = sanitize_and_validate_math_text(
        "$\\displaystyle \\frac{x^2-1}{x-1}=x+1$ za $x \\neq 1$, "
        "dakle $\\boxed{x+1}$.")
    assert is_safe
    assert "\\displaystyle" in text
    assert "\\boxed{x+1}" in text
    # textstyle je simetrični par iste komande.
    text, is_safe = sanitize_and_validate_math_text(
        "$\\textstyle \\frac{1}{2}$")
    assert is_safe


def test_f5l_unknown_command_still_fails_closed():
    """Kontrolni slučaj: širenje allowlista NIJE oslabilo kapiju — i dalje
    nepoznata komanda (npr. \\cancel) zatvara cio odgovor."""
    _text, is_safe = sanitize_and_validate_math_text(
        "$\\frac{\\cancel{(x-1)}(x+1)}{\\cancel{x-1}}=x+1$")
    assert not is_safe


def test_pp1_live150_vector_commands_are_allowed():
    """ŽIVI PP-1 LIVE-150 NALAZ (E009/A029/A031, vektorske lekcije): model
    standardno piše "\\overrightarrow{AB}" za vektor i veličinske zagrade
    "\\bigl(...\\bigr)" oko parova koordinata. Sve tri su standardne prikazne
    komande; dok ih allowlist nije znao, kodovi
    unknown_mathjax_command:overrightarrow/bigl/bigr obarali su ispravne
    pakete i turnovi su završavali kao tehnički fallback."""
    text, is_safe = sanitize_and_validate_math_text(
        "Vektor $\\overrightarrow{AB}$ ima suprotan vektor $\\overrightarrow{BA}$.")
    assert is_safe
    assert "\\overrightarrow{AB}" in text
    text, is_safe = sanitize_and_validate_math_text(
        "$\\bigl(x+1\\bigr)\\cdot 2$")
    assert is_safe
    assert "\\bigl(x+1\\bigr)" in text
    # kombinacija obje komande unutar istih delimitera
    text, is_safe = sanitize_and_validate_math_text(
        "$\\overrightarrow{u}=\\bigl(3,-2\\bigr)$")
    assert is_safe
    assert "\\overrightarrow{u}" in text and "\\bigl(3,-2\\bigr)" in text


def test_pp1_live150_widening_is_exactly_three_commands():
    """Kontrolni slučajevi: dodane su TAČNO tri komande. Kapitalizovane i
    ostale veličinske varijante NISU — nepoznata komanda i dalje pada
    zatvoreno, a strukturna komanda van $...$ ostaje odbijena."""
    _text, is_safe = sanitize_and_validate_math_text("$\\Bigl(x+1\\Bigr)$")
    assert not is_safe
    _text, is_safe = sanitize_and_validate_math_text("$\\bigg(x+1\\bigg)$")
    assert not is_safe
    # postojeći fail-closed za nepoznatu komandu ostaje netaknut
    _text, is_safe = sanitize_and_validate_math_text("$\\ty 3$")
    assert not is_safe
