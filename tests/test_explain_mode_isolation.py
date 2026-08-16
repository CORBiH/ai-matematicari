"""Explain audit C-1: Practice-only active-task tracking (LASTTASK_KEY,
interactionPhase) NIKAD ne smije procuriti u Explain/Quick. templates/index.html
se ne izvršava u pytest-u (nema browser/DOM okruženja u ovom repou, vidi
tests/test_frontend_retry_ux.py) — ovo je statička provjera da je popravka
STVARNO ožičena na svakom mjestu koje čita/piše to stanje, i da ostaje ožičena
kroz buduće izmjene.

Scenario iz živog nalaza: učenik pređe u Objašnjenje preko chipa "📘 Objasni
postupak" dok je Vježba imala aktivan zadatak (LASTTASK_KEY nije bio prazan).
Prije popravke: chip je nulirao SAMO in-memory interactionPhase, ne i
localStorage LASTTASK_KEY, pa je "ne razumijem"/"objasni" u Objašnjenju i dalje
čitalo stari zadatak preko storedLastTask() i slalo interaction_phase=
answering_practice_task na Explain zahtjev."""
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def _read():
    return INDEX_HTML.read_text(encoding="utf-8")


def test_shared_mode_guard_function_exists():
    html = _read()
    assert "function modeTracksPracticeTask(mode){" in html
    body_start = html.index("function modeTracksPracticeTask")
    body = html[body_start: body_start + 200]
    assert "'practice'" in body and "'exam'" in body


# ---------------------------------------------------------------------------
# 24. Practice → Explain switch clears active-task tracking
# ---------------------------------------------------------------------------

def test_case24_chip_mode_switch_clears_awaiting_practice_task():
    html = _read()
    switch_start = html.index("if (c.mode && c.mode !== state.mode){")
    # Region je ISJEČEN DO KRAJA BLOKA, ne na fiksnih 500 znakova: prozor po
    # broju znakova je pukao čim je u blok ušla konstrukcijska brana protiv
    # prelaska u Vježbu bez lekcije (audit 2026-08-16), iako se sam kod nije
    # promijenio. Granica je `hideChips();`, prvi red POSLIJE bloka.
    switch_region = html[switch_start: html.index("hideChips();", switch_start)]
    assert "clearAwaitingPracticeTask();" in switch_region
    # NE smije se vratiti na samo in-memory nuliranje (stari bag: interactionPhase
    # se nulirao, ali LASTTASK_KEY u localStorage je ostajao netaknut).
    assert "interactionPhase = null;" not in switch_region


def test_case24b_explain_quick_to_practice_switch_also_clears_stale_task():
    html = _read()
    switch_start = html.index("wantsPracticeTask(typed)){")
    switch_region = html[switch_start: switch_start + 300]
    assert "clearAwaitingPracticeTask();" in switch_region


# ---------------------------------------------------------------------------
# 25/26. "ne razumijem" / "objasni" u Explainu ne smije pokrenuti Practice
# answer-submission logiku ni ponovo naoružati zadatak
# ---------------------------------------------------------------------------

def test_case25_26_answer_phase_detection_gated_by_practice_mode():
    html = _read()
    marker = "savedTask && isShortPracticeAnswer(typed)"
    assert marker in html
    idx = html.index(marker)
    # provjera moda mora biti u ISTOM uslovnom izrazu, neposredno prije njega
    preceding = html[max(0, idx - 200):idx]
    assert "modeTracksPracticeTask(state.mode)" in preceding


def test_case26b_rearm_after_answer_requires_practice_mode():
    html = _read()
    marker = "setAwaitingPracticeTask(prevTask)"
    assert marker in html
    idx = html.index(marker)
    line_start = html.rfind("\n", 0, idx)
    line = html[line_start:idx]
    assert "canTrackTask" in line


def test_isshortpracticeanswer_matches_common_explain_followups():
    """Dokumentuje TAČNO zašto je guard neophodan: ove fraze su najčešći
    Explain follow-upovi, i isShortPracticeAnswer ih prepoznaje kao practice
    odgovor kad god postoji bilo kakav sačuvan zadatak (bez obzira na mod)."""
    html = _read()
    fn_start = html.index("function isShortPracticeAnswer(t){")
    fn_body = html[fn_start: fn_start + 500]
    assert "ne razumijem" in fn_body
    assert "objasni" in fn_body


# ---------------------------------------------------------------------------
# 27. Explain → Result (Quick) ostaje jednako izolovan (isti dijeljeni guard)
# ---------------------------------------------------------------------------

def test_case27_quick_mode_excluded_from_practice_tracking():
    html = _read()
    fn_start = html.index("function modeTracksPracticeTask(mode){")
    fn_body = html[fn_start: fn_start + 150]
    assert "'quick'" not in fn_body
    assert "'explain'" not in fn_body


# ---------------------------------------------------------------------------
# 28. Povratak u Practice i dalje radi normalno (canTrackTask=true tamo)
# ---------------------------------------------------------------------------

def test_case28_practice_mode_still_tracked():
    html = _read()
    fn_start = html.index("function modeTracksPracticeTask(mode){")
    fn_body = html[fn_start: fn_start + 150]
    assert "mode === 'practice'" in fn_body
    assert "mode === 'exam'" in fn_body


# ---------------------------------------------------------------------------
# 29. Reload u Explainu ne smije obnoviti stari Practice zadatak
# ---------------------------------------------------------------------------

def test_case29_replay_history_gates_stale_task_restore_by_mode():
    html = _read()
    marker = "interactionPhase = 'awaiting_practice_answer';"
    # postoje TAČNO dva mjesta koja ovo postavljaju: setAwaitingPracticeTask
    # (uvijek ispravno korišten iza canTrackTask provjere pozivaoca) i
    # replayTutorHistory (reload put) — provjeravamo baš ovaj drugi.
    replay_start = html.index("modeTracksPracticeTask(state.mode) && storedLastTask()")
    replay_region = html[max(0, replay_start - 20):replay_start + 80]
    assert "modeTracksPracticeTask(state.mode)" in replay_region
    assert marker in html  # sanity: linija i dalje postoji negdje


# ---------------------------------------------------------------------------
# Regresija: canTrackTask u applyTutorResponse dijeli ISTU funkciju (jedan
# izvor istine), umjesto svoje kopije uslova.
# ---------------------------------------------------------------------------

def test_apply_tutor_response_reuses_shared_guard():
    html = _read()
    assert "const canTrackTask = modeTracksPracticeTask(state.mode);" in html
