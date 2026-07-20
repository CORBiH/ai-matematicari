# -*- coding: utf-8 -*-
"""Three production defects.

BUG 1 — UI showed Objašnjenje while the backend ran Quick and answered "1".
BUG 2 — exam help/non-answers were stored as answers and advanced the exam.
BUG 3 — a RUNTIME topic id (12880) was unresolvable, so a fractions tema produced
        an equation via free model generation.
Plus: template quality (no trivial "NZD(32, 32)").
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import exam_engine as ee
from matbot import task_templates as tt
from matbot import topic_resolver as tr
from matbot import content_loader as cl
from matbot.answer_checker import derive_expected

EXPAND_TEMA = "Proširivanje razlomaka"
RUNTIME_ID = "12880"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "a.sqlite3"))
    for f in ("MATBOT_ENGINE_V2", "MATBOT_ENGINE_V2_GRADING",
              "MATBOT_ENGINE_V2_PRACTICE", "MATBOT_ENGINE_V2_EXAM"):
        monkeypatch.setenv(f, "off")
    tr.reset_cache()
    yield
    tr.reset_cache()


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _model(reply="U redu."):
    calls = {"n": 0}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=reply))])
    chat.calls = calls
    return chat


# =========================================================================== #
# BUG 1 — mode synchronization + boolean rendering                            #
# =========================================================================== #
PROD_MSG = "Provjeri da li je broj 30 djeljiv sa 5 i sa 3. Obrazloži."


def test_1_explanation_request_never_routes_to_quick(master, tmap):
    out = svc.handle_chat({"grade": 6, "mode": "quick", "session_id": "b1",
                           "student_message": PROD_MSG},
                          _model("Objašnjenje."), master, tmap, model="m", timeout=1)
    assert out["mode"] == "explain"
    assert out["session_mode"] == "explain"


def test_2_boolean_true_renders_da_not_1():
    exp = derive_expected("Provjeri da li je broj 30 djeljiv sa 5 i sa 3. Obrazloži.")
    rendered = svc._fmt_result_value(exp)
    assert rendered.startswith("Da")
    assert rendered.strip() not in ("1", "1.0")


def test_3_boolean_false_renders_ne_not_0():
    exp = derive_expected("Provjeri da li je broj 7 djeljiv sa 2. Obrazloži.")
    rendered = svc._fmt_result_value(exp)
    assert rendered.startswith("Ne")
    assert rendered.strip() not in ("0", "0.0")


def test_4_obrazlozi_cannot_be_result_only():
    assert svc.is_result_mode({"mode": "quick", "student_message": PROD_MSG}) is False
    assert svc.is_result_mode({"mode": "quick",
                               "student_message": "objasni mi postupak"}) is False
    # a genuine result request stays Quick
    assert svc.is_result_mode({"mode": "quick", "student_message": "12 - 23x = 4x"}) is True


def test_5_visible_mode_and_logged_session_mode_agree(master, tmap):
    for ui_mode in ("explain", "practice", "exam", "quick"):
        out = svc.handle_chat({"grade": 6, "mode": ui_mode, "session_id": "s",
                               "student_message": "12 - 23x = 4x"},
                              _model("Odgovor."), master, tmap, model="m", timeout=1)
        assert out["session_mode"] == ui_mode, ui_mode


def test_6_frontend_default_mode_is_not_quick():
    """A session that never explicitly chose a mode must not send Quick."""
    html = open("templates/index.html", encoding="utf-8").read()
    assert "grade: '6', mode: 'explain'" in html
    assert "grade: '6', mode: 'quick'" not in html


def test_7_genuine_quick_still_works(master, tmap):
    out = svc.handle_chat({"grade": 6, "mode": "quick", "session_id": "q",
                           "student_message": "Koliko je 20% od 50?"},
                          _model("10"), master, tmap, model="m", timeout=1)
    assert out["mode"] == "quick" and out["session_mode"] == "quick"


# =========================================================================== #
# BUG 2 — exam intent / skip / help                                           #
# =========================================================================== #
def _exam(master, tmap, msg, prev, session="e"):
    payload = {"grade": 6, "mode": "exam", "session_id": session,
               "selected_oblast": "Djeljivost brojeva", "student_message": msg}
    if prev:
        payload["previous_next_state"] = prev
    return svc.handle_chat(payload, _model(), master, tmap, model="m", timeout=1)


@pytest.fixture()
def started(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    return _exam(master, tmap, "kontrolni", None)["next_state"]


@pytest.mark.parametrize("msg", ["objasni ti", "pomozi", "ne znam", "daj pravilo",
                                 "reci mi odgovor"])
def test_help_intents_never_advance_or_store(monkeypatch, master, tmap, started, msg):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = _exam(master, tmap, msg, started)
    es = out["exam_state"]
    assert es["current_item_index"] == 0                  # 1, 2 — no advance
    assert es["items"][0]["status"] == "unanswered"
    assert es["items"][0]["student_answer"] is None       # never stored as answer
    assert out["answer_verdict_detail"] == "exam_help"


def test_3_repeated_ne_znam_does_not_advance(monkeypatch, master, tmap, started):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns, replies = started, []
    for _ in range(3):
        out = _exam(master, tmap, "ne znam", ns)
        ns = out["next_state"]
        replies.append(out["answer"])
        assert out["exam_state"]["current_item_index"] == 0
    assert all(r for r in replies)


def test_4_repeated_help_is_not_identical(monkeypatch, master, tmap, started):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns, replies = started, []
    for _ in range(3):
        out = _exam(master, tmap, "ne znam", ns)
        ns = out["next_state"]
        replies.append(out["answer"])
    assert len(set(replies)) > 1                          # progressive support


def test_5_explicit_skip_advances_as_skipped(monkeypatch, master, tmap, started):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = _exam(master, tmap, "preskoči", started)
    es = out["exam_state"]
    assert es["current_item_index"] == 1
    assert es["items"][0]["status"] == "skipped"
    assert es["items"][0]["verdict"] == "skipped"
    assert es["items"][0]["student_answer"] is None


def test_6_skipped_is_distinct_from_incorrect(monkeypatch, master, tmap, started):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    skipped = _exam(master, tmap, "preskoči", started)["exam_state"]["items"][0]
    wrong = _exam(master, tmap, "999", started)["exam_state"]["items"][0]
    assert skipped["verdict"] == "skipped" and wrong["verdict"] != "skipped"
    assert skipped["status"] == "skipped" and wrong["status"] == "graded"


def test_7_post_exam_never_reports_help_as_answer(monkeypatch, master, tmap, started):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns = _exam(master, tmap, "objasni ti", started)["next_state"]
    ns = _exam(master, tmap, "predaj", ns)["next_state"]
    out = _exam(master, tmap, "objasni prvi", ns)
    assert "objasni ti" not in out["answer"]
    assert "Ti si odgovorio" not in out["answer"] or "nisi predao" in out["answer"]


def test_8_malformed_text_does_not_advance(monkeypatch, master, tmap, started):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = _exam(master, tmap, "qwerty zzz", started)
    assert out["exam_state"]["current_item_index"] == 0
    assert out["answer_verdict_detail"] == "exam_needs_answer"
    assert out["exam_state"]["items"][0]["student_answer"] is None


def test_9_exactly_one_sheets_row_per_turn(monkeypatch, master, tmap, started):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    counter = {"n": 0}
    orig = svc.log_transcript_to_sheet
    svc.log_transcript_to_sheet = lambda p, r: counter.__setitem__("n", counter["n"] + 1)
    try:
        for msg in ("ne znam", "objasni ti", "preskoči"):
            counter["n"] = 0
            _exam(master, tmap, msg, started)
            assert counter["n"] == 1, msg
    finally:
        svc.log_transcript_to_sheet = orig


def test_10_completed_exam_remains_terminal(monkeypatch, master, tmap, started):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns = _exam(master, tmap, "predaj", started)["next_state"]
    assert ns["exam_state"]["exam_status"] == "completed"
    for msg in ("ne znam", "objasni ti", "hvala"):
        out = _exam(master, tmap, msg, ns)
        assert out["exam_state"]["exam_status"] == "completed"


# =========================================================================== #
# BUG 3 — runtime topic-ID canonicalization                                   #
# =========================================================================== #
def _inject_runtime_id(monkeypatch, runtime_id, npp="6-04-035"):
    """Simulate the production sheet carrying a runtime lesson id (the local
    Excel leaves thinkific_lesson_id empty)."""
    real = cl.load_master_content

    def patched(*a, **kw):
        m = dict(real(*a, **kw))
        vids = {k: list(v) for k, v in (m.get("videos_by_topic") or {}).items()}
        vids.setdefault(npp, []).append({"thinkific_lesson_id": runtime_id})
        m["videos_by_topic"] = vids
        return m
    monkeypatch.setattr(tr, "load_master_content", patched)
    tr.reset_cache()


def test_1_runtime_id_resolves_to_expansion(monkeypatch):
    _inject_runtime_id(monkeypatch, RUNTIME_ID)
    topic = tr.resolve_topic(6, RUNTIME_ID)
    assert topic is not None
    assert topic.npp_id == "6-04-035"
    assert topic.tema == EXPAND_TEMA


def test_4_all_templated_skills_resolve_by_canonical_id():
    """Every tema_id a template claims must resolve in the curriculum."""
    for t in tt._TEMPLATES:
        for tid in t.tema_ids:
            assert tr.resolve_topic(6, tid) is not None, (t.skill_id, tid)


def test_canonical_probe_keeps_tema_identity():
    assert "6-04-035" in tr.canonical_tema_probe(6, "6-04-035")
    assert EXPAND_TEMA.lower() in tr.canonical_tema_probe(6, "6-04-035").lower()


@pytest.mark.parametrize("msg", [
    "daj mi zadatak",
    "Daj mi teži zadatak iz iste teme.",
    "Daj mi lakši zadatak iz iste teme.",
])
def test_2_3_runtime_id_keeps_fraction_expand(monkeypatch, master, tmap, msg):
    _inject_runtime_id(monkeypatch, RUNTIME_ID)
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    out = svc.handle_chat({"grade": 6, "mode": "practice", "session_id": "t3",
                           "selected_oblast": "Razlomci", "selected_topic": RUNTIME_ID,
                           "student_message": msg},
                          _model("Zadatak: Riješi jednačinu: 3x + 2 = 14."),
                          master, tmap, model="m", timeout=1)
    task = out["last_tutor_task"]
    assert task.lower().startswith("proširi"), task
    assert "jednačin" not in task.lower()


def test_5_unknown_runtime_id_never_produces_unrelated_task(monkeypatch, master, tmap):
    """Unresolvable id under a fractions oblast must NOT yield an equation."""
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    out = svc.handle_chat({"grade": 6, "mode": "practice", "session_id": "t5",
                           "selected_oblast": "Razlomci", "selected_topic": "999999",
                           "student_message": "daj mi zadatak"},
                          _model("Zadatak: Riješi jednačinu: 3x + 2 = 14."),
                          master, tmap, model="m", timeout=1)
    assert "jednačin" not in (out["last_tutor_task"] or "").lower()
    assert "/" in (out["last_tutor_task"] or "")          # stayed a fractions task


def test_6_task_definition_stores_runtime_and_canonical(monkeypatch, master, tmap):
    _inject_runtime_id(monkeypatch, RUNTIME_ID)
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    out = svc.handle_chat({"grade": 6, "mode": "practice", "session_id": "t6",
                           "selected_oblast": "Razlomci", "selected_topic": RUNTIME_ID,
                           "student_message": "daj mi zadatak"},
                          _model(), master, tmap, model="m", timeout=1)
    td = out["next_state"]["task"]
    assert td["runtime_topic_id"] == RUNTIME_ID
    assert td["tema_id"] == "6-04-035"
    assert td["tema_title"] == EXPAND_TEMA
    assert td["skill_id"] == "fraction_expand"


def test_7_exam_uses_same_canonical_resolution(monkeypatch, master, tmap):
    _inject_runtime_id(monkeypatch, RUNTIME_ID)
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = svc.handle_chat({"grade": 6, "mode": "exam", "session_id": "t7",
                           "selected_oblast": "Razlomci", "selected_topic": RUNTIME_ID,
                           "student_message": "daj mi kontrolni"},
                          _model(), master, tmap, model="m", timeout=1)
    es = out["exam_state"]
    assert es["topic_covered"] is True
    assert all(i["question"].lower().startswith("proširi") for i in es["items"])


def test_8_canonical_id_tests_remain_green():
    assert [t.skill_id for t in tt.select_templates(6, "Razlomci", "6-04-035")] \
        == ["fraction_expand"]
    assert [t.skill_id for t in tt.select_templates(6, "Razlomci", "6-04-041")] \
        == ["fraction_mul"]


# =========================================================================== #
# Template quality                                                            #
# =========================================================================== #
def test_gcd_lcm_never_have_equal_operands():
    import random
    for skill in ("gcd", "lcm"):
        for s in range(400):
            q, a = tt._BY_ID[skill].generate(random.Random(s))
            nums = [int(x) for x in __import__("re").findall(r"\d+", q)]
            assert nums[0] != nums[1], q


def test_quality_gate_rejects_trivial_instances():
    assert tt.quality_ok("gcd", "Odredi NZD(32, 32).", "32") is False
    assert tt.quality_ok("lcm", "Odredi NZS(7, 7).", "7") is False
    assert tt.quality_ok("gcd", "Odredi NZD(12, 18).", "6") is True


def test_exam_items_are_quality_checked():
    state = ee.start_exam(seed="q", count=3, grade=6, oblast="Djeljivost brojeva")
    for it in state.items:
        assert tt.quality_ok("gcd", it.question, it.expected_display)
        assert tt.quality_ok("lcm", it.question, it.expected_display)
