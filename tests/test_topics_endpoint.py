"""Testovi za GET /api/ai-tutor/topics i postojeće rute (/, /healthz, /_healthz)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402


def client():
    app.testing = True
    return app.test_client()


def test_topics_grade_6_has_grouped_and_oblast_order():
    r = client().get("/api/ai-tutor/topics?grade=6")
    assert r.status_code == 200
    data = r.get_json()
    assert "grouped" in data
    assert "oblast_order" in data
    assert len(data["oblast_order"]) > 0
    assert set(data["grouped"].keys()) == set(data["oblast_order"])


def test_topics_grade_7_8_9_all_work():
    for grade in ("7", "8", "9"):
        r = client().get(f"/api/ai-tutor/topics?grade={grade}")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data["oblast_order"]) > 0
        total_lessons = sum(len(v) for v in data["grouped"].values())
        assert total_lessons > 0


def test_every_lesson_has_topic_and_display_name():
    r = client().get("/api/ai-tutor/topics?grade=6")
    data = r.get_json()
    for lessons in data["grouped"].values():
        for lesson in lessons:
            assert "topic" in lesson and lesson["topic"]
            assert "display_name" in lesson and lesson["display_name"]


def test_invalid_grade_returns_controlled_empty_response():
    r = client().get("/api/ai-tutor/topics?grade=99")
    assert r.status_code == 200
    data = r.get_json()
    assert data == {"grouped": {}, "oblast_order": []}


def test_missing_grade_param_does_not_crash():
    r = client().get("/api/ai-tutor/topics")
    assert r.status_code == 200
    data = r.get_json()
    assert data == {"grouped": {}, "oblast_order": []}


def test_non_numeric_grade_does_not_crash():
    r = client().get("/api/ai-tutor/topics?grade=abc")
    assert r.status_code == 200
    data = r.get_json()
    assert data == {"grouped": {}, "oblast_order": []}


def test_index_route_still_works():
    r = client().get("/")
    assert r.status_code == 200


def test_healthz_still_works():
    r = client().get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}


def test_underscore_healthz_still_works():
    r = client().get("/_healthz")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
