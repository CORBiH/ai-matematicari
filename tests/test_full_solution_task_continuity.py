r"""„Uradi ga ti“ rješava AKTIVAN zadatak — nikad ga ne zamjenjuje novim.

PRODUKCIJSKI NALAZ (ručni smoke test, 6. razred, lekcija 6-03-004, zastavice
`MATBOT_PRACTICE_PIPELINE=universal_two_call` i
`MATBOT_PRACTICE_DIFFICULTY_LEVELS=enabled`):

    1. objavljen zadatak (opcije 8 · 6 · 7 · 9)
    2. „Ne znam — daj mi hint“  → hint, zadatak ostaje
    3. „Uradi ga ti“            → NOV ZADATAK („Dopuni: □50 …“) umjesto
                                  rješenja postojećeg

UZROK: univerzalni dvopozivni put (`matbot/tutor/pipeline.py`) nikad nije čitao
`turn["intent"]`. Frontend eksplicitno šalje `intent=solution_request`
(templates/index.html), ali je taj signal ostajao neiskorišten, pa je namjeru
odlučivao ISKLJUČIVO model iz slobodnog teksta „Uradi ga ti.“ Kad model vrati
`next_task`, `_run_text_turn` objavi nov paket i pregazi aktivan zadatak.

Klijentski `intent` se ovdje koristi SAMO da SUZI ono što server smije uraditi
(zabrana objave novog zadatka) — nikad da nešto odobri. Zato ostaje u skladu s
pravilom „klijentu se ne vjeruje“: lažna vrijednost može izazvati odbijanje,
nikad objavu koja inače ne bi prošla.
"""
import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_task_payload, make_tutor_draft,
                            queue_two_call)

LESSON, GRADE = "6-03-004", 6

ACTIVE_TASK = "Koji od sljedećih brojeva je djeljiv i sa 6 i sa 25?"
ACTIVE_OPTIONS = ("150", "60", "75", "90")        # tačna je 150
REPLACEMENT_TASK = ("Dopuni: $\\square 50$ tako da je broj djeljiv i sa 6 i sa "
                    "25. Koja cifra u $\\square$ to omogućava?")
REPLACEMENT_OPTIONS = ("8", "6", "7", "9")


@pytest.fixture(autouse=True)
def _universal_runtime(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(session_id, message, **changes):
    turn = {
        "session_id": session_id, "grade": GRADE, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    turn.update(changes)
    return turn


def _publish_active_task(store, fake, session_id):
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=ACTIVE_TASK, options=ACTIVE_OPTIONS,
                                   correct_option_index=0, expected="150")))
    response = run_practice_turn(
        store, fake, _turn(session_id, "Daj mi jedan zadatak za vježbu iz ove teme."))
    assert response["status"] == "ready"
    return response


def _task_state(session):
    """Sve što identifikuje AKTIVAN zadatak — potpis, opcije, označen odgovor."""
    return {
        "current_task": session["current_task"],
        "current_options": [dict(option) for option in session["current_options"]],
        "correct_option_id": session["correct_option_id"],
        "expected_answer_summary": session["expected_answer_summary"],
        "current_task_signature": session["current_task_signature"],
    }


# ---------------------------------------------------------------------------
# 1. TAČAN PRODUKCIJSKI NIZ: zadatak → hint → puno rješenje
# ---------------------------------------------------------------------------

def test_hint_leaves_the_active_task_untouched():
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-1")
    before = _task_state(store.peek("seq-1"))

    queue_two_call(fake, draft=make_tutor_draft(
        intent="hint_request", new_task=None,
        reply="Krenimo korak po korak.",
        hint="Prvo provjeri pravilo za djeljivost sa 25: pogledaj posljednje dvije cifre broja."))
    hint = run_practice_turn(
        store, fake,
        _turn("seq-1", "Ne znam.", intent="hint_request",
              interaction_phase="practice_help"))

    assert hint["status"] == "ready"
    session = store.peek("seq-1")
    assert _task_state(session) == before
    assert session["hint_level"] == 1


def test_full_solution_request_solves_the_active_task():
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-2")
    queue_two_call(fake, draft=make_tutor_draft(
        intent="hint_request", new_task=None, reply="Idemo redom.",
        hint="Pogledaj posljednje dvije cifre broja."))
    run_practice_turn(store, fake, _turn("seq-2", "Ne znam.", intent="hint_request",
                                         interaction_phase="practice_help"))
    before = _task_state(store.peek("seq-2"))
    calls_before = fake.call_count

    queue_two_call(fake, draft=make_tutor_draft(
        intent="full_solution_request", new_task=None,
        reply="Evo cijelog postupka.",
        worked_solution="Broj mora biti djeljiv sa 150, a to je jedino 150."))
    solution = run_practice_turn(
        store, fake,
        _turn("seq-2", "Uradi ga ti.", intent="solution_request",
              interaction_phase="practice_help"))

    assert solution["status"] == "ready"
    assert "150" in solution["answer"]
    # Zadatak se rješava, ne zamjenjuje.
    assert "Zadatak:" not in solution["answer"]
    assert solution["last_tutor_task"] == before["current_task"]
    assert solution["next_state"]["task"]["question"] == before["current_task"]
    assert solution["next_state"]["task"]["options"] == before["current_options"]
    assert solution["revealed_correct_option_id"] == before["correct_option_id"]

    session = store.peek("seq-2")
    assert _task_state(session) == before
    assert session["task_completed"] is True
    assert session["last_result"] == "full_solution"
    assert fake.call_count - calls_before <= 2      # nikad treći poziv


def test_full_solution_request_never_publishes_a_replacement_task():
    """Model NE MOŽE pregaziti aktivan zadatak — od Faze 2 se ne pita.

    ZATEČENO PONAŠANJE (do Faze 2): server je pozvao model, dobio `next_task` i
    odbio ga, pa je učenik dobio tehničku poruku umjesto rješenja. Nalaz je bio
    ispravan, ali cijena je bila izgubljen turn.

    OD FAZE 2 „uradi ga ti“ uopšte ne stiže do modela: puno rješenje sastavlja
    server iz RECENZENTOM ODOBRENOG `solution` polja objavljenog paketa
    (`hint_policy.compose_full_solution`). Zamjenski zadatak je time nemoguć PO
    KONSTRUKCIJI — nema payloada koji bi ga mogao donijeti — a učenik dobije
    rješenje. Zatečena invarijanta (aktivan zadatak netaknut) se dokazuje
    STROŽE: uz nula poziva modela."""
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-3")
    before = _task_state(store.peek("seq-3"))
    calls_before = fake.call_count

    # Namjerno pripremljen ZABRANJEN nacrt: ne smije biti ni pročitan.
    queue_two_call(fake, draft=make_tutor_draft(
        intent="next_task", reply="Evo sljedećeg zadatka.",
        new_task=make_task_payload(text=REPLACEMENT_TASK, options=REPLACEMENT_OPTIONS,
                                   correct_option_index=2, expected="7")))
    response = run_practice_turn(
        store, fake,
        _turn("seq-3", "Uradi ga ti.", intent="solution_request",
              interaction_phase="practice_help"))

    assert response["status"] == "ready"
    assert REPLACEMENT_TASK not in response["answer"]
    assert "Zadatak:" not in response["answer"]
    assert response["last_tutor_task"] == before["current_task"]
    assert _task_state(store.peek("seq-3")) == before
    assert fake.call_count == calls_before, "puno rješenje ne troši nijedan poziv"


def test_forbidden_task_intent_stops_before_the_reviewer_call():
    """Vrijedi i za nagovještaj: nacrt s namjerom zadatka pada prije recenzenta.

    Rješenje („uradi ga ti“) od Faze 2 ne zove model uopšte, pa se ova
    invarijanta dokazuje na nagovještaju — jedinom putu pomoći na kojem model
    još piše (računski zadatak, nivo 1)."""
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-4")
    reviewer_calls_before = len(fake.reviewer_calls)

    fake.queue(make_tutor_draft(
        intent="generate_task", reply="Evo zadatka.",
        new_task=make_task_payload(text=REPLACEMENT_TASK, options=REPLACEMENT_OPTIONS,
                                   correct_option_index=2, expected="7")))
    response = run_practice_turn(
        store, fake,
        _turn("seq-4", "Ne znam.", intent="hint_request",
              interaction_phase="practice_help"))

    assert "status" not in response
    assert len(fake.reviewer_calls) == reviewer_calls_before


def test_the_full_solution_comes_from_the_reviewer_approved_solution_field():
    """Objavljeno rješenje je DOSLOVNO provjeren artefakt, ne svježa proza."""
    from matbot import hint_policy

    store, fake = SessionStore(), FakeLLM()
    approved = ("Broj mora biti djeljiv i sa $6$ i sa $25$, dakle sa $150$. "
                "Među ponuđenim brojevima to je samo $150$.")
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=ACTIVE_TASK, options=ACTIVE_OPTIONS,
                                   correct_option_index=0, expected="150",
                                   solution=approved)))
    assert run_practice_turn(
        store, fake,
        _turn("seq-3b", "Daj mi jedan zadatak za vježbu iz ove teme."))["status"] == "ready"
    session = store.peek("seq-3b")
    assert session["solution_summary"] == approved

    response = run_practice_turn(
        store, fake,
        _turn("seq-3b", "Uradi ga ti.", intent="solution_request",
              interaction_phase="practice_help"))

    assert response["status"] == "ready"
    assert approved in response["answer"]
    assert response["answer"].startswith(hint_policy.FULL_SOLUTION_INTRO)


def test_hint_request_also_refuses_to_replace_the_active_task():
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-5")
    before = _task_state(store.peek("seq-5"))

    fake.queue(make_tutor_draft(
        intent="generate_task", reply="Evo zadatka.",
        new_task=make_task_payload(text=REPLACEMENT_TASK, options=REPLACEMENT_OPTIONS,
                                   correct_option_index=2, expected="7")))
    response = run_practice_turn(
        store, fake,
        _turn("seq-5", "Ne znam.", intent="hint_request",
              interaction_phase="practice_help"))

    assert "status" not in response
    assert _task_state(store.peek("seq-5")) == before


def test_explicit_new_task_request_still_publishes():
    """Zabrana važi SAMO za eksplicitnu UI akciju hint/rješenje."""
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-6")

    queue_two_call(fake, draft=make_tutor_draft(
        intent="next_task", reply="Evo sljedećeg zadatka.",
        new_task=make_task_payload(text=REPLACEMENT_TASK, options=REPLACEMENT_OPTIONS,
                                   correct_option_index=2, expected="7")))
    response = run_practice_turn(
        store, fake, _turn("seq-6", "Daj mi novi zadatak."))

    assert response["status"] == "ready"
    assert REPLACEMENT_TASK in response["answer"]
    assert store.peek("seq-6")["current_task"] == REPLACEMENT_TASK


# ---------------------------------------------------------------------------
# 2. SIGURAN PAD: rješenje ne uspije → aktivan zadatak preživi
# ---------------------------------------------------------------------------

def test_an_llm_outage_no_longer_costs_the_student_the_solution():
    """FAZA 2: puno rješenje je serverska činjenica, pa pad SDK-a na njega ne utiče.

    ZATEČENO: `LLMUnavailable` je obarao „uradi ga ti“ i učenik je dobio tehničku
    poruku, iako je server cijelo vrijeme imao odobreno rješenje u sesiji."""
    from matbot.llm import LLMUnavailable

    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-7")
    before = _task_state(store.peek("seq-7"))

    fake.queue(LLMUnavailable("boom"))
    response = run_practice_turn(
        store, fake,
        _turn("seq-7", "Uradi ga ti.", intent="solution_request",
              interaction_phase="practice_help"))

    assert response["status"] == "ready"
    assert "150" in response["answer"]
    session = store.peek("seq-7")
    assert _task_state(session) == before
    assert session["task_completed"] is True


def test_a_missing_verified_artifact_fails_closed_instead_of_inventing_one():
    """Bez PROVJERENOG rješenja puno otkrivanje pada zatvoreno.

    To je jedina dozvoljena alternativa: tražiti od modela svjež, neprovjeren
    izvod je upravo klasa FW-X03 nagovještaj 3."""
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-7b")
    session = store.peek("seq-7b")
    session["solution_summary"] = ""            # ruta bez provjerenog artefakta
    store.save(session)
    before = _task_state(store.peek("seq-7b"))
    calls_before = fake.call_count

    response = run_practice_turn(
        store, fake,
        _turn("seq-7b", "Uradi ga ti.", intent="solution_request",
              interaction_phase="practice_help"))

    assert "status" not in response
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert response["last_tutor_task"] == before["current_task"]
    after = store.peek("seq-7b")
    assert _task_state(after) == before
    assert after["task_completed"] is False
    assert fake.call_count == calls_before, "fail closed ne troši poziv"


# ---------------------------------------------------------------------------
# 3. UGOVOR PROMPTA: server saopštava modelu što je već sam utvrdio
# ---------------------------------------------------------------------------

def test_help_prompt_carries_the_explicit_ui_action():
    """Ugovor je ostao, ali sada u PROMPTU POMOĆI (Faza 2).

    Zatečeni test je mjerio isto nad promptom izrade zadatka i nad „uradi ga ti“;
    to više nije mjerljivo tamo (rješenje ne zove model). Serverska činjenica o
    pritisnutom dugmetu i zabrana novog zadatka i dalje moraju stići — na
    jedinom putu pomoći koji model još piše."""
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-8")

    fake.queue(make_tutor_draft(
        intent="hint_request", new_task=None, reply="Idemo korak po korak.",
        hint="Provjeri posljednje dvije cifre svakog ponuđenog broja."))
    run_practice_turn(
        store, fake,
        _turn("seq-8", "Ne znam.", intent="hint_request",
              interaction_phase="practice_help"))

    instructions, input_text = fake.tutor_calls[-1]
    assert "hint_request" in input_text
    assert "TRAŽENA AKCIJA" in input_text
    assert "nov zadatak" in instructions.lower()


# ---------------------------------------------------------------------------
# 4. ŽIVI F5L NALAZ (I01–I03, produkcijski smoke 20260808T144302Z):
#    identitetska lekcija 9-01-017 — svjež → hint → puno rješenje.
#    Produkcija je objavila vjeran zadatak i hint, a rješenje odbila u objavi
#    porukom „nebezbjedan matematički zapis [full_solution_request]“ BEZ koda
#    defekta (help-turn nema preflight), pa je klasa (M06, M18, I03) bila
#    nedijagnostikljiva. Lokalna reprodukcija na istom kodu objavila je čisto
#    rješenje — validator NIJE lažno pozitivan; testovi pinuju oba ishoda.
# ---------------------------------------------------------------------------

IDENTITY_LESSON, IDENTITY_GRADE = "9-01-017", 9
IDENTITY_TASK = ("Pokaži da je identitet istinit tako što ćeš pojednostaviti "
                 "lijevu stranu i navesti uslov definisanosti: Pokaži da je "
                 "$\\frac{x^2-1}{x-1}=x+1$ za sve vrijednosti $x$ za koje je "
                 "izraz definisan.")
IDENTITY_OPTIONS = ("$x+1$", "$x^2+1$", "$x-1$", "1")   # tačna je $x+1$
IDENTITY_SOLUTION = ("Faktorišemo brojnik: $x^2-1=(x-1)(x+1)$, pa je "
                     "$\\frac{x^2-1}{x-1}=\\frac{(x-1)(x+1)}{x-1}=x+1$ za "
                     "svako $x \\neq 1$. Uslov definisanosti: $x \\neq 1$. "
                     "Tačna opcija je $x+1$.")


def _identity_turn(session_id, message, **changes):
    turn = _turn(session_id, message, grade=IDENTITY_GRADE,
                 selected_topic=IDENTITY_LESSON)
    turn.update(changes)
    return turn


def _publish_identity_task(store, fake, session_id, solution=None):
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=IDENTITY_TASK,
                                   options=IDENTITY_OPTIONS,
                                   correct_option_index=0, expected="$x+1$",
                                   solution=solution)))
    response = run_practice_turn(
        store, fake, _identity_turn(session_id, "Daj mi zadatak."))
    assert response["status"] == "ready", response.get("answer", "")
    return response


def test_identity_lifecycle_fresh_hint_solution_publishes_cleanly():
    """Vjeran tok I01–I03: čisto rješenje s uslovom definisanosti $x \\neq 1$
    MORA biti objavljeno, uz otkrivanje opcije i očuvano stanje.

    FAZA 2: isti vidljivi ishod, ali izvor je sada RECENZENTOM ODOBRENO polje
    `solution` objavljenog paketa, a ne svježa proza help-turna."""
    store, fake = SessionStore(), FakeLLM()
    _publish_identity_task(store, fake, "f5l-id-1", solution=IDENTITY_SOLUTION)
    assert store.peek("f5l-id-1")["solution_summary"] == IDENTITY_SOLUTION

    fake.queue(make_tutor_draft(
        intent="hint_request", new_task=None, reply="Idemo korak po korak.",
        hint="Faktoriši brojnik $x^2-1$ kao razliku kvadrata."))
    hint = run_practice_turn(
        store, fake, _identity_turn("f5l-id-1", "Ne znam.",
                                    intent="hint_request",
                                    interaction_phase="practice_help"))
    assert hint["status"] == "ready"
    before = _task_state(store.peek("f5l-id-1"))
    calls_before = fake.call_count

    solution = run_practice_turn(
        store, fake, _identity_turn("f5l-id-1", "Uradi ga ti.",
                                    intent="solution_request",
                                    interaction_phase="practice_help"))

    assert solution["status"] == "ready"
    assert "x+1" in solution["answer"]
    assert "\\neq 1" in solution["answer"]          # uslov definisanosti
    assert solution["revealed_correct_option_id"] == before["correct_option_id"]
    assert fake.call_count == calls_before          # nula poziva na rješenju
    session = store.peek("f5l-id-1")
    assert _task_state(session) == before
    assert session["task_completed"] is True


UNSAFE_SOLUTION = ("Skratimo: $\\frac{x^2-1}{x-1}"
                   "=\\frac{\\cancel{(x-1)}(x+1)}{\\cancel{x-1}}=x+1$, "
                   "uz $x \\neq 1$.")


def test_unsafe_solution_notation_is_now_rejected_at_publication(caplog):
    """ŽIVI F5L NALAZ (I03), s NOVIM vlasnikom (Faza 2).

    ZATEČENO: komanda van allowlista (`\\cancel`) je do modela stizala u
    help-turnu, a odbijanje se dešavalo TEK pri prikazu pomoći — bez preflighta,
    pa je klasa (M06, M18, I03) bila nedijagnostikljiva; kod defekta je dodan u
    F5L.

    OD FAZE 2 rješenje koje učenik vidi dolazi iz polja `solution` OBJAVLJENOG
    paketa, a to polje prolazi kroz `_validate_task_server_side`. Isti defekt zato
    pada JEDAN KORAK RANIJE — prije nego što je zadatak ikad objavljen — i nosi
    isti ograničen kod. Time je nemoguće da objavljen zadatak ima nebezbjedno
    rješenje koje pomoć kasnije treba servirati."""
    import logging

    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=IDENTITY_TASK, options=IDENTITY_OPTIONS,
                                   correct_option_index=0, expected="$x+1$",
                                   solution=UNSAFE_SOLUTION)))
    with caplog.at_level(logging.WARNING, logger="matbot.tutor.pipeline"):
        response = run_practice_turn(
            store, fake, _identity_turn("f5l-id-2", "Daj mi zadatak."))

    assert "status" not in response                  # sigurna poruka, ne objava
    assert store.peek("f5l-id-2") is None or not store.peek("f5l-id-2")["current_task"]
    # Nalaz stiže JOŠ RANIJE nego što je pokazivao F5L: preflight nad paketom ga
    # dokazuje prije mutacije sesije, uz isti ograničen kod komande.
    assert "unsafe_solution_notation" in caplog.text
    assert "unknown_mathjax_command:cancel" in caplog.text
    # Kod NIKAD ne nosi sadržaj rečenice — samo ime komande.
    assert "Skratimo" not in caplog.text


def test_an_unsafe_model_hint_still_fails_closed_with_a_defect_code(caplog):
    """Isti prag i dalje čuva JEDINU površinu koju model još piše: nagovještaj.

    Bez ovoga bi izmjena vlasništva mogla tiho ugasiti F5L kod za pomoć.

    ZAVRŠNA POPRAVKA KLASIFIKATORA: mjeri se na zadatku s DOKAZANO brojevnim
    odgovorom (`150` / `60` / `75` / `90`), jer je to jedini oblik za koji je
    računska ljestvica dokaziva — pa jedini na kojem model još piše nivo 1.
    Identitetski zadatak (odgovor `$x+1$`) od sada ide serverskom ljestvicom,
    tamo modelovog teksta više nema i ovaj prag se na njemu ne može mjeriti."""
    import logging

    from matbot import hint_policy

    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "f5l-id-3")
    session = store.peek("f5l-id-3")
    assert hint_policy.session_task_class(session) == hint_policy.COMPUTATIONAL
    before = _task_state(session)

    fake.queue(make_tutor_draft(
        intent="hint_request", new_task=None, reply="Idemo korak po korak.",
        hint=UNSAFE_SOLUTION))
    with caplog.at_level(logging.WARNING, logger="matbot.tutor.pipeline"):
        response = run_practice_turn(
            store, fake, _turn("f5l-id-3", "Ne znam.", intent="hint_request",
                               interaction_phase="practice_help"))

    assert "status" not in response
    session = store.peek("f5l-id-3")
    assert _task_state(session) == before
    assert session["hint_level"] == 0                # odbijena pomoć ne pomjera nivo
    assert "nebezbjedan matematički zapis [hint_request]" in caplog.text
    assert "unknown_mathjax_command" in caplog.text


# ---------------------------------------------------------------------------
# Kapacitetna ekspanzija: ovi testovi ispituju MODEL-strategiju (Tutor +
# Recenzent) i na lekcijama koje produkcija sada rutira deterministički
# (blocking ugovor + potpun generator). Izričito isključenje je ISTI mehanizam
# koji služi i kao produkcijski rollback (MATBOT_DETERMINISTIC_PRACTICE=
# disabled) — model-put time ostaje trajno testiran, bajt za bajt kakav je bio.
# ---------------------------------------------------------------------------
import pytest as _pytest_capex


@_pytest_capex.fixture(autouse=True)
def _model_route_only_capex(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
