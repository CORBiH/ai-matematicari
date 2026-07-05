"""Phase 2 (audit) — eval harness radi OFFLINE (nikad API iz pytest-a).

`run_eval(live=False)` koristi fake chat; anti-network guard iz conftest-a bi
oborio svaki stvarni poziv.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import eval_tutor  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield


def test_cases_file_valid():
    cases = eval_tutor.load_cases()
    assert len(cases) >= 15
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplirani case id"
    grades = {c["payload"].get("grade") for c in cases}
    assert {6, 7, 8} <= grades                    # svi podržani tutor razredi pokriveni
    # pokriveni svi modovi + wrong-answer follow-up + image-like slučaj
    blob = json.dumps(cases, ensure_ascii=False)
    for needle in ("answering_practice_task", "image_ocr_text",
                   '"mode": "exam"', '"mode": "quick"', '"mode": "practice"'):
        assert needle in blob, needle


def test_dry_eval_runs_offline_and_routes_correctly():
    report, results = eval_tutor.run_eval(live=False)
    assert len(results) >= 15
    # DRY: nijedan odgovor nije pravi model output
    for r in results:
        if r["status"] == "ready":
            assert r["answer"] == eval_tutor.DRY_ANSWER, r["id"]
    # rutiranje: SVI expect_* checkovi moraju proći (regresioni čuvar)
    bad = [(r["id"], r["checks"]) for r in results
           if any(not ok for *_x, ok in r["checks"])]
    assert not bad, f"rutiranje palo: {bad}"
    # izvještaj sadrži sve slučajeve
    for r in results:
        assert f"## {r['id']}" in report
    assert "DRY (bez API poziva)" in report


def test_live_eval_requires_explicit_env(monkeypatch):
    monkeypatch.delenv("MATBOT_EVAL_LIVE", raising=False)
    with pytest.raises(SystemExit):
        eval_tutor.make_live_chat()


def test_dry_regression_mnozenje_cijelih():
    """Čuvar detektorske regresije kroz eval: množenje cijelih ide na pravu temu."""
    _, results = eval_tutor.run_eval(live=False)
    case = next(r for r in results if r["id"] == "g7-freechat-mnozenje-cijelih")
    assert case["final_topic"] == "cijeli_mnozenje_dijeljenje"
    case58 = next(r for r in results if r["id"] == "g6-practice-answer-correct")
    assert case58["final_topic"] == "razlomci_mnozenje_razlomkom_svojstva"
    assert case58["mode"] == "practice"


def test_dry_regression_grade8_cases():
    _, results = eval_tutor.run_eval(live=False)
    by_id = {r["id"]: r for r in results}
    assert by_id["g8-explain-stepeni"]["final_topic"] == "stepeni_pravila_i_pojasnjenja_stepeni"
    assert by_id["g8-practice-korijeni"]["final_topic"] == "realni_korijeni_pravila_za_racunske_operacije"
    assert by_id["g8-image-conflict-koordinatni-pitagora"]["final_topic"] == "pitagora_pitagorina_teorema_osnovno"
    assert by_id["g8-practice-valjak-ne-znam"]["mode"] == "practice"
