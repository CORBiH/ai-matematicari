r"""Naučni zapis: popravlja se GENERISANJE, ne popuštaju se validatori.

MJERENO (12 živih turnova na 8-01-017, samo 6 objavljenih). Šest odbijanja NIJE
šest grešaka nego JEDAN problem generisanja, grupisano po uzroku:

    semantically_duplicate_options (dvije opcije su ISTI broj)   5/6
    numeric_inconsistency u TEKSTU ZADATKA (pogrešna jednakost)  4/6
    raznolikost / težina / promašen ključ / drift                0/6

Uzrok je specifičan za ovu lekciju: najprirodniji distraktor za naučni zapis je
ISTA VRIJEDNOST drukčije zapisana ($7,2\cdot 10^{-4} = 72\cdot 10^{-5} =
0,72\cdot 10^{-3}$), a to su tri TAČNA odgovora koje `option_equivalence` mora
odbiti. Uz to je oblik „Dopuni: $0,0065 = 6,5\cdot 10^{\square}$“ stavljao
jednakost u tekst zadatka, koju je model onda pogriješio ($0,00072 = 7,2\cdot
10^{4}$).

POPRAVKA JE PODATAK, NE MOTOR: ugovor lekcije (`data/semantic_families.json` →
kompajlirano u `lesson_semantics.compiled.json`) sada imenuje dva legitimna
smjera i zabranjuje obje izmjerene greške. Tekst ide ISTI Tutoru i Recenzentu
postojećim putem (`prompt_block`). Nijedan validator nije oslabljen i nijedan
novi bloker nije uključen.
"""
import json
import re
from pathlib import Path

import pytest

from matbot.semantics import contracts as semantic_contracts
from matbot.semantics import detectors

ROOT = Path(__file__).resolve().parent.parent
COMPILED = json.loads((ROOT / "data" / "lesson_semantics.compiled.json")
                      .read_text(encoding="utf-8"))["lessons"]
LESSON = "8-01-017"


def _contract():
    return semantic_contracts.contract_for(LESSON)


def _block():
    return _contract().prompt_block()


# ---------------------------------------------------------------------------
# 1. OBA LEGITIMNA SMJERA SU IMENOVANA
# ---------------------------------------------------------------------------

def test_both_curriculum_supported_directions_are_named():
    """Smjerove dokazuje deterministički generator te lekcije
    (`plain_to_scientific` i `scientific_to_plain`) — nisu izmišljeni."""
    block = _block()
    assert "TAČNO JEDNOM od dva smjera" in block
    assert "traži se naučni zapis" in block
    assert "traži se OBIČNA vrijednost" in block


def test_the_forward_direction_states_the_canonical_rule():
    block = _block()
    assert "1 \\le |a| < 10" in block
    assert "cio broj" in block


def test_the_reverse_direction_must_not_demand_scientific_notation():
    """„Koliko iznosi $9,9\\cdot 10^5$?“ ima OBIČAN broj kao tačan odgovor."""
    block = _block()
    assert "obična decimalna vrijednost, a NE naučni zapis" in block


# ---------------------------------------------------------------------------
# 2. OBJE IZMJERENE GREŠKE SU IZRIČITO ZABRANJENE
# ---------------------------------------------------------------------------

def test_the_dominant_failure_duplicate_valued_options_is_forbidden():
    """5/6 odbijanja: dvije opcije su bile isti broj drukčije zapisan."""
    block = _block()
    assert "RAZLIČITU BROJEVNU VRIJEDNOST" in block
    assert "72 \\cdot 10^{-5}" in block          # konkretan izmjeren primjer
    assert "nikad drugim zapisom iste vrijednosti" in block


def test_the_equality_in_the_task_text_shape_is_forbidden():
    """4/6 odbijanja: pogrešna jednakost u tekstu zadatka."""
    block = _block()
    assert "ne piši jednakost koju učenik treba dopuniti" in block
    assert "0,0065 = 6,5 \\cdot 10^{\\square}" in block


# ---------------------------------------------------------------------------
# 3. TEKST STVARNO STIŽE DO TUTORA I RECENZENTA
# ---------------------------------------------------------------------------

def test_the_contract_reaches_both_prompts_identically():
    from matbot.tutor import lesson_context, prompts
    context = lesson_context.build(8, LESSON)
    assert context is not None
    block = prompts._semantic_contract_block(context)
    assert "TAČNO JEDNOM od dva smjera" in block
    tutor = prompts.build_tutor_instructions(context)
    reviewer = prompts.build_reviewer_instructions(context)
    for needle in ("RAZLIČITU BROJEVNU VRIJEDNOST",
                   "ne piši jednakost koju učenik treba dopuniti"):
        assert needle in tutor, needle
        assert needle in reviewer, needle


# ---------------------------------------------------------------------------
# 4. NIŠTA DRUGO NIJE DIRANO
# ---------------------------------------------------------------------------

def test_other_power_lessons_keep_their_original_contract():
    others = [lid for lid, entry in COMPILED.items()
              if entry["detector"] == "power_arithmetic" and lid != LESSON]
    assert others
    for lesson_id in others:
        lines = COMPILED[lesson_id]["prompt_lines"]
        assert len(lines) == 3, (lesson_id, lines)
        assert "dva smjera" not in "\n".join(lines)


def test_only_this_lesson_received_the_extra_contract():
    long_ones = [lid for lid, entry in COMPILED.items()
                 if len(entry["prompt_lines"]) >= 9]
    assert long_ones == [LESSON], long_ones


def test_the_builder_extension_is_generic_not_lesson_specific():
    source = (ROOT / "scripts" / "build_lesson_semantics.py").read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", source), "ID lekcije u kompajleru"
    assert "scientific_notation" not in source, "naziv pojma u kompajleru"


# ---------------------------------------------------------------------------
# 5. VALIDATORI NISU OSLABLJENI (regresija nad izmjerenim greškama)
# ---------------------------------------------------------------------------

def test_options_with_the_same_value_are_still_rejected():
    """Tačno oni parovi koji su rušili turnove moraju i dalje padati."""
    from matbot import option_equivalence
    equal_forms = [r"$7,2 \cdot 10^{-4}$", r"$72 \cdot 10^{-5}$",
                   r"$0,72 \cdot 10^{-3}$", r"$7,2 \cdot 10^{-5}$"]
    assert option_equivalence.find_equivalent_option_pairs(equal_forms)


def test_a_wrong_power_of_ten_equality_is_still_rejected():
    """Živi nalaz: $0,00072 = 7,2\\cdot 10^{4}$ (server: 0.00072 vs 72000)."""
    from matbot import mathcheck
    assert mathcheck.find_numeric_inconsistencies(
        r"Dopuni: $0,00072 = 7,2 \cdot 10^{4}$")


def test_a_correct_equality_is_still_accepted():
    from matbot import mathcheck
    assert not mathcheck.find_numeric_inconsistencies(
        r"Vrijedi $0,00072 = 7,2 \cdot 10^{-4}$.")


def test_distinct_small_valued_options_are_no_longer_falsely_equivalent():
    """IZMJERENI UZROK 5/6 ODBIJANJA: tolerancija zaokruživanja se računala iz
    broja decimala ZAPISA, pa je za `7,2\\cdot 10^{-4}` bila 0,055 dok su same
    vrijednosti reda 0,0007 — svaka mala vrijednost je bila „jednaka“ svakoj.

    Ove četiri opcije su očito RAZLIČITI brojevi i nijedan par ne smije pasti."""
    from matbot import option_equivalence
    distinct = [r"$7,2 \cdot 10^{-4}$", r"$7,2 \cdot 10^{-5}$",
                r"$7,2 \cdot 10^{-3}$", r"$6,2 \cdot 10^{-4}$"]
    assert not option_equivalence.find_equivalent_option_pairs(distinct)


def test_equal_values_are_still_caught_after_the_tolerance_fix():
    """Popravka NE popušta provjeru: isti broj drukčije zapisan i dalje pada,
    i za negativne i za pozitivne izložioce, i prema običnom decimalnom zapisu."""
    from matbot import option_equivalence
    for pair in ([r"$7,2 \cdot 10^{-4}$", r"$72 \cdot 10^{-5}$"],
                 [r"$7,2 \cdot 10^{-4}$", r"$0,72 \cdot 10^{-3}$"],
                 [r"$7,2 \cdot 10^{-4}$", r"$0,00072$"],
                 [r"$7,2 \cdot 10^{4}$", r"$72 \cdot 10^{3}$"],
                 [r"$\sqrt{12}$", r"$2\sqrt{3}$"],
                 [r"$\frac{1}{2}$", r"$0,5$"]):
        assert option_equivalence.find_equivalent_option_pairs(pair), pair


def test_the_rounding_tolerance_never_swallows_the_values_own_magnitude():
    from matbot import option_equivalence as oe
    # Zapis s jednom decimalom, a vrijednosti reda 1e-4: tolerancija mora pasti
    # na udio veličine, ne ostati apsolutnih 0,055.
    tolerance = oe._numeric_tolerance(r"7,2 \cdot 10^{-4}", r"6,2 \cdot 10^{-4}",
                                      0.00072)
    assert tolerance < 0.00072, tolerance
    # Zaokruživanje na velikim vrijednostima ostaje netaknuto.
    big = oe._numeric_tolerance("16,67", "16,6667", 16.6667)
    assert big == pytest.approx(0.5 * (10 ** -4) * 1.1)


# ---------------------------------------------------------------------------
# 6. SEMANTIČKI BLOKER OSTAJE ISKLJUČEN; SUSJEDNE GARANCIJE NEPROMIJENJENE
# ---------------------------------------------------------------------------

def test_the_scientific_notation_semantic_blocker_stays_unwired():
    assert detectors._detect_scientific_notation not in detectors.DETECTORS.values()
    assert "power_arithmetic" not in detectors.DETECTORS
    result = detectors.detect(_contract(), "bilo šta", answer_text=r"$42 \cdot 10^{4}$")
    assert result.status == detectors.STATUS_UNSUPPORTED
    assert not result.blocking


def test_authority_status_still_reports_unknown():
    status = json.loads((ROOT / "data" / "semantic_authority_status.json")
                        .read_text(encoding="utf-8"))["lessons"]
    assert status[LESSON]["detector_status"] == "UNKNOWN"
    assert status[LESSON]["server_can_refuse_publication"] is False


def test_single_hint_routing_and_config_are_untouched():
    from matbot import archetype_support, config, form_variants, release_config
    assert config.practice_single_hint_enabled() is True
    assert archetype_support._enabled() is True
    assert form_variants._enabled() is True
    assert release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_PIPELINE"] == \
        "universal_two_call"
    assert release_config.REQUIRED_EFFECTIVE_CONFIG["fast_model"] == "gpt-5.6-luna"


def test_zero_call_routes_stay_zero_call(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore

    class NoModel:
        def __getattr__(self, name):
            def explode(*args, **kwargs):
                raise AssertionError(f"model pozvan na 0-pozivnoj ruti: {name}")
            return explode

    response = run_practice_turn(SessionStore(), NoModel(), {
        "session_id": "sn-gen-det", "grade": 6, "selected_topic": "6-01-001",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": ""})
    assert response.get("status") == "ready"
