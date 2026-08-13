"""ŽIVI NALAZI IZ UI-a: prazno objašnjenje i zaglavljen vrh ljestvice.

BUG A — uz AKTIVAN zadatak učenik piše „objasni na drugi način“ i dobija samo
najavu: „Dobro, objasniću na drugi način, korak po korak i kratko.“ Na
sljedeću poruku („pa objasni“) — opet najavu. Uzrok je asimetrija ugovora
polja: `hint_request` mora donijeti `hint`, `full_solution_request` mora
donijeti `worked_solution`, a `explanation_request` NEMA nijedno obavezno polje
sadržaja — sve živi u `reply`, pa najava prolazi kao ispravan odgovor.

BUG B — „Proširivanje razlomaka“ se penje 1 → 2, a onda svako „Daj mi teži
zadatak“ vraća generičku grešku. Uzrok NIJE „nema nivoa 4“: profil nivoa 3
postavlja SVAKU dimenziju na maksimum, pa arhetip s jednim razlomkom dobije
`term_count = 2` i priprema kostura se iscrpi (`generation_exhausted`).
Mjereno nad svih šest ugovornih lekcija: tri od šest ne mogu proizvesti nivo 3.
Odgovor je pošten vrh — najjači zadatak koji ugovor UMIJE, nepromijenjen nivo i
postojeći uvod za maksimum — nikad izmišljen nivo i nikad pad na grešku.
"""
import os

import pytest

from matbot import difficulty_level, hint_policy
from matbot.contracts import pipeline as contract_pipeline
from matbot.contracts import registry as contract_registry
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_options, make_output, make_task,
                            make_tutor_draft)

CONTRACT_LESSON = "6-04-005"      # „Proširivanje razlomaka“ — ugovorni put
SEMANTIC_LESSON = "6-04-009"      # semantički ugovor — univerzalni put
META_ONLY = "Dobro, objasniću na drugi način, korak po korak i kratko."


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def turn(sid, message, lesson, request=""):
    return {"session_id": sid, "grade": 6, "selected_topic": lesson,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": request, "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}


def _has_mathematics(text):
    return not hint_policy.is_meta_only_explanation(text)


# ===========================================================================
# BUG A — objašnjenje mora biti u ISTOM odgovoru
# ===========================================================================

def _session_with_task(store, fake, sid):
    """Objavi pravi zadatak, pa PRIKUJ artefakt da test ne ovisi o slučaju.

    Deterministički generator svaki put daje druge brojeve, a ponekad i
    rješenje koje rezultat navodi PRIJE postupka — tada server (ispravno) nema
    leak-free kompoziciju. Ta grana se provjerava zasebno; ovdje se mjeri
    ponašanje nad poznatim artefaktom."""
    assert run_practice_turn(store, fake, turn(sid, "Daj mi zadatak.",
                                               SEMANTIC_LESSON))["status"] == "ready"
    session = store.peek(sid)
    assert session["current_task"] and session["solution_summary"]
    # CIJEO paket se prikiva, ne samo rješenje: nasumičan očekivani odgovor
    # mogao bi se doslovno pojaviti i usred prikovanog postupka, pa bi rez
    # „prije rezultata“ pao na prvu pojavu i test bi bio nestabilan.
    session["current_task"] = r"Izračunaj: $\frac{3}{9}+\frac{1}{9}$"
    session["expected_answer_summary"] = r"$\frac{4}{9}$"
    session["current_options"] = [
        {"id": "a", "text": r"$\frac{4}{9}$"}, {"id": "b", "text": r"$\frac{4}{18}$"},
        {"id": "c", "text": r"$\frac{2}{9}$"}, {"id": "d", "text": r"$\frac{3}{9}$"}]
    session["correct_option_id"] = "a"
    session["solution_summary"] = (
        r"Nazivnici su jednaki — sabiraju se samo brojnici, a nazivnik ostaje "
        r"isti. Računamo: $\frac{3}{9}+\frac{1}{9}$, dakle saberi brojnike. "
        r"Rezultat je " + session["expected_answer_summary"] + ".")
    store.save(session)
    return store.peek(sid)


@pytest.mark.parametrize("message", ["objasni na drugi način", "pa objasni",
                                     "možeš li drugačije objasniti",
                                     "nisam razumio, objasni opet"])
def test_a_alternative_explanation_contains_actual_mathematics(universal, message):
    store, fake = SessionStore(), FakeLLM()
    _session_with_task(store, fake, "a1")
    before = store.peek("a1")
    calls_before = fake.call_count

    fake.queue(make_tutor_draft(intent="explanation_request", reply=META_ONLY,
                                lesson_focus="razlomci"))
    response = run_practice_turn(store, fake, turn("a1", message, SEMANTIC_LESSON))

    answer = response.get("answer") or ""
    assert response.get("status") == "ready"
    assert _has_mathematics(answer), answer                 # A/B/C: nije najava
    assert META_ONLY not in answer                          # prazno obećanje ne stiže
    assert fake.call_count - calls_before == 1              # I: bez dodatnog poziva

    after = store.peek("a1")
    assert after["current_task"] == before["current_task"]          # D
    assert after["expected_answer_summary"] == before["expected_answer_summary"]  # E
    assert after["difficulty_level"] == before["difficulty_level"]  # F
    assert after["current_options"] == before["current_options"]    # G/H


def test_a_unprovable_leak_free_prefix_falls_back_instead_of_guessing():
    """Kad rješenje navede rezultat PRIJE postupka, server ne izmišlja."""
    session = {"current_task": "Izračunaj: $2+2$",
               "solution_summary": "Rezultat je $4$, jer se sabiraju jedinice.",
               "expected_answer_summary": "$4$",
               "current_options": [{"id": "a", "text": "$4$"}],
               "correct_option_id": "a"}
    assert hint_policy.compose_alternative_explanation_for_session(session) == ""


def test_a_meta_only_measure_is_narrow_and_provable():
    assert hint_policy.is_meta_only_explanation("Dobro, objasniću ti drugim riječima.")
    assert hint_policy.is_meta_only_explanation("")
    # Čim tekst nosi matematiku, mjera ga NE dira.
    assert not hint_policy.is_meta_only_explanation("Saberi brojnike: 3 + 1.")
    assert not hint_policy.is_meta_only_explanation(r"Računamo $\frac{3}{9}$.")


def test_a_model_written_explanation_is_left_alone(universal):
    """Kad model STVARNO objasni, server ne dira njegov tekst."""
    store, fake = SessionStore(), FakeLLM()
    _session_with_task(store, fake, "a2")
    real = r"Nazivnici su isti, pa saberi brojnike: $3 + 1 = 4$."
    fake.queue(make_tutor_draft(intent="explanation_request", reply=real,
                                lesson_focus="razlomci"))
    answer = run_practice_turn(store, fake,
                               turn("a2", "objasni na drugi način",
                                    SEMANTIC_LESSON))["answer"]
    assert "saberi brojnike" in answer.lower()


def test_a_open_task_explanation_never_reveals_the_answer(universal):
    """Objašnjenje NERIJEŠENOG zadatka ne smije odati označeni odgovor."""
    store, fake = SessionStore(), FakeLLM()
    session = _session_with_task(store, fake, "a3")
    marked = session["expected_answer_summary"]
    fake.queue(make_tutor_draft(intent="explanation_request", reply=META_ONLY,
                                lesson_focus="razlomci"))
    answer = run_practice_turn(store, fake, turn("a3", "objasni na drugi način",
                                                 SEMANTIC_LESSON))["answer"]
    assert marked not in answer, answer
    assert _has_mathematics(answer)


def test_a_completed_task_may_get_the_full_explanation():
    session = {"current_task": "Izračunaj: $2+2$", "task_completed": True,
               "solution_summary": "Saberi: $2+2=4$.",
               "expected_answer_summary": "$4$",
               "current_options": [{"id": "a", "text": "$4$"}],
               "correct_option_id": "a"}
    composed = hint_policy.compose_alternative_explanation_for_session(session)
    assert composed and "$2+2=4$" in composed


def test_a_no_task_means_no_invented_explanation():
    """J: bez aktivnog zadatka server ne izmišlja — zatečeno ponašanje ostaje."""
    assert hint_policy.compose_alternative_explanation_for_session({}) == ""
    assert hint_policy.compose_alternative_explanation_for_session(
        {"current_task": "", "solution_summary": "nešto"}) == ""


def test_a_prompt_contract_demands_the_explanation_in_the_same_reply():
    from matbot.tutor import prompts
    text = prompts._FIELD_RULE
    assert "explanation_request" in text
    assert "NISU" in text and "objašnjenje" in text


# ===========================================================================
# BUG B — vrh ljestvice ugovorne lekcije
# ===========================================================================

def _queue_contract_draft(store, fake, sid, request):
    """Server pripremi kostur; nacrt ga samo obuče u prozu (isti ugovor)."""
    contract = contract_registry.contract_for(CONTRACT_LESSON)
    session = store.peek(sid) or {}
    plan = contract_pipeline.build_plan(
        contract, student_message="",
        recently_used=session.get("recently_used_families", []),
        current=session.get("current_family", ""),
        retry_required=session.get("retry_required", False),
        difficulty_request=request)
    transition = difficulty_level.transition(
        session.get("difficulty_level", 1), request)
    prepared = contract_pipeline.prepare_task(
        contract, plan, difficulty_request=request,
        target_level=transition.target_level,
        avoid_texts=session.get("recent_tasks", []))
    if not prepared.ok and transition.level_changed:
        prepared = contract_pipeline.prepare_task(
            contract, plan, difficulty_request=request,
            target_level=transition.previous_level,
            avoid_texts=session.get("recent_tasks", []))
    assert prepared.ok, "kostur se mora moći pripremiti bar na dostižnom nivou"
    skeleton = prepared.skeleton
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text=skeleton.question_text,
                           expected=skeleton.expected_answer,
                           options=make_options(*skeleton.option_texts),
                           correct_option_index=skeleton.correct_index)))


def _step(store, fake, sid, message, request=""):
    _queue_contract_draft(store, fake, sid, request)
    calls_before = fake.call_count
    response = run_practice_turn(store, fake,
                                 turn(sid, message, CONTRACT_LESSON, request))
    return response, store.peek(sid), fake.call_count - calls_before


def test_b_ladder_climbs_and_then_holds_at_the_generatable_top(universal):
    store, fake = SessionStore(), FakeLLM()
    response, session, calls = _step(store, fake, "b1", "Daj mi zadatak.")
    assert response["status"] == "ready" and session["difficulty_level"] == 1
    assert calls == 1                                                    # H

    response, session, calls = _step(store, fake, "b1", "Daj mi teži zadatak.", "harder")
    assert response["status"] == "ready" and session["difficulty_level"] == 2
    assert calls == 1

    # C/D/E: dalje „teže“ ostaje na dostižnom vrhu i UVIJEK objavljuje zadatak.
    top = session["difficulty_level"]
    for attempt in range(3):
        previous_task = session["current_task"]
        response, session, calls = _step(store, fake, "b1",
                                         "Daj mi teži zadatak.", "harder")
        assert response["status"] == "ready", attempt                    # K
        assert SAFE_ERROR_MESSAGE not in (response.get("answer") or "")
        assert session["difficulty_level"] == top, attempt               # nema pada
        assert session["current_task"] != previous_task, attempt         # nov zadatak
        assert calls == 1, attempt                                       # H/I


def test_b_top_of_ladder_uses_the_honest_max_level_intro(universal):
    store, fake = SessionStore(), FakeLLM()
    _step(store, fake, "b2", "Daj mi zadatak.")
    _step(store, fake, "b2", "Daj mi teži zadatak.", "harder")
    response, _session, _calls = _step(store, fake, "b2",
                                       "Daj mi teži zadatak.", "harder")
    answer = response["answer"]
    assert "naprednog zadatka" in answer, answer
    for leak in ("nivo 4", "generation", "exhausted", "kostur"):
        assert leak not in answer.lower()


def test_b_new_and_easier_still_behave_at_the_top(universal):
    store, fake = SessionStore(), FakeLLM()
    _step(store, fake, "b3", "Daj mi zadatak.")
    _step(store, fake, "b3", "Daj mi teži zadatak.", "harder")
    top = store.peek("b3")["difficulty_level"]

    response, session, calls = _step(store, fake, "b3", "Daj mi novi zadatak.")
    assert response["status"] == "ready" and session["difficulty_level"] == top  # F
    assert calls == 1

    response, session, calls = _step(store, fake, "b3", "Daj mi lakši zadatak.", "easier")
    assert response["status"] == "ready"                                          # G
    assert session["difficulty_level"] == top - 1
    assert calls == 1


def test_b_boundary_helper_keeps_the_level_and_names_the_end():
    source = difficulty_level.transition(2, "harder")
    assert source.target_level == 3 and source.level_changed
    capped = difficulty_level.at_generatable_boundary(source)
    assert capped.previous_level == 2 and capped.target_level == 2
    assert not capped.level_changed
    assert capped.boundary_reason == "at_maximum"
    # Ulazni prelaz se NE mijenja.
    assert source.target_level == 3


def test_b_no_creative_escalation_is_imported_into_the_contract_route(universal):
    """Pilot 6-04-015 se NE preslikava na ugovorne lekcije."""
    from matbot.tutor import creative_escalation as esc
    from matbot.tutor import lesson_context
    context = lesson_context.build(6, CONTRACT_LESSON)
    assert not esc.is_pilot_lesson(context)


# ===========================================================================
# BUG C — jedan neuspjeli turn = jedna poruka o grešci
# ===========================================================================

def test_c_one_failed_backend_turn_yields_exactly_one_error_payload(universal):
    """Backend na jedan zahtjev vraća JEDAN odgovor s jednom porukom."""
    class FailingLLM(FakeLLM):
        def practice_turn(self, *args, **kwargs):
            raise RuntimeError("simulirani pad modela")

    store, fake = SessionStore(), FakeLLM()
    _step(store, fake, "c1", "Daj mi zadatak.")
    published = store.peek("c1")["current_task"]

    response = run_practice_turn(store, FailingLLM(),
                                 turn("c1", "Daj mi teži zadatak.",
                                      CONTRACT_LESSON, "harder"))

    answer = response.get("answer") or ""
    # JEDAN zahtjev → JEDAN odgovor s porukom TAČNO jednom.
    assert answer.count(SAFE_ERROR_MESSAGE) == 1, answer
    assert response.get("status") != "ready"
    # Aktivni zadatak se čuva — greška ne briše stanje.
    assert store.peek("c1")["current_task"] == published


def test_c_frontend_renders_one_bubble_per_request_and_one_retry_control():
    """Statička provjera ožičenja (isti obrazac kao test_frontend_retry_ux)."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "templates"
            / "index.html").read_text(encoding="utf-8")
    # Jedan render po zahtjevu: streaming završava JEDNIM finalnim renderom…
    assert "made.bubble.innerHTML = renderTutorHTML(finalAnswer);" in html
    assert "finalni render JEDNOM" in html
    # …a JSON fallback se koristi tek kad se red streaminga ukloni.
    assert "made.row.remove(); return null;" in html
    # Zastarjela generacija UKLANJA svoj red umjesto da doda još jedan.
    assert "staleRow.remove();" in html
    # Dugme za ponovni pokušaj se poslije klika UKLANJA, pa ostaje najviše
    # jedna kontrola — tri poruke znače tri ODVOJENA neuspjela pokušaja.
    assert "retryBtn.remove();" in html
