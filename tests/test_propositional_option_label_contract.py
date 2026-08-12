"""PROPOZICIJSKI MCQ: oznaka opcije u SUBJEKTU rečenice obara objavu.

ŽIVA KAPIJA IZDANJA, DVA PUTA NA ISTOJ LEKCIJI (6-04-001):

  2fa9507  role=harder_level2  intent=harder_task   „Koja tvrdnja o razlomku
           $\\frac{7}{4}$ je tačna?“   — recenzent vratio `correct`
  64c2940  role=fresh_level1   intent=generate_task „Koja od sljedećih tvrdnji o
           razlomku $\\frac{3}{5}$ je tačna?“ — recenzent vratio `approve`,
           pa je osnova objave bio TUTOROV nacrt

Oba puta ista dijagnostika: `solution_option_label_claim [solution]`.

ZAŠTO BAŠ OVA LEKCIJA. Kad su opcije TVRDNJE, tačan odgovor JESTE tvrdnja, pa
model prirodno počinje rečenicu njenom oznakom („Tvrdnja pod a) je tačna.“).
Tada oznaka nije apozicija nego SUBJEKAT, a serverska normalizacija briše samo
DOKAZIVO uklonjive klauzule — subjekat se ne smije nagađati, pa paket pada
zatvoreno. Kod MCQ-a s vrijednošću model vodi vrijednošću, oznaka ispadne na
kraj i normalizacija je uredno ukloni; zato se defekt vidi baš ovdje.

ŠTA OVAJ FAJL DOKAZUJE:
  * kapija NIJE oslabljena — oznaka u subjektu i dalje pada zatvoreno;
  * semantičko objašnjenje iste matematike prolazi objavu;
  * oba prompta (Tutor i Recenzent) izričito nose propozicijski slučaj;
  * ispravno rješenje ostaje ispravno bez obzira na ishod miješanja.

ŠTA NE DOKAZUJE: da model pravilo neće prekršiti. Prompt je prevencija, kapija
je dokaz. Zato je ovdje i jedno i drugo.
"""
from __future__ import annotations

import pytest

from matbot import mcq_integrity
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import prompts as tutor_prompts

GATE_GRADE, GATE_LESSON = 6, "6-04-001"

# Doslovni oblici iz obje pale kapije: oznaka je SUBJEKAT ili nosilac uzroka.
UNSAFE_SUBJECT_FORMS = (
    "Tvrdnja pod a) je tačna.",
    "Tačna je tvrdnja pod a) jer je brojnik $3$.",
    "Opcija c tačno opisuje razlomak $\\frac{7}{4}$.",
    "Tačan odgovor je b) jer je $7$ brojnik.",
)
# Ista matematika, bez ijedne oznake — ono što prompt sada izričito traži.
SAFE_SEMANTIC_FORMS = (
    "U razlomku $\\frac{3}{5}$ brojnik je $3$, a nazivnik $5$.",
    "Brojnik je broj iznad razlomačke crte, a nazivnik ispod nje.",
    "Brojnik $7$ je veći od nazivnika $4$, pa je razlomak nepravi.",
)


@pytest.fixture
def context():
    return lesson_context_module.build(GATE_GRADE, GATE_LESSON)


def _flat(text):
    return " ".join((text or "").split())


# ===========================================================================
# TEST 1 — oznaka u subjektu MORA i dalje pasti zatvoreno
# ===========================================================================

@pytest.mark.parametrize("solution", UNSAFE_SUBJECT_FORMS)
def test_unsafe_subject_label_claim_still_fails_closed(solution):
    """Dokaz da kapija NIJE oslabljena ovom popravkom."""
    assert mcq_integrity.option_label_claims(solution)
    _, code = mcq_integrity.option_label_normalization(solution)
    assert code == mcq_integrity.OPTION_LABEL_CLAIM_CODE, solution


# ===========================================================================
# TEST 2 — semantičko objašnjenje prolazi objavu netaknuto
# ===========================================================================

@pytest.mark.parametrize("solution", SAFE_SEMANTIC_FORMS)
def test_safe_semantic_solution_publishes_unchanged(solution):
    assert mcq_integrity.option_label_claims(solution) == ()
    normalized, code = mcq_integrity.option_label_normalization(solution)
    assert code == ""
    assert normalized == solution          # ništa se ne prepravlja


def test_safe_and_unsafe_carry_the_same_mathematics():
    """Popravka mijenja FORMU, ne sadržaj: oba oblika nose iste brojeve."""
    unsafe = "Tačan odgovor je b) jer je $7$ brojnik, a $4$ nazivnik."
    safe = "Brojnik je $7$, a nazivnik $4$."
    assert mcq_integrity.option_label_normalization(unsafe)[1] != ""
    assert mcq_integrity.option_label_normalization(safe)[1] == ""
    for number in ("7", "4"):
        assert number in unsafe and number in safe


# ===========================================================================
# TEST 3 — UGOVOR PROMPTA (Tutor i Recenzent)
# ===========================================================================

def test_tutor_prompt_covers_the_propositional_case(context):
    rule = _flat(tutor_prompts.build_tutor_instructions(context))
    assert "KAD SU OPCIJE TVRDNJE" in rule
    assert "PONOVI SVOJIM RIJEČIMA" in rule
    assert "Tvrdnja pod a) je tačna." in rule          # zabranjen oblik imenovan
    # stara, šira zabrana i dalje stoji
    assert "NIKAD ne imenuj slovo opcije" in rule


def test_reviewer_prompt_covers_the_propositional_case():
    rule = _flat(tutor_prompts._REVIEWER_OPTION_IDENTITY_RULE)
    assert "WHEN THE OPTIONS ARE STATEMENTS" in rule
    assert "RESTATE that statement" in rule
    assert "Never let the label be the subject" in rule
    assert "OPTION IDENTITY IS THE SERVER'S, NEVER YOURS" in rule


def test_both_prompts_still_say_labels_are_server_owned(context):
    tutor = _flat(tutor_prompts.build_tutor_instructions(context))
    reviewer = _flat(tutor_prompts._REVIEWER_OPTION_IDENTITY_RULE)
    assert "server opcije izmiješa POSLIJE tebe" in tutor
    assert "The server shuffles the options AFTER you answer" in reviewer


# ===========================================================================
# TEST 5 — ISPRAVNO RJEŠENJE JE NEOVISNO O MIJEŠANJU
# ===========================================================================

@pytest.mark.parametrize("solution", SAFE_SEMANTIC_FORMS)
def test_safe_solution_survives_every_option_ordering(solution):
    """Bez oznake nema šta da zastari kad server izmiješa opcije.

    Mjeri se SVOJSTVO teksta: ne sadrži nijedno slovo opcije, pa ga nijedan
    ishod miješanja ne može učiniti netačnim."""
    import itertools

    options = ["Brojnik je $3$.", "Nazivnik je $3$.",
               "Brojnik je $5$.", "Razlomak je nepravi."]
    for order in itertools.permutations(range(4)):
        labels = dict(zip("abcd", (options[i] for i in order)))
        # rješenje ne imenuje nijednu oznaku, pa je svaki raspored jednako valjan
        assert mcq_integrity.option_label_claims(solution) == ()
        assert set(labels) == {"a", "b", "c", "d"}


def test_unsafe_solution_would_go_stale_under_shuffle():
    """Kontrapunkt: oblik s oznakom je tačan samo za JEDAN ishod miješanja."""
    solution = "Tvrdnja pod a) je tačna."
    assert mcq_integrity.option_label_claims(solution) == ("a",)
    # Server oznaku dodjeljuje TEK poslije miješanja, pa je ova tvrdnja
    # neprovjerljiva u trenutku pisanja — i zato se paket odbija.
    _, code = mcq_integrity.option_label_normalization(solution)
    assert code == mcq_integrity.OPTION_LABEL_CLAIM_CODE


# ===========================================================================
# TEST 4 — KROZ STVARNI PUT OBJAVE (recenzent `approve`, osnova = Tutorov nacrt)
# ===========================================================================
# Ovo je najbliža moguća rekonstrukcija pale kapije bez ijednog živog poziva:
# ista lekcija, isti oblik zadatka, ista ruta (non_contract, dva poziva),
# recenzent `approve` — dakle osnova objave je TUTOROV nacrt, tačno kao u
# `64c2940`.

PROPOSITIONAL_TASK = r"Koja od sljedećih tvrdnji o razlomku $\frac{3}{5}$ je tačna?"
PROPOSITIONAL_OPTIONS = ("Brojnik je $3$, a nazivnik $5$.",
                         "Brojnik je $5$, a nazivnik $3$.",
                         "Brojnik je $8$.",
                         "Nazivnik je $2$.")


def _turn(session_id, message="Daj mi zadatak."):
    return {"session_id": session_id, "grade": GATE_GRADE,
            "selected_topic": GATE_LESSON, "selected_oblast": "",
            "student_message": message, "intent": "", "difficulty_request": "",
            "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "student_question", "selected_option_id": "",
            "client_turn_id": ""}


def _run_with_solution(monkeypatch, session_id, solution):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore
    from tests.conftest import (FakeLLM, make_reviewer_final,
                                make_task_payload, make_tutor_draft)

    payload = make_task_payload(
        text=PROPOSITIONAL_TASK, options=PROPOSITIONAL_OPTIONS,
        correct_option_index=0, expected=PROPOSITIONAL_OPTIONS[0],
        solution=solution, difficulty="easy")
    draft = make_tutor_draft(intent="generate_task", reply="Evo zadatka.",
                             lesson_focus="pojam razlomka", new_task=payload)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(draft)
    fake.queue(make_reviewer_final(decision="approve", final=draft))
    response = run_practice_turn(store, fake, _turn(session_id))
    return {"response": response, "session": store.peek(session_id),
            "tutor_calls": len(fake.tutor_calls),
            "reviewer_calls": len(fake.reviewer_calls)}


def test_reviewer_approve_publishes_a_safe_propositional_solution(monkeypatch):
    result = _run_with_solution(monkeypatch, "prop-safe",
                                SAFE_SEMANTIC_FORMS[0])
    assert result["response"]["status"] == "ready", result["response"]
    assert PROPOSITIONAL_TASK in (result["session"].get("current_task") or "")
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


@pytest.mark.parametrize("solution", UNSAFE_SUBJECT_FORMS)
def test_reviewer_approve_cannot_publish_a_subject_label_claim(
        monkeypatch, solution):
    """Kapija stoji i kad je recenzent odobrio — tačan oblik pale kapije."""
    result = _run_with_solution(monkeypatch, "prop-unsafe", solution)
    assert "status" not in result["response"]          # sigurna poruka
    assert not (result["session"] or {}).get("current_task")
    # Odbijanje NE troši treći poziv.
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1
