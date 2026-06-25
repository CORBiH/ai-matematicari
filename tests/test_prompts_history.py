"""Testovi gradnje system prompta po razredima i sanitizacije historije."""
import app as matbot


# ---------- build_system_prompt ----------

def test_prompt_grade5_rules():
    p = matbot.build_system_prompt("5")
    assert "RAZREDNA PRAVILA — 5. RAZRED" in p
    assert "JEDNAČINE I NEJEDNAČINE (5–6. razred)" in p
    assert "(7–9. razred)" not in p.split("GLOBALNA PRAVILA ZAPISA")[0].split("DIJELJENJE DECIMALNIH BROJEVA")[1]

def test_prompt_grade6_uses_lower_eq_rules():
    p = matbot.build_system_prompt("6")
    assert "JEDNAČINE I NEJEDNAČINE (5–6. razred)" in p
    assert "RAZREDNA PRAVILA — 6. RAZRED" in p

def test_prompt_grade7_uses_upper_eq_rules():
    p = matbot.build_system_prompt("7")
    assert "JEDNAČINE I NEJEDNAČINE (7–9. razred)" in p
    assert "RAZREDNA PRAVILA — 7. RAZRED" in p

def test_prompt_grade9():
    p = matbot.build_system_prompt("9")
    assert "RAZREDNA PRAVILA — 9. RAZRED" in p
    assert "JEDNAČINE I NEJEDNAČINE (7–9. razred)" in p

def test_prompt_invalid_grade_falls_back_to_5():
    p = matbot.build_system_prompt("13")
    assert "RAZREDNA PRAVILA — 5. RAZRED" in p

def test_prompt_contains_core_sections():
    p = matbot.build_system_prompt("8")
    for fragment in ("TI SI:", "OPŠTA OGRANIČENJA", "TERMINOLOGIJA I JEZIK", "GLOBALNA PRAVILA ZAPISA"):
        assert fragment in p


# ---------- strip_html_to_text ----------

def test_strip_html_basic():
    assert matbot.strip_html_to_text("<p>a &amp; b<br>c</p>") == "a & b\nc"

def test_strip_html_paragraphs_to_newlines():
    assert matbot.strip_html_to_text("<p>prvi</p><p>drugi</p>") == "prvi\ndrugi"

def test_strip_html_removes_script():
    out = matbot.strip_html_to_text('<script>alert("x")</script>rezultat')
    assert "<script>" not in out and "rezultat" in out


# ---------- sanitize_history ----------

def test_sanitize_non_list():
    assert matbot.sanitize_history("nije lista") == []
    assert matbot.sanitize_history(None) == []
    assert matbot.sanitize_history({"user": "a", "bot": "b"}) == []

def test_sanitize_skips_bad_entries():
    raw = [
        "string",
        {"user": 5, "bot": "x"},
        {"user": "ok", "bot": "<p>odgovor</p>"},
        {"bez": "kljuceva"},
    ]
    out = matbot.sanitize_history(raw)
    assert out == [{"user": "ok", "bot": "odgovor"}]

def test_sanitize_caps_turns():
    raw = [{"user": f"q{i}", "bot": f"a{i}"} for i in range(10)]
    out = matbot.sanitize_history(raw)
    assert len(out) == matbot.HISTORY_MAX_TURNS
    assert out[-1]["user"] == "q9"  # zadržava NAJNOVIJE

def test_sanitize_caps_chars_and_strips_html():
    big_html = "<p>" + ("korak<br>" * 2000) + "</p>"
    out = matbot.sanitize_history([{"user": "u" * 99999, "bot": big_html}])
    assert len(out[0]["user"]) <= matbot.HISTORY_MAX_CHARS
    assert len(out[0]["bot"]) <= matbot.HISTORY_MAX_CHARS
    assert "<p>" not in out[0]["bot"]


# ---------- _append_history_messages ----------

def test_append_history_messages_strips_bot_html():
    messages = []
    matbot._append_history_messages(messages, [{"user": "pitanje", "bot": "<p>x = 3</p>"}])
    assert messages == [
        {"role": "user", "content": "pitanje"},
        {"role": "assistant", "content": "x = 3"},
    ]

def test_append_history_messages_tolerates_garbage():
    messages = []
    matbot._append_history_messages(messages, [None, {"foo": 1}, {"user": "", "bot": ""}])
    assert messages == []

def test_append_history_messages_caps_context():
    messages = []
    hist = [{"user": f"q{i}", "bot": f"a{i}"} for i in range(12)]
    matbot._append_history_messages(messages, hist)
    assert len(messages) == 2 * matbot.HISTORY_CONTEXT_TURNS
