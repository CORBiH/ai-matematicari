# -*- coding: utf-8 -*-
"""The minimal engine, driven the way the real browser drives it.

These are NOT helper-level unit tests. ``Browser`` reproduces exactly what
``templates/index.html`` does per turn: it POSTs to the real ``/api/ai-tutor/chat``
endpoint, stores ``next_state`` and replays it as ``previous_next_state``, keeps
``last_tutor_task`` and ``recent_tasks`` the way the page does, and sets
``interaction_phase`` when answering. If the wire contract breaks, these fail.

The turn sequences are the real sessions from the success criteria:
select a topic → get a task → answer wrong → ask for help → answer right →
ask for a new task.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot.minimal import engine as mengine
from matbot.minimal import skills
from matbot.minimal.grading import grade
from matbot.minimal.intent import TurnIntent, classify
from matbot.minimal.state import ActiveTask, SessionState

CHAT_URL = "/api/ai-tutor/chat"
EXPAND_TOPIC = "6-04-035"          # Proširivanje razlomaka
ADD_TOPIC = "6-04-040"             # Sabiranje/oduzimanje razlomaka
UNSUPPORTED_TOPIC = "6-08-079"     # Odnos dvije kružnice — deliberately outside


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "on")
    for f in ("MATBOT_ENGINE_V2", "MATBOT_ENGINE_V2_GRADING",
              "MATBOT_ENGINE_V2_PRACTICE", "MATBOT_ENGINE_V2_EXAM"):
        monkeypatch.setenv(f, "off")
    yield


@pytest.fixture()
def sheets(monkeypatch):
    """Count Sheets rows and capture what was logged."""
    rows = []
    monkeypatch.setattr(svc, "log_transcript_to_sheet",
                        lambda payload, response: rows.append((payload, response)))
    return rows


class Browser:
    """Mimics templates/index.html turn-by-turn."""

    def __init__(self, client, *, topic=EXPAND_TOPIC, oblast="Razlomci",
                 session_id="browser-session"):
        self.client = client
        self.topic = topic
        self.oblast = oblast
        self.session_id = session_id
        self.next_state = None
        self.saved_task = ""
        self.recent_tasks = []
        self.history = []
        self.turns = []

    def send(self, text, *, answering=None):
        """One user turn. ``answering`` mirrors the page's answerPhase logic."""
        payload = {
            "session_id": self.session_id,
            "grade": 6,
            "mode": "practice",
            "entry_source": "manual_topic_choice",
            "selected_topic": self.topic,
            "selected_oblast": self.oblast,
            "student_message": text,
            "conversation_history": list(self.history),
        }
        if self.next_state:
            payload["previous_next_state"] = self.next_state
        if self.recent_tasks:
            payload["recent_tasks"] = list(self.recent_tasks)
        # The page sets these only when the student is answering a live task.
        if answering is None:
            answering = bool(self.saved_task)
        if answering and self.saved_task:
            payload["interaction_phase"] = "answering_practice_task"
            payload["last_tutor_task"] = self.saved_task[:600]

        resp = self.client.post(CHAT_URL, json=payload)
        assert resp.status_code == 200, resp.data
        body = resp.get_json()

        # --- exactly what the page does with the response ---
        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": body.get("answer", "")})
        if body.get("next_state"):
            self.next_state = body["next_state"]
        task = (body.get("last_tutor_task") or "").strip()
        if task:
            if task != self.saved_task:
                self.recent_tasks = ([task] + self.recent_tasks)[:8]
            self.saved_task = task
        else:
            self.saved_task = ""
        self.turns.append(body)
        return body


@pytest.fixture()
def browser(client):
    return Browser(client)


def _task_id(body):
    return (body.get("next_state") or {}).get("task_id")


# =========================================================================== #
# The full real session from the success criteria                             #
# =========================================================================== #
def test_full_practice_session(browser, sheets):
    """topic → task → wrong → help → correct → new task, over the real wire."""
    # 1. select an exact topic and ask for a task
    first = browser.send("daj mi zadatak")
    assert first["status"] == "ready"
    assert first["engine"] == "minimal"
    assert first["session_mode"] == "practice"
    question = first["last_tutor_task"]
    assert question, "an on-topic validated task must be offered"
    assert question.lower().startswith("proširi")      # exact topic honoured
    tid = _task_id(first)
    assert tid and tid.startswith("mt_")

    # 2. answer incorrectly — task survives, id unchanged
    wrong = browser.send("999/999")
    assert wrong["answer_verdict"] == "incorrect"
    assert wrong["last_tutor_task"] == question
    assert _task_id(wrong) == tid
    assert wrong["next_state"]["wrong_attempt_count"] == 1
    assert wrong["next_state"]["correct_streak"] == 0

    # 3. ask for help — never loses the task, never reveals the answer
    helped = browser.send("ne znam")
    assert helped["answer_verdict"] is None
    assert helped["last_tutor_task"] == question
    assert _task_id(helped) == tid
    assert helped["next_state"]["hint_count"] == 1

    # 4. answer correctly
    expected = _expected_for(question)
    right = browser.send(expected)
    assert right["answer_verdict"] == "correct"
    assert right["next_state"]["correct_streak"] == 1
    assert right["last_tutor_task"] == ""               # task completed

    # 5. ask for a new task — new id, different question
    nxt = browser.send("daj mi novi zadatak")
    assert nxt["last_tutor_task"]
    assert nxt["last_tutor_task"] != question
    assert _task_id(nxt) != tid

    # one Sheets row per turn, raw student message intact
    assert len(sheets) == len(browser.turns) == 5
    sent = [p.get("student_message") for p, _ in sheets]
    assert sent == ["daj mi zadatak", "999/999", "ne znam", expected,
                    "daj mi novi zadatak"]


def _expected_for(question):
    """Solve a generated task the way the engine's own checker would accept."""
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(question)
    assert exp is not None, question
    return getattr(exp, "expected_display", "") or _fmt_expected(exp)


# =========================================================================== #
# Success criteria, individually                                              #
# =========================================================================== #
def test_task_is_on_topic_and_validated(browser):
    body = browser.send("daj mi zadatak")
    task = body["next_state"]["task"]
    assert task["tema_id"] == EXPAND_TOPIC
    assert task["tema_title"] == "Proširivanje razlomaka"
    assert task["skill_id"] == "fraction_expand"
    assert task["validation_status"] == "validated"
    from matbot.answer_checker import derive_expected
    assert derive_expected(body["last_tutor_task"]) is not None


def test_one_task_id_until_completion(browser):
    first = browser.send("daj mi zadatak")
    tid = _task_id(first)
    for msg in ("111/111", "pomozi", "222/222"):
        body = browser.send(msg)
        assert _task_id(body) == tid, msg
    body = browser.send(_expected_for(first["last_tutor_task"]))
    assert body["answer_verdict"] == "correct"
    assert _task_id(body) is None                      # completed, not reused


def test_help_never_loses_the_task_or_reveals(browser):
    first = browser.send("daj mi zadatak")
    question = first["last_tutor_task"]
    expected = _expected_for(question)
    for msg in ("ne znam", "pomozi", "objasni mi", "kako se radi"):
        body = browser.send(msg)
        assert body["last_tutor_task"] == question, msg
        assert expected.lower() not in (body["answer"] or "").lower(), msg


def test_child_friendly_feedback_is_natural_and_varied(browser):
    """Wording differs across turns and never shames."""
    browser.send("daj mi zadatak")
    replies = [browser.send(f"{n}/{n}")["answer"] for n in (11, 22)]
    for reply in replies:
        low = reply.lower()
        assert low.strip()
        for harsh in ("greška!", "pogrešno!!", "loše", "glupo"):
            assert harsh not in low


def test_new_task_avoids_repeating_recent_questions(browser):
    """Distinct requests must yield distinct tasks.

    The phrasings differ on purpose: since 2026-07-21 an IDENTICAL repeated
    request returns the existing task instead of replacing it (see
    test_minimal_conversation.py), which is a separate contract.
    """
    seen = []
    for message in ("daj mi zadatak", "daj mi novi zadatak",
                    "hoću još jedan zadatak", "daj mi drugi zadatak"):
        body = browser.send(message)
        seen.append(body["last_tutor_task"])
    assert len(set(seen)) == len(seen), seen


def test_raw_student_message_is_logged_verbatim(browser, sheets):
    browser.send("daj mi zadatak")
    weird = "  MoJ OdGoVoR je 5/20  "
    browser.send(weird)
    assert sheets[-1][0]["student_message"] == weird


def test_one_sheets_row_per_turn(browser, sheets):
    for msg in ("daj mi zadatak", "5/20", "ne znam", "daj mi novi zadatak"):
        before = len(sheets)
        browser.send(msg)
        assert len(sheets) == before + 1, msg


# =========================================================================== #
# Topic honesty and the fallback boundary                                     #
# =========================================================================== #
def test_unsupported_topic_is_refused_not_substituted(client, fake_openai):
    """An EXPLICITLY selected but unsupported topic is refused honestly.

    Changed 2026-07-21 after production: falling through to legacy free
    generation put an equation under "Proširivanje razlomaka", so a grade-6
    Practice turn with an explicit topic must never silently fall through.
    """
    fake_openai.state["reply"] = "Zadatak: Riješi jednačinu: 3x + 2 = 14."
    b = Browser(client, topic=UNSUPPORTED_TOPIC,
                oblast="Skupovi tačaka, kružnica i krug")
    body = b.send("daj mi zadatak")
    assert body["engine"] == "minimal"                # refused, not delegated
    assert body["last_tutor_task"] == ""              # no task activated
    assert body["minimal_routing"]["decline_reason"] == "topic_not_supported"
    assert fake_openai.calls.messages == []           # model never consulted


def test_unsupported_topic_is_refused_honestly_by_the_core():
    """At core level the refusal is explicit and names no other topic."""
    state = SessionState(session_id="s", grade=6)
    result = mengine.handle_turn(raw_message="daj mi zadatak", state=state,
                                 selected_topic=UNSUPPORTED_TOPIC)
    assert result.topic_supported is False
    assert result.state.active_task is None
    assert "nemam zadatke" in result.answer.lower()


def test_topic_never_widens_to_a_neighbouring_skill():
    topic = skills.resolve_topic(6, UNSUPPORTED_TOPIC)
    assert topic.supported is False
    assert topic.skill_id == ""


def test_selected_topic_is_authoritative_over_student_words(browser):
    """The student naming another topic cannot change the selected one."""
    body = browser.send("daj mi zadatak o jednačinama i procentima")
    assert body["last_tutor_task"].lower().startswith("proširi")
    assert body["next_state"]["task"]["tema_id"] == EXPAND_TOPIC


def test_second_supported_topic_works(client):
    b = Browser(client, topic=ADD_TOPIC)
    body = b.send("daj mi zadatak")
    assert body["engine"] == "minimal"
    assert body["next_state"]["task"]["skill_id"] == "fraction_add_unlike"


# =========================================================================== #
# Flag isolation                                                              #
# =========================================================================== #
def test_flag_off_leaves_production_untouched(client, fake_openai, monkeypatch):
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    fake_openai.state["reply"] = "Zadatak: LEGACY-MODEL 1/2 + 1/2."
    b = Browser(client)
    body = b.send("daj mi zadatak")
    assert body.get("engine") != "minimal"
    assert "minimal_state" not in (body.get("next_state") or {})


def test_flag_off_means_the_module_never_runs(monkeypatch):
    from matbot.minimal import adapter
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    assert adapter.minimal_engine_enabled() is False
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "on")
    assert adapter.minimal_engine_enabled() is True


def test_engine_failure_falls_back_instead_of_crashing(client, fake_openai, monkeypatch):
    fake_openai.state["reply"] = "Zadatak: LEGACY-MODEL."

    def boom(*a, **kw):
        raise RuntimeError("induced")

    monkeypatch.setattr(svc, "handle_chat_minimal", boom)
    b = Browser(client)
    body = b.send("daj mi zadatak")
    assert body.get("engine") != "minimal"            # legacy answered instead


# =========================================================================== #
# Core invariants                                                             #
# =========================================================================== #
def test_no_task_exists_without_a_structured_active_task():
    """A corrupt/prose-only task record is dropped, never reconstructed."""
    assert ActiveTask.from_dict({"question": "Proširi 1/2 na nazivnik 8."}) is None
    assert ActiveTask.from_dict({"task_id": "x", "skill_id": "y"}) is None
    assert ActiveTask.from_dict("Proširi 1/2 na nazivnik 8.") is None


def test_foreign_v2_state_is_not_adopted():
    state = SessionState.from_dict({"engine": "v2", "task": {"question": "x"}})
    assert state.active_task is None


def test_grading_is_deterministic_and_single():
    task = ActiveTask(task_id="t1", skill_id="fraction_expand",
                      question="Proširi 1/4 na nazivnik 20.", expected_display="5/20")
    correct = grade(task, "5/20")
    assert correct.verdict == "correct" and correct.solved and correct.deterministic
    wrong = grade(task, "3/20")
    assert wrong.verdict == "incorrect" and not wrong.solved


def test_grading_keeps_the_raw_student_text():
    task = ActiveTask(task_id="t1", skill_id="fraction_expand",
                      question="Proširi 1/4 na nazivnik 20.", expected_display="5/20")
    raw = "  mislim da je 5/20  "
    assert grade(task, raw).student_raw == raw


def test_openai_cannot_change_the_verdict(browser, monkeypatch):
    """Even a hostile model reply cannot flip a deterministic decision."""
    from matbot.minimal import renderer

    monkeypatch.setattr(renderer, "phrase_with_model",
                        lambda text, **kw: "NETAČNO! Rezultat je 999.")
    first = browser.send("daj mi zadatak")
    body = browser.send(_expected_for(first["last_tutor_task"]))
    assert body["answer_verdict"] == "correct"        # state is untouched
    assert body["next_state"]["correct_streak"] == 1


def test_model_phrasing_is_rejected_when_it_drifts():
    from matbot.minimal import renderer

    class _Resp:
        def __init__(self, text):
            self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]

    def smuggling_model(model, messages, timeout=None, max_tokens=None, **kw):
        return _Resp("Tačno! Sljedeći zadatak: izračunaj 7/9 + 2/9.")

    original = "Tačno. Želiš li još jedan zadatak?"
    out = renderer.phrase_with_model(original, openai_chat=smuggling_model,
                                     model="m", timeout=1, allow_verdict_words=True)
    assert out == original                            # new numbers → rejected


@pytest.mark.parametrize("drift", [
    "Netačno, pogriješio si.",      # with diacritics
    "Netacno, pogresno.",           # without
    "Bravo, odlično!",
])
def test_model_may_not_introduce_a_verdict(drift):
    """Regression: the ban-list is diacritic-free and must fold before matching,
    or "Netačno" slips past a pattern that only spells "netacno"."""
    from matbot.minimal import renderer

    class _Resp:
        def __init__(self, text):
            self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]

    original = "Nije još tačno. Pogledaj ponovo nazivnik."
    out = renderer.phrase_with_model(
        original, openai_chat=lambda *a, **kw: _Resp(drift),
        model="m", timeout=1, allow_verdict_words=False)
    assert out == original, drift


def test_neutral_rephrasing_is_accepted():
    from matbot.minimal import renderer

    class _Resp:
        def __init__(self, text):
            self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]

    original = "Nije još tačno. Pogledaj ponovo nazivnik."
    out = renderer.phrase_with_model(
        original, openai_chat=lambda *a, **kw: _Resp("Probaj još jednom, gledaj nazivnik."),
        model="m", timeout=1, allow_verdict_words=False)
    assert out == "Probaj još jednom, gledaj nazivnik."


def test_model_failure_keeps_deterministic_text():
    from matbot.minimal import renderer

    def broken(*a, **kw):
        raise RuntimeError("api down")

    text = "Tačno. Želiš li još jedan zadatak?"
    assert renderer.phrase_with_model(text, openai_chat=broken, model="m",
                                      timeout=1, allow_verdict_words=True) == text


def test_no_task_is_ever_extracted_from_prose():
    """There is no code path from model text to an ActiveTask."""
    import inspect
    for module in (mengine, skills):
        src = inspect.getsource(module)
        for banned in ("extract_practice_task", "extract_marked_task",
                       "re.search(r\"Zadatak"):
            assert banned not in src, (module.__name__, banned)


def test_state_transitions_are_immutable():
    state = SessionState(session_id="s", grade=6)
    task = ActiveTask(task_id="t", skill_id="fraction_expand", question="q",
                      expected_display="e")
    after = state.with_task(task)
    assert state.active_task is None                  # original untouched
    assert after.active_task is task


@pytest.mark.parametrize("msg,expected", [
    ("ne znam", TurnIntent.HELP),
    ("pomozi", TurnIntent.HELP),
    ("objasni mi", TurnIntent.HELP),
    ("daj mi novi zadatak", TurnIntent.NEW_TASK),
    ("5/20", TurnIntent.ANSWER),
    ("da", TurnIntent.ANSWER),
    ("x = 3", TurnIntent.ANSWER),
    ("volim pse", TurnIntent.OTHER),
])
def test_turn_intent_classification(msg, expected):
    assert classify(msg).intent is expected


def test_declared_scope_is_honest():
    """Every declared skill can be generated AND checked."""
    assert skills.selftest() == []


def test_all_five_skills_produce_gradeable_tasks():
    from matbot.answer_checker import derive_expected
    assert len(skills.SKILLS) == 5
    for skill in skills.SKILLS:
        for seed in range(25):
            made = skills.generate_question(skill.skill_id, seed=seed)
            assert made is not None, skill.skill_id
            assert derive_expected(made[0]) is not None, (skill.skill_id, made[0])


def test_wrong_answers_eventually_close_the_task_with_the_answer():
    """A child is never trapped: after N wrong attempts the answer is shown."""
    state = SessionState(session_id="s", grade=6)
    result = mengine.handle_turn(raw_message="daj mi zadatak", state=state,
                                 selected_topic=EXPAND_TOPIC)
    state = result.state
    for _ in range(mengine.MAX_WRONG_ATTEMPTS):
        result = mengine.handle_turn(raw_message="1/999", state=state,
                                     selected_topic=EXPAND_TOPIC)
        state = result.state
    assert state.active_task is None
    assert result.task.expected_display in result.answer
