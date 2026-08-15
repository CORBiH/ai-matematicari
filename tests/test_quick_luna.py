"""„Samo rezultat“ v1 — identitet modela i ugovor efektivnog prompta.

Migracija 2026-08-15 (ista kao Explain): tekstualni Quick poziv dobija
vlastiti, kodom auditiran izbor gpt-5.6-luna / low. Poziv sa SLIKOM namjerno
ostaje na modelu adaptera (OPENAI_MODEL_TEXT): stroga kapija čitljivosti
(D35-5/D35-6) i nezavisni imagecheck mjereni su na njemu.

Baseline prije migracije (gpt-5-mini, 20 živih poziva, sve ručno provjereno):
20/20 matematički tačno, tačne jedinice, oba rješenja za $x^2=9$, uredno
„Nedostaju podaci“ ponašanje — pa migracija NIJE mijenjala prompt; ovi testovi
čuvaju identitet modela i postojeći ugovor.
"""
import pytest

from matbot import config, release_config
from matbot.llm import OpenAIPracticeLLM
from matbot.prompts import build_quick_input, build_quick_instructions
from matbot.quick import MAX_HISTORY_MESSAGES, _clean_history


# ---------------------------------------------------------------------------
# 1) IDENTITET MODELA
# ---------------------------------------------------------------------------

def test_quick_model_is_luna_low_by_code():
    assert config.QUICK_MODEL == "gpt-5.6-luna"
    assert config.QUICK_REASONING_EFFORT == "low"


def test_release_enforcement_covers_the_quick_choice():
    assert release_config.REQUIRED_EFFECTIVE_CONFIG["quick_model"] == "gpt-5.6-luna"
    assert release_config.REQUIRED_EFFECTIVE_CONFIG["quick_reasoning_effort"] == "low"
    report = release_config.effective_configuration({})
    assert report["quick_model"] == "gpt-5.6-luna"
    assert report["quick_reasoning_effort"] == "low"


def test_text_call_sends_luna_low_but_image_call_keeps_the_adapter_model(monkeypatch):
    """Tekst → Luna/low; slika → BEZ per-poziv modela (adapter default),
    jer su kapija čitljivosti i imagecheck mjereni na tom modelu."""
    llm = OpenAIPracticeLLM()
    seen = {}

    def spy(instructions, input_text, text_format, **kw):
        seen.clear()
        seen.update(kw)
        raise RuntimeError("stop-before-network")

    monkeypatch.setattr(llm, "_structured_turn", spy)

    with pytest.raises(RuntimeError):
        llm.quick_turn("i", "u")
    assert seen["model"] == "gpt-5.6-luna"
    assert seen["reasoning_effort"] == "low"

    class _FakeImage:
        pass

    with pytest.raises(RuntimeError):
        llm.quick_turn("i", "u", image=_FakeImage())
    assert "model" not in seen and "reasoning_effort" not in seen


def test_practice_and_explain_choices_are_untouched_by_the_quick_migration():
    assert config.FAST_MODEL == "gpt-5.6-luna"
    assert config.FAST_REASONING_EFFORT == "low"
    assert config.EXPLAIN_MODEL == "gpt-5.6-luna"
    assert config.EXPLAIN_REASONING_EFFORT == "low"
    assert release_config.REQUIRED_EFFECTIVE_CONFIG["fast_model"] == "gpt-5.6-luna"
    assert release_config.REQUIRED_EFFECTIVE_CONFIG["explain_model"] == "gpt-5.6-luna"


# ---------------------------------------------------------------------------
# 2) UGOVOR PROMPTA — rezultat-only pravila stvarno stižu
# ---------------------------------------------------------------------------

def _instructions(grade=7):
    return build_quick_instructions(grade, lesson_title="Naslov", oblast="Oblast")


def test_role_and_language_reach_the_prompt():
    text = _instructions()
    assert "Samo rezultat" in text
    assert "bosanski jezik" in text
    assert "7. razred" in text


def test_result_only_and_exactness_rules_reach_the_prompt():
    text = _instructions()
    assert "SAMO konačan rezultat" in text
    assert "Ne prikazuj dugačak postupak" in text
    assert "s ispravnom jedinicom kad je potrebna" in text


def test_missing_data_rule_reaches_the_prompt():
    text = _instructions()
    assert "NE izmišljaj podatke" in text
    assert "koji podatak nedostaje" in text


def test_ambiguity_rule_reaches_the_prompt():
    text = _instructions()
    assert "više različitih tumačenja" in text


def test_notation_and_terminology_reach_the_prompt():
    text = _instructions()
    assert "\\frac" in text and "$...$" in text
    assert "Decimalni separator" in text          # decimalni zarez
    assert "uglomjer" in text and "kutomer" in text


def test_injection_anchor_reaches_the_prompt():
    assert "NEPOUZDAN" in _instructions()


def test_student_message_is_separately_labelled():
    text = build_quick_input("", "", [], "3/4 + 1/2")
    assert "PORUKA UČENIKA: 3/4 + 1/2" in text


def test_history_stays_bounded():
    raw = [{"role": "user", "content": f"p{i}"} for i in range(30)]
    assert len(_clean_history(raw)) == MAX_HISTORY_MESSAGES
