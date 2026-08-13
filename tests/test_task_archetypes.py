"""ARHETIP zadatka — druga mjera raznolikosti, jaca od sablona.

Nalaz iz rucnog QA: lekcija je imala 12 razlicitih recenica, a sve su bile ista
vjezba (kupovina, ukupna cijena, kusur). Sablon kaze kako recenica izgleda,
arhetip kaze sta ucenik mora URADITI.
"""
import json
import re
from pathlib import Path

import pytest

from matbot import archetype_support, task_archetypes as ta
from matbot.tutor import package_preflight as preflight

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = json.loads(
    (ROOT / "data" / "task_archetype_support.json").read_text(encoding="utf-8"))
QUALITY = json.loads(
    (ROOT / "data" / "deterministic_quality.json").read_text(encoding="utf-8"))
ROUTING = json.loads(
    (ROOT / "data" / "deterministic_routing.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clear():
    archetype_support._payload.cache_clear()
    yield
    archetype_support._payload.cache_clear()


# ---------------------------------------------------------------------------
# 1-3) POVRSINSKA ZAMJENA NIJE NOV ARHETIP; DRUGI OBLIK JESTE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first,second", [
    ("Izracunaj: $7+5$", "Izracunaj: $45+8$"),
    ("Ana kupuje hljeb od $2$ KM i mlijeko od $3$ KM. Koliki je kusur od $10$ KM?",
     "Haris kupuje sok od $4$ KM i kiflu od $1$ KM. Koliki je kusur od $20$ KM?"),
])
def test_number_and_name_substitution_keeps_the_archetype(first, second):
    assert ta.classify(first) == ta.classify(second)


@pytest.mark.parametrize("text,expected", [
    ("Izracunaj obim kvadrata stranice $5$ cm.", ta.DIRECT_COMPUTE),
    ("Ucenik je izracunao obim kao $5 \\cdot 3$. Gdje je pogrijesio?", ta.ERROR_ANALYSIS),
    ("Koji broj nedostaje: $12 + \\square = 19$?", ta.FIND_MISSING_VALUE),
    ("Nastavi skracivanje razlomka do kraja.", ta.COMPLETE_MISSING_STEP),
    ("Koja tvrdnja o razlomku je tacna?", ta.CHOOSE_CORRECT_REASONING),
    ("Koliko je $5$ m izrazeno u cm?", ta.TRANSLATE_REPRESENTATION),
    ("Koji od ponudjenih brojeva je prost?", ta.CLASSIFY_CASE),
])
def test_different_task_forms_get_different_archetypes(text, expected):
    assert ta.classify(text) == expected


def test_unknown_text_falls_back_to_the_neutral_archetype():
    """Nepoznato se ne proglasava egzoticnim oblikom da bi metrika izgledala bolje."""
    assert ta.classify("Neka recenica bez ijednog markera.") == ta.DIRECT_COMPUTE
    assert ta.classify("") == ""


# ---------------------------------------------------------------------------
# 4-5) SERVER BIRA SLJEDECI OBLIK
# ---------------------------------------------------------------------------

def _lesson_with(minimum):
    for lesson_id, row in SUPPORT["lessons"].items():
        if len(row["supported"]) >= minimum and not row["narrow_scope"]:
            return lesson_id
    raise AssertionError("nema lekcije s dovoljno arhetipa")


def test_server_selects_an_unused_supported_archetype():
    lesson_id = _lesson_with(3)
    supported = archetype_support.supported(lesson_id)
    chosen = archetype_support.preferred_archetype(lesson_id, [supported[0]])
    assert chosen in supported
    assert chosen != supported[0]


def test_server_recycles_least_recently_used_when_all_are_spent():
    lesson_id = _lesson_with(3)
    supported = list(archetype_support.supported(lesson_id))
    assert archetype_support.preferred_archetype(lesson_id, supported) == supported[0]


def test_narrow_lesson_is_never_pushed_to_invent_variety():
    narrow = SUPPORT["narrow_scope_lessons"]
    assert narrow, "ocekivana bar jedna uska lekcija"
    assert archetype_support.preferred_archetype(narrow[0], []) == ""


def test_rotation_has_a_rollback(monkeypatch):
    monkeypatch.setenv("MATBOT_ARCHETYPE_ROTATION", "disabled")
    assert archetype_support.preferred_archetype(_lesson_with(3), []) == ""


def test_preferred_archetype_reaches_the_prompt():
    from matbot.tutor import lesson_context, prompts

    context = lesson_context.build(6, "6-05-011")
    session = {
        "lesson_id": "6-05-011", "recent_tasks": ["Neki raniji zadatak."],
        "recent_turns": [], "difficulty_level": 1, "current_task": "",
        "current_options": [], "correct_option_id": "", "wrong_option_ids": [],
        "task_completed": False, "hint_level": 0, "difficulty": "standard",
        "expected_answer_summary": "", "recently_used_families": [],
        "recent_structures": [{"signature": "x",
                               "archetype": ta.MULTI_STEP_APPLICATION,
                               "text": "Kupovina i kusur."}],
    }
    text = prompts.build_tutor_input(context, session, "Daj mi novi zadatak.",
                                     required_task_intent="next_task")
    assert "TRAŽENI OBLIK SLJEDEĆEG ZADATKA" in text


# ---------------------------------------------------------------------------
# 6-7) KAPIJA NA „DAJ NOVI“
# ---------------------------------------------------------------------------

class _Option:
    def __init__(self, text):
        self.id, self.text = "a", text


class _Task:
    def __init__(self, text):
        self.text = text
        self.options = [_Option(f"${index}$") for index in range(4)]
        self.correct_option_index = 0


SUPPORTED = (ta.MULTI_STEP_APPLICATION, ta.DIRECT_COMPUTE, ta.ERROR_ANALYSIS,
             ta.FIND_MISSING_VALUE, ta.COMPARE_RESULTS)

SHOPPING = ("Ana kupuje hljeb od $2$ KM, mlijeko od $3$ KM i sok od $1$ KM. "
            "Koliki kusur dobije od $10$ KM?")


def _history(text, archetype=None):
    return [{"signature": "ranije", "text": text,
             "archetype": archetype or ta.classify(text)}]


def _gate(text, recent, supported=SUPPORTED):
    return preflight.structural_repetition_issue(
        _Task(text), recent, "next_task", lesson_id="6-05-011",
        supported_archetypes=supported)


# --- 4-5) KOZMETICKA ZAMJENA JE I DALJE ISTA VJEZBA -------------------------

def test_same_archetype_with_only_new_numbers_is_rejected():
    issue = _gate("Ana kupuje hljeb od $4$ KM, mlijeko od $2$ KM i sok od $2$ KM. "
                  "Koliki kusur dobije od $20$ KM?", _history(SHOPPING))
    assert issue is not None and issue.code == preflight.TASK_TOO_SIMILAR_CODE


def test_same_archetype_with_only_new_names_and_objects_is_rejected():
    """Kanonski par iz produkcije: zamijenjeni su ime i roba, zahtjev je isti."""
    issue = _gate("Haris kupuje kiflu od $1$ KM, jogurt od $2$ KM i vodu od $3$ KM. "
                  "Koliki kusur dobije od $20$ KM?", _history(SHOPPING))
    assert issue is not None and issue.code == preflight.TASK_TOO_SIMILAR_CODE


# --- 2-3) ISTI ARHETIP NIJE SAM PO SEBI DEFEKT ------------------------------

def test_repeating_the_archetype_is_not_automatically_invalid():
    """Regresija: prva verzija kapije obarala je objavu na 87,5 % baš ovdje."""
    candidate = ("Ana je od $10$ KM dobila $3$ KM kusura, a prva dva artikla "
                 "koštaju $4$ i $2$ KM. Koliko košta treći artikal?")
    assert ta.classify(candidate) == ta.classify(SHOPPING)      # isti arhetip…
    assert _gate(candidate, _history(SHOPPING)) is None         # …ali druga vježba


def test_same_archetype_with_a_different_requirement_publishes():
    previous = "Izracunaj obim kvadrata stranice $6$ cm."
    candidate = "Kvadrat ima obim $28$ cm. Kolika je njegova stranica?"
    assert _gate(candidate, _history(previous)) is None


def test_a_different_archetype_passes_the_gate():
    assert _gate("Ucenik je izracunao obim kao $5 \\cdot 3$. Gdje je pogrijesio?",
                 _history("Izracunaj obim kvadrata stranice $6$ cm.")) is None


def test_repetition_is_measured_against_the_whole_bounded_history():
    """Ponavljanje otprije dva zadatka je i dalje ponavljanje."""
    recent = _history("Izracunaj obim kvadrata stranice $6$ cm.")
    recent.append({"signature": "b", "text": "Koji broj nedostaje: $12+\\square=19$?",
                   "archetype": ta.FIND_MISSING_VALUE})
    issue = _gate("Izracunaj obim kvadrata stranice $9$ cm.", recent)
    assert issue is not None and issue.code == preflight.TASK_TOO_SIMILAR_CODE


# --- ROTACIJA: SAVJETODAVAN NALAZ, NIKAD PAD U OBJAVI ----------------------

def _rotation(text, recent, lesson_id="6-05-011"):
    return preflight.archetype_rotation_issue(
        _Task(text), recent, "next_task", lesson_id=lesson_id)


def test_repeated_form_asks_for_an_unused_one_without_being_a_defect():
    """Nalaz postoji da bi recenzent dobio recept — ali nije `task_too_similar`."""
    issue = _rotation("Izracunaj obim kvadrata stranice $9$ cm.",
                      _history("Izracunaj povrsinu kvadrata stranice $4$ cm."))
    assert issue is not None
    assert issue.code == preflight.TASK_FORM_REPEATED_CODE
    assert issue.code != preflight.TASK_TOO_SIMILAR_CODE
    assert "neiskorišten oblik" in issue.detail


def test_rotation_looks_two_tasks_back_not_only_at_the_previous_one():
    """Živi nalaz: model se vraćao na `DIRECT_COMPUTE` i preko jednog zadatka."""
    recent = _history("Izracunaj obim kvadrata stranice $4$ cm.")
    recent.append({"signature": "b", "archetype": ta.CHOOSE_CORRECT_REASONING,
                   "text": "Koja tvrdnja o obimu je tacna?"})
    assert _rotation("Izracunaj obim kvadrata stranice $9$ cm.", recent) is not None


def test_rotation_is_silent_once_every_supported_form_has_been_used():
    supported = archetype_support.supported("6-05-011")
    recent = [{"signature": str(index), "archetype": archetype,
               "text": f"Zadatak broj {index}."}
              for index, archetype in enumerate(supported)]
    recent.append({"signature": "z", "archetype": ta.DIRECT_COMPUTE,
                   "text": "Izracunaj obim kvadrata stranice $4$ cm."})
    assert _rotation("Izracunaj obim kvadrata stranice $9$ cm.", recent) is None


def test_rotation_advisory_follows_the_rollback_flag(monkeypatch):
    monkeypatch.setenv("MATBOT_ARCHETYPE_ROTATION", "disabled")
    archetype_support._payload.cache_clear()
    assert _rotation("Izracunaj obim kvadrata stranice $9$ cm.",
                     _history("Izracunaj povrsinu kvadrata stranice $4$ cm.")) is None


def test_narrow_lesson_gets_no_rotation_advisory():
    assert _rotation("Izracunaj obim kvadrata stranice $9$ cm.",
                     _history("Izracunaj povrsinu kvadrata stranice $4$ cm."),
                     lesson_id=SUPPORT["narrow_scope_lessons"][0]) is None


def test_narrow_lesson_is_not_gated_on_archetype_repetition():
    assert preflight.structural_repetition_issue(
        _Task("Ana kupuje kiflu od $1$ KM i sok od $2$ KM. Koliki kusur dobije "
              "od $10$ KM?"), _history(SHOPPING), "next_task",
        lesson_id="x", supported_archetypes=(ta.MULTI_STEP_APPLICATION,)) is None


# ---------------------------------------------------------------------------
# 8-10) POSLJEDICA PO RUTIRANJE
# ---------------------------------------------------------------------------

def test_template_rich_but_single_archetype_lesson_moves_to_luna():
    """Nalaz koji je pokrenuo ovaj rad: mnogo recenica, jedan oblik vjezbe."""
    demoted = ROUTING["archetype_demoted_lessons"]
    assert demoted, "ocekivana bar jedna takva lekcija"
    for lesson_id in demoted[:5]:
        row = QUALITY["lessons"][lesson_id]
        assert row["weak"] is False                    # po SABLONU je dobra
        assert row["distinct_archetypes"] <= 1         # ali nudi jedan oblik
        assert len(row["supported_archetypes"]) >= 3   # a mogla bi vise
        assert lesson_id in ROUTING["migrated_lessons"]
        assert lesson_id not in ROUTING["deterministic_lesson_exceptions"]


def test_archetype_rich_lesson_keeps_its_zero_call_route():
    for lesson_id in ROUTING["deterministic_lesson_exceptions"]:
        row = QUALITY["lessons"][lesson_id]
        assert row["weak"] is False
        assert (row["distinct_archetypes"] >= 2
                or row.get("narrow_scope")
                or len(row["supported_archetypes"]) < 3), lesson_id


def test_no_handwritten_lesson_id_routing_in_the_engine():
    for module in ("archetype_support.py", "task_archetypes.py",
                   "deterministic_variety.py"):
        source = (ROOT / "matbot" / module).read_text(encoding="utf-8")
        assert not re.search(r"\b\d-\d\d-\d\d\d\b", source), module
