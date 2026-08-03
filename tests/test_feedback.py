"""Testovi kratkog feedbacka na pogrešan odgovor (matbot/feedback.py + Practice).

Živi nalaz koji ovi testovi zaključavaju: nakon netačnog klika tutor je pisao
dugačak dokaz zašto je izabrana opcija pogrešna. Ugovor je sada: „Netačno.“ +
JEDAN sažet hint, bez otkrivanja tačne opcije.
"""
from matbot import config, feedback
from tests.conftest import FakeLLM, make_options, make_output, make_task
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore


# ---------------------------------------------------------------------------
# Jedinični testovi oblikovanja
# ---------------------------------------------------------------------------

def test_first_wrong_starts_exactly_with_netacno():
    out = feedback.shape_first_wrong_feedback("Pomnoži nazivnik.", "")
    assert out.startswith("Netačno.")


def test_first_wrong_contains_a_hint_label():
    out = feedback.shape_first_wrong_feedback("Pomnoži nazivnik sa 3.", "")
    assert "Hint: " in out
    assert "Pomnoži nazivnik sa 3." in out


def test_model_own_verdict_is_stripped_so_it_is_not_doubled():
    out = feedback.shape_first_wrong_feedback("Netačno. Pomnoži nazivnik.", "")
    assert out.count("Netačno.") == 1


def test_repeated_model_verdicts_are_all_stripped():
    out = feedback.shape_first_wrong_feedback("Netačno. Nije tačno. Pomnoži nazivnik.", "")
    assert out.count("Netačno.") == 1
    assert "Nije tačno" not in out


def test_model_own_hint_label_is_not_doubled():
    out = feedback.shape_first_wrong_feedback("Hint: Pomnoži nazivnik.", "")
    assert out.count("Hint:") == 1


def test_falls_back_to_reply_when_hint_missing():
    out = feedback.shape_first_wrong_feedback("", "Provjeri koji je zajednički nazivnik.")
    assert "Provjeri koji je zajednički nazivnik." in out


def test_generic_hint_used_when_both_hint_and_reply_empty():
    out = feedback.shape_first_wrong_feedback("", "")
    assert feedback.GENERIC_HINT in out


def test_hint_leaking_correct_option_is_replaced_by_generic():
    out = feedback.shape_first_wrong_feedback(
        "Tačan odgovor je $\\frac{9}{24}$.", "", correct_option_text="$\\frac{9}{24}$"
    )
    assert "\\frac{9}{24}" not in out
    assert feedback.GENERIC_HINT in out


def test_hint_leaking_expected_answer_is_replaced_by_generic():
    out = feedback.shape_first_wrong_feedback(
        "Rezultat je 16/60.", "", expected_answer="16/60"
    )
    assert "16/60" not in out
    assert feedback.GENERIC_HINT in out


def test_short_numbers_in_hint_are_not_treated_as_leak():
    """Goli broj se prirodno pojavljuje u ispravnom hintu — ne smije obarati
    hint na generički."""
    out = feedback.shape_first_wrong_feedback(
        "Izračunaj $24:8$ pa tim brojem pomnoži brojnik.", "", expected_answer="3"
    )
    assert "Izračunaj" in out
    assert feedback.GENERIC_HINT not in out


def test_first_wrong_respects_length_bound():
    long_hint = ("Ovo je jako duga rečenica koja objašnjava korak. " * 20)
    out = feedback.shape_first_wrong_feedback(long_hint, "")
    assert len(out) <= config.MAX_FIRST_WRONG_FEEDBACK_CHARS


def test_clipping_never_breaks_mathjax():
    hint = "Prvo izračunaj $\\frac{24}{8}$. " + ("Dodatna napomena koja produžava tekst. " * 20)
    out = feedback.shape_first_wrong_feedback(hint, "")
    assert out.count("$") % 2 == 0


def test_clip_returns_none_when_no_safe_boundary_exists_at_all():
    """Bez razmaka i bez kraja rečenice nema sigurne granice — modul MORA
    signalizovati neuspjeh (None) umjesto da vrati predug tekst ili presiječe
    usred $...$ (vidi shape_first_wrong_feedback koji tada pada na GENERIC_HINT)."""
    text = "$" + "a" * 500 + "$"
    assert feedback.clip_preserving_math(text, 100) is None


def test_clip_falls_back_to_word_boundary_when_no_sentence_end_exists():
    text = "Prvo pomnoži pa podijeli pa saberi pa oduzmi pa provjeri rezultat opet"
    out = feedback.clip_preserving_math(text, 40)
    assert out is not None
    assert len(out) <= 40
    assert text.startswith(out)


def test_clip_returns_text_untouched_when_already_short():
    assert feedback.clip_preserving_math("Kratko.", 100) == "Kratko."


def test_clip_never_cuts_inside_mathjax_at_word_boundary():
    text = "Provjeri korak: " + "x " * 30 + "$\\frac{1}{2}$ ostatak teksta ovdje"
    out = feedback.clip_preserving_math(text, 45)
    assert out is not None
    assert out.count("$") % 2 == 0


def test_final_wrong_prefix_starts_with_netacno_and_keeps_body():
    out = feedback.shape_final_wrong_prefix("Postupak je $2 \\cdot 3 = 6$.")
    assert out.startswith("Netačno.")
    assert "$2 \\cdot 3 = 6$" in out


def test_final_wrong_is_not_length_clipped():
    body = "Detaljan postupak. " * 40
    out = feedback.shape_final_wrong_prefix(body)
    assert len(out) > config.MAX_FIRST_WRONG_FEEDBACK_CHARS


# ---------------------------------------------------------------------------
# C. Otkrivanje odgovora kroz eksplicitne fraze — i KRATKI odgovori
# ---------------------------------------------------------------------------

def test_reveal_phrase_catches_short_numeric_answer():
    assert feedback.leaks_answer("Odgovor je 2.", expected_answer="2")


def test_reveal_phrase_catches_negative_number():
    assert feedback.leaks_answer("Rješenje je -3.", expected_answer="-3")


def test_reveal_phrase_catches_pi():
    assert feedback.leaks_answer("Tačno je π.", expected_answer="π")


def test_reveal_phrase_catches_latex_pi():
    assert feedback.leaks_answer("Tačno je \\pi.", expected_answer="π")


def test_reveal_phrase_catches_letter_answer():
    assert feedback.leaks_answer("Tačna opcija je A.", correct_option_text="A")


def test_reveal_phrase_catches_option_letter_variant():
    assert feedback.leaks_answer("Izaberi opciju B.", correct_option_text="B")


def test_reveal_phrase_catches_variable_equals_short_value():
    assert feedback.leaks_answer("Dakle x=2.", expected_answer="2")


def test_reveal_phrase_catches_dobijes_form():
    assert feedback.leaks_answer("Kad podijeliš, dobiješ 5.", expected_answer="5")


def test_reveal_phrase_catches_zato_je_form():
    assert feedback.leaks_answer("Zato je 5 tačan odgovor.", expected_answer="5")


def test_reveal_phrase_ignores_unrelated_value():
    """Fraza postoji, ali izgovorena vrijednost NIJE tačan odgovor — hint i
    dalje ne otkriva ništa relevantno (npr. objašnjava zašto NEŠTO DRUGO nije
    tačno) i ne smije se lažno tretirati kao curenje ovog zadatka."""
    assert not feedback.leaks_answer("Odgovor je 7.", expected_answer="2")


def test_legitimate_hint_divide_both_sides_is_not_a_leak():
    assert not feedback.leaks_answer("Podijeli obje strane sa 2.", expected_answer="2")


def test_legitimate_hint_multiply_denominator_is_not_a_leak():
    assert not feedback.leaks_answer(
        "Pomnoži nazivnik 8 sa 3.", expected_answer="24", correct_option_text="24/8"
    )


def test_legitimate_hint_mentioning_factor_is_not_a_leak():
    assert not feedback.leaks_answer("Posmatraj faktor 5.", expected_answer="5")


def test_legitimate_hint_about_sign_after_division_is_not_a_leak():
    assert not feedback.leaks_answer(
        "Provjeri znak nakon dijeljenja sa -2.", expected_answer="-2"
    )


def test_short_wrong_answer_reveal_replaced_by_generic_in_full_flow():
    out = feedback.shape_first_wrong_feedback("Odgovor je 2.", "", expected_answer="2")
    assert "2." not in out.split("Hint: ")[1][:5]
    assert feedback.GENERIC_HINT in out


def test_x_equals_form_does_not_misfire_on_unrelated_equation_hint():
    """'x = 2' oblik ne smije se okinuti kad hint samo UPOREĐUJE dvije stvari
    bez ikakve veze s tačnim odgovorom ovog zadatka."""
    assert not feedback.leaks_answer("Provjeri da li je a = b prije zamjene.", expected_answer="7")


# ---------------------------------------------------------------------------
# D. Garantovana gornja granica prvog netačnog odgovora
# ---------------------------------------------------------------------------

def test_700_char_hint_without_any_period_stays_within_bound():
    hint = "riječ " * 120  # ~700 znakova, bez ijedne tačke/upitnika/uskličnika
    out = feedback.shape_first_wrong_feedback(hint, "")
    assert len(out) <= config.MAX_FIRST_WRONG_FEEDBACK_CHARS
    assert out.startswith("Netačno.")


def test_long_hint_with_mathjax_stays_within_bound_and_keeps_balanced_dollars():
    hint = ("Prvo izračunaj $\\frac{24}{8}$ pažljivo " * 15)
    out = feedback.shape_first_wrong_feedback(hint, "")
    assert len(out) <= config.MAX_FIRST_WRONG_FEEDBACK_CHARS
    assert out.count("$") % 2 == 0


def test_unbalanced_mathjax_hint_falls_back_to_generic_within_bound():
    """Hint s neparnim brojem '$' (teorijski ne bi trebao proći sanitizaciju
    prije ovog modula, ali provjeravamo odbrambeni sloj) ne smije procuriti
    slomljen MathJax — pada na generički hint."""
    hint = "Izračunaj $\\frac{1}{2 i nastavi dalje bez zatvaranja"
    out = feedback.shape_first_wrong_feedback(hint, "")
    assert len(out) <= config.MAX_FIRST_WRONG_FEEDBACK_CHARS
    assert out.count("$") % 2 == 0
    assert feedback.GENERIC_HINT in out


def test_safe_short_hint_is_used_verbatim_within_bound():
    out = feedback.shape_first_wrong_feedback("Podijeli brojnik i nazivnik sa 4.", "")
    assert "Podijeli brojnik i nazivnik sa 4." in out
    assert len(out) <= config.MAX_FIRST_WRONG_FEEDBACK_CHARS


def test_fallback_generic_hint_remains_within_hard_limit():
    huge_unclippable = "x" * 2000  # nema razmaka, nema kraja rečenice, nema $
    out = feedback.shape_first_wrong_feedback(huge_unclippable, "")
    assert len(out) <= config.MAX_FIRST_WRONG_FEEDBACK_CHARS
    assert feedback.GENERIC_HINT in out


def test_no_boundary_case_still_starts_exactly_with_netacno():
    text = "$" + "a" * 900 + "$"
    out = feedback.shape_first_wrong_feedback(text, "")
    assert out.startswith("Netačno.")
    assert len(out) <= config.MAX_FIRST_WRONG_FEEDBACK_CHARS


def test_first_wrong_never_exceeds_bound_across_many_shapes():
    """Nasumičan spektar loših unosa — nijedan ne smije probiti granicu."""
    samples = [
        "a" * 5000,
        ("riječ" * 50),
        "$" + ("x" * 400) + "$" + ("y" * 400),
        "Prva rečenica. " * 30 + "$\\sqrt{2}$",
        "",
        "   ",
        "$$$$$$$$$$",
    ]
    for sample in samples:
        out = feedback.shape_first_wrong_feedback(sample, "")
        assert len(out) <= config.MAX_FIRST_WRONG_FEEDBACK_CHARS, repr(sample)
        assert out.startswith("Netačno.")


# ---------------------------------------------------------------------------
# Integracija kroz Practice
# ---------------------------------------------------------------------------

def _payload(msg="Daj zadatak.", **kw):
    base = {
        "session_id": "sess-fb", "grade": 6, "selected_topic": "6-04-007",
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "", "selected_option_id": "", "client_turn_id": "",
    }
    base.update(kw)
    return base


def _start(store, fake):
    # Zadatak mora zadovoljiti ugovor prve dodijeljene porodice za ovu lekciju
    # (Razlomci → expand_to_given_denominator); interno rješenje je „5/8“ da
    # testovi curenja odgovora ostanu smisleni.
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Proširi razlomak $\\frac{5}{8}$ tako da nazivnik bude $32$.", expected="5/8",
        options=make_options("$\\frac{20}{32}$", "$\\frac{5}{32}$",
                              "$\\frac{10}{32}$", "$\\frac{15}{32}$"))))
    run_practice_turn(store, fake, _payload())
    return store.peek("sess-fb")


def _click(store, fake, option_id, turn_id):
    return run_practice_turn(store, fake, _payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=option_id, client_turn_id=turn_id))


def _wrong_id(sess):
    return next(o["id"] for o in sess["current_options"] if o["id"] != sess["correct_option_id"])


def test_practice_first_wrong_answer_starts_with_netacno():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="Duga analiza zašto je to pogrešno.",
                            hint="Podijeli brojnik i nazivnik istim brojem."))
    r = _click(store, fake, _wrong_id(sess), "t1")
    assert r["answer"].startswith("Netačno.")
    assert r["answer_verdict"] == "incorrect"


def test_practice_first_wrong_uses_model_hint_not_long_reply():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(
        reply="Izabrao si $\\frac{10}{16}$, ali to nije tačno zato što nije skraćeno do kraja. " * 5,
        hint="Podijeli brojnik i nazivnik sa 4."))
    r = _click(store, fake, _wrong_id(sess), "t1")
    assert "Podijeli brojnik i nazivnik sa 4." in r["answer"]
    assert "ali to nije tačno zato što" not in r["answer"]


def test_practice_first_wrong_is_short():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="Dugačak dokaz. " * 60, hint="Kratki hint. " * 60))
    r = _click(store, fake, _wrong_id(sess), "t1")
    assert len(r["answer"]) <= config.MAX_FIRST_WRONG_FEEDBACK_CHARS


def test_practice_first_wrong_never_reveals_correct_option():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    correct_text = next(o["text"] for o in sess["current_options"]
                        if o["id"] == sess["correct_option_id"])
    fake.queue(make_output(reply="x", hint=f"Tačan odgovor je {correct_text}."))
    r = _click(store, fake, _wrong_id(sess), "t1")
    assert "revealed_correct_option_id" not in r
    assert correct_text.strip("$") not in r["answer"]


def test_practice_first_wrong_never_reveals_expected_answer():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="x", hint="Rezultat je 5/8 naravno."))
    r = _click(store, fake, _wrong_id(sess), "t1")
    assert "5/8" not in r["answer"]


def test_practice_second_wrong_still_starts_with_netacno_and_reveals():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    wrong = _wrong_id(sess)
    fake.queue(make_output(reply="x", hint="Prvi hint."))
    _click(store, fake, wrong, "t1")

    sess2 = store.peek("sess-fb")
    second_wrong = next(o["id"] for o in sess2["current_options"]
                        if o["id"] not in (sess2["correct_option_id"], wrong))
    fake.queue(make_output(reply="Postupak: podijeli sa 4 i dobiješ rezultat."))
    r = _click(store, fake, second_wrong, "t2")

    assert r["answer"].startswith("Netačno.")
    assert r["revealed_correct_option_id"] == sess2["correct_option_id"]
    assert "Postupak: podijeli sa 4" in r["answer"]


def test_practice_correct_answer_never_gets_netacno():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="Tačno! Skratio si do kraja.", evaluation="correct"))
    r = _click(store, fake, sess["correct_option_id"], "t1")
    assert not r["answer"].startswith("Netačno.")
    assert r["answer_verdict"] == "correct"


def test_student_question_is_never_marked_netacno():
    """Tekstualna poruka nije pokušaj odgovora — ne smije dobiti ocjenu."""
    store, fake = SessionStore(), FakeLLM()
    _start(store, fake)
    fake.queue(make_output(reply="Brojnik je gornji broj razlomka."))
    r = run_practice_turn(store, fake, _payload(msg="Šta znači brojnik?"))
    assert not r["answer"].startswith("Netačno.")
    assert r["answer_verdict"] is None


def test_first_wrong_makes_exactly_one_llm_call():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    fake.queue(make_output(reply="x", hint="Hint."))
    _click(store, fake, _wrong_id(sess), "t1")
    assert fake.call_count == 2  # 1 bootstrap + 1 klik
