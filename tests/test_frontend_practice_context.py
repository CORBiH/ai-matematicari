"""Statički frontend ugovor za Practice promjenu kurikularnog konteksta."""

from pathlib import Path


INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def _html():
    return INDEX.read_text(encoding="utf-8")


def _region(html, start, end, offset=0):
    begin = html.index(start) + offset
    return html[begin:html.index(end, begin)]


def test_last_task_is_bound_to_the_canonical_practice_fingerprint():
    html = _html()
    fingerprint = _region(html, "function trackedTaskFingerprint", "function tutorContextKey")
    assert "state.grade" in fingerprint
    assert "state.topicOblastId" in fingerprint
    assert "state.topic" in fingerprint
    assert "'practice'" in fingerprint

    writer = _region(html, "function setAwaitingPracticeTask", "function clearAwaitingPracticeTask")
    reader = _region(html, "function storedLastTask", "const IMAGE_HISTORY_MARKER")
    assert "v: 2" in writer and "fingerprint: trackedTaskFingerprint()" in writer
    assert "saved.fingerprint !== fingerprint" in reader
    assert "localStorage.removeItem(LASTTASK_KEY)" in reader


def test_grade_oblast_and_topic_changes_immediately_deactivate_practice_task_ui():
    html = _html()
    invalidator = _region(
        html, "function invalidatePracticeCurriculumState", "function clearStoredActiveTopic"
    )
    for action in ("clearAwaitingPracticeTask();", "clearNextState();", "clearOptions();", "hideChips();"):
        assert action in invalidator

    grade_handler = _region(html, "gradeSel.addEventListener('change'", "backBtn.addEventListener")
    oblast_handler = _region(html, "oblastSelect.addEventListener('change'", "continueBtn.addEventListener")
    assert "invalidatePracticeCurriculumState();" in grade_handler
    assert "invalidatePracticeCurriculumState();" in oblast_handler
    assert "topicSelect.addEventListener('change', invalidatePracticeCurriculumState)" in oblast_handler


def test_header_identity_and_payload_both_come_from_the_same_canonical_topic_maps():
    html = _html()
    continue_handler = _region(html, "continueBtn.addEventListener", "function updateTopbar")
    assert "state.topic = t;" in continue_handler
    assert "state.topicName = topicNames[t] || t;" in continue_handler
    assert "state.topicOblastId = topicOblastIds[t]" in continue_handler
    assert "opt.textContent) || t" not in continue_handler

    assert "selected_topic: selectedTopicForPayload" in html
    assert "selectedTopicForPayload ? state.topicOblastId" in html
    assert "selected_topic: state.topic" in html
    assert "selected_oblast: state.topic ? state.topicOblastId : ''" in html


def test_late_response_from_old_lesson_cannot_relabel_or_restore_its_task():
    html = _html()
    apply_start = html.index("function applyTutorResponse")
    guard_end = html.index("const isFailedNewTaskGeneration", apply_start)
    guard = html[apply_start:guard_end]
    assert "opts.requestFingerprint !== trackedTaskFingerprint()" in guard
    assert "j.effective_topic" in guard and "opts.requestTopic" in guard
    assert "if (stalePracticeResponse)" in guard
    assert "return;" in guard


def test_explain_and_result_do_not_acquire_practice_task_state():
    html = _html()
    invalidator = _region(
        html, "function invalidatePracticeCurriculumState", "function clearStoredActiveTopic"
    )
    assert "if (state.mode !== 'practice') return;" in invalidator
    fingerprint = _region(html, "function trackedTaskFingerprint", "function payloadPracticeFingerprint")
    assert "return '';" in fingerprint



def test_frontend_abort_budget_exceeds_the_two_call_server_worst_case():
    """DEPLOYMENT GUARD (dvopozivni put).

    Practice radi DVA sekvencijalna modela poziva. Ako frontend prekine prije
    nego što server stigne završiti oba, turn koji bi uspio biva odbačen — a
    oba poziva su već plaćena. Zato granica prekida mora nadmašiti
    2 × AI_TUTOR_TIMEOUT + serverska obrada, i ostati ISPOD GUNICORN_TIMEOUT
    (inače gunicorn presijeca prvi).

    Ranije je ovdje bilo 60s, iz vremena jednog poziva: izmjereno na 96 živih
    poziva dvopozivni p95 je ≈60s, dakle tačno na staroj granici."""
    import re

    html = _html()
    match = re.search(r"const TUTOR_ABORT_MS\s*=\s*(\d+);", html)
    assert match, "TUTOR_ABORT_MS mora biti definisan na jednom mjestu"
    abort_ms = int(match.group(1))

    per_call_s = 45          # AI_TUTOR_TIMEOUT u produkciji (.env.example)
    server_overhead_s = 5
    gunicorn_s = 120         # GUNICORN_TIMEOUT u produkciji

    worst_case_ms = (2 * per_call_s + server_overhead_s) * 1000
    assert abort_ms >= worst_case_ms, (
        f"granica prekida {abort_ms}ms ne pokriva dvopozivni najgori slučaj "
        f"{worst_case_ms}ms"
    )
    assert abort_ms < gunicorn_s * 1000, (
        "granica prekida ne smije preći GUNICORN_TIMEOUT"
    )
    # Svi prekidači koriste ISTU konstantu (nijedan zaostali literal).
    assert "}, 60000);" not in html
    assert html.count("TUTOR_ABORT_MS") >= 4
