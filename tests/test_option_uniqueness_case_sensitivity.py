# -*- coding: utf-8 -*-
"""Jedinstvenost opcija mora biti CASE-SENSITIVE (živi nalaz, poziv 12).

Validan `choose_correct_formula` zadatak s opcijama

    $R=2r$   $r=2R$   $R=r^2$   $O=\\pi r^2$

bio je odbijen kao „duple opcije“. Dva NEZAVISNA uzroka, oba popravljena:

  1. schema._validate_options je radio `text.lower()` prije poređenja, pa su
     `$R=2r$` i `$r=2R$` postali isti ključ `$r=2r$`.
  2. option_equivalence._value_expression je poredio SAMO desnu stranu
     jednakosti, pa su `$D=a\\sqrt3$` i `$d=a\\sqrt3$` (i `$P=ab$`/`$p=ab$`,
     `$B=a^2$`/`$b=a^2$`, ...) bili proglašeni SEMANTIČKI istima.

U ovom projektu veličina slova nosi značenje: r/R (poluprečnik/prečnik),
d/D (dijagonala strane/prostorna), P/p, O/o, B/b, H/h.
"""
import pytest

from tests.conftest import FakeLLM, make_options, make_output, make_task
from matbot import option_equivalence as oe
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore

LIVE_CALL12_OPTIONS = ("$R=2r$", "$r=2R$", "$R=r^2$", "$O=\\pi r^2$")

# Parovi koji se razlikuju SAMO po veličini slova simbola.
CASE_DISTINCT_PAIRS = [
    ("$R=2r$", "$r=2R$"),
    ("$D=a\\sqrt3$", "$d=a\\sqrt3$"),
    ("$P=ab$", "$p=ab$"),
    ("$O=2a+2b$", "$o=2a+2b$"),
    ("$B=a^2$", "$b=a^2$"),
    ("$H=10$", "$h=10$"),
]


GEOMETRY_GROUP_ORDER = ["direct_formula_application", "choose_correct_formula",
                        "find_missing_dimension", "inverse_formula_problem",
                        "detect_formula_error", "compare_figures",
                        "unit_conversion", "practical_geometry_problem"]


def seed_choose_correct_formula(store, topic="6-08-006", grade=6,
                                title="Centar, poluprečnik/polumjer i prečnik/promjer",
                                oblast="Skupovi tačaka, kružnica i krug", sid="uniq-sess"):
    """Označi prvu porodicu geometrijske grupe kao savladanu da server izabere
    `choose_correct_formula` — to je porodica kojoj pitanje „Koja formula...“
    stvarno pripada (živi poziv 12)."""
    s = store.load(session_id=sid, grade=grade, lesson_id=topic,
                   lesson_title=title, oblast=oblast, mode="practice")
    s["correctly_completed_families"] = GEOMETRY_GROUP_ORDER[:1]
    store.save(s)


def turn(msg="Daj mi zadatak.", topic="6-08-006", grade=6, **kw):
    base = {"session_id": "uniq-sess", "grade": grade, "selected_topic": topic,
            "selected_oblast": "", "student_message": msg, "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": ""}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 1: tačan živi skup je prihvaćen
# ---------------------------------------------------------------------------

def test_1_exact_live_option_set_is_accepted_by_both_layers():
    assert oe.find_textual_duplicate_pairs(list(LIVE_CALL12_OPTIONS)) == []
    assert oe.find_equivalent_option_pairs(list(LIVE_CALL12_OPTIONS)) == []


def test_1b_exact_live_option_set_accepted_through_full_practice_path():
    store, fake = SessionStore(), FakeLLM()
    seed_choose_correct_formula(store)
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Koja formula pravilno povezuje prečnik i poluprečnik kruga?",
        expected="$R=2r$",
        options=make_options(*LIVE_CALL12_OPTIONS), correct_option_index=0,
        task_family="choose_correct_formula", answer_kind="formula")))
    r = run_practice_turn(store, fake, turn())
    assert r["status"] == "ready", r["answer"]
    texts = [o["text"] for o in r["next_state"]["task"]["options"]]
    assert sorted(texts) == sorted(LIVE_CALL12_OPTIONS)
    assert fake.practice_call_count == 1


# ---------------------------------------------------------------------------
# 2-4: pravi duplikati i dalje padaju
# ---------------------------------------------------------------------------

def test_2_identical_options_still_rejected():
    assert oe.find_textual_duplicate_pairs(["$R=2r$", "$R=2r$", "$x$", "$y$"]) == [(0, 1)]


def test_3_leading_trailing_whitespace_does_not_evade_detection():
    assert oe.find_textual_duplicate_pairs(["  $R=2r$  ", "$R=2r$", "$x$", "$y$"]) == [(0, 1)]
    assert oe.find_textual_duplicate_pairs(
        ["Sabrao je brojnike.  ", "Sabrao je brojnike.", "A", "B"]) == [(0, 1)]


def test_4_harmless_internal_spacing_does_not_evade_detection():
    assert oe.find_textual_duplicate_pairs(["$R = 2r$", "$R=2r$", "$x$", "$y$"]) == [(0, 1)]
    assert oe.find_textual_duplicate_pairs(["$P = a b$", "$P=ab$", "$x$", "$y$"]) == [(0, 1)]


def test_identical_prose_is_caught_only_by_textual_layer():
    """Semantička provjera ne umije kanonikalizovati prozu — zato gruba
    tekstualna provjera MORA ostati."""
    a = "Sabrao je brojnike i nazivnike odvojeno."
    assert oe.options_are_equivalent(a, a) is False
    assert oe.find_textual_duplicate_pairs([a, a]) == [(0, 1)]


def test_duplicate_prose_rejected_through_full_path_without_mutation():
    store, fake = SessionStore(), FakeLLM()
    before = store.peek("uniq-sess")
    fake.queue(make_output(reply="Evo.", new_task=make_task(
        options=make_options("Ista tvrdnja.", "Ista tvrdnja.", "Druga.", "Treća."),
        correct_option_index=0)))
    r = run_practice_turn(store, fake, turn())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r
    assert store.peek("uniq-sess") == before
    assert fake.practice_call_count == 1


# ---------------------------------------------------------------------------
# 5-10: razlike u veličini slova ostaju RAZLIČITE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", CASE_DISTINCT_PAIRS)
def test_5_to_10_case_differing_symbols_remain_distinct(a, b):
    assert oe.options_are_equivalent(a, b) is False, f"{a} vs {b} semantic"
    assert oe.find_textual_duplicate_pairs([a, b]) == [], f"{a} vs {b} textual"


@pytest.mark.parametrize("a,b", CASE_DISTINCT_PAIRS)
def test_case_differing_pair_accepted_through_full_practice_path(a, b):
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Koja tvrdnja o oznakama je tačna?",
        expected=a,
        options=make_options(a, b, "$x=1$", "$y=2$"), correct_option_index=0,
        task_family="recognize_correct_statement", answer_kind="formula")))
    r = run_practice_turn(store, fake, turn(msg="Daj mi zadatak."))
    # Zadatak smije pasti na drugom ugovoru, ali NIKAD zbog duplikata opcija.
    assert "duple opcije" not in (r.get("answer") or "")
    assert fake.practice_call_count == 1


def test_normalized_key_preserves_case_and_symbols():
    assert oe.normalized_option_key("$R = 2r$") == "$R=2r$"
    assert oe.normalized_option_key("$r = 2R$") == "$r=2R$"
    assert oe.normalized_option_key("$R=2r$") != oe.normalized_option_key("$r=2R$")


# ---------------------------------------------------------------------------
# 11: semantički duplikati i dalje padaju
# ---------------------------------------------------------------------------

SEMANTIC_DUPLICATES = [
    ("$a\\sqrt{2}$", "$\\sqrt{2}a$"),
    ("$2a$", "$a\\cdot2$"),
    ("$8\\sqrt{2}\\,\\text{cm}$", "$11,3\\,\\text{cm}$"),
    ("$\\frac{3}{8}$", "$\\frac{6}{16}$"),
    ("$d=a\\sqrt{2}$", "$d=\\sqrt{2}a$"),      # ISTA lijeva strana → i dalje isto
    ("$d=2a$", "$d=a\\cdot2$"),
]


@pytest.mark.parametrize("a,b", SEMANTIC_DUPLICATES)
def test_11_semantic_duplicates_still_detected(a, b):
    assert oe.options_are_equivalent(a, b) is True, f"{a} vs {b}"


def test_11b_semantic_duplicate_still_rejected_through_full_path():
    store, fake = SessionStore(), FakeLLM()
    before = store.peek("uniq-sess")
    fake.queue(make_output(reply="Evo.", new_task=make_task(
        text="Kolika je dijagonala kvadrata stranice $a$?",
        expected="$d=a\\sqrt{2}$",
        options=make_options("$d=a\\sqrt{2}$", "$d=\\sqrt{2}a$", "$d=2a$", "$d=a^2$"),
        correct_option_index=0, task_family="direct_formula_application")))
    r = run_practice_turn(store, fake, turn(topic="8-04-004", grade=8))
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("uniq-sess") == before
    assert fake.practice_call_count == 1


def test_units_still_prevent_false_equivalence():
    assert oe.options_are_equivalent("$16\\,\\text{cm}$", "$16\\,\\text{cm}^2$") is False


# ---------------------------------------------------------------------------
# 12-15: identitet tačne opcije, curenje, stanje, jedan poziv
# ---------------------------------------------------------------------------

def test_12_correct_option_identity_survives_sanitation_and_shuffle():
    for _ in range(12):
        store, fake = SessionStore(), FakeLLM()
        seed_choose_correct_formula(store)
        fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
            text="Koja formula pravilno povezuje prečnik i poluprečnik kruga?",
            expected="$R=2r$",
            options=make_options(*LIVE_CALL12_OPTIONS), correct_option_index=0,
            task_family="choose_correct_formula", answer_kind="formula")))
        r = run_practice_turn(store, fake, turn())
        assert r["status"] == "ready"
        sess = store.peek("uniq-sess")
        by_id = {o["id"]: o["text"] for o in sess["current_options"]}
        assert by_id[sess["correct_option_id"]] == "$R=2r$"
        texts = [o["text"] for o in r["next_state"]["task"]["options"]]
        assert sorted(texts) == sorted(LIVE_CALL12_OPTIONS)


def test_13_expected_answer_and_correct_option_id_do_not_leak():
    import json

    store, fake = SessionStore(), FakeLLM()
    seed_choose_correct_formula(store)
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Koja formula pravilno povezuje prečnik i poluprečnik kruga?",
        expected="INTERNO-RJESENJE-R2R",
        options=make_options(*LIVE_CALL12_OPTIONS), correct_option_index=0,
        task_family="choose_correct_formula", answer_kind="formula")))
    r = run_practice_turn(store, fake, turn())
    raw = json.dumps(r, ensure_ascii=False)
    assert "INTERNO-RJESENJE-R2R" not in raw
    assert "correct_option_id" not in raw
    assert "expected_answer" not in raw
    for opt in r["next_state"]["task"]["options"]:
        assert set(opt.keys()) == {"id", "text"}


def test_14_15_rejected_duplicate_does_not_mutate_state_and_one_call():
    store, fake = SessionStore(), FakeLLM()
    before = store.peek("uniq-sess")
    fake.queue(make_output(reply="Evo.", new_task=make_task(
        options=make_options("$R=2r$", "$R=2r$", "$x$", "$y$"), correct_option_index=0)))
    r = run_practice_turn(store, fake, turn())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("uniq-sess") == before
    assert fake.practice_call_count == 1


# ---------------------------------------------------------------------------
# 16-18: susjedni invarijanti nisu oslabljeni
# ---------------------------------------------------------------------------

def test_16_geometry_notation_validation_remains_strict():
    from matbot import geometrycheck as gc

    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Krug ima prečnik $D=10\\,\\text{cm}$. Izračunaj obim kruga.",
        expected="$31,4\\,\\text{cm}$",
        options=make_options("$31,4\\,\\text{cm}$", "$15,7\\,\\text{cm}$",
                             "$62,8\\,\\text{cm}$", "$314\\,\\text{cm}$"),
        correct_option_index=0, task_family="direct_formula_application",
        answer_kind="decimal")))
    r = run_practice_turn(store, fake, turn())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert gc.CIRCLE_DIAMETER_USES_D in gc.find_geometry_issues(
        "Krug ima prečnik $D=10\\,\\text{cm}$.", "plane", ["krug"])


def test_17_intentional_wrong_distractors_remain_allowed():
    """Distraktor smije nositi pogrešnu oznaku ($S$ za površinu) dok god je
    semantički različit od ostalih."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Stranica $a=6\\,\\text{cm}$, visina $h_a=4\\,\\text{cm}$. Kolika je površina trougla?",
        expected="$P=12\\,\\text{cm}^2$",
        options=make_options("$P=12\\,\\text{cm}^2$", "$S=24\\,\\text{cm}^2$",
                             "$P=10\\,\\text{cm}^2$", "$P=48\\,\\text{cm}^2$"),
        correct_option_index=0, task_family="direct_formula_application",
        answer_kind="short_text")))
    r = run_practice_turn(store, fake, turn(topic="7-05-021", grade=7))
    assert r["status"] == "ready", r["answer"]
    assert fake.practice_call_count == 1


def test_18_all_family_contracts_remain_registered():
    from matbot.task_family_validation import CONTRACTS, missing_contracts
    from matbot.task_families import FAMILY_DESCRIPTIONS

    assert missing_contracts() == []
    # 36 porodica: pet „fraction_*“ porodica opslužuje SAMO nemigrirane
    # lekcije kroz legacy granicu (matbot/legacy/practice_routing.py).
    # Brišu se tek kad njihovi potrošači dobiju ugovor — vidi
    # tests/test_legacy_routing_parity.py.
    assert len(FAMILY_DESCRIPTIONS) == 36
    assert len(CONTRACTS) == 36
