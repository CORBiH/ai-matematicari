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


def test_practice_choice_is_untouched_by_the_migration():
    """Migracija Explain-a NE dira Luna brzu rutu.

    (Quick je u međuvremenu dobio VLASTITU migraciju — njegov identitet čuva
    tests/test_quick_luna.py, pa raniji „Quick nema svoj model“ dio ovog
    testa više ne opisuje proizvod.)"""
    assert config.FAST_MODEL == "gpt-5.6-luna"
    assert config.FAST_REASONING_EFFORT == "low"


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


def test_student_message_is_a_separate_labelled_line():
    text = build_explain_input("Naslov", "Oblast", [], "Zašto?")
    assert "PORUKA UČENIKA: Zašto?" in text


def test_curriculum_objectives_stay_out_of_the_explain_input():
    """POVUČENO NA ŽIVOM DOKAZU (finalna kampanja f01): mapiranje ishoda za
    6-01-006 nosi gradivo pogrešnog razreda (ℝ=ℚ∪𝕀 za šestaše) uz
    confidence=high. Dok se artefakt ne auditira po razredu, Explain prompt
    NE SMIJE nositi „CILJ LEKCIJE“ liniju."""
    import inspect

    from matbot import explain as explain_module
    from matbot import prompts as prompts_module
    assert "lesson_objectives" not in inspect.signature(
        prompts_module.build_explain_input).parameters
    assert "CILJ LEKCIJE" not in inspect.getsource(prompts_module)
    assert "lesson_objectives" not in inspect.getsource(explain_module)


def test_divisibility_self_check_reaches_the_prompt():
    """Živi nalaz f02 (Luna, 56 poziva): „$12+18=30$ … vidimo da $30$ nije
    djeljiv sa $6$“ — a $30=6\cdot5$. Tvrdnja o djeljivosti mora biti
    provjerena dijeljenjem prije slanja."""
    text = _instructions()
    assert "TVRDNJA O DJELJIVOSTI" in text


def test_environment_ban_covers_aligned_not_only_cases():
    """Živi nalaz f08/f27: Luna piše $egin{aligned}x+y&=5\end{aligned}$,
    server to ispravno odbija cio — pa je pravilo prošireno s cases na SVA
    okruženja i znak &."""
    text = _instructions()
    assert "aligned" in text and "&" in text


def test_history_is_bounded_to_three_exchanges():
    raw = [{"role": "user", "content": f"p{i}"} for i in range(20)]
    assert len(_clean_history(raw)) == MAX_HISTORY_MESSAGES
