"""„Samo rezultat“ v1 — identitet modela i ugovor efektivnog prompta.

Dvije migracije 2026-08-15: (1) tekstualni Quick poziv → gpt-5.6-luna / low
(isti obrazac kao Explain); (2) poziv sa SLIKOM → gpt-5.6-sol / low /
detail="original", po vision benchmarku (scratchpad/vision_ab_test: Sol 94,3%,
100% na štampanom, 0 čisto računskih grešaka). Stroga kapija čitljivosti
(D35-5/D35-6) i nezavisni imagecheck ostaju netaknuti.

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


def test_text_call_sends_luna_and_image_call_sends_sol(monkeypatch):
    """Tekst → Luna/low. Slika → Sol/low (migracija 2026-08-15, vision
    benchmark: Sol 94,3% / 0 čisto računskih grešaka — vidi
    scratchpad/vision_ab_test i obrazloženje u matbot/config.py)."""
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
    assert seen["model"] == "gpt-5.6-sol"
    assert seen["reasoning_effort"] == "low"
    assert seen["image"] is not None


def test_quick_image_identity_is_sol_low_original_by_code():
    assert config.QUICK_IMAGE_MODEL == "gpt-5.6-sol"
    assert config.QUICK_IMAGE_REASONING_EFFORT == "low"
    assert config.QUICK_IMAGE_DETAIL == "original"


def test_release_enforcement_covers_the_image_choice():
    """Zaostala env varijabla ne može tiho vratiti stari model slike."""
    req = release_config.REQUIRED_EFFECTIVE_CONFIG
    assert req["quick_image_model"] == "gpt-5.6-sol"
    assert req["quick_image_reasoning_effort"] == "low"
    assert req["quick_image_detail"] == "original"
    report = release_config.effective_configuration({})
    assert report["quick_image_model"] == "gpt-5.6-sol"
    assert report["quick_image_detail"] == "original"


def test_image_input_carries_detail_original():
    """Model vidi ORIGINALNU fotografiju — isto podešavanje kojim je
    benchmark mjeren; nema downscalinga radi cijene/latencije."""
    llm = OpenAIPracticeLLM()

    class _FakeImage:
        data_url = "data:image/jpeg;base64,AAAA"

    built = llm._build_input("tekst", _FakeImage())
    image_part = built[0]["content"][1]
    assert image_part["detail"] == "original"
    assert image_part["image_url"] == "data:image/jpeg;base64,AAAA"
    # tekstualni put netaknut: string prolazi kroz isto mjesto nepromijenjen
    assert llm._build_input("samo tekst", None) == "samo tekst"


def test_generic_model_variable_cannot_override_the_image_identity(monkeypatch):
    """OPENAI_MODEL_TEXT (generička varijabla) NE smije uticati na sliku:
    identitet slike je vlastita konstanta, a drift hvata release enforcement."""
    monkeypatch.setattr(config, "OPENAI_MODEL_TEXT", "gpt-nesto-drugo")
    llm = OpenAIPracticeLLM()
    seen = {}

    def spy(instructions, input_text, text_format, **kw):
        seen.update(kw)
        raise RuntimeError("stop")

    monkeypatch.setattr(llm, "_structured_turn", spy)

    class _FakeImage:
        pass

    with pytest.raises(RuntimeError):
        llm.quick_turn("i", "u", image=_FakeImage())
    assert seen["model"] == "gpt-5.6-sol"


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
    """v2: „samo rezultat" je PODRAZUMIJEVANI ugovor oblika (intent=result),
    a ne više jedna rečenica u opštem opisu moda. Kratkoća se traži i dalje —
    samo je sada vezana za namjeru koju je server prepoznao."""
    text = _instructions()
    assert "OBLIK ODGOVORA ZA OVU PORUKU: SAMO REZULTAT" in text
    assert "Bez postupka" in text
    assert "s jedinicom kad je potrebna" in text
    # Zahtjev za objašnjenjem dobija DRUGI ugovor, u istom modu.
    explain = build_quick_instructions(7, lesson_title="Naslov", oblast="Oblast",
                                       intent="explain")
    assert "UČENIK JE TRAŽIO OBJAŠNJENJE" in explain
    assert "NE upućuj učenika u drugi mod" in explain


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
