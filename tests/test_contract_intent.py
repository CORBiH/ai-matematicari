"""Deterministička intent tabela + kapija vjernosti proze (Faza 1).

Dvije nove serverske odluke poslije Live96:
  1. učenikova IZRIČITA molba za oblik zadatka se prepoznaje iz zatvorene
     tabele fraza (bez modela, bez slobodnog pogađanja) i poštuje SAMO kad je
     ugovor dozvoljava i generator implementira;
  2. modelova proza uz serverski zadatak ne smije izmišljati brojeve — broj
     koji se ne da objasniti iz zadatka obara taj tekst (hint pada na siguran
     generički), nikad cio turn s mutacijom stanja.
"""
import copy

import pytest

from matbot.contracts import generator, intent, pipeline, registry
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_output, make_task,
                            make_task_for_family)

CONTRACTS = registry.load_all()


# ---------------------------------------------------------------------------
# Intent tabela — zatvorene fraze, tačno jedan pogodak ili ništa
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,expected", [
    ("Daj zadatak u kojem nedostaje jedna vrijednost.", "find_missing_value"),
    ("Dopuni prazninu u jednakosti.", "find_missing_value"),
    ("Daj zadatak u kojem trebam pronaći grešku u postupku.", "identify_error"),
    ("Gdje je učenik pogriješio?", "identify_error"),
    ("Daj mi zadatak gdje biram zapis koji ima istu vrijednost.", "identify_equivalent"),
    ("Hoću ekvivalentan razlomak.", "identify_equivalent"),
    ("Daj mi zadatak da izračunam izraz.", "direct_computation"),
    ("Izračunaj mi nešto.", "direct_computation"),
])
def test_canonical_phrases_map_to_exactly_one_archetype(message, expected):
    assert intent.requested_archetype(message) == expected


@pytest.mark.parametrize("message", [
    "",                                        # prazno
    "Daj mi lakši zadatak iz iste lekcije.",   # nema fraze oblika
    "Izračunaj gdje je greška.",               # dvosmisleno: dva poklapanja
    "Objasni mi ovu lekciju.",                 # nije molba za oblik
])
def test_absent_or_ambiguous_requests_return_nothing(message):
    assert intent.requested_archetype(message) == ""


def test_normalization_is_diacritics_and_punctuation_insensitive():
    assert intent.requested_archetype("IZRAČUNAJ!!!") == "direct_computation"
    assert intent.requested_archetype("gdje je greska") == "identify_error"


# ---------------------------------------------------------------------------
# Plan — molba se poštuje samo unutar ugovora i implementiranih generatora
# ---------------------------------------------------------------------------

def test_supported_request_is_honored():
    plan = pipeline.build_plan(
        CONTRACTS["6-04-005"],
        student_message="Daj mi zadatak gdje biram zapis koji ima istu vrijednost.")
    assert plan.archetype_id == "identify_equivalent"
    assert plan.source == "student_request"


def test_unsupported_request_falls_back_to_rotation_without_error():
    plan = pipeline.build_plan(
        CONTRACTS["6-04-009"],
        student_message="Daj zadatak u kojem nedostaje jedna vrijednost.")
    assert plan.archetype_id == "direct_computation"   # rotacija, ne molba
    assert plan.source == "rotation"
    assert plan.requested == "find_missing_value"      # zabilježeno za log


def test_retry_keeps_the_current_skill_over_a_new_request():
    plan = pipeline.build_plan(
        CONTRACTS["6-04-009"],
        student_message="Daj mi zadatak da izračunam izraz.",
        current="direct_computation", retry_required=True)
    assert plan.archetype_id == "direct_computation"
    assert plan.source == "retry"


def test_unimplemented_but_allowed_archetype_is_never_planned(monkeypatch):
    """I kad bi ugovor dozvolio oblik bez generatora, plan ga ne smije izabrati
    na osnovu molbe — pada na rotaciju."""
    from matbot.contracts import schema
    widened = schema.replace(
        CONTRACTS["6-04-009"],
        allowed_task_archetypes=("direct_computation", "find_missing_value"))
    plan = pipeline.build_plan(
        widened, student_message="Daj zadatak u kojem nedostaje jedna vrijednost.")
    assert plan.archetype_id == "direct_computation"
    assert plan.source == "rotation"


# ---------------------------------------------------------------------------
# Kapija vjernosti proze — brojevi u prozi moraju biti objašnjivi iz zadatka
# ---------------------------------------------------------------------------

TASK_TEXTS = [
    r"Izračunaj: $\frac{3}{4} + \frac{5}{6}$.",
    r"$\frac{19}{12}$", r"$\frac{8}{10}$", r"$\frac{13}{15}$", r"$\frac{2}{12}$",
]


def test_prose_without_numbers_always_passes():
    ok, offending = pipeline.verify_prose_fidelity(
        "Sjajno! Prvo svedi razlomke na zajednički imenilac.", TASK_TEXTS)
    assert ok and offending == ()


def test_prose_with_task_and_derived_values_passes():
    # 12 = zajednički imenilac, 9 i 10 = prošireni brojnici, 19 = njihov zbir.
    ok, offending = pipeline.verify_prose_fidelity(
        r"Zajednički imenilac je $12$: $\frac{3}{4}=\frac{9}{12}$, "
        r"$\frac{5}{6}=\frac{10}{12}$, pa je zbir $\frac{19}{12}$.", TASK_TEXTS)
    assert ok, offending


def test_prose_with_invented_numbers_is_rejected():
    ok, offending = pipeline.verify_prose_fidelity(
        r"Rezultat je $\frac{47}{99}$.", TASK_TEXTS)
    assert not ok
    assert offending


def test_invented_hint_falls_back_to_the_generic_hint_without_killing_the_turn():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    turn = {
        "session_id": "intent-gate", "grade": 6, "selected_topic": "6-04-009",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    first = run_practice_turn(store, fake, turn)
    assert first["status"] == "ready"
    session = store.peek("intent-gate")
    wrong = next(o["id"] for o in session["current_options"]
                 if o["id"] != session["correct_option_id"])

    fake.queue(make_output(reply="", hint="Saberi brojnike 999 i 1000."))
    response = run_practice_turn(store, fake, dict(
        turn, interaction_type="choice_answer", selected_option_id=wrong,
        student_message="[klik]", client_turn_id="gate-1"))
    assert response["answer_verdict"] == "incorrect"
    assert "999" not in response["answer"]
    assert response["answer"].startswith("Netačno.")


def test_reply_that_invents_a_number_is_rejected_without_a_second_call():
    """Živi nalaz post-push provjere: na pitanje „kako da riješim ovo?“ model je
    vratio „Rezultat je $\\frac{47}{99}$“ — broj koji nije ni tačan odgovor, ni
    ijedna opcija, ni izvediv iz zadatka. mathcheck ga NE hvata (nema
    nedosljednog lanca), pa ga mora uhvatiti kapija vjernosti."""
    store, fake = SessionStore(), FakeLLM()
    turn = {
        "session_id": "reply-gate", "grade": 6, "selected_topic": "6-04-009",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    run_practice_turn(store, fake, turn)
    before = copy.deepcopy(store.peek("reply-gate"))
    calls_before = fake.call_count

    fake.queue(make_output(reply=r"Rezultat je $\frac{47}{99}$."))
    response = run_practice_turn(store, fake, dict(
        turn, student_message="Kako da riješim ovo?"))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert fake.call_count == calls_before + 1     # bez drugog/repair poziva
    assert store.peek("reply-gate") == before      # bez mutacije stanja


def test_reply_that_stays_inside_the_task_is_published():
    """Suprotan smjer: objašnjenje koje koristi SAMO brojeve iz zadatka prolazi
    — kapija ne smije gušiti ispravnu pomoć."""
    store, fake = SessionStore(), FakeLLM()
    turn = {
        "session_id": "reply-gate-ok", "grade": 6, "selected_topic": "6-04-009",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    run_practice_turn(store, fake, turn)

    explanation = "Imenilac ostaje isti — saberi samo brojnike."
    fake.queue(make_output(reply=explanation))
    response = run_practice_turn(store, fake, dict(turn, student_message="Kako?"))
    assert response["status"] == "ready"
    assert response["answer"] == explanation


def test_reply_gate_does_not_touch_the_legacy_path():
    """Lekcija bez ugovora zadržava zatečeno ponašanje — server tamo ne posjeduje
    matematiku zadatka, pa nema čemu mjeriti vjernost."""
    store, fake = SessionStore(), FakeLLM()
    turn = {
        "session_id": "reply-gate-legacy", "grade": 6, "selected_topic": "6-04-014",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    fake.queue(make_output(reply="Evo zadatka.",
                           new_task=make_task_for_family("fraction_operation")))
    run_practice_turn(store, fake, turn)
    fake.queue(make_output(reply=r"Pogledaj i $\frac{47}{99}$ kao primjer."))
    response = run_practice_turn(store, fake, dict(turn, student_message="Kako?"))
    assert response["status"] == "ready"


def test_faithful_hint_is_published():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    turn = {
        "session_id": "intent-gate-2", "grade": 6, "selected_topic": "6-04-009",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    run_practice_turn(store, fake, turn)
    session = store.peek("intent-gate-2")
    wrong = next(o["id"] for o in session["current_options"]
                 if o["id"] != session["correct_option_id"])

    hint = "Imenilac ostaje isti — saberi samo brojnike."
    fake.queue(make_output(reply="", hint=hint))
    response = run_practice_turn(store, fake, dict(
        turn, interaction_type="choice_answer", selected_option_id=wrong,
        student_message="[klik]", client_turn_id="gate-2"))
    assert hint in response["answer"]


# ---------------------------------------------------------------------------
# Regresijske brave iz Live96 revizije (ponašanja koja SU ispravna i moraju
# takva ostati) — pozivi 558 i 579/583.
# ---------------------------------------------------------------------------

def test_live96_579_doubled_backslash_in_option_is_collapsed_before_publish():
    """Poziv 579: model je vratio DVA backslasha ispred \\sqrt/\\text u
    opcijama; sanitizacija ih je svela na jedan i učenik je vidio ispravan
    MathJax. (Revizorski nalaz „dupli backslash je objavljen“ bio je pogrešno
    čitanje JSON escape-a — artefakti dokazuju suprotno.)"""
    from matbot.mathsafe import sanitize_and_validate_math_text
    raw = "$48\\\\sqrt{2}\\\\,\\\\text{cm}$"
    cleaned, safe = sanitize_and_validate_math_text(raw, allow_whole_expression_wrap=True)
    assert safe
    assert cleaned == "$48\\sqrt{2}\\,\\text{cm}$"


def test_live96_558_unmatched_dollar_option_degrades_to_plain_text():
    """Poziv 558: opcija '$0,58' (neupareni delimiter) se objavljuje kao čist
    tekst umjesto MathJax greške — dokumentovano sigurno ponašanje."""
    from matbot.mathsafe import sanitize_and_validate_math_text
    cleaned, safe = sanitize_and_validate_math_text("$0,58", allow_whole_expression_wrap=True)
    assert safe
    assert "$" not in cleaned
    assert "0,58" in cleaned
