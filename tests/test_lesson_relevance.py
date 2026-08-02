"""D35-3: izabrana lekcija ne smije nadjačati izričito pitanje iz druge teme.

Regresija je vezana za poziv 20 kampanje od 35: izabrana lekcija je bila iz
oblasti Racionalni brojevi, pitanje o uglovima trougla, a odgovor je počeo
nepovezanom lekcijom o pretvaranju decimalnog broja u razlomak."""
from matbot.explain import run_explain_turn
from matbot.lesson_relevance import lesson_context_is_strong
from matbot.terminology import normalize_terminology
from tests.conftest import FakeLLM, make_explain_output

FRACTION_LESSON = "6-04-001"      # lekcija iz oblasti Razlomci
TRIANGLE_QUESTION = "Koliki je zbir unutrašnjih uglova trougla i zašto?"


def payload(msg, topic=FRACTION_LESSON, history=None):
    return {
        "session_id": "rel-sess", "grade": 6, "selected_topic": topic,
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "",
        "last_tutor_task": "", "last_tutor_message": "",
        "conversation_history": history or [],
    }


def prompt_for(msg, **kw):
    fake = FakeLLM()
    fake.queue(make_explain_output())
    run_explain_turn(fake, payload(msg, **kw))
    return fake.explain_calls[0]  # (instructions, input_text)


# --- klasifikator ----------------------------------------------------------

def test_unrelated_named_topic_is_weak_context():
    assert not lesson_context_is_strong(TRIANGLE_QUESTION, "Sabiranje razlomaka", "Razlomci")


def test_deictic_message_stays_strong_context():
    for msg in ("Objasni mi ovo.", "Ne razumijem.", "Kako se ovo radi?",
                "Daj mi primjer.", "Može jednostavnije?"):
        assert lesson_context_is_strong(msg, "Sabiranje razlomaka", "Razlomci"), msg


def test_matching_named_topic_stays_strong_context():
    assert lesson_context_is_strong(
        "Kako se sabiraju razlomci s različitim nazivnicima?", "Sabiranje razlomaka", "Razlomci"
    )


def test_expression_alone_is_not_treated_as_another_topic():
    # Guard ne pogađa: izraz sam po sebi ne dokazuje drugu temu.
    assert lesson_context_is_strong("Riješi $2x+3=7$", "Linearne jednačine", "Jednačine")


def test_no_selected_lesson_is_always_strong():
    assert lesson_context_is_strong(TRIANGLE_QUESTION, "", "")


def test_croatian_form_in_student_input_is_recognized():
    # Ulaz učenika se NIKAD ne normalizuje, pa klasifikator mora prepoznati i
    # hrvatski oblik koji učenik može otkucati.
    assert not lesson_context_is_strong(
        "Koliki je zbir uglova trokuta?", "Sabiranje razlomaka", "Razlomci"
    )


# --- prompt ----------------------------------------------------------------

def test_unrelated_question_omits_first_explanation_rule():
    instructions, input_text = prompt_for(TRIANGLE_QUESTION)
    assert "PRVO OBJAŠNJENJE" not in instructions
    assert "PRIORITET" in instructions
    assert "NE spominji izabranu lekciju" in instructions
    assert "kontekst, ne ograničenje" in input_text
    assert "daj prvo objašnjenje teme" not in input_text


def test_generic_objasni_still_uses_selected_lesson():
    instructions, input_text = prompt_for("Objasni mi ovo.")
    assert "PRVO OBJAŠNJENJE" in instructions
    assert "NAZIV LEKCIJE" in instructions
    assert input_text.startswith("LEKCIJA:")
    assert "daj prvo objašnjenje teme" in input_text


def test_relevant_explicit_question_keeps_lesson_context():
    instructions, input_text = prompt_for("Kako se sabiraju razlomci?")
    assert "PRVO OBJAŠNJENJE" in instructions
    assert input_text.startswith("LEKCIJA:")


def test_follow_up_still_uses_conversation_history():
    history = [
        {"role": "user", "content": "Objasni mi razlomke."},
        {"role": "assistant", "content": "Razlomak ima brojnik i nazivnik."},
    ]
    _instructions, input_text = prompt_for("A drugi korak?", history=history)
    assert "KRATKA HISTORIJA:" in input_text
    assert "brojnik i nazivnik" in input_text


def test_history_is_kept_even_for_an_unrelated_question():
    history = [{"role": "assistant", "content": "Ranije objašnjenje o razlomcima."}]
    _instructions, input_text = prompt_for(TRIANGLE_QUESTION, history=history)
    assert "KRATKA HISTORIJA:" in input_text


# --- terminologija ---------------------------------------------------------

def test_croatian_triangle_term_is_normalized_in_output():
    assert normalize_terminology("Svaki trokut ima 180 stepeni.") == "Svaki trougao ima 180 stepeni."
    assert normalize_terminology("Zbir uglova trokuta.") == "Zbir uglova trougla."
    assert normalize_terminology("U trokutu ABC.") == "U trouglu ABC."


def test_croatian_correct_term_is_normalized_in_output():
    assert normalize_terminology("Točan odgovor.") == "Tačan odgovor."
    assert normalize_terminology("Točno je!") == "Tačno je!"
    assert normalize_terminology("TOČNO") == "TAČNO"


def test_similar_words_are_never_touched():
    for text in ("Točka je oznaka.", "Točak se okreće.", "Potočni kamen.",
                 "Tačka A i tačka B."):
        assert normalize_terminology(text) == text


def test_mathjax_is_not_altered_by_terminology_processing():
    text = "Vidi $\\frac{trokut}{2}$ i $$x=točno$$ ali trokut vani."
    out = normalize_terminology(text)
    assert "$\\frac{trokut}{2}$" in out
    assert "$$x=točno$$" in out
    assert "trougao vani" in out


def test_explain_answer_is_normalized_end_to_end():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Svaki trokut ima zbir uglova $180^\\circ$."))
    response = run_explain_turn(fake, payload("Objasni mi ovo."))
    assert "trokut" not in response["answer"]
    assert "trougao" in response["answer"]


# --- Uska čišćenja iz kampanje od 14 poziva (poziv 5) ------------------------

def test_croatian_angle_term_is_normalized():
    assert normalize_terminology("Kut je 90 stepeni.") == "Ugao je 90 stepeni."
    assert normalize_terminology("Kutovi su jednaki.") == "Uglovi su jednaki."
    assert normalize_terminology("Zbir kutova trougla.") == "Zbir uglova trougla."
    assert normalize_terminology("Mjeri kutom.") == "Mjeri uglom."


def test_unrelated_words_with_the_same_letters_are_untouched():
    for text in ("Kutija je puna.", "Kutak za učenje.", "Skuter je brz.",
                 "Akutni ugao.", "Kutijica sa olovkama."):
        assert normalize_terminology(text) == text


def test_kutomer_rule_still_wins_over_the_angle_rule():
    assert normalize_terminology("Mjeri kutomerom.") == "Mjeri uglomjerom."
    assert normalize_terminology("kutomer") == "uglomjer"


def test_angle_term_is_not_touched_inside_math():
    out = normalize_terminology("$kut=90$ ali kut vani")
    assert "$kut=90$" in out
    assert "ugao vani" in out
