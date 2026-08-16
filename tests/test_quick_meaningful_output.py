# -*- coding: utf-8 -*-
"""Quick odgovor mora imati SADRŽAJ, ne samo biti neprazan.

ŽIVI NALAZ: raniji P0 („status=ready, answer=''“) je zatvoren, ali je ostao uži
oblik — odgovor koji NIJE prazan, a ne znači ništa. U produkciji je to bilo
doslovno `$:$`.

Mjeri se SADRŽAJ, ne dužina: kratki tačni odgovori (`0`, `$x=4$`, `$\\pi$`)
moraju ostati netaknuti.
"""
import pytest

from matbot import quick
from matbot.mathsafe import lacks_meaningful_content, visible_text_is_empty
from tests.conftest import FakeLLM, make_quick_image_output, make_quick_output


def _turn(fake, message="Koliko je $84:7$?", image=None):
    return quick.run_quick_turn(fake, {
        "session_id": "meaning", "grade": 7, "selected_topic": "",
        "selected_oblast": "", "student_message": message,
        "conversation_history": [], "interaction_phase": ""}, image=image)


MEANINGLESS = ["$:$", ":", ";", "$;$", "$.$", "$,$", "$...$", "$-$", "$+$",
               "$()$", "$$", " ", "$\\quad$", "$\\,$", "\n\n", "$ $"]
MEANINGFUL = ["0", "$0$", "-1", "$-1$", "12", "$12$", "1/2", "$\\frac{1}{2}$",
              "$x=4$", "∅", "$\\emptyset$", "$\\pi$", "$45^\\circ$",
              "$6\\sqrt{2}$", "Da.", "Ne.", "12:3=4", "$12:3=4$", "$1,15$",
              "$102$", "$-\\frac{1}{8}$", "$108\\,\\text{cm}^2$"]


def test_historical_colon_only_answer_fails_closed():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$:$"))
    response = _turn(fake)
    assert response["answer"] == quick.SAFE_ERROR_MESSAGE
    assert "status" not in response          # bez statusa → frontend ne crta prečice
    assert fake.call_count == 1              # bez retryja i bez drugog poziva


@pytest.mark.parametrize("reply", MEANINGLESS)
def test_meaningless_answers_fail_closed(reply):
    fake = FakeLLM()
    fake.queue(make_quick_output(reply=reply))
    response = _turn(fake)
    assert response["answer"] == quick.SAFE_ERROR_MESSAGE, reply
    assert "status" not in response
    assert fake.call_count == 1


@pytest.mark.parametrize("reply", MEANINGFUL)
def test_meaningful_short_answers_still_publish(reply):
    fake = FakeLLM()
    fake.queue(make_quick_output(reply=reply))
    response = _turn(fake, message="Koliko je?")
    assert response.get("status") == "ready", (reply, response.get("answer"))
    assert response["answer"].strip()
    assert fake.call_count == 1


def test_image_path_shares_the_invariant():
    class _Image:
        data_url = "data:image/jpeg;base64,AAAA"
        image_format, width, height, normalized_bytes = "JPEG", 10, 10, 32

        def log_metadata(self):
            return "test-image"

    fake = FakeLLM()
    fake.queue(make_quick_image_output(reply="$:$"))
    response = _turn(fake, message="", image=_Image())
    assert response["answer"] == quick.SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert fake.call_count == 1


# ---------------------------------------------------------------------------
# PREDIKAT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", MEANINGLESS)
def test_predicate_flags_meaningless(text):
    assert lacks_meaningful_content(text), text


@pytest.mark.parametrize("text", MEANINGFUL)
def test_predicate_accepts_meaningful(text):
    assert not lacks_meaningful_content(text), text


def test_empty_predicate_is_still_a_subset():
    """Sve što je prazno je i bezsadržajno — raniji P0 ostaje zatvoren."""
    for text in ("", "   ", "\n", "$$", "$\\quad$"):
        assert visible_text_is_empty(text)
        assert lacks_meaningful_content(text)


def test_no_word_count_requirement():
    """Jedan znak je dovoljan ako nosi sadržaj."""
    assert not lacks_meaningful_content("0")
    assert not lacks_meaningful_content("$7$")
    assert not lacks_meaningful_content("∅")
