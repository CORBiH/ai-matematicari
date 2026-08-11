"""UGOVOR GENERISANJA: identitet opcije je serverski, i to mora znati MODEL.

ŽIVA KAPIJA IZDANJA (canonical live release gate na `2fa9507`, scenario
`release_gate_core_harder_level1_to_2`, lekcija 6-04-001, `request_type=harder`,
prelaz nivoa 1→2). Recenzent je vratio `decision=correct` s kompletno
prepravljenim paketom, a njegov KONAČAN `solution` je imenovao slovo opcije.
Server je odbio objavu:

    tutor_rejected request_id=9d6135ce3d6c topic=6-04-001 stage=publication
      intent=harder_task detail=solution_option_label_claim [solution]

Deterministička kapija je time odradila svoj posao — paket nije objavljen i
sesija nije mutirana. Ali scenario nije objavio ništa, pa je kapija izdanja
pala. Popravka je u GENERISANJU: model mora prestati pisati oznaku, da backstop
ne mora ni da se oglasi.

ŠTA OVAJ FAJL DOKAZUJE, A ŠTA NE:
  * dokazuje da su pravila STVARNO poslana i Tutoru i Recenzentu, i da
    recenzentovo pravilo izričito pokriva stanje POSLIJE ISPRAVKE;
  * dokazuje da deterministička kapija nije oslabljena;
  * NE tvrdi da je prompt garancija. Model i dalje može prekršiti pravilo —
    zato kapija ostaje, i zato je zadnji test u fajlu njen dokaz.

GRANICA DOKAZA (namjerno): detektor je uzak i hvata SLOVA uz riječ-oznaku
(`opcija a`, `odgovor je b`, `pod c)`). Redne/položajne oblike („treća opcija")
i golo „Izaberi c)." NE hvata. Prompt ih zabranjuje šire nego što detektor
dokazuje — prevencija smije biti šira od dokaza. Testovi ispod to mjere
doslovno, umjesto da detektoru pripisuju domet koji nema.
"""
from __future__ import annotations

import pytest

from matbot import mcq_integrity
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor import lesson_context as lesson_context_module

# Lekcija na kojoj je kapija pala.
GATE_GRADE = 6
GATE_LESSON = "6-04-001"


@pytest.fixture
def context():
    return lesson_context_module.build(GATE_GRADE, GATE_LESSON)


# ===========================================================================
# A — TUTOROV UGOVOR
# ===========================================================================

def _flat(text):
    """Prompt se prelama u izvoru; ugovor se tiče RIJEČI, ne tačke preloma."""
    return " ".join((text or "").split())


def test_tutor_is_told_not_to_name_option_letters_or_positions(context):
    rule = _flat(tutor_prompts.build_tutor_instructions(context))
    assert "NIKAD ne imenuj slovo opcije" in rule
    assert "`solution`" in rule and "`expected_answer`" in rule
    # Položaj/redni broj — živi oblik koji detektor NE dokazuje, pa ga prompt
    # mora pokriti sam.
    assert "prva opcija" in rule and "treća tvrdnja" in rule
    # Razlog mora biti u pravilu: bez njega je to proizvoljna zabrana.
    assert "izmiješa POSLIJE tebe" in rule


def test_tutor_is_told_to_explain_the_content_instead(context):
    rule = _flat(tutor_prompts.build_tutor_instructions(context))
    assert "Objasni SADRŽAJ" in rule
    # Legitimni matematički nazivi se izričito NE zabranjuju.
    assert "`tačka A`" in rule and "nije oznaka opcije" in rule


# ===========================================================================
# B — RECENZENTOV UGOVOR
# ===========================================================================

def test_reviewer_is_told_option_identity_is_the_servers(context):
    rule = tutor_prompts.build_reviewer_instructions(context)
    assert "OPTION IDENTITY IS THE SERVER'S, NEVER YOURS" in rule
    assert "shuffles the options AFTER you" in rule
    assert "`solution`" in rule and "`expected_answer`" in rule


def test_reviewer_rule_covers_letters_numbers_and_positions(context):
    rule = tutor_prompts.build_reviewer_instructions(context)
    block = tutor_prompts._REVIEWER_OPTION_IDENTITY_RULE
    assert block in rule
    for forbidden in ("opcija a", "odgovor je b", "pod c)", "option number",
                      "prva opcija", "treća tvrdnja", "izaberi"):
        assert forbidden in block, forbidden


def test_reviewer_rule_allows_the_mathematical_content_and_object_names(context):
    block = tutor_prompts._REVIEWER_OPTION_IDENTITY_RULE
    assert "Say the MATHEMATICS instead" in block
    assert "CONTENT is always" in block
    for allowed in ("`tačka A`", "`duž AB`", "`ugao ABC`", "`funkcija f`", "`skup B`"):
        assert allowed in block, allowed


# ===========================================================================
# C — PRAVILO VAŽI POSLIJE ISPRAVKE (živi uzrok pada kapije)
# ===========================================================================

def test_reviewer_rule_binds_the_package_it_returns_not_the_draft():
    """Kapija je pala na `decision=correct`, dakle na PREPRAVLJENOM paketu."""
    block = tutor_prompts._REVIEWER_OPTION_IDENTITY_RULE
    assert "AFTER ANY REPAIR" in block
    assert "FINAL `solution`" in block
    # Kopiranje oznake iz nacrta je isti prekršaj kao pisanje nove.
    assert "copied over from the draft" in block
    assert "The package that is judged is the one you return." in block


def test_the_option_identity_rule_ships_exactly_once_and_only_to_the_reviewer(context):
    """Jedna kanonska formulacija — rečenica iz tačke 11 je PREMJEŠTENA."""
    reviewer = tutor_prompts.build_reviewer_instructions(context)
    block = tutor_prompts._REVIEWER_OPTION_IDENTITY_RULE
    assert reviewer.count(block) == 1
    assert block not in tutor_prompts.build_tutor_instructions(context)
    # Stara, zakopana kopija u tački 11 više ne postoji ni u jednom obliku.
    assert "so any letter you write becomes false" not in reviewer


@pytest.mark.parametrize("grade,lesson", [
    (GATE_GRADE, GATE_LESSON),          # lekcija na kojoj je kapija pala
    (6, "6-10-007"), (7, "7-02-019"), (9, "9-04-002"),
])
def test_the_rule_ships_for_every_lesson(grade, lesson):
    context = lesson_context_module.build(grade, lesson)
    assert tutor_prompts._REVIEWER_OPTION_IDENTITY_RULE in \
        tutor_prompts.build_reviewer_instructions(context)


# ===========================================================================
# D — DETERMINISTIČKA KAPIJA JE NEPROMIJENJENA (backstop)
# ===========================================================================

@pytest.mark.parametrize("solution", [
    "Tačan odgovor je b).",
    "Odgovor je opcija B.",
    "Opcija d) je tačna.",
    "Koordinate su $(3,2)$, a tačna je opcija a jer ostale ne odgovaraju.",
    "pod a) stoji netačna tvrdnja",
])
def test_a_final_solution_naming_an_option_letter_is_still_rejected(solution):
    assert mcq_integrity.option_label_claims(solution)
    _, code = mcq_integrity.option_label_normalization(solution)
    assert code == mcq_integrity.OPTION_LABEL_CLAIM_CODE


# ===========================================================================
# E — SADRŽAJNO RJEŠENJE PROLAZI
# ===========================================================================

@pytest.mark.parametrize("solution", [
    ("Brojnik pokazuje koliko dijelova uzimamo, a nazivnik na koliko je "
     "jednakih dijelova cjelina podijeljena."),
    "Rezultat je $\\frac{3}{5}$ jer brojnik ostaje isti, a nazivnik se ne mijenja.",
    "Vrijednost funkcije je $2$ jer se $x=3$ pridružuje tačno jednom $y$.",
])
def test_a_content_only_solution_publishes_untouched(solution):
    assert mcq_integrity.option_label_claims(solution) == ()
    normalized, code = mcq_integrity.option_label_normalization(solution)
    assert code == ""
    assert normalized == solution          # bajt za bajt, nikakva sanitizacija


# ===========================================================================
# F — LEGITIMNI MATEMATIČKI NAZIVI NISU OZNAKE OPCIJA
# ===========================================================================

@pytest.mark.parametrize("solution", [
    "Tačka A leži na duži AB.",
    "Ugao ABC ima tjeme B i krake BA i BC.",
    "Funkcija f je zadana tačkama $(1,2)$ i $(2,3)$.",
    "Skup A je podskup skupa B.",
    "Prava a je paralelna pravoj b.",
])
def test_mathematical_object_names_are_never_option_claims(solution):
    assert mcq_integrity.option_label_claims(solution) == ()
    _, code = mcq_integrity.option_label_normalization(solution)
    assert code == ""


# ===========================================================================
# G — POLOŽAJNI OBLICI: DETEKTOR IH NE TVRDI, PROMPT IH ZABRANJUJE
# ===========================================================================

POSITION_FORMS = ["Tačna je treća opcija.", "Prva opcija je tačna.",
                  "Izaberi drugu opciju.", "Treći odgovor je tačan.",
                  "Izaberi c)."]

# Za svaki oblik iznad — riječ koja MORA stajati u recenzentovom pravilu, jer
# detektor taj oblik ne dokazuje pa ga jedino prompt može spriječiti.
POSITION_FORM_KEYWORDS = [("Tačna je treća opcija.", "treća tvrdnja"),
                          ("Prva opcija je tačna.", "prva opcija"),
                          ("Izaberi drugu opciju.", "izaberi"),
                          ("Treći odgovor je tačan.", "position"),
                          ("Izaberi c).", "izaberi")]


@pytest.mark.parametrize("solution", POSITION_FORMS)
def test_the_detector_does_not_claim_position_forms(solution):
    """POŠTENO O DOMETU: uski detektor ove oblike NE hvata i ne pretvara se da hvata.

    Zato prevencija (prompt) mora biti šira od dokaza (detektor). Ako se ovaj
    test jednom promijeni, to znači da je detektor NAMJERNO proširen — a to je
    zaseban zadatak s vlastitom kalibracijom nad zamrznutim korpusom.
    """
    assert mcq_integrity.option_label_claims(solution) == ()


@pytest.mark.parametrize("solution,keyword", POSITION_FORM_KEYWORDS)
def test_the_prompt_forbids_what_the_detector_cannot_prove(solution, keyword,
                                                           context):
    """Svaki oblik koji detektor propušta mora biti imenovan u pravilu."""
    assert mcq_integrity.option_label_claims(solution) == ()   # detektor ćuti
    block = _flat(tutor_prompts._REVIEWER_OPTION_IDENTITY_RULE).lower()
    assert keyword in block, (solution, keyword)
    assert block in _flat(tutor_prompts.build_reviewer_instructions(context)).lower()


# ===========================================================================
# H — OBLIK PADA KAPIJE OD KRAJA DO KRAJA
# ===========================================================================
# Postojeći `tests/test_phase2_option_binding.py` već pokriva oznaku koja stiže
# iz NACRTA. Kapija je pala na DRUGOM vlasniku: oznaku je nosio RECENZENTOV
# `final` uz `decision=correct`, na `harder` turnu (nivo 1 → 2). Ovdje se
# reprodukuje baš taj tok — prvo se objavi zadatak nivoa 1, pa se traži teži.

from matbot.practice import run_practice_turn                    # noqa: E402
from matbot.session_store import SessionStore                    # noqa: E402
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE             # noqa: E402
from tests.conftest import (FakeLLM, make_difficulty_diagnostics,  # noqa: E402
                            make_reviewer_final, make_task_payload,
                            make_tutor_draft, queue_two_call)

LEVEL1_TASK = "Koja tvrdnja o razlomku $\\frac{3}{5}$ je tačna?"
LEVEL1_OPTIONS = ("Brojnik je $3$, a nazivnik $5$.",
                  "Brojnik je $5$, a nazivnik $3$.",
                  "Razlomak je veći od $1$.",
                  "Nazivnik smije biti $0$.")
# Teži zadatak MORA biti drugi zadatak — inače ga `duplicate_active_task`
# odbije prije kapije oznake i test bi prolazio iz pogrešnog razloga.
LEVEL2_TASK = "Koja tvrdnja o razlomku $\\frac{7}{4}$ je tačna?"
LEVEL2_OPTIONS = ("Brojnik je $7$, nazivnik $4$, pa je razlomak veći od $1$.",
                  "Brojnik je $4$, nazivnik $7$, pa je razlomak veći od $1$.",
                  "Brojnik je $7$, nazivnik $4$, pa je razlomak manji od $1$.",
                  "Razlomak je jednak $1$ jer su brojnik i nazivnik različiti.")
LABEL_FREE_SOLUTION = ("Brojnik je gornji broj razlomka, dakle $7$, a nazivnik "
                       "donji, dakle $4$. Kako je $7>4$, razlomak je veći od $1$.")
# NEUKLONJIVA oznaka: slovo je JEZGRO rečenice koja nosi i matematiku, pa se
# klauzula ne može dokazivo obrisati i paket pada zatvoreno — baš kod koji je
# kapija izdanja prijavila (`solution_option_label_claim [solution]`).
LABELLED_SOLUTION = ("Opcija a je tačna jer je brojnik $7$ veći od nazivnika "
                     "$4$, pa je razlomak veći od $1$.")
# UKLONJIVA oznaka (apozicija na kraju): normalizacija je dokazivo briše i paket
# se objavljuje. Drži se ovdje da se ne pomiješa s gornjim slučajem.
REMOVABLE_LABELLED_SOLUTION = LABEL_FREE_SOLUTION + " Dakle tačan odgovor je opcija a."


@pytest.fixture
def gate_runtime(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")


def _evidence(level):
    from matbot.tutor.schema import DifficultyEvidence
    counts = 1 if level == 1 else 2
    return DifficultyEvidence(
        reasoning_steps=counts, condition_count=counts, operation_count=counts,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


def _package(solution, level=1):
    from matbot.tutor.schema import SignatureParameter
    text, options = ((LEVEL1_TASK, LEVEL1_OPTIONS) if level == 1
                     else (LEVEL2_TASK, LEVEL2_OPTIONS))
    task = make_task_payload(text=text, options=list(options),
                             correct_option_index=0, expected=options[0],
                             solution=solution)
    return task.model_copy(update={
        "target_difficulty_level": level,
        "difficulty_evidence": _evidence(level),
        "task_signature": task.task_signature.model_copy(update={
            "normalized_parameters": [
                SignatureParameter(name="r", value=f"level{level}")]}),
    })


def _turn(session_id, message, difficulty_request=""):
    return {
        "session_id": session_id, "grade": GATE_GRADE,
        "selected_topic": GATE_LESSON, "selected_oblast": "",
        "student_message": message, "intent": "",
        "difficulty_request": difficulty_request, "interaction_phase": "",
        "last_tutor_task": "", "interaction_type": "student_question",
        "selected_option_id": "", "client_turn_id": "",
    }


def _publish_level_one(store, fake, session_id):
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=_package("Brojnik je $7$, a nazivnik $4$.")))
    response = run_practice_turn(store, fake, _turn(session_id, "Daj mi zadatak."))
    assert store.peek(session_id) is not None, response
    return response


def _ask_harder(store, fake, session_id, final_solution):
    """Recenzent vraća `correct` s KOMPLETNO prepravljenim paketom nivoa 2."""
    draft = make_tutor_draft(
        intent="harder_task", new_task=_package("Nepotpuno objašnjenje.", level=2),
        difficulty_diagnostics=make_difficulty_diagnostics("higher"))
    corrected = make_tutor_draft(
        intent="harder_task", new_task=_package(final_solution, level=2),
        difficulty_diagnostics=make_difficulty_diagnostics("higher"))
    fake.queue(draft)
    fake.queue(make_reviewer_final(decision="correct", final=corrected))
    return run_practice_turn(store, fake, _turn(session_id, "Daj mi teži zadatak.",
                                                difficulty_request="harder"))


def test_a_reviewer_correction_that_introduces_a_label_cannot_publish(
        gate_runtime, caplog):
    """Živi oblik s kapije: recenzent „correct" → oznaka u KONAČNOM rješenju."""
    import logging

    store, fake = SessionStore(), FakeLLM()
    _publish_level_one(store, fake, "gate-shape")
    before = store.peek("gate-shape")["current_task"]
    calls_before = fake.call_count

    caplog.set_level(logging.WARNING, logger="matbot.tutor")
    response = _ask_harder(store, fake, "gate-shape", LABELLED_SOLUTION)

    assert response["answer"] == SAFE_ERROR_MESSAGE
    # Odbijeno BAŠ zbog oznake opcije — ne zbog duplikata, težine ili sheme.
    rejections = [r.message for r in caplog.records if "tutor_rejected" in r.message]
    assert any(f"{mcq_integrity.OPTION_LABEL_CLAIM_CODE} [solution]" in line
               for line in rejections), rejections
    # Sesija ostaje na objavljenom zadatku nivoa 1 — nijedna mutacija.
    assert store.peek("gate-shape")["current_task"] == before
    assert fake.call_count - calls_before == 2      # nikad treći poziv


def test_the_same_correction_without_the_label_publishes(gate_runtime):
    """Kapija mora biti prohodna — inače je popravka samo nedostupnost."""
    store, fake = SessionStore(), FakeLLM()
    _publish_level_one(store, fake, "gate-shape-ok")
    calls_before = fake.call_count

    response = _ask_harder(store, fake, "gate-shape-ok", LABEL_FREE_SOLUTION)

    assert response["answer"] != SAFE_ERROR_MESSAGE
    assert fake.call_count - calls_before == 2


def test_a_removable_trailing_label_is_normalized_and_still_publishes(gate_runtime):
    """DVIJE RAZLIČITE SUDBINE, namjerno razdvojene.

    Apozicijska oznaka na kraju rečenice se DOKAZIVO briše (nema `$`, nema
    cifre), pa paket izlazi bez nje. Samo neuklonjiv oblik pada zatvoreno. Bez
    ovog testa bi se lako povjerovalo da svaka oznaka obara turn — a onda bi
    prva „popravka" bila da se normalizacija proširi, što je upravo ono što se
    ne smije raditi."""
    store, fake = SessionStore(), FakeLLM()
    _publish_level_one(store, fake, "gate-shape-removable")

    response = _ask_harder(store, fake, "gate-shape-removable",
                           REMOVABLE_LABELLED_SOLUTION)

    assert response["answer"] != SAFE_ERROR_MESSAGE
    stored = store.peek("gate-shape-removable")
    served = " ".join(str(value) for value in stored.values() if isinstance(value, str))
    assert mcq_integrity.option_label_claims(served) == ()
