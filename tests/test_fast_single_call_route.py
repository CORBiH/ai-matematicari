"""EKSPERIMENTALNA BRZA RUTA `fast_single_call` — ugovor i granice.

ZAŠTO POSTOJI (široki živi audit 6.–7. razreda): kašnjenje i padovi su vezani
za RUTU, ne za razred. Deterministička ruta ~0 s, ugovorna ~8 s, a univerzalna
dvopozivna 30–41 s — i njen DRUGI poziv se troši i onda kad prvi nacrt nema
nijedan dokazan defekt. Brza ruta troši recenzenta SAMO kad `package_preflight`
nađe nalaz.

ŠTA OVAJ FAJL ZAKLJUČAVA:
  • ruta se bira ISKLJUČIVO konfiguracijom po lekciji; bez nje ništa se ne mijenja;
  • čist nacrt se objavljuje na JEDNOM pozivu, bez recenzenta;
  • nalaz preflighta eskalira na POSTOJEĆEG recenzenta — najviše dva poziva;
  • trećeg poziva nema ni u jednom ishodu;
  • turn izrade zadatka NE SMIJE završiti razgovornim odgovorom;
  • kreativna ruta i dalje ide na recenzenta (semantičke presude);
  • deterministička i K1/K3 ruta ostaju netaknute.
"""
import json

import pytest

from matbot import config, mathsafe
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.schema import DifficultyEvidence, SignatureParameter
from tests.conftest import (FakeLLM, make_difficulty_diagnostics,
                            make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

FAST_LESSONS = ("6-01-012", "6-01-013", "7-04-005", "7-03-001")
TARGET, GRADE = "7-03-001", 7
DETERMINISTIC_LESSON = ("6-04-012", 6)      # semantički generator, 0 poziva
CONTRACT_LESSON = ("6-04-005", 6)           # K1/K3, 1 poziv


@pytest.fixture
def fast_route(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_LESSONS", ",".join(FAST_LESSONS))


@pytest.fixture
def universal_only(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_LESSONS", "")


class FastFake(FakeLLM):
    """FakeLLM koji zna i `fast_turn` — ista šema kao Tutor."""

    def __init__(self):
        super().__init__()
        self.fast_inputs = []
        self.last_fast_input = ""

    def fast_turn(self, instructions, input_text, timeout_s=None):
        self.fast_inputs.append(input_text)
        self.last_fast_input = input_text
        return self.tutor_turn(instructions, input_text)

    @property
    def fast_calls(self):
        return len(self.fast_inputs)

    @property
    def reviewer_call_count(self):
        return len(self.reviewer_calls)


def turn(sid, message, lesson=TARGET, grade=GRADE, request=""):
    return {"session_id": sid, "grade": grade, "selected_topic": lesson,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": request, "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}


_VARIANTS = [
    ("$\\frac{3}{4}$", "$\\sqrt{2}$", "$\\pi$", "$\\sqrt{7}$"),
    ("$-\\frac{5}{6}$", "$\\sqrt{3}$", "$\\pi$", "$\\sqrt{11}$"),
    ("$0{,}25$", "$\\sqrt{5}$", "$2\\pi$", "$\\sqrt{13}$"),
]


def rational_task(suffix="", correct_index=0, level=1, variant=0):
    """Ispravan paket iz lekcije „Skup racionalnih brojeva Q“.

    `variant` mijenja PONUĐENE VRIJEDNOSTI, ne samo tekst: duplikat se u ovom
    projektu mjeri strukturisanim potpisom, pa dva zadatka s istim opcijama
    jesu isti zadatak bez obzira na drugačiju rečenicu."""
    options = _VARIANTS[variant]
    payload = make_task_payload(
        text=f"Koji od navedenih brojeva pripada skupu $\\mathbb{{Q}}$?{suffix}",
        options=options, correct_option_index=correct_index,
        expected=options[correct_index],
        solution="Racionalan broj se može zapisati kao količnik dva cijela broja.",
        difficulty="standard")
    return payload.model_copy(update={
        "selected_lesson_id": TARGET,
        "selected_lesson_title": "Skup racionalnih brojeva Q",
        "target_difficulty_level": level,
        # Dokaz težine mora pratiti CILJNI nivo — isti ugovor koji objava
        # ionako provjerava; bez njega bi paket eskalirao na recenzenta.
        # Potpis se mijenja s varijantom: duplikat se mjeri STRUKTURISANIM
        # potpisom, pa dvije varijante moraju imati različite veličine.
        "task_signature": payload.task_signature.model_copy(update={
            "normalized_parameters": [
                SignatureParameter(name="variant", value=str(variant)),
                SignatureParameter(name="level", value=str(level))]}),
        "difficulty_evidence": DifficultyEvidence(
            reasoning_steps=level, condition_count=level,
            operation_count=level, representation_change_count=0,
            requires_explanation=False, requires_comparison=False,
            requires_construction=False, requires_proof_or_justification=False,
            combines_concepts=False)})


def draft_for(task, intent="generate_task"):
    kwargs = {"intent": intent, "reply": "Evo zadatka.",
              "lesson_focus": "skup racionalnih brojeva",
              "new_task": task}
    if intent in ("harder_task", "easier_task"):
        kwargs["difficulty_diagnostics"] = make_difficulty_diagnostics(
            direction="higher" if intent == "harder_task" else "lower")
    return make_tutor_draft(**kwargs)


# ---------------------------------------------------------------------------
# 1–4) IZBOR RUTE I KONFIGURACIJA
# ---------------------------------------------------------------------------

def test_route_is_off_by_default(monkeypatch):
    monkeypatch.delenv("MATBOT_FAST_SINGLE_CALL_LESSONS", raising=False)
    assert config.fast_single_call_lessons() == frozenset()
    for lesson in FAST_LESSONS:
        assert not config.fast_single_call_enabled_for(lesson)


def test_route_targets_exactly_the_configured_lessons(fast_route):
    for lesson in FAST_LESSONS:
        assert config.fast_single_call_enabled_for(lesson)
    for lesson in ("6-04-005", "6-04-012", "6-04-015", "7-02-016"):
        assert not config.fast_single_call_enabled_for(lesson)


def test_model_and_effort_are_independently_selectable(monkeypatch):
    assert config.FAST_MODEL == "gpt-5.6-luna"
    assert config.FAST_REASONING_EFFORT == "low"
    # Produkcijski izbor se NE mijenja postojanjem brze rute.
    assert config.TUTOR_MODEL == config.OPENAI_MODEL_TEXT
    assert config.REVIEWER_MODEL == config.OPENAI_MODEL_TEXT


def test_universal_route_remains_available_for_the_same_lesson(universal_only):
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    fake.queue(make_reviewer_final(decision="approve",
                                   checks=make_reviewer_checks()))
    response = run_practice_turn(store, fake, turn("u1", "Daj mi zadatak."))
    assert response["status"] == "ready"
    assert (fake.fast_calls, fake.reviewer_call_count) == (0, 1)   # stari put


def test_deterministic_route_is_untouched_and_contract_lesson_joins_the_fast_route(
        fast_route, monkeypatch):
    """Determinističke lekcije ostaju na nula poziva; K1/K3 se MIGRIRA.

    Ranije je ovaj test dokazivao da ugovorna lekcija NE ide brzom rutom. To je
    bila implementacijska činjenica zatečene arhitekture, a ne osobina lekcije:
    ugovor je podatak o zadatku. Nakon migracije ugovorna lekcija ide istim
    putem kao svaka druga modelski podržana lekcija, uz sva svoja ograničenja."""
    store, fake = SessionStore(), FastFake()
    assert run_practice_turn(store, fake, turn(
        "d1", "Daj mi zadatak.", *DETERMINISTIC_LESSON))["status"] == "ready"
    assert fake.call_count == 0                               # 0-call ostaje 0-call
    # PRODUKCIJSKI OPSEG: ruta se bira po klasi lekcije, ne po spisku ID-jeva.
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_SCOPE", "model_backed")
    store2, fake2 = SessionStore(), FastFake()
    contract_task = make_task_payload(
        text="Koji razlomak je jednak $\frac{2}{3}$?",
        options=["$\frac{4}{6}$", "$\frac{3}{4}$", "$\frac{5}{6}$", "$\frac{1}{3}$"],
        correct_option_index=0, expected="$\frac{4}{6}$",
        solution="Proširivanjem s 2 dobijemo $\frac{4}{6}$, iste vrijednosti.",
        difficulty="standard").model_copy(update={
            "selected_lesson_id": CONTRACT_LESSON[0],
            "selected_lesson_title": "Proširivanje razlomaka"})
    fake2.queue(make_tutor_draft(intent="generate_task", reply="Evo zadatka.",
                                 lesson_focus="proširivanje razlomaka",
                                 new_task=contract_task))
    # Recenzent je uslovan; ovdje se dokazuje RUTA, pa mu se odgovor pripremi da
    # turn može završiti bez obzira na to je li preflight našao nalaz.
    fake2.queue(make_reviewer_final(decision="approve", checks=make_reviewer_checks()))
    run_practice_turn(store2, fake2, turn("c1", "Daj mi zadatak.", *CONTRACT_LESSON))
    assert fake2.fast_calls == 1                              # K1/K3 sada ide brzom rutom
    assert fake2.practice_call_count == 0                     # NIKAD stari ugovorni poziv
    assert fake2.call_count <= 2                              # plafon se ne mijenja


# ---------------------------------------------------------------------------
# 5–8) JEDAN POZIV, USLOVNA ESKALACIJA, BUDŽET
# ---------------------------------------------------------------------------

def test_clean_draft_publishes_on_one_call_without_reviewer(fast_route):
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    response = run_practice_turn(store, fake, turn("f1", "Daj mi zadatak."))
    assert response["status"] == "ready"
    assert fake.fast_calls == 1
    assert fake.reviewer_call_count == 0                            # BEZ recenzenta
    assert fake.call_count == 1
    session = store.peek("f1")
    assert session["current_task"]
    assert session["lesson_id"] == TARGET


def test_failed_local_validation_escalates_to_the_existing_reviewer(fast_route):
    """Nalaz preflighta → recenzent → objava. Najviše dva poziva."""
    store, fake = SessionStore(), FastFake()
    broken = rational_task().model_copy(update={"expected_answer": "$\\pi$"})
    fake.queue(draft_for(broken))
    fixed = draft_for(rational_task(suffix=" "))
    fake.queue(make_reviewer_final(decision="correct", final=fixed,
                                   checks=make_reviewer_checks()))
    run_practice_turn(store, fake, turn("f2", "Daj mi zadatak."))
    assert fake.fast_calls == 1
    assert fake.reviewer_call_count == 1
    assert fake.call_count == 2                                # tvrda granica


def test_no_third_call_in_any_outcome(fast_route):
    for name, second in (("fail_closed", make_reviewer_final(
            decision="fail_closed", fail_reason_code="unsafe_or_unverifiable",
            checks=make_reviewer_checks(math_correct=False))),
            ("correct", make_reviewer_final(
                decision="correct", final=draft_for(rational_task(" ")),
                checks=make_reviewer_checks()))):
        store, fake = SessionStore(), FastFake()
        fake.queue(draft_for(rational_task().model_copy(
            update={"expected_answer": "$\\pi$"})))
        fake.queue(second)
        run_practice_turn(store, fake, turn(f"f3-{name}", "Daj mi zadatak."))
        assert fake.call_count <= 2, name


def test_unusable_draft_stops_at_one_call(fast_route):
    """Nacrt koji ni šemu ne zadovoljava NE dobija drugi poziv."""
    store, fake = SessionStore(), FastFake()
    fake.queue(make_tutor_draft(intent="generate_task", reply="Evo zadatka.",
                                lesson_focus="skup Q", new_task=None))
    response = run_practice_turn(store, fake, turn("f4", "Daj mi zadatak."))
    assert fake.call_count == 1
    assert response.get("status") != "ready"


# ---------------------------------------------------------------------------
# 9–11) SERVERSKA NADLEŽNOST: TEŽINA I OBAVEZAN ZADATAK
# ---------------------------------------------------------------------------

def test_task_turn_may_not_end_in_a_conversational_reply(fast_route):
    """Živi defekt: „već si na najtežem nivou, hoćeš li drugi?“ umjesto zadatka."""
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("f5", "Daj mi zadatak."))
    published = store.peek("f5")["current_task"]

    fake.queue(make_tutor_draft(
        intent="clarification",
        reply="Već si na najtežem nivou. Želiš li drugi zadatak iste težine?",
        lesson_focus="skup Q"))
    response = run_practice_turn(store, fake,
                                 turn("f5", "Daj mi teži zadatak.", request="harder"))
    assert response.get("status") != "ready"                   # pada zatvoreno
    assert store.peek("f5")["current_task"] == published       # zadatak sačuvan
    assert fake.call_count == 2                                # 1 + 1, bez recenzenta


def test_prompt_states_that_the_server_owns_the_turn_type(fast_route):
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("f6", "Daj mi zadatak."))
    sent = fake.last_fast_input
    assert "SERVERSKA ODLUKA O OVOM TURNU" in sent
    assert "`new_task` OBAVEZAN" in sent
    assert "najjačem dozvoljenom" in sent


def test_reviewer_is_told_how_to_repair_an_over_long_option():
    """`unchanged=True` u živom padu: recenzent je vidio nalaz ali nije znao
    ŠTA da uradi — pravilo nije imalo recept za predugu opciju."""
    rule = tutor_prompts._REVIEWER_PREFLIGHT_RULE
    assert "task_structure_invalid" in rule
    assert "SHORTEN" in rule and "solution" in rule
    assert "approve` is FORBIDDEN" in rule


def test_prompt_states_the_server_option_length_limit():
    """Granica dužine opcije je serverska činjenica; poslije uklanjanja neslaganja
    ciljnog nivoa „preduga opcija“ je postala vodeći uzrok eskalacije."""
    from matbot import config as _config
    assert f"KRAĆA od {_config.MAX_OPTION_TEXT_CHARS} znakova" in tutor_prompts._TASK_RULE
    assert "@@" not in tutor_prompts._TASK_RULE
    # LaTeX vitičaste zagrade u pravilima moraju preživjeti materijalizaciju
    assert "{3}" in tutor_prompts._TASK_RULE or "{5}" in tutor_prompts._TASK_RULE


def test_scope_rule_routes_by_class_not_by_lesson_list(monkeypatch):
    """Spisak od stotinu ID-jeva bio bi grananje po lekciji prerušeno u podatak.

    Deterministička strategija bira PRIJE ove tačke, pa je svaka lekcija koja
    dovde stigne po definiciji modelski podržana."""
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_LESSONS", "")
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_EXCLUDE", "")
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_SCOPE", "lessons")
    assert not config.fast_single_call_enabled_for("7-03-001")
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_SCOPE", "model_backed")
    assert config.fast_single_call_enabled_for("7-03-001")
    assert config.fast_single_call_enabled_for("9-07-018")
    # Rollback jedne lekcije ne gasi rutu.
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_EXCLUDE", "7-03-001")
    assert not config.fast_single_call_enabled_for("7-03-001")
    assert config.fast_single_call_enabled_for("9-07-018")
    # Potpuni rollback.
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_SCOPE", "off")
    assert not config.fast_single_call_enabled_for("9-07-018")


def test_scope_default_preserves_the_pilot_behaviour(monkeypatch):
    monkeypatch.delenv("MATBOT_FAST_SINGLE_CALL_SCOPE", raising=False)
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_LESSONS", "6-04-015")
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_EXCLUDE", "")
    assert config.fast_single_call_enabled_for("6-04-015")
    assert not config.fast_single_call_enabled_for("6-04-016")


def test_prompt_names_the_exact_server_target_level(fast_route):
    """ŽIVI NALAZ (val 5): 12 od 18 eskalacija bilo je `difficulty_target_mismatch`
    jer je model morao POGODITI nivo koji server već zna — stanje nosi POČETNI
    nivo, ne cilj turna."""
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("lvl", "Daj mi zadatak."))
    fake.queue(draft_for(rational_task(" teži", level=2, variant=1), intent="harder_task"))
    run_practice_turn(store, fake, turn("lvl", "Daj mi teži zadatak.", request="harder"))
    sent = fake.last_fast_input
    assert "`target_difficulty_level` mora biti TAČNO 2" in sent, sent[-400:]


def test_target_level_line_is_absent_when_the_controller_is_off():
    assert "target_difficulty_level" not in tutor_prompts._required_task_block(
        "harder_task", None)
    assert "TAČNO 3" in tutor_prompts._required_task_block("harder_task", 3)


def test_required_task_block_is_absent_without_a_server_task_intent():
    """Bez serverske namjere ulaz je bajt-identičan zatečenom."""
    assert tutor_prompts._required_task_block("") == ""


def test_new_preserves_level_and_changes_the_task(fast_route):
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("f7", "Daj mi zadatak."))
    before = store.peek("f7")
    fake.queue(draft_for(rational_task(suffix=" Odaberi tačan odgovor.", variant=1),
                         intent="next_task"))
    run_practice_turn(store, fake, turn("f7", "Daj mi novi zadatak."))
    after = store.peek("f7")
    assert after["difficulty_level"] == before["difficulty_level"]
    assert after["current_task"] != before["current_task"]


def test_harder_follows_the_server_target_level(fast_route):
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("f8", "Daj mi zadatak."))
    fake.queue(draft_for(rational_task(" teži", level=2, variant=1),
                         intent="harder_task"))
    run_practice_turn(store, fake, turn("f8", "Daj mi teži zadatak.", request="harder"))
    assert store.peek("f8")["difficulty_level"] == 2


# ---------------------------------------------------------------------------
# 12) DUPLIKATI
# ---------------------------------------------------------------------------

def test_duplicate_draft_never_republishes_the_same_task(fast_route):
    """Živi defekt 7-03-001: ista rečenica objavljena iznova.

    Identičan nacrt HVATA deterministički preflight (isti vidljivi potpis), pa
    turn eskalira na recenzenta. Bez obzira na njegov ishod, duplikat se NIKAD
    ne objavljuje: ili izađe DRUGI zadatak, ili turn padne zatvoreno."""
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("f9", "Daj mi zadatak."))
    published = store.peek("f9")["current_task"]

    fake.queue(draft_for(rational_task(), intent="next_task"))       # ISTI paket
    fake.queue(make_reviewer_final(
        decision="correct",
        final=draft_for(rational_task(" Odaberi tačan odgovor.", variant=1),
                        "next_task"),
        checks=make_reviewer_checks()))
    response = run_practice_turn(store, fake, turn("f9", "Daj mi novi zadatak."))

    assert fake.reviewer_call_count == 1                  # eskaliralo, nije prošlo
    assert fake.call_count == 3                           # 1 + (1 + 1), nikad više
    if response.get("status") == "ready":
        assert store.peek("f9")["current_task"] != published
    else:
        assert store.peek("f9")["current_task"] == published


def test_distinct_task_publishes_on_one_call(fast_route):
    """Kontrola: RAZLIČIT zadatak prolazi bez recenzenta, na jednom pozivu."""
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("f9b", "Daj mi zadatak."))
    published = store.peek("f9b")["current_task"]
    fake.queue(draft_for(rational_task(" Odaberi tačan odgovor.", variant=2),
                         intent="next_task"))
    response = run_practice_turn(store, fake, turn("f9b", "Daj mi novi zadatak."))
    assert response["status"] == "ready"
    assert store.peek("f9b")["current_task"] != published
    assert fake.reviewer_call_count == 0


# ---------------------------------------------------------------------------
# 12b) CILJNI NIVO — POPRAVLJIV NALAZ, NE PAD U OBJAVI
# ---------------------------------------------------------------------------

def test_wrong_target_level_escalates_and_can_be_repaired(fast_route):
    """Živi nalaz 7-03-001: nacrt deklarisao nivo 2 dok server traži 3.

    Ranije je takav paket prolazio preflight i padao TEK u objavi — bez ijedne
    prilike za ispravku. Sada je nalaz popravljiv: recenzent u DRUGOM (i
    posljednjem) pozivu vraća paket s ISPRAVNIM ciljem i turn objavljuje."""
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("t1", "Daj mi zadatak."))
    assert store.peek("t1")["difficulty_level"] == 1

    # Nacrt tvrdi nivo 1, a server za „teže“ traži 2.
    fake.queue(draft_for(rational_task(" pogrešan nivo", level=1, variant=1),
                         intent="harder_task"))
    fake.queue(make_reviewer_final(
        decision="correct",
        final=draft_for(rational_task(" ispravljen", level=2, variant=2),
                        "harder_task"),
        checks=make_reviewer_checks()))
    response = run_practice_turn(store, fake,
                                 turn("t1", "Daj mi teži zadatak.", request="harder"))

    assert response["status"] == "ready"                  # objavljeno, ne palo
    assert store.peek("t1")["difficulty_level"] == 2      # serverski cilj
    assert fake.reviewer_call_count == 1                  # eskaliralo
    assert fake.call_count == 3                           # 1 + (1 + 1)


def test_wrong_target_level_still_fails_closed_when_repair_fails(fast_route):
    """Granica ostaje: neuspjela ispravka NE objavljuje pogrešan nivo."""
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("t2", "Daj mi zadatak."))
    published = store.peek("t2")["current_task"]

    fake.queue(draft_for(rational_task(" pogrešan nivo", level=1, variant=1),
                         intent="harder_task"))
    fake.queue(make_reviewer_final(
        decision="fail_closed", fail_reason_code="difficulty_not_changed",
        checks=make_reviewer_checks(difficulty_evidence_valid=False)))
    response = run_practice_turn(store, fake,
                                 turn("t2", "Daj mi teži zadatak.", request="harder"))

    assert response.get("status") != "ready"
    assert store.peek("t2")["current_task"] == published
    assert store.peek("t2")["difficulty_level"] == 1
    assert fake.call_count == 3                           # nikad treći poziv u turnu


def test_matching_target_level_still_publishes_on_one_call(fast_route):
    """Kontrola: ispravan cilj se i dalje objavljuje BEZ recenzenta."""
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("t3", "Daj mi zadatak."))
    calls_before = fake.call_count
    fake.queue(draft_for(rational_task(" teži", level=2, variant=1),
                         intent="harder_task"))
    response = run_practice_turn(store, fake,
                                 turn("t3", "Daj mi teži zadatak.", request="harder"))
    assert response["status"] == "ready"
    assert store.peek("t3")["difficulty_level"] == 2
    assert fake.call_count - calls_before == 1
    assert fake.reviewer_call_count == 0


def test_target_issue_helper_is_exact_and_inert_without_a_target():
    from matbot.tutor import package_preflight
    task = rational_task(level=2)
    assert package_preflight.difficulty_target_issue(task, 2) is None
    assert package_preflight.difficulty_target_issue(task, None) is None
    assert package_preflight.difficulty_target_issue(None, 3) is None
    issue = package_preflight.difficulty_target_issue(task, 3)
    assert issue is not None
    assert issue.code == package_preflight.DIFFICULTY_TARGET_MISMATCH_CODE
    assert "server target is 3" in issue.detail


def test_reviewer_is_told_the_server_target_is_authoritative():
    from matbot.tutor import package_preflight
    block = package_preflight.format_for_reviewer(
        (package_preflight.difficulty_target_issue(rational_task(level=1), 3),))
    assert package_preflight.DIFFICULTY_TARGET_MISMATCH_CODE in block
    assert "AUTHORITATIVE" in block
    assert "never lower the target" in block


# ---------------------------------------------------------------------------
# 13–14) NOTACIJA SKUPOVA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["setminus", "backslash", "cup", "cap",
                                     "subset", "in"])
def test_taught_set_notation_is_allowed(command):
    assert command in mathsafe.MATHJAX_COMMAND_ALLOWLIST


def test_set_difference_survives_the_notation_gate():
    text = r"Ako je $A=\{1,2,3\}$ i $B=\{2\}$, koliko je $A \setminus B$?"
    cleaned, safe = mathsafe.sanitize_and_validate_math_text(text)
    assert safe, cleaned
    other = r"Izračunaj $(A \cup B) \setminus C$ i $A \cap (B \cup C)$."
    cleaned, safe = mathsafe.sanitize_and_validate_math_text(other)
    assert safe, cleaned


def test_unknown_commands_are_still_rejected():
    _cleaned, safe = mathsafe.sanitize_and_validate_math_text(r"Ovo je $\tyabc{3}$.")
    assert not safe


def test_multi_operation_set_task_passes_local_validation(fast_route):
    """6-01-012: razlika skupova više ne pada na vlastitom sigurnosnom sloju."""
    store, fake = SessionStore(), FastFake()
    options = ("$\\{1,3\\}$", "$\\{2\\}$", "$\\{1,2,3\\}$", "$\\varnothing$")
    payload = make_task_payload(
        text=r"Neka je $A=\{1,2,3\}$, $B=\{2,4\}$ i $C=\{3\}$. "
             r"Odredi $(A \setminus B) \setminus C$.",
        options=options, correct_option_index=0, expected=options[0],
        solution=r"$A \setminus B=\{1,3\}$, pa $\{1,3\} \setminus C=\{1\}$... "
                 r"tačnije $\{1,3\}$ bez $3$ daje $\{1\}$.",
        difficulty="standard")
    payload = payload.model_copy(update={
        "selected_lesson_id": "6-01-012",
        "selected_lesson_title": "Zadaci s više skupovnih operacija",
        "target_difficulty_level": 1})
    fake.queue(make_tutor_draft(intent="generate_task", reply="Evo zadatka.",
                               lesson_focus="skupovne operacije",
                               new_task=payload))
    response = run_practice_turn(store, fake, turn(
        "sets", "Daj mi zadatak.", "6-01-012", 6))
    # Notacija više NIJE razlog pada; ako paket padne, to nije zbog nje.
    answer = (response.get("answer") or "") + json.dumps(
        store.peek("sets"), ensure_ascii=False, default=str)
    assert "unsafe_task_text_notation" not in answer


# ---------------------------------------------------------------------------
# 15–17) NEPROMIJENJENE INVARIJANTE
# ---------------------------------------------------------------------------

def test_creative_escalation_still_requires_the_reviewer(fast_route, monkeypatch):
    """Semantičke presude kreativne rute NE preskaču recenzenta."""
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_LESSONS", "6-04-015")
    from matbot.tutor import creative_escalation as esc
    from matbot.tutor import lesson_context
    assert esc.is_pilot_lesson(lesson_context.build(6, "6-04-015"))
    source = tutor_pipeline._fast_single_call.__doc__ or ""
    assert "recenzent" in source.lower()


def test_fast_route_reuses_the_shared_reviewer_stage():
    """Jedna implementacija recenzenta za obje rute — bez druge kopije."""
    assert hasattr(tutor_pipeline, "_reviewer_stage")
    import inspect
    fast = inspect.getsource(tutor_pipeline._fast_single_call)
    two = inspect.getsource(tutor_pipeline._two_call)
    assert "_reviewer_stage(" in fast and "_reviewer_stage(" in two


def test_help_turns_keep_their_existing_route(fast_route):
    """Pomoć ide postojećim putem — brza ruta je ne dira."""
    store, fake = SessionStore(), FastFake()
    fake.queue(draft_for(rational_task()))
    run_practice_turn(store, fake, turn("f10", "Daj mi zadatak."))
    calls_before = fake.call_count
    payload = turn("f10", "")
    payload["intent"] = "solution_request"
    response = run_practice_turn(store, fake, payload)
    assert response.get("status") == "ready"
    assert fake.call_count == calls_before          # server-vlasnička pomoć, 0 poziva
