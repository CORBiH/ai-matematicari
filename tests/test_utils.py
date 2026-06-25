"""Testovi čistih pomoćnih funkcija."""
import app as matbot


# ---------- extract_requested_tasks ----------

def test_extract_explicit_zadatak():
    assert matbot.extract_requested_tasks("Riješi zadatak 3") == [3]

def test_extract_zadatak_broj():
    assert matbot.extract_requested_tasks("zadatak broj 12") == [12]

def test_extract_multiple_dedup():
    assert matbot.extract_requested_tasks("zadatak 3, zadatak 7 i zadatak 3") == [3, 7]

def test_extract_bare_number_dot():
    assert matbot.extract_requested_tasks("324.") == [324]

def test_extract_ordinal_words():
    assert matbot.extract_requested_tasks("uradi prvi i treći") == [1, 3]

def test_extract_zadnji_is_minus_one():
    assert matbot.extract_requested_tasks("uradi zadnji") == [-1]

def test_extract_skips_grade_mention():
    # "5. razred" nije broj zadatka — ranije lažni pogodak
    assert matbot.extract_requested_tasks("Imam 5. razred") == []

def test_extract_skips_decimal_numbers():
    # "3.5" je decimalni broj, ne zadatak 3
    assert matbot.extract_requested_tasks("Izračunaj 3.5 + 1") == []

def test_extract_empty():
    assert matbot.extract_requested_tasks("") == []
    assert matbot.extract_requested_tasks(None) == []


# ---------- requested_clause ----------

def test_requested_clause_empty():
    assert matbot.requested_clause([]) == ""

def test_requested_clause_numbers():
    c = matbot.requested_clause([3, 7])
    assert "3, 7" in c and "ISKLJUČIVO" in c

def test_requested_clause_translates_zadnji():
    c = matbot.requested_clause([-1])
    assert "posljednji" in c
    assert "-1" not in c


# ---------- should_plot / extract_plot_expression ----------

def test_should_plot_trigger():
    assert matbot.should_plot("nacrtaj graf funkcije y=2x")

def test_should_plot_negation():
    assert not matbot.should_plot("riješi bez grafa: y=2x")

def test_should_plot_plain_question():
    assert not matbot.should_plot("koliko je 2+2?")

def test_extract_plot_expression_y_form():
    assert matbot.extract_plot_expression("nacrtaj y = 2x + 1") == "y = 2x + 1"

def test_extract_plot_expression_f_form():
    assert matbot.extract_plot_expression("nacrtaj f(x) = 3x - 2") is not None

def test_extract_plot_expression_none():
    assert matbot.extract_plot_expression("koliko je 2+2") is None


# ---------- add_plot_div_once ----------

def test_add_plot_div_once_idempotent():
    out1 = matbot.add_plot_div_once("<p>odgovor</p>", "y=2x")
    out2 = matbot.add_plot_div_once(out1, "y=2x")
    assert out1 == out2
    assert out1.count('class="plot-request"') == 1


# ---------- latexify_fractions ----------

def test_latexify_fractions_simple():
    assert "\\frac{3}{4}" in matbot.latexify_fractions("3/4")

def test_latexify_fractions_leaves_words():
    assert matbot.latexify_fractions("pola torte") == "pola torte"


# ---------- strip_ascii_graph_blocks ----------

def test_strip_ascii_graph_removed():
    text = "Prije\n```\n  |\n--+--\n  |\n```\nPoslije"
    out = matbot.strip_ascii_graph_blocks(text)
    assert "--+--" not in out
    assert "Prije" in out and "Poslije" in out

def test_strip_keeps_real_content_blocks():
    # regresija: ranije su SVI fenced blokovi bili obrisani
    text = "Rješenje:\n```\nPovršina kvadrata se računa kao stranica puta stranica\n```\nKraj"
    out = matbot.strip_ascii_graph_blocks(text)
    assert "Površina kvadrata" in out


# ---------- render_model_html (XSS) ----------

def test_render_model_html_escapes_script():
    out = matbot.render_model_html("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out

def test_render_model_html_escapes_lt():
    out = matbot.render_model_html("2 < 5")
    assert "&lt;" in out

def test_render_model_html_newlines_to_br():
    out = matbot.render_model_html("red1\nred2")
    assert "red1<br>red2" in out

def test_render_model_html_keeps_latex_dollars():
    out = matbot.render_model_html("$$\\frac{1}{2}$$")
    assert "$$" in out and "\\frac{1}{2}" in out


# ---------- _sniff_image_mime ----------

def test_sniff_png():
    assert matbot._sniff_image_mime(b"\x89PNG\r\n\x1a\n" + b"0" * 16) == "image/png"

def test_sniff_jpeg():
    assert matbot._sniff_image_mime(b"\xff\xd8\xff" + b"0" * 16) == "image/jpeg"

def test_sniff_webp():
    assert matbot._sniff_image_mime(b"RIFF1234WEBP" + b"0" * 8) == "image/webp"

def test_sniff_fallback():
    assert matbot._sniff_image_mime(b"nepoznato-nesto") == "image/jpeg"


# ---------- looks_heavy / estimate_tokens ----------

def test_looks_heavy_image_always():
    assert matbot.looks_heavy("", has_image=True)

def test_looks_heavy_long_text():
    assert matbot.looks_heavy("x" * 10000, has_image=False)

def test_looks_heavy_short_text():
    assert not matbot.looks_heavy("koliko je 2+2", has_image=False)


# ---------- FOLLOWUP_TASK_RE ----------

def test_followup_re_matches():
    assert matbot.FOLLOWUP_TASK_RE.match("324a)")
    assert matbot.FOLLOWUP_TASK_RE.match(" 45 b ")

def test_followup_re_rejects():
    assert not matbot.FOLLOWUP_TASK_RE.match("objasni postupak")
