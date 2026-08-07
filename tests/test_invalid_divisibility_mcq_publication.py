r"""Nijedan MCQ bez ijednog tačnog odgovora ne smije stići do učenika.

PRODUKCIJSKI NALAZ (ručni smoke test, 6. razred, lekcija 6-03-004 „Pravila
djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25“, zastavice
`MATBOT_PRACTICE_PIPELINE=universal_two_call` i
`MATBOT_PRACTICE_DIFFICULTY_LEVELS=enabled`):

    učenik        : „Daj mi jedan zadatak za vježbu iz ove teme.“
    prvi pokušaj  : sigurna poruka (paket odbijen)
    drugi pokušaj : OBJAVLJENO
                    „Primijeni pravila djeljivosti: koji od sljedećih brojeva
                     je djeljiv i sa 6 i sa 25?“
                    opcije: 8 · 6 · 7 · 9

Broj djeljiv i sa 6 i sa 25 djeljiv je sa NZS(6,25)=150. Nijedna od četiri
ponuđene opcije to nije — objavljen zadatak nije imao NIJEDAN tačan odgovor.

UZROK: `mcq_integrity._explicit_divisors` je PARCIJALAN parser. Kad nastavak
liste djelilaca ne odgovara nijednom priznatom obliku („…sa 6 i istovremeno sa
25“, „…sa 6 i sa brojem 25“, „…sa 25, a ni sa 4“), on TIHO vrati ono što je do
tada pročitao. Oracle taj KRNJI uslov uzme kao istinu i onda AKTIVNO POTVRDI da
je npr. 6 jedini tačan odgovor — pa i preflight i recenzentska invarijanta i
objava vide „uredan paket“. Guard koji uslov ne može dokazati ne smije ga
izmisliti (CLAUDE.md: što se ne može dokazati, mora se preskočiti).

GRANICA, NE SLABLJENJE: podržani oblici („…djeljiv sa 25?“, „…i sa 6 i sa 25?“,
„…sa 2, 3 i 5?“) nemaju nepročitan broj u istoj rečenici i ostaju netaknuti.
"""
import pytest

from matbot import mcq_integrity
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import package_preflight
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor.schema import UnifiedOutputError
from tests.conftest import (FakeLLM, make_task_payload, make_tutor_draft,
                            queue_two_call)

LESSON, GRADE = "6-03-004", 6

# --- DOSLOVNI PRODUKCIJSKI ZADATAK — ne mijenjati ---------------------------
LIVE_TASK = ("Primijeni pravila djeljivosti: koji od sljedećih brojeva je "
             "djeljiv i sa 6 i sa 25?")
LIVE_OPTIONS = ("8", "6", "7", "9")
LIVE_MARKED_INDEX = 1          # označena opcija je bila „6“

# Iste četiri opcije i isti par djelilaca, formulacije koje parser NIJE umio
# pročitati do kraja. Svaka od njih objavljuje TAČNO ekran sa slike.
TRUNCATING_VARIANTS = (
    "Koji od sljedećih brojeva je djeljiv i sa 6 i istovremeno sa 25?",
    "Koji od sljedećih brojeva je djeljiv sa 6 i sa brojem 25?",
    "Koji od sljedećih brojeva je djeljiv sa 6, ali i sa 25?",
    "Koji od sljedećih brojeva je djeljiv sa 6 te sa 25?",
    "Koji od sljedećih brojeva je djeljiv sa 6 i, naravno, sa 25?",
)

# --- KONTROLE ---------------------------------------------------------------
POSITIVE_OPTIONS = ("150", "60", "75", "90")      # samo 150 = 6·25 zadovoljava
MULTIPLE_OPTIONS = ("150", "300", "75", "90")     # 150 i 300 — dva tačna
SINGLE_DIVISOR_TASK = "Koji od sljedećih brojeva je djeljiv sa 6?"
SINGLE_DIVISOR_OPTIONS = ("60", "25", "7", "11")  # samo 60 je djeljiv sa 6
PLACEHOLDER_TASK = ("Dopuni: $\\square 50$ tako da je broj djeljiv i sa 6 i sa "
                    "25. Koja cifra u $\\square$ to omogućava?")
PLACEHOLDER_OPTIONS = ("8", "6", "7", "9")        # 750 = 6·125 = 25·30 → cifra 7


def _turn(session_id, message="Daj mi jedan zadatak za vježbu iz ove teme.", **changes):
    turn = {
        "session_id": session_id, "grade": GRADE, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    turn.update(changes)
    return turn


@pytest.fixture(autouse=True)
def _universal_runtime(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _payload(text, options, marked_index):
    return make_task_payload(text=text, options=options,
                             correct_option_index=marked_index,
                             expected=options[marked_index])


def _context():
    return lesson_context_module.build(GRADE, LESSON)


# ---------------------------------------------------------------------------
# 1. DOSLOVNI PRODUKCIJSKI PAKET
# ---------------------------------------------------------------------------

def test_live_task_is_inside_the_oracle_scope_with_both_divisors():
    result = mcq_integrity.evaluate_divisibility_mcq(LIVE_TASK, LIVE_OPTIONS)
    assert result.applicable
    assert set(result.divisors) == {6, 25}


def test_live_task_has_zero_mathematically_correct_options():
    result = mcq_integrity.evaluate_divisibility_mcq(LIVE_TASK, LIVE_OPTIONS)
    assert result.correct_indices == ()
    assert not result.valid
    assert result.reason_code == "no_correct_option"
    # Nezavisna kontrola same matematike, bez oracla.
    assert [value for value in (8, 6, 7, 9) if value % 150 == 0] == []


def test_live_package_is_rejected_by_preflight():
    issues = package_preflight.collect_package_issues(
        _payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED_INDEX))
    assert "no_correct_option" in package_preflight.describe_issues(issues)


def test_live_package_is_rejected_by_final_publication_validation():
    with pytest.raises(UnifiedOutputError) as error:
        tutor_pipeline._validate_task_server_side(
            _payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED_INDEX), _context())
    assert "no_correct_option" in str(error.value)


# ---------------------------------------------------------------------------
# 2. FORMULACIJE KOJE SU PARSER TIHO SKRAĆIVALE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", TRUNCATING_VARIANTS)
def test_partially_read_divisor_list_never_certifies_an_option(text):
    """Krnje pročitan uslov ne smije proglasiti nijednu opciju tačnom."""
    result = mcq_integrity.evaluate_divisibility_mcq(text, LIVE_OPTIONS)
    assert not result.valid, f"oracle je potvrdio krnji uslov: {result}"
    assert result.correct_indices == ()


@pytest.mark.parametrize("text", TRUNCATING_VARIANTS)
def test_partially_read_divisor_list_fails_publication_closed(text):
    failure, _result = mcq_integrity.publication_failure(
        text, LIVE_OPTIONS, LIVE_MARKED_INDEX, LIVE_OPTIONS[LIVE_MARKED_INDEX])
    assert failure, "objava je propustila paket s nepročitanim djeliocem"


@pytest.mark.parametrize("text", TRUNCATING_VARIANTS)
def test_partially_read_divisor_list_is_rejected_by_preflight(text):
    issues = package_preflight.collect_package_issues(
        _payload(text, LIVE_OPTIONS, LIVE_MARKED_INDEX))
    assert issues, "preflight nije vidio nijedan nalaz"


@pytest.mark.parametrize("text", TRUNCATING_VARIANTS)
def test_partially_read_divisor_list_is_rejected_at_publication(text):
    with pytest.raises(UnifiedOutputError):
        tutor_pipeline._validate_task_server_side(
            _payload(text, LIVE_OPTIONS, LIVE_MARKED_INDEX), _context())


# ---------------------------------------------------------------------------
# 3. CIO TURN: RECENZENTOV KONAČAN PAKET I STANJE SESIJE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", (LIVE_TASK,) + TRUNCATING_VARIANTS)
def test_invalid_package_never_reaches_the_browser_and_leaves_no_state(text):
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=_payload(text, LIVE_OPTIONS, LIVE_MARKED_INDEX)))

    response = run_practice_turn(store, fake, _turn("invalid-1"))

    assert "status" not in response          # sigurna poruka, bez next_state
    assert response["last_tutor_task"] == ""
    assert store.peek("invalid-1") is None   # nijedna mutacija sesije
    assert fake.call_count <= 2              # nikad treći poziv


def test_rejected_package_preserves_an_already_active_task():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=_payload(SINGLE_DIVISOR_TASK, SINGLE_DIVISOR_OPTIONS, 0)))
    first = run_practice_turn(store, fake, _turn("invalid-2"))
    assert first["status"] == "ready"
    before = dict(store.peek("invalid-2"))

    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=_payload(LIVE_TASK, LIVE_OPTIONS, LIVE_MARKED_INDEX)))
    second = run_practice_turn(store, fake, _turn("invalid-2", message="Daj mi novi zadatak."))

    assert "status" not in second
    after = store.peek("invalid-2")
    assert after["current_task"] == before["current_task"]
    assert after["current_options"] == before["current_options"]
    assert after["correct_option_id"] == before["correct_option_id"]


# ---------------------------------------------------------------------------
# 4. KONTROLE — ORACLE SE NE SLABI I NE ŠIRI
# ---------------------------------------------------------------------------

def test_positive_control_accepts_exactly_one_correct_option():
    result = mcq_integrity.evaluate_divisibility_mcq(LIVE_TASK, POSITIVE_OPTIONS)
    assert result.applicable and result.valid
    assert result.correct_indices == (0,)
    assert result.correct_value == 150
    failure, _ = mcq_integrity.publication_failure(
        LIVE_TASK, POSITIVE_OPTIONS, 0, "150")
    assert failure == ""


def test_positive_control_publishes_through_the_whole_pipeline():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task", new_task=_payload(LIVE_TASK, POSITIVE_OPTIONS, 0)))

    response = run_practice_turn(store, fake, _turn("positive-1"))

    assert response["status"] == "ready"
    assert LIVE_TASK in response["answer"]
    session = store.peek("positive-1")
    correct = next(option for option in session["current_options"]
                   if option["id"] == session["correct_option_id"])
    assert correct["text"] == "150"
    assert fake.call_count == 2


def test_multiple_correct_options_are_rejected():
    result = mcq_integrity.evaluate_divisibility_mcq(LIVE_TASK, MULTIPLE_OPTIONS)
    assert result.applicable and not result.valid
    assert result.reason_code == "multiple_correct_options"
    assert result.correct_indices == (0, 1)
    with pytest.raises(UnifiedOutputError):
        tutor_pipeline._validate_task_server_side(
            _payload(LIVE_TASK, MULTIPLE_OPTIONS, 0), _context())


def test_single_divisor_question_is_not_read_as_requiring_25():
    result = mcq_integrity.evaluate_divisibility_mcq(
        SINGLE_DIVISOR_TASK, SINGLE_DIVISOR_OPTIONS)
    assert result.applicable and result.valid
    assert result.divisors == (6,)
    # 25 NIJE dopisano u uslov: da jeste, nijedna opcija ne bi bila tačna.
    assert result.correct_indices == (0,) and result.correct_value == 60
    tutor_pipeline._validate_task_server_side(
        _payload(SINGLE_DIVISOR_TASK, SINGLE_DIVISOR_OPTIONS, 0), _context())


def test_placeholder_digit_task_uses_digit_candidate_logic():
    """Opcije su kandidati za CIFRU, ne brojevi čija se djeljivost tvrdi."""
    result = mcq_integrity.evaluate_divisibility_mcq(
        PLACEHOLDER_TASK, PLACEHOLDER_OPTIONS)
    assert not result.applicable, "oracle je sudio zadatku s mjestodržačem"
    # Tačna cifra je 7: $750$ je djeljiv i sa 6 i sa 25.
    assert 750 % 6 == 0 and 750 % 25 == 0
    assert [digit for digit in (8, 6, 7, 9)
            if (digit * 100 + 50) % 150 == 0] == [7]
    # Paket s cifrom 7 mora proći objavu (oracle ćuti, ostali validatori rade).
    tutor_pipeline._validate_task_server_side(
        _payload(PLACEHOLDER_TASK, PLACEHOLDER_OPTIONS, 2), _context())


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
