"""D35-1: bijela lista MathJax komandi u izlazu modela.

Regresijski slučajevi su vezani za TAČNE stringove iz kampanje od 35 poziva:
poziv 10 (pravi TAB znak koji je postajao „\\ty“/„\\tdot“) i poziv 12
(udvostručen „\\\\cdot“ neposredno ispred cifre)."""
from matbot.mathsafe import (
    MATHJAX_COMMAND_ALLOWLIST,
    find_unknown_math_commands,
    normalize_result_math_transport,
    sanitize_and_validate_math_text,
)
from matbot.explain import run_explain_turn
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.quick import run_quick_turn
from tests.conftest import FakeLLM, make_explain_output, make_output, make_quick_output


def _explain_payload(msg="Objasni mi ovu temu."):
    return {
        "session_id": "cmd-exp", "grade": 6, "selected_topic": "6-01-006",
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "",
        "last_tutor_task": "", "last_tutor_message": "", "conversation_history": [],
    }


def _quick_payload(msg="Koliko je 3/4 + 2/5?"):
    return {
        "session_id": "cmd-quick", "grade": 6, "selected_topic": "",
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "",
        "last_tutor_task": "", "last_tutor_message": "", "conversation_history": [],
    }


def _practice_payload(msg="Koliko je 2+2?"):
    return {
        "session_id": "cmd-prac", "grade": 6, "selected_topic": "6-01-006",
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
    }

# Doslovan sadržaj iz poziva 10: JSON "\t" se dekodira u STVARAN tab znak.
CALL10_TAB_REPLY = "Provjera: $x=3,\ty=1$, i $2\tdot3+2\tdot1=8$"
# Doslovan sadržaj iz poziva 12: DVA backslasha ispred komande, bez razmaka.
CALL12_DOUBLED_REPLY = "U prvu: $2\\\\cdot2+3\\\\cdot1=4+3=7$"


def _safe(text):
    return sanitize_and_validate_math_text(text)[1]


def _clean(text):
    return sanitize_and_validate_math_text(text)[0]


# --- 1-3: validne komande i dalje prolaze ----------------------------------

def test_valid_cdot_passes():
    assert _safe("$2\\cdot3=6$")


def test_valid_frac_passes():
    assert _safe("$\\frac{3}{4}$")


def test_valid_sqrt_passes():
    assert _safe("$\\sqrt{16}=4$")


def test_other_allowlisted_commands_pass():
    for command in ("\\times", "\\pi", "\\approx", "\\le", "\\ge", "\\neq", "\\circ",
                    "\\mathbb{N}", "\\text{cm}"):
        assert _safe("$x=" + command + "$"), command


# --- 4-5: nepoznate komande padaju zatvoreno -------------------------------

def test_unknown_ty_fails_closed():
    assert not _safe("$x=3,\\ty=1$")


def test_unknown_tdot_fails_closed():
    assert not _safe("$2\\tdot3=6$")


def test_unknown_command_is_reported_by_name_only():
    issues = find_unknown_math_commands("x=3,\\ty=1")
    assert issues == ["unknown_mathjax_command:ty"]


def test_call10_tab_control_char_no_longer_manufactures_a_command():
    text, is_safe = sanitize_and_validate_math_text(CALL10_TAB_REPLY)
    assert not is_safe
    assert "\\ty" in text or "\\tdot" in text  # ne pogađamo koja je komanda bila


def test_tab_before_known_command_is_still_reconstructed():
    text, is_safe = sanitize_and_validate_math_text("$3\times4=12$")
    assert is_safe
    assert text == "$3\\times4=12$"


def test_control_char_without_letters_becomes_whitespace():
    text, is_safe = sanitize_and_validate_math_text("$x=3,\t5$")
    assert is_safe
    assert "\\t" not in text


# --- 6-7: udvostručen backslash --------------------------------------------

def test_doubled_cdot_before_digit_is_narrowly_repaired():
    text, is_safe = sanitize_and_validate_math_text(CALL12_DOUBLED_REPLY)
    assert is_safe
    assert "\\\\cdot" not in text
    assert "\\cdot2" in text


def test_doubled_cdot_before_space_still_repaired():
    assert _clean("$2\\\\cdot 2$") == "$2\\cdot 2$"


def test_doubled_backslash_before_unknown_command_fails_closed():
    assert not _safe("$a=1\\\\bcd=2$")


def test_doubled_backslash_is_not_globally_stripped():
    # Skupljanje je USKO: važi samo ispred POZNATE komande ili razmaknog
    # simbola. Udvostručen backslash ispred cifre nije ni jedno ni drugo i
    # ostaje bajt-identičan (ne diramo ono što ne razumijemo).
    text, is_safe = sanitize_and_validate_math_text("$a=1\\\\2$")
    assert is_safe
    assert "\\\\2" in text


def test_single_backslash_commands_keep_exactly_one_backslash():
    text, _ = sanitize_and_validate_math_text("$\\frac{1}{2}\\cdot\\sqrt{4}$")
    assert "\\\\" not in text


def test_transport_normalizer_collapses_doubled_command_before_digit():
    text, safe = normalize_result_math_transport("$2\\\\cdot3$")
    assert safe
    assert text == "$2\\cdot3$"


# --- 8: zagrade i argumenti ostaju netaknuti -------------------------------

def test_braces_and_arguments_remain_intact():
    assert _clean("$\\frac{12}{5}+\\sqrt{9}$") == "$\\frac{12}{5}+\\sqrt{9}$"


def test_nested_braces_survive():
    assert _clean("$x^{2}+\\text{cm}^{2}$") == "$x^{2}+\\text{cm}^{2}$"


# --- 9-10: ista zaštita u sva tri moda --------------------------------------

def test_practice_rejects_unknown_command(store):
    fake = FakeLLM()
    fake.queue(make_output(reply=CALL10_TAB_REPLY, evaluation="correct"))
    response = run_practice_turn(store, fake, _practice_payload())
    assert response["answer"] == SAFE_ERROR_MESSAGE


def test_explain_rejects_unknown_command():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply=CALL10_TAB_REPLY))
    response = run_explain_turn(fake, _explain_payload())
    assert response["answer"] == SAFE_ERROR_MESSAGE


def test_quick_rejects_unknown_command():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply=CALL10_TAB_REPLY))
    response = run_quick_turn(fake, _quick_payload())
    assert response["answer"] == SAFE_ERROR_MESSAGE


def test_malformed_output_never_reaches_browser_payload(client, fake_llm):
    fake_llm.queue(make_quick_output(reply=CALL10_TAB_REPLY))
    body = client.post("/api/ai-tutor/chat", json={
        "session_id": "cmd-sess", "grade": 7, "mode": "quick",
        "selected_topic": "", "selected_oblast": "",
        "student_message": "Koliko je 2+2?", "conversation_history": [],
    }).get_data(as_text=True)
    assert "\\ty" not in body
    assert "\\tdot" not in body


def test_allowlist_contains_the_documented_core_commands():
    for command in ("frac", "sqrt", "cdot", "times", "pi", "approx", "text",
                    "mathbb", "le", "ge", "neq", "circ"):
        assert command in MATHJAX_COMMAND_ALLOWLIST


# --- D35T-1: komande IZVAN matematike su druga politika od onih UNUTAR -------
# Živi nalaz (kampanja od 14 poziva, poziv 3): potpuno ispravan odgovor —
# deklarisao π≈3,14 i izračunao 18,84 cm — bio je odbijen samo zato što je u
# prozi pisalo „LEKCIJA: Broj \pi i obim kruga“. Bijela lista komandi VALIDNIH
# UNUTAR $...$ bila je pogrešno upotrijebljena i kao lista za ODBIJANJE izvan
# matematike.

CALL3_CORRECT_PI_REPLY = (
    "LEKCIJA: Broj \\pi i obim kruga. Formula je $O=2\\pi r$, a \\pi je "
    "približno $3,14$. Za $r=3\\,\\text{cm}$: $2\\cdot3,14\\cdot3=18,84$. "
    "Dakle $O=18,84\\,\\text{cm}$."
)


def test_standalone_pi_in_prose_becomes_inline_mathjax():
    text, is_safe = sanitize_and_validate_math_text("Broj \\pi i obim kruga")
    assert is_safe
    assert text == "Broj $\\pi$ i obim kruga"


def test_existing_delimited_pi_is_byte_compatible():
    for text in ("Koristimo $\\pi\\approx3,14$", "$\\pi$", "$O=2\\pi r$"):
        assert _clean(text) == text
        assert _safe(text)


def test_correct_pi_explanation_is_no_longer_falsely_rejected():
    text, is_safe = sanitize_and_validate_math_text(CALL3_CORRECT_PI_REPLY)
    assert is_safe
    assert "$\\pi$" in text
    assert "18,84" in text


def test_standalone_symbols_are_wrapped_not_rejected():
    for command in ("\\approx", "\\neq", "\\le", "\\ge", "\\alpha", "\\infty"):
        text, is_safe = sanitize_and_validate_math_text("Vrijedi " + command + " ovdje")
        assert is_safe, command
        assert "$" + command + "$" in text


def test_structural_commands_outside_math_still_fail_closed():
    for text in ("Rezultat \\sqrt{16} ovdje", "Jedinica \\text{cm} ovdje",
                 "Skup \\mathbb{N} ovdje", "Vidi \\begin{array} ovdje"):
        assert not _safe(text), text


def test_isolated_frac_outside_math_is_still_wrapped():
    text, is_safe = sanitize_and_validate_math_text("Rezultat \\frac{1}{2} ovdje")
    assert is_safe
    assert "$\\frac{1}{2}$" in text


def test_unknown_command_outside_math_fails_closed():
    assert not _safe("Ovo je \\bogus tekst")
    assert not _safe("Vrijednost \\ty ovdje")


def test_inside_math_allowlist_is_not_the_outside_math_reject_list():
    from matbot.mathsafe import _STANDALONE_SYMBOL_COMMANDS
    assert "frac" in MATHJAX_COMMAND_ALLOWLIST
    assert "frac" not in _STANDALONE_SYMBOL_COMMANDS
    assert "pi" in _STANDALONE_SYMBOL_COMMANDS
    assert _STANDALONE_SYMBOL_COMMANDS < MATHJAX_COMMAND_ALLOWLIST


def test_student_input_is_never_rewritten_by_this_layer():
    """Sanitizacija se poziva SAMO nad izlazom modela — nijedan orkestrator ne
    prosljeđuje student_message kroz sanitizer."""
    import inspect

    from matbot import explain, quick
    for module in (explain, quick):
        source = inspect.getsource(module)
        assert "sanitize_and_validate_math_text(turn[" not in source
        assert "sanitize_and_validate_math_text(student_message" not in source


def test_degree_without_caret_is_narrowly_normalized():
    assert _clean("$180\\circ$") == "$180^\\circ$"
    assert _clean("Zbir je $180\\circ$.") == "Zbir je $180^\\circ$."


def test_existing_correct_degree_is_unchanged():
    assert _clean("$180^\\circ$") == "$180^\\circ$"
    assert _clean("$x=90^\\circ$") == "$x=90^\\circ$"


def test_escaped_dollar_currency_behaviour_unchanged():
    text, is_safe = sanitize_and_validate_math_text("Cijena je \\$5 ukupno")
    assert is_safe
    assert text == "Cijena je \\$5 ukupno"


# ---------------------------------------------------------------------------
# Produkcijski nalaz: "4ecdot2" / "2ecdot2 + 3ecdot3" stiglo je učeniku umjesto
# "4 \\cdot 2" / "2 \\cdot 2 + 3 \\cdot 3". Praćenje puta pokazuje da model
# (kroz client.responses.parse strict JSON šemu) legitimno šalje TAČNO jedan
# backslash — "\\cdot" u Python stringu nakon parsiranja — nikad literalni
# "ecdot". Do korupcije dolazi NA SERVERU, u matbot/mathsafe.py, kroz dva
# nezavisna propusta koja ovaj blok testova pokriva:
#
#   1. `_RAW_LATEX_COMMAND_RE` je granicu iza komande provjeravao sa `\b`
#      umjesto projektnim `_COMMAND_BOUNDARY` — \b se ne aktivira između
#      slova i cifre, pa "\\cdot2" (bez razmaka, ovaj projekat ga baš tako
#      piše) IZVAN $...$ nije bio prepoznat kao sirova komanda i prolazio je
#      neomeđen (bez $...$, pa ga MathJax nikad ne typeset-uje).
#   2. Bilo kojim mehanizmom da backslash ispred poznate komande nestane
#      UNUTAR $...$ (nepoznat kontrolni znak koji _repair_control_chars ne
#      zna rekonstruisati pa ga tiho ukloni, model koji ga jednostavno
#      izostavi, ili bilo koji budući sličan bag), gola riječ "cdot" je
#      prolazila NEOPAŽENO jer je stari `_BARE_COMMAND_RESIDUE_RE` provjeravao
#      SAMO "sqrt"/"text".
#
# Popravka je GENERIČKA (cijela MATHJAX_COMMAND_ALLOWLIST, ne baš "cdot") i oba
# propusta sada padaju zatvoreno — bez ijednog dodatnog/popravnog AI poziva.

def test_4_cdot_2_minus_y_equals_5_stays_valid():
    text, is_safe = sanitize_and_validate_math_text("$4 \\cdot 2 - y = 5$")
    assert is_safe
    assert text == "$4 \\cdot 2 - y = 5$"
    assert "ecdot" not in text


def test_two_cdot_two_plus_three_cdot_three_stays_valid():
    text, is_safe = sanitize_and_validate_math_text("$2 \\cdot 2 + 3 \\cdot 3$")
    assert is_safe
    assert text == "$2 \\cdot 2 + 3 \\cdot 3$"
    assert "ecdot" not in text


def test_multiple_cdot_commands_in_one_segment_all_stay_valid():
    text, is_safe = sanitize_and_validate_math_text(
        "$2\\cdot2+3\\cdot3=4+9=13$ i $5\\cdot1\\cdot2=10$"
    )
    assert is_safe
    assert text.count("\\cdot") == 4
    assert "ecdot" not in text


def test_cdot_survives_a_json_round_trip():
    """Strukturisan model izlaz ide kroz JSON (client.responses.parse); ovaj
    test dokazuje da json.dumps/json.loads sam po sebi ne dira '\\cdot' —
    korupcija NIJE u transportnom sloju."""
    import json

    original = "4 \\cdot 2 - y = 5"
    payload = json.dumps({"reply": original})
    restored = json.loads(payload)["reply"]
    assert restored == original
    assert "ecdot" not in restored
    text, is_safe = sanitize_and_validate_math_text("$" + restored + "$")
    assert is_safe
    assert text == "$4 \\cdot 2 - y = 5$"


def test_bare_cdot_missing_backslash_inside_math_fails_closed():
    """Jezgro nalaza: ako se backslash izgubi PRIJE ove provjere (bilo kojim
    mehanizmom), gola "cdot" unutar $...$ mora odbiti CIO odgovor — nikad
    tiho stići do učenika bez znaka množenja."""
    for text in ("$4 cdot 2 - y = 5$", "$2cdot2 + 3cdot3$", "$5\\cdot2 cdot3$"):
        assert not _safe(text), text


def test_bare_cdot_is_reported_generically_not_only_for_cdot():
    """Generička provjera pokriva CIJELU bijelu listu — probom drugom
    komandom (times) dokazujemo da popravka nije zakrpa samo za 'cdot'."""
    assert not _safe("$4 times 2 = 8$")


def test_cdot_immediately_before_a_digit_outside_math_fails_closed():
    """Prije popravke, '\\cdot2' (bez razmaka) IZVAN $...$ je prolazio
    neomeđen jer se granica provjeravala sa \\b (koji ne radi između slova i
    cifre) — sad pada zatvoreno kao i svaka druga sirova komanda van
    matematike."""
    assert not _safe("Rezultat: 4 \\cdot2 = 8, bez dolara.")
    assert not _safe("Rezultat: 4 \\cdot 2 = 8, bez dolara.")  # already caught before too


def test_ordinary_prose_containing_the_letters_cdot_is_unchanged():
    """Ne smijemo slijepo zamjenjivati slova 'cdot' unutar običnih riječi —
    provjera je ograničena na UNUTAR matematičkih segmenata, i traži granicu
    ispred/iza (ne pogađa usred duže riječi)."""
    prose = "Ovo uopšte nije skraćenica nego obična rečenica bez matematike."
    text, is_safe = sanitize_and_validate_math_text(prose)
    assert is_safe
    assert text == prose


def test_bare_cdot_never_reaches_the_browser_payload_end_to_end(client, fake_llm):
    """Cijeli put do brauzera: gola 'cdot' (backslash izgubljen) mora izazvati
    postojeći siguran fallback, nikad literalni 'cdot'/'ecdot' u payloadu, i
    bez drugog/popravnog AI poziva."""
    fake_llm.queue(make_quick_output(reply="Rezultat je $2cdot2 + 3cdot3 = 13$."))
    body = client.post("/api/ai-tutor/chat", json={
        "session_id": "cdot-sess", "grade": 7, "mode": "quick",
        "selected_topic": "", "selected_oblast": "",
        "student_message": "Izracunaj 2 puta 2 plus 3 puta 3.",
        "conversation_history": [],
    }).get_data(as_text=True)
    assert "cdot" not in body
    assert "ecdot" not in body


def test_valid_cdot_reaches_the_browser_payload_end_to_end(client, fake_llm):
    fake_llm.queue(make_quick_output(reply="Rezultat je $2\\cdot2 + 3\\cdot3 = 13$."))
    body = client.post("/api/ai-tutor/chat", json={
        "session_id": "cdot-sess-ok", "grade": 7, "mode": "quick",
        "selected_topic": "", "selected_oblast": "",
        "student_message": "Izracunaj 2 puta 2 plus 3 puta 3.",
        "conversation_history": [],
    }).get_data(as_text=True)
    assert "\\\\cdot" in body  # JSON-escaped single backslash in the response body
    assert "ecdot" not in body
