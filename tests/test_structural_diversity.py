"""STRUKTURNA raznolikost: „isti zadatak s drugim brojevima“ nije nov zadatak.

Zahtjev iz produkcije. Ucenik ne dozivljava kao novu vjezbu ni promjenu brojeva,
ni promjenu imena, ni promijenjen redoslijed opcija.
"""
import pytest

from matbot import config
from matbot.tutor import package_preflight as preflight
from matbot.tutor import task_identity


class _Option:
    def __init__(self, text):
        self.id = "a"
        self.text = text


class _Task:
    def __init__(self, text, options=("$1$", "$2$", "$3$", "$4$")):
        self.text = text
        self.options = [_Option(t) for t in options]
        self.correct_option_index = 0


def _sig(text, options=("$1$", "$2$", "$3$", "$4$")):
    return task_identity.structural_signature(text, options)


# ---------------------------------------------------------------------------
# 1) POVRSINSKA VARIJACIJA JE ISTA STRUKTURA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first,second", [
    ("Koliko je $12 + 7$?", "Koliko je $45 + 8$?"),
    ("Ana ima 12 jabuka. Koliko joj ostane?", "Haris ima 19 jabuka. Koliko mu ostane?"),
    ("Izračunaj obim kvadrata stranice $5$ cm.", "Izračunaj obim kvadrata stranice $9$ cm."),
    ("Koji od ponuđenih brojeva je djeljiv sa $25$?",
     "Koji od ponuđenih brojeva je djeljiv sa $10$?"),
])
def test_number_and_name_swaps_are_the_same_structure(first, second):
    assert _sig(first) == _sig(second)


def test_option_order_and_values_do_not_create_a_new_structure():
    left = _sig("Koliko je $12 + 7$?", ("$19$", "$18$", "$20$", "$21$"))
    right = _sig("Koliko je $45 + 8$?", ("$53$", "$52$", "$54$", "$55$"))
    assert left == right


# ---------------------------------------------------------------------------
# 2) STVARNO DRUGA VJEZBA JE DRUGA STRUKTURA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first,second", [
    # direktno racunanje vs trazenje nedostajuce velicine
    ("Koliko je $12 + 7$?", "Koji broj nedostaje: $12 + \square = 19$?"),
    # direktno racunanje vs analiza greske
    ("Izračunaj obim kvadrata stranice $5$ cm.",
     "Učenik je obim kvadrata izračunao kao $5 \cdot 3$. Gdje je pogriješio?"),
    # prepoznavanje vs obrazlozenje
    ("Koji od ponuđenih brojeva je djeljiv sa $10$?",
     "Broj $70$ je djeljiv sa $10$. Koji korak to najbolje obrazlaže?"),
    # simbolicki vs tekstualni oblik
    ("Riješi jednačinu $x + 5 = 12$.",
     "Zamišljenom broju dodaš $5$ i dobiješ $12$. Koji je to broj?"),
])
def test_genuinely_different_task_forms_are_distinct_structures(first, second):
    assert _sig(first) != _sig(second)


# ---------------------------------------------------------------------------
# 3) KAPIJA OBJAVE ZA „DAJ NOVI“
# ---------------------------------------------------------------------------

def _recent(*texts):
    return [{"signature": _sig(text), "text": text} for text in texts]


def test_new_task_that_only_swaps_numbers_is_flagged():
    issue = preflight.structural_repetition_issue(
        _Task("Koliko je $45 + 8$?"), _recent("Koliko je $12 + 7$?"), "next_task")
    assert issue is not None
    assert issue.code == preflight.TASK_TOO_SIMILAR_CODE


def test_new_task_with_a_different_form_passes():
    assert preflight.structural_repetition_issue(
        _Task("Koji broj nedostaje: $12 + \square = 19$?"),
        _recent("Koliko je $12 + 7$?"), "next_task") is None


@pytest.mark.parametrize("intent", ["harder_task", "easier_task", "generate_task"])
def test_only_an_explicit_new_task_request_is_gated(intent):
    """Na „teze“/„lakse“ struktura smije ostati ista — mijenja se NIVO."""
    assert preflight.structural_repetition_issue(
        _Task("Koliko je $45 + 8$?"), _recent("Koliko je $12 + 7$?"), intent) is None


def test_no_history_means_no_finding():
    assert preflight.structural_repetition_issue(
        _Task("Koliko je $45 + 8$?"), [], "next_task") is None


def test_unreadable_task_is_skipped_not_guessed():
    assert preflight.structural_repetition_issue(
        _Task(""), _recent("Koliko je $12 + 7$?"), "next_task") is None


def test_reviewer_is_told_to_change_the_archetype_not_the_numbers():
    from matbot.tutor import prompts

    rule = prompts._REVIEWER_PREFLIGHT_RULE
    assert preflight.TASK_TOO_SIMILAR_CODE in rule
    assert "ARCHETYPE" in rule
    assert "fail_closed" in rule          # uska lekcija ne izlazi iz opsega


# ---------------------------------------------------------------------------
# 4) HISTORIJA JE OGRANICENA
# ---------------------------------------------------------------------------

def test_no_control_byte_ever_lands_in_a_structural_regex():
    """Cuvar protiv tihe greske: `\b` napisan u ne-raw stringu postaje bajt
    0x08 i regex nikad ne okine. To se vec desilo — vise se ne smije desiti
    neprimjeceno."""
    source = (__import__("pathlib").Path(task_identity.__file__)).read_bytes()
    assert b"" not in source


def test_recent_structure_history_is_bounded():
    from matbot.session_store import SessionStore

    store = SessionStore()
    session = store.load("bound", 6, "6-04-001", "Naslov", "Oblast", "practice")
    session["recent_structures"] = [
        {"signature": f"s{i}", "text": f"t{i}"} for i in range(50)]
    store.save(session)
    assert len(store.peek("bound")["recent_structures"]) <= config.MAX_RECENT_STRUCTURES
