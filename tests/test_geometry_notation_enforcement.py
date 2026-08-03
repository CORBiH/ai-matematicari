# -*- coding: utf-8 -*-
"""Integracija geometrijske notacije u Practice/Explain/Quick put (fake LLM).

Pokriva ROLE-AWARE politiku iz Faze 3: autoritativan sadržaj (pitanje, tačna
opcija, expected_answer, feedback, Explain/Quick odgovor) mora poštovati
konvenciju, dok NAMJERNO pogrešni distraktori i porodice za detekciju greške
smiju prikazati pogrešnu oznaku.
"""
from tests.conftest import FakeLLM, make_options, make_output, make_task
from matbot import geometrycheck as gc
from matbot.explain import run_explain_turn
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.quick import run_quick_turn
from matbot.session_store import SessionStore

# 6-08-006 „Centar, poluprečnik/polumjer i prečnik/promjer“ → plane / krug
CIRCLE_TOPIC = "6-08-006"
# 7-05-021 „Površina trougla“ → plane / trougao
TRIANGLE_TOPIC = "7-05-021"

GOOD_CIRCLE_OPTIONS = ("$31,4\\,\\text{cm}$", "$15,7\\,\\text{cm}$",
                       "$62,8\\,\\text{cm}$", "$314\\,\\text{cm}$")


def turn(msg="Daj mi zadatak.", topic=CIRCLE_TOPIC, grade=6, **kw):
    base = {
        "session_id": "geo-sess", "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
    }
    base.update(kw)
    return base


def explain_turn(msg, topic=CIRCLE_TOPIC, grade=6):
    return {"grade": grade, "selected_topic": topic, "selected_oblast": "",
            "student_message": msg, "interaction_phase": "",
            "last_tutor_message": "", "conversation_history": []}


def seed_completed(store, families, topic_title, oblast, grade=6, sid="geo-sess"):
    """Označi porodice kao savladane da server izabere ŽELJENU sljedeću porodicu.
    Ovo je test-instrumentacija stanja, ne zaobilaženje rutiranja."""
    s = store.load(session_id=sid, grade=grade, lesson_id=CIRCLE_TOPIC,
                   lesson_title=topic_title, oblast=oblast, mode="practice")
    s["correctly_completed_families"] = list(families)
    store.save(s)


# ---------------------------------------------------------------------------
# 22, 27: obično pitanje i expected_answer moraju poštovati konvenciju
# ---------------------------------------------------------------------------

def test_22_ordinary_practice_question_using_D_as_diameter_is_rejected():
    """Tačna regresija poziva 41 kroz PUNI Practice put."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Krug ima prečnik $D=10\\,\\text{cm}$. Izračunaj obim kruga.",
        expected="$O=\\pi R=31,4\\,\\text{cm}$",
        options=make_options(*GOOD_CIRCLE_OPTIONS), correct_option_index=0,
        task_family="direct_formula_application", answer_kind="decimal")))
    r = run_practice_turn(store, fake, turn())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r
    assert fake.practice_call_count == 1                      # bez repair poziva
    assert store.peek("geo-sess") is None            # nula mutacije stanja


def test_22b_canonical_R_question_is_accepted():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Krug ima prečnik $R=10\\,\\text{cm}$. Izračunaj obim kruga.",
        expected="$O=\\pi R=3,14\\cdot10=31,4\\,\\text{cm}$",
        options=make_options(*GOOD_CIRCLE_OPTIONS), correct_option_index=0,
        task_family="direct_formula_application", answer_kind="decimal")))
    r = run_practice_turn(store, fake, turn())
    assert r["status"] == "ready"
    assert "prečnik $R=10" in r["last_tutor_task"]
    assert fake.practice_call_count == 1


def test_27_expected_answer_using_D_as_diameter_is_rejected():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Krug ima prečnik $R=10\\,\\text{cm}$. Izračunaj obim kruga.",
        expected="$O=\\pi D=3,14\\cdot10=31,4\\,\\text{cm}$",   # interno, ali autoritativno
        options=make_options(*GOOD_CIRCLE_OPTIONS), correct_option_index=0,
        task_family="direct_formula_application", answer_kind="decimal")))
    r = run_practice_turn(store, fake, turn())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("geo-sess") is None


# ---------------------------------------------------------------------------
# 25, 26: distraktori smiju biti pogrešni, tačna opcija ne
# ---------------------------------------------------------------------------

def test_25_incorrect_distractor_with_S_as_area_does_not_reject_task():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Stranica $a=6\\,\\text{cm}$, visina $h_a=4\\,\\text{cm}$. Kolika je površina trougla?",
        expected="$P=12\\,\\text{cm}^2$",
        options=make_options("$P=12\\,\\text{cm}^2$", "$S=24\\,\\text{cm}^2$",
                             "$P=10\\,\\text{cm}^2$", "$P=48\\,\\text{cm}^2$"),
        correct_option_index=0, task_family="direct_formula_application",
        answer_kind="short_text")))
    r = run_practice_turn(store, fake, turn(topic=TRIANGLE_TOPIC, grade=7))
    assert r["status"] == "ready", r["answer"]
    texts = [o["text"] for o in r["next_state"]["task"]["options"]]
    assert any("S=24" in t for t in texts)   # namjerno pogrešan distraktor je prošao
    assert fake.practice_call_count == 1


def test_26_marked_correct_option_using_S_as_area_is_rejected():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Stranica $a=6\\,\\text{cm}$, visina $h_a=4\\,\\text{cm}$. Kolika je površina trougla?",
        expected="$P=12\\,\\text{cm}^2$",
        options=make_options("$S=12\\,\\text{cm}^2$", "$P=24\\,\\text{cm}^2$",
                             "$P=10\\,\\text{cm}^2$", "$P=48\\,\\text{cm}^2$"),
        correct_option_index=0, task_family="direct_formula_application",
        answer_kind="short_text")))
    r = run_practice_turn(store, fake, turn(topic=TRIANGLE_TOPIC, grade=7))
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("geo-sess") is None


# ---------------------------------------------------------------------------
# 23, 24: porodice za detekciju greške smiju prikazati pogrešnu oznaku
# ---------------------------------------------------------------------------

ERROR_FAMILY_PREFIX = ["direct_formula_application", "choose_correct_formula",
                       "find_missing_dimension", "inverse_formula_problem"]


def test_23_detect_formula_error_question_may_show_D_as_wrong_diameter():
    store, fake = SessionStore(), FakeLLM()
    seed_completed(store, ERROR_FAMILY_PREFIX,
                   "Centar, poluprečnik/polumjer i prečnik/promjer",
                   "Skupovi tačaka, kružnica i krug")
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Učenik je obim kruga računao kao $O=\\pi D$, gdje je $D$ prečnik. "
             "Šta je pogriješio u oznaci?",
        expected="Prečnik se označava sa $R$, a ne sa $D$.",
        options=make_options(
            "Prečnik se u ovoj konvenciji označava sa $R$, ne sa $D$.",
            "Obim se računa kao $O=\\pi r$, pa je formula pogrešna.",
            "Umjesto $\\pi$ trebao je koristiti broj $3$.",
            "Obim kruga se uopšte ne računa preko prečnika."),
        correct_option_index=0, task_family="detect_formula_error",
        answer_kind="short_text")))
    r = run_practice_turn(store, fake, turn(msg="Daj mi novi zadatak."))
    assert r["status"] == "ready", r["answer"]
    assert "$O=\\pi D$" in r["last_tutor_task"]
    assert store.peek("geo-sess")["current_family"] == "detect_formula_error"
    assert fake.practice_call_count == 1


def test_24_detect_formula_error_correct_option_must_use_canonical_R():
    """Pitanje smije prikazati $D$, ali TAČNA opcija ne smije ga POTVRDITI."""
    store, fake = SessionStore(), FakeLLM()
    seed_completed(store, ERROR_FAMILY_PREFIX,
                   "Centar, poluprečnik/polumjer i prečnik/promjer",
                   "Skupovi tačaka, kružnica i krug")
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Učenik je obim kruga računao kao $O=2\\pi r$. Šta je pogriješio?",
        expected="Ništa, formula je tačna.",
        options=make_options(
            "Prečnik je označen sa $D$, pa je $O=\\pi D$ ispravno.",   # tačna opcija KRŠI konvenciju
            "Formula je ispravna.",
            "Trebao je koristiti $O=\\pi r$.",
            "Trebao je koristiti $O=r^2$."),
        correct_option_index=0, task_family="detect_formula_error",
        answer_kind="short_text")))
    r = run_practice_turn(store, fake, turn(msg="Daj mi novi zadatak."))
    assert r["answer"] == SAFE_ERROR_MESSAGE
    sess = store.peek("geo-sess")
    assert sess["current_task"] == ""          # zadatak nije primijenjen
    assert fake.practice_call_count == 1


# ---------------------------------------------------------------------------
# 28, 29: hint i reveal
# ---------------------------------------------------------------------------

def _start_valid_circle_task(store, fake):
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Krug ima prečnik $R=10\\,\\text{cm}$. Izračunaj obim kruga.",
        expected="$31,4\\,\\text{cm}$",
        options=make_options(*GOOD_CIRCLE_OPTIONS), correct_option_index=0,
        task_family="direct_formula_application", answer_kind="decimal")))
    r = run_practice_turn(store, fake, turn())
    assert r["status"] == "ready"
    return store.peek("geo-sess")


def test_28_first_wrong_hint_with_bad_notation_is_replaced_safely():
    from matbot.feedback import GENERIC_HINT

    store, fake = SessionStore(), FakeLLM()
    sess = _start_valid_circle_task(store, fake)
    wrong = next(o["id"] for o in sess["current_options"] if o["id"] != sess["correct_option_id"])

    fake.queue(make_output(reply="", evaluation="incorrect", gave_hint=True,
                           hint="Sjeti se da je prečnik $D$, pa koristi $O=\\pi D$."))
    r = run_practice_turn(store, fake, turn(
        msg="Izabrana opcija.", interaction_type="choice_answer", selected_option_id=wrong))

    assert r["status"] == "ready"                     # turn NIJE odbijen
    assert r["answer"].startswith("Netačno.")
    assert GENERIC_HINT in r["answer"]                # loš hint zamijenjen sigurnim
    assert "$D$" not in r["answer"] and "\\pi D" not in r["answer"]
    assert fake.practice_call_count == 2                       # tačno jedan poziv po turnu
    assert store.peek("geo-sess")["retry_required"] is True


def test_29_reveal_with_bad_notation_is_rejected():
    store, fake = SessionStore(), FakeLLM()
    sess = _start_valid_circle_task(store, fake)
    wrongs = [o["id"] for o in sess["current_options"] if o["id"] != sess["correct_option_id"]]

    fake.queue(make_output(reply="", evaluation="incorrect", gave_hint=True,
                           hint="Kojom formulom se računa obim kruga?"))
    run_practice_turn(store, fake, turn(
        msg="Izabrana opcija.", interaction_type="choice_answer",
        selected_option_id=wrongs[0], client_turn_id="t1"))
    before = store.peek("geo-sess")

    # drugi pogrešan klik → otkrivanje rješenja, ali s pogrešnom oznakom
    fake.queue(make_output(reply="Prečnik je $D=10$, pa je $O=\\pi D=31,4$ cm.",
                           evaluation="incorrect"))
    r = run_practice_turn(store, fake, turn(
        msg="Izabrana opcija.", interaction_type="choice_answer",
        selected_option_id=wrongs[1], client_turn_id="t2"))

    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "revealed_correct_option_id" not in r
    after = store.peek("geo-sess")
    assert after["wrong_option_ids"] == before["wrong_option_ids"]   # 30: bez mutacije
    assert after["task_completed"] == before["task_completed"]
    assert fake.practice_call_count == 3                                     # 31: 1 poziv po turnu


def test_30_31_rejected_output_never_mutates_state_and_one_call_per_turn():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Krug ima prečnik $D=8\\,\\text{cm}$. Izračunaj obim.",
        expected="$25,12\\,\\text{cm}$",
        options=make_options(*GOOD_CIRCLE_OPTIONS), correct_option_index=0,
        task_family="direct_formula_application", answer_kind="decimal")))
    r = run_practice_turn(store, fake, turn())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("geo-sess") is None
    assert fake.practice_call_count == 1


# ---------------------------------------------------------------------------
# 32-35: Explain i Quick
# ---------------------------------------------------------------------------

def test_32_explain_using_D_as_circle_diameter_is_blocked():
    from tests.conftest import make_explain_output

    fake = FakeLLM()
    fake.queue(make_explain_output(
        "Prečnik kruga označavamo sa $D$. Obim je tada $O=\\pi D$."))
    r = run_explain_turn(fake, explain_turn("Objasni prečnik i obim kruga."))
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r
    assert fake.call_count == 1
    for code in gc.ALL_ISSUE_CODES:
        assert code not in r["answer"]          # 35: kodovi ne cure


def test_33_quick_using_S_as_triangle_area_is_blocked():
    from tests.conftest import make_quick_output

    fake = FakeLLM()
    fake.queue(make_quick_output("$S=12\\,\\text{cm}^2$"))
    r = run_quick_turn(fake, explain_turn("Površina trougla?", topic=TRIANGLE_TOPIC, grade=7))
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1
    for code in gc.ALL_ISSUE_CODES:
        assert code not in r["answer"]


def test_34_correct_explain_and_quick_geometry_passes_unchanged():
    from tests.conftest import make_explain_output, make_quick_output

    good_explain = ("Poluprečnik je $r$, a prečnik $R=2r$. "
                    "Obim kruga je $O=2\\pi r=\\pi R$, a površina $P=\\pi r^2$.")
    fake = FakeLLM()
    fake.queue(make_explain_output(good_explain))
    r = run_explain_turn(fake, explain_turn("Objasni prečnik i obim kruga."))
    assert r["status"] == "ready"
    assert r["answer"] == good_explain           # bajt-identično

    good_quick = "$P=12\\,\\text{cm}^2$"
    fake2 = FakeLLM()
    fake2.queue(make_quick_output(good_quick))
    r2 = run_quick_turn(fake2, explain_turn("Površina trougla?", topic=TRIANGLE_TOPIC, grade=7))
    assert r2["status"] == "ready"
    assert r2["answer"] == good_quick


def test_35_safe_error_message_contains_no_internal_issue_code():
    assert "geometry" not in SAFE_ERROR_MESSAGE.lower()
    for code in gc.ALL_ISSUE_CODES:
        assert code not in SAFE_ERROR_MESSAGE


def test_non_geometry_topic_is_never_geometry_checked():
    """Algebarska lekcija ne smije dobiti geometrijske provjere — $S$ i $D$ su
    tamo legitimne oznake."""
    from tests.conftest import make_quick_output

    fake = FakeLLM()
    fake.queue(make_quick_output("Skup $S=\\{1,2,3\\}$ ima 3 elementa."))
    r = run_quick_turn(fake, explain_turn("Koliko elemenata?", topic="6-01-006", grade=6))
    assert r["status"] == "ready"
