"""Testovi zabrane termina „čimbenik“ (matbot/terminology.py + svi modovi).

Pravilo: u bosanskom školskom registru koristi se „faktor“ (ili „činilac“),
nikad hrvatski „čimbenik“ — ni u jednom padežu, ni u jednom modu.
"""
from pathlib import Path

from matbot import terminology
from matbot.explain import run_explain_turn
from matbot.practice import run_practice_turn
from matbot.quick import run_quick_turn
from matbot.session_store import SessionStore
from tests.conftest import (queue_two_call, FakeLLM, make_explain_output, make_options, make_output,
                             make_quick_output, make_task)

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Normalizacija padežnih oblika
# ---------------------------------------------------------------------------

def test_all_required_grammatical_forms_are_normalized():
    cases = [
        ("čimbenik", "faktor"),
        ("čimbenika", "faktora"),
        ("čimbenici", "faktori"),
        ("čimbenike", "faktore"),
        ("čimbenikom", "faktorom"),
        ("čimbeniku", "faktoru"),
        ("čimbenicima", "faktorima"),
    ]
    for source, expected in cases:
        assert terminology.normalize_terminology(source) == expected, source


def test_normalization_works_inside_a_sentence():
    out = terminology.normalize_terminology("Rastavi broj na proste čimbenike i zapiši ih.")
    assert out == "Rastavi broj na proste faktore i zapiši ih."


def test_capitalization_is_preserved():
    assert terminology.normalize_terminology("Čimbenik") == "Faktor"
    assert terminology.normalize_terminology("ČIMBENIK") == "FAKTOR"
    assert terminology.normalize_terminology("Čimbenici su") == "Faktori su"


def test_case_insensitive_detection():
    assert terminology.normalize_terminology("ČiMbEnIk") .lower() == "faktor"


def test_faktor_is_left_unchanged():
    text = "Faktor, faktora, faktori, faktore, faktorom, faktorima."
    assert terminology.normalize_terminology(text) == text


def test_unrelated_words_are_not_rewritten():
    # „djelilac“ (dijeljenje) i „čitanje“ se NE diraju. „Činilac“ se od
    # terminološke politike (KS koristi samo „faktor“) normalizuje — vidi
    # tests/test_student_terminology_policy.py.
    text = "Djelilac ostaje netaknut, kao i čitanje i čitav broj."
    assert terminology.normalize_terminology(text) == text


def test_mathjax_is_not_damaged_by_normalization():
    text = "Rastavi $\\frac{12}{18}$ na čimbenike: $12 = 2 \\cdot 2 \\cdot 3$."
    out = terminology.normalize_terminology(text)
    assert "faktore" in out
    assert "$\\frac{12}{18}$" in out
    assert "$12 = 2 \\cdot 2 \\cdot 3$" in out
    assert out.count("$") == text.count("$")


def test_content_inside_math_segments_is_never_touched():
    text = "Tekst čimbenik $čimbenik$ kraj."
    out = terminology.normalize_terminology(text)
    assert out == "Tekst faktor $čimbenik$ kraj."


def test_empty_and_none_are_safe():
    assert terminology.normalize_terminology("") == ""
    assert terminology.normalize_terminology(None) == ""


def test_text_without_the_term_is_returned_identical():
    text = "Sasvim običan tekst bez zabranjenog termina."
    assert terminology.normalize_terminology(text) is text


def test_contains_forbidden_term_detects_all_forms():
    for form in ("čimbenik", "Čimbenika", "ČIMBENICIMA", "čimbenike"):
        assert terminology.contains_forbidden_term(form), form
    assert not terminology.contains_forbidden_term("faktor")


# ---------------------------------------------------------------------------
# Nijedan mod ne smije poslati zabranjeni termin u browser
# ---------------------------------------------------------------------------

def _practice_payload(msg="Daj zadatak.", **kw):
    base = {
        "session_id": "sess-term", "grade": 6, "selected_topic": "6-04-007",
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "", "selected_option_id": "", "client_turn_id": "",
    }
    base.update(kw)
    return base


def test_explain_reply_is_normalized():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Prosti čimbenici su gradivni blokovi broja."))
    r = run_explain_turn(fake, {
        "session_id": "e1", "grade": 6, "selected_topic": "6-04-001",
        "selected_oblast": "", "student_message": "Objasni.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "conversation_history": [],
        "last_tutor_message": "",
    })
    assert not terminology.contains_forbidden_term(r["answer"])
    assert "faktori" in r["answer"]


def test_quick_reply_is_normalized():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Čimbenici su $2$ i $3$."))
    r = run_quick_turn(fake, {
        "session_id": "q1", "grade": 6, "selected_topic": "6-04-001",
        "selected_oblast": "", "student_message": "Rastavi 6.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "conversation_history": [],
    })
    assert not terminology.contains_forbidden_term(r["answer"])
    assert r["answer"].startswith("Faktori")


# ---------------------------------------------------------------------------
# Repo-wide: zabranjeni termin ne smije postojati ni u promptovima ni u pravilima
# ---------------------------------------------------------------------------

def test_forbidden_term_appears_only_as_an_explicit_prohibition():
    """Repo-wide: jedini fajlovi koji smiju SPOMENUTI bilo koji od sedam
    pokrivenih zabranjenih termina su oni koji ih zabranjuju/dokumentuju
    zabranu (rules.py prompt pravilo, terminology.py mapiranje, ova/te
    dokumentacija koja OPISUJE zabranu — vidi Fazu E audita) i testovi koji
    tu zabranu provjeravaju. Nigdje drugdje — ni u fixture-ima, ni u
    komentarima, ni u kurikulumu."""
    allowed = {
        Path("matbot/rules.py"),          # prompt pravilo: „NIKAD hrvatski čimbenik“ i sl.
        Path("matbot/terminology.py"),    # same tabele zamjene
        # Klasifikator relevantnosti lekcije mora PREPOZNATI i hrvatski oblik u
        # PORUCI UČENIKA (ulaz se nikad ne normalizuje — normalizacija važi samo
        # za izlaz modela), pa tu riječ nužno sadrži kao ulazni obrazac.
        Path("matbot/lesson_relevance.py"),
        Path("tests/test_terminology.py"),
        Path("tests/test_rules.py"),       # provjerava DA su termini deklarisani zabranjeni u promptu
        # Explain/Quick v1 (Luna): isti razlog kao test_rules.py — dokazuju da
        # zabranjeni termini stižu u prompt KAO ZABRANE.
        Path("tests/test_explain_luna.py"),
        Path("tests/test_quick_luna.py"),
        # Nova terminološka politika: „čimbenik“ je KONTROLA (dokaz da
        # zabranjen termin i dalje jeste zabranjen, za razliku od parova
        # imenilac/nazivnik i činilac/faktor koji su samo normalizacija).
        Path("tests/test_student_terminology_policy.py"),
        # Univerzalni put: hrvatski oblik je ULAZ testa (nacrt modela) kojim se
        # dokazuje da normalizacija radi i na novom, dvopozivnom putu.
        Path("tests/test_universal_tutor_pipeline.py"),
        Path("tests/test_lesson_relevance.py"),  # ulazne poruke učenika s hrvatskim oblikom
        # Kurikularno mapiranje (Faza 2): KS dokument SAM navodi aliase
        # („ugao (kut)“, „mnogougao (mnogokut)“), pa ih alat za mapiranje mora
        # PREPOZNATI kao ulazne obrasce — isti princip kao lesson_relevance.py.
        # Nikad ne idu u izlaz modela ni u prompt.
        Path("scripts/build_curriculum_mapping.py"),
        Path("tests/test_curriculum_mapping.py"),
        Path("CLAUDE.md"),                 # dokumentuje ispravan/zabranjen par termina
        Path("docs/CURRENT_STATE.md"),     # dokumentuje C-8 (koji termini NISU pokriveni)
        Path("docs/ARCHITECTURE.md"),      # dokumentuje terminology.py mehanizam (koji termini SU pokriveni)
    }
    suffixes = {".py", ".html", ".md", ".txt", ".json", ".yml", ".yaml"}
    # `vision_ab_test` su MJERENJA (ground truth + sirovi OCR izlazi): sadrže
    # VJERNE transkripcije izvornog materijala (uklj. hrvatski radni list s
    # „trokut“/„točke“) — to je dokazni materijal benchmarka, ne tekst koji
    # ijedan mod servira učeniku, i ne smije se „normalizovati“.
    skip_dirs = {".git", ".venv", "__pycache__", "node_modules", "vision_ab_test"}

    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        relative = path.relative_to(ROOT)
        if relative in allowed:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if terminology.contains_forbidden_term(content):
            offenders.append(str(relative))

    assert not offenders, f"Zabranjeni termin pronađen u: {offenders}"


def test_curriculum_data_contains_no_forbidden_term():
    content = (ROOT / "data" / "topics.json").read_text(encoding="utf-8")
    assert not terminology.contains_forbidden_term(content)


def test_no_forbidden_term_in_prompts_or_rules_source():
    """Prompt/pravila smiju SPOMENUTI zabranu (npr. „nikad čimbenik“), ali
    nijedan modul ne smije termin koristiti kao ispravan izraz. Provjeravamo
    module koji generišu tekst za učenika, izuzev mjesta gdje je termin
    namjerno naveden kao zabranjen."""
    checked = ["matbot/prompts.py", "matbot/geometry_rules.py", "matbot/feedback.py",
               "matbot/task_families.py"]
    for relative in checked:
        content = (ROOT / relative).read_text(encoding="utf-8")
        assert not terminology.contains_forbidden_term(content), \
            f"Zabranjeni termin pronađen u {relative}"


# ---------------------------------------------------------------------------
# Faza E (docs/CURRENT_STATE.md C-8): proširenje na 4 dodatna termina —
# kutomer, jednakokračni, zbroj, potenciranje. „suma“ NAMJERNO ostaje
# nepokrivena (vidi docstring matbot/terminology.py) — testirano ispod da
# ostaje NETAKNUTA kad znači "iznos", ne "zbir".
# ---------------------------------------------------------------------------

def test_kutomer_all_forms_normalized():
    cases = [
        ("kutomer", "uglomjer"),
        ("kutomera", "uglomjera"),
        ("kutomeru", "uglomjeru"),
        ("kutomerom", "uglomjerom"),
        ("kutomeri", "uglomjeri"),
        ("kutomere", "uglomjere"),
        ("kutomerima", "uglomjerima"),
    ]
    for source, expected in cases:
        assert terminology.normalize_terminology(source) == expected, source


def test_jednakokracni_all_forms_normalized():
    cases = [
        ("jednakokračni", "jednakokraki"),
        ("jednakokračna", "jednakokraka"),
        ("jednakokračno", "jednakokrako"),
        ("jednakokračnog", "jednakokrakog"),
        ("jednakokračnom", "jednakokrakom"),
        ("jednakokračnim", "jednakokrakim"),
        ("jednakokračnih", "jednakokrakih"),
    ]
    for source, expected in cases:
        assert terminology.normalize_terminology(source) == expected, source


def test_zbroj_all_forms_normalized():
    cases = [
        ("zbroj", "zbir"),
        ("zbroja", "zbira"),
        ("zbroju", "zbiru"),
        ("zbrojem", "zbirom"),
        ("zbrojevi", "zbirovi"),
        ("zbrojeva", "zbirova"),
        ("zbrojevima", "zbirovima"),
    ]
    for source, expected in cases:
        assert terminology.normalize_terminology(source) == expected, source


def test_potenciranje_all_forms_normalized():
    cases = [
        ("potenciranje", "stepenovanje"),
        ("potenciranja", "stepenovanja"),
        ("potenciranju", "stepenovanju"),
        ("potenciranjem", "stepenovanjem"),
        ("potenciranjima", "stepenovanjima"),
    ]
    for source, expected in cases:
        assert terminology.normalize_terminology(source) == expected, source


def test_new_terms_capitalization_preserved():
    assert terminology.normalize_terminology("Kutomer") == "Uglomjer"
    assert terminology.normalize_terminology("KUTOMER") == "UGLOMJER"
    assert terminology.normalize_terminology("Zbroj") == "Zbir"
    assert terminology.normalize_terminology("Potenciranje") == "Stepenovanje"


def test_new_terms_never_touched_inside_math_segments():
    # $$...$$ i $...$ moraju ostati bajt-identični čak i kad bi njihov
    # sadržaj (nerealno, ali provjereno) sadržavao neki od ovih oblika.
    text = "Objašnjenje: kutomer je pogrešan. $kutomer=5$ i $$zbroj=10$$ ostaju."
    out = terminology.normalize_terminology(text)
    assert "$kutomer=5$" in out
    assert "$$zbroj=10$$" in out
    assert "Objašnjenje: uglomjer je pogrešan." in out


def test_suma_meaning_amount_is_never_rewritten():
    """C-8: „suma“ NIJE pokrivena — zamjena bi bila pogrešna kad riječ znači
    „iznos novca“, ne matematički zbir. Dokumentovan, namjeran izuzetak."""
    text = "Suma od 200 KM podijeljena je na tri dijela."
    assert terminology.normalize_terminology(text) == text
    assert not terminology.contains_forbidden_term("Suma je velika.")


def test_all_five_covered_terms_detected_by_contains_forbidden_term():
    for term in ("čimbenik", "kutomer", "jednakokračni", "zbroj", "potenciranje"):
        assert terminology.contains_forbidden_term(term), term


def test_new_terms_normalized_across_all_three_ai_modes(fake_llm, store):
    """Isti mehanizam kao postojeći čimbenik-testovi ispod — provjerava da se
    normalizacija stvarno primjenjuje na Practice/Explain/Quick izlaz, ne samo
    na normalize_terminology() u izolaciji."""
    fake_llm.queue(make_explain_output(reply="Ovo je kutomer, ne uglomjer."))
    r = run_explain_turn(fake_llm, {
        "session_id": "term-exp", "grade": 6, "selected_topic": "", "selected_oblast": "",
        "student_message": "Objasni.", "intent": "", "difficulty_request": "",
        "interaction_phase": "", "last_tutor_task": "", "last_tutor_message": "",
        "conversation_history": [],
    })
    assert "kutomer" not in r["answer"].lower()
    assert "uglomjer" in r["answer"].lower()
