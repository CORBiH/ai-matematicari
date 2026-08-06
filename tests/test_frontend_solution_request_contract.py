r"""Chip „Uradi ga ti“ mora poslati EKSPLICITAN ugovor, ne samo tekst dugmeta.

PRODUKCIJSKI NALAZ: klik na „Uradi ga ti“ slao je `intent=solution_request`,
ali je `interaction_phase` padao u granu odgovora na zadatak
(`answering_practice_task`) — dakle signal koji backendu kaže „ovo je pokušaj
odgovora“, iako je učenik tražio rješenje. Backend je zbog toga namjeru morao
pogađati iz slobodnog teksta „Uradi ga ti.“

`templates/index.html` se u pytestu ne izvršava (nema DOM-a), pa je ovo
statička provjera da je mehanizam ožičen — isti pristup kao
tests/test_frontend_retry_ux.py.
"""
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def _read():
    return INDEX_HTML.read_text(encoding="utf-8")


def test_solution_chip_declares_the_explicit_intent():
    html = _read()
    assert "'👉 Uradi ga ti'" in html or "👉 Uradi ga ti" in html
    assert "intent: 'solution_request'" in html


def test_solution_request_is_recognized_as_an_explicit_ui_action():
    """Postoji imenovana zastavica, kao i za hint — ne samo tekst poruke."""
    html = _read()
    assert "explicitSolutionRequest" in html


def test_solution_request_sends_intent_and_help_phase():
    html = _read()
    branch = html[html.index("explicitSolutionRequest && savedTask"):]
    branch = branch[:600]
    assert "payload.intent = 'solution_request'" in branch
    assert "payload.interaction_phase = 'practice_help'" in branch
    assert "payload.mode = 'practice'" in branch
    assert "payload.last_tutor_task" in branch


def test_solution_request_is_never_sent_as_an_answer_attempt():
    """Grana koja postavlja `answering_practice_task` mora ga izuzeti."""
    html = _read()
    answer_branch_start = html.index("answerPhase = 'answering_practice_task'")
    guard = html[max(0, answer_branch_start - 500):answer_branch_start]
    assert "!explicitSolutionRequest" in guard
