"""„Objasni mi“ v1 — identitet modela i ugovor efektivnog prompta.

Migracija 2026-08-15: Explain je do tada NASLJEĐIVAO model adaptera
(OPENAI_MODEL_TEXT → gpt-5-mini u produkciji). Sada ima vlastiti, kodom
auditiran izbor: gpt-5.6-luna uz reasoning effort low — isti mehanizam
(REQUIRED_EFFECTIVE_CONFIG) kojim je zaštićen izbor brzog Practice modela.

Ovi testovi čuvaju:
  1. identitet modela (kod, efektivna konfiguracija, stvarni poziv);
  2. da SVAKI sloj ugovora stvarno stigne u prompt (jezik, razred, lekcija,
     oblast, ishodi, terminologija, notacija, zabrana izmišljenih slika,
     ograničena historija, odvojena poruka učenika);
  3. da Practice i Quick ostaju netaknuti migracijom.
"""
import pytest

from matbot import config, release_config
from matbot.explain import MAX_HISTORY_MESSAGES, _clean_history, run_explain_turn
from matbot.llm import OpenAIPracticeLLM
from matbot.prompts import build_explain_input, build_explain_instructions
from tests.conftest import FakeLLM, make_explain_output


# ---------------------------------------------------------------------------
# 1) IDENTITET MODELA
# ---------------------------------------------------------------------------

def test_explain_model_is_luna_low_by_code():
    assert config.EXPLAIN_MODEL == "gpt-5.6-luna"
    assert config.EXPLAIN_REASONING_EFFORT == "low"


def test_release_enforcement_covers_the_explain_choice():
    """Odstupanje efektivnog izbora pada zatvoreno — isti mehanizam kao Luna
    brza ruta. Zaostala env varijabla ne može tiho vratiti stari model."""
    assert release_config.REQUIRED_EFFECTIVE_CONFIG["explain_model"] == "gpt-5.6-luna"
    assert release_config.REQUIRED_EFFECTIVE_CONFIG["explain_reasoning_effort"] == "low"
    report = release_config.effective_configuration({})
    assert report["explain_model"] == "gpt-5.6-luna"
    assert report["explain_reasoning_effort"] == "low"


def test_the_real_adapter_sends_luna_low_for_explain(monkeypatch):
    """Dokaz na STVARNOM adapteru (bez mreže): explain_turn prosljeđuje
    eksplicitan model i effort u _structured_turn."""
    llm = OpenAIPracticeLLM()
    seen = {}

    def spy(instructions, input_text, text_format, **kw):
        seen.update(kw)
        raise RuntimeError("stop-before-network")

    monkeypatch.setattr(llm, "_structured_turn", spy)
    with pytest.raises(RuntimeError):
        llm.explain_turn("i", "u")
    assert seen["model"] == "gpt-5.6-luna"
    assert seen["reasoning_effort"] == "low"
    assert seen["max_output_tokens"] == config.MAX_OUTPUT_TOKENS_EXPLAIN


def test_practice_and_quick_choices_are_untouched_by_the_migration(monkeypatch):
    """Migracija Explain-a NE dira ni Luna brzu rutu ni Quick."""
    assert config.FAST_MODEL == "gpt-5.6-luna"
    assert config.FAST_REASONING_EFFORT == "low"
    llm = OpenAIPracticeLLM()
    seen = {}

    def spy(instructions, input_text, text_format, **kw):
        seen.update(kw)
        raise RuntimeError("stop-before-network")

    monkeypatch.setattr(llm, "_structured_turn", spy)
    with pytest.raises(RuntimeError):
        llm.quick_turn("i", "u")
    # Quick i dalje nasljeđuje model adaptera — nikakav novi izbor.
    assert "model" not in seen or seen.get("model") is None


# ---------------------------------------------------------------------------
# 2) EFEKTIVNI PROMPT — svaki sloj ugovora stvarno stigne
# ---------------------------------------------------------------------------

def _instructions(grade=7):
    return build_explain_instructions(
        grade, lesson_title="Jednačine sa sabiranjem i oduzimanjem u Z",
        oblast="Cijeli brojevi")


def test_language_grade_and_lesson_reach_the_prompt():
    text = _instructions()
    assert "bosanski jezik" in text
    assert "7. razred" in text
    # Naslov lekcije i oblast idu u ULAZ prompta (build_explain_input), ne u
    # instrukcije — instrukcije nose pravila razreda/oblasti iz rules.py.
    input_text = build_explain_input(
        "Jednačine sa sabiranjem i oduzimanjem u Z", "Cijeli brojevi",
        [], "Objasni mi ovo.")
    assert "LEKCIJA: Jednačine sa sabiranjem i oduzimanjem u Z" in input_text
    assert "Cijeli brojevi" in input_text


def test_terminology_and_notation_rules_reach_the_prompt():
    text = _instructions()
    assert "uglomjer" in text and "trougao" in text          # obavezni termini
    assert "kutomer" in text and "zbroj" in text             # zabranjeni termini
    assert "\\frac" in text and "$...$" in text              # MathJax pravila
    assert "Decimalni separator" in text                     # decimalni zarez


def test_language_precision_covers_the_measured_drift():
    """Baseline (gpt-5-mini, 20 poziva) je mjerio: „jednadžba“, „ravnina“,
    „obe“, „promenljive“ i uniju opisanu kao „zbir elemenata“."""
    text = _instructions()
    assert "jednadžba" in text and "ravnina" in text
    assert "obje" in text and "promjenljiva" in text
    assert "ne kao „zbir“" in text


def test_no_fake_visual_rule_reaches_the_prompt():
    text = _instructions()
    assert "NEMAŠ mogućnost prikazivanja slika" in text
    assert "kao što vidiš na" in text


def test_ne_vs_cdot_caution_reaches_the_prompt():
    """Baseline b06: model je napisao $O=n\\ne a$ (≠ umjesto množenja)."""
    text = _instructions()
    assert "\\ne" in text and "\\cdot" in text


def test_injection_anchor_reaches_the_prompt():
    text = _instructions()
    assert "NEPOUZDAN" in text            # učenikov tekst ne mijenja pravila


def test_grade_styles_differ():
    assert _instructions(6) != build_explain_instructions(
        9, lesson_title="Jednačine sa sabiranjem i oduzimanjem u Z",
        oblast="Cijeli brojevi")


def test_lesson_objectives_reach_the_input_when_present():
    text = build_explain_input(
        "Naslov", "Oblast", [], "Objasni mi ovo.",
        lesson_objectives=("prepoznavanje razlomka", "pisanje razlomka"))
    assert "CILJ LEKCIJE" in text
    assert "prepoznavanje razlomka" in text


def test_missing_objectives_change_nothing():
    text = build_explain_input("Naslov", "Oblast", [], "Objasni mi ovo.")
    assert "CILJ LEKCIJE" not in text


def test_student_message_is_a_separate_labelled_line():
    text = build_explain_input("Naslov", "Oblast", [], "Zašto?",
                               lesson_objectives=())
    assert "PORUKA UČENIKA: Zašto?" in text


def test_history_is_bounded_to_three_exchanges():
    raw = [{"role": "user", "content": f"p{i}"} for i in range(20)]
    assert len(_clean_history(raw)) == MAX_HISTORY_MESSAGES


def test_run_explain_turn_feeds_objectives_from_canonical_data(store=None):
    """Integracija: lekcija s mapiranim ishodima ih dobije u ulazu prompta."""
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).resolve().parent.parent
                       / "data" / "lesson_objectives.compiled.json")
                      .read_text(encoding="utf-8"))["lessons"]
    lesson_id = next(k for k, v in data.items()
                     if v.get("primary_skills") and k.startswith("6-"))
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Evo objašnjenja."))
    captured = {}
    original = fake.explain_turn

    def spy(instructions, input_text):
        captured["input"] = input_text
        return original(instructions, input_text)

    fake.explain_turn = spy
    r = run_explain_turn(fake, {
        "grade": 6, "selected_topic": lesson_id, "selected_oblast": "",
        "student_message": "Objasni mi ovu lekciju.",
        "conversation_history": [], "interaction_phase": "",
    })
    assert r["status"] == "ready"
    assert "CILJ LEKCIJE" in captured["input"]
