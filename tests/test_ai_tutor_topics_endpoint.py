"""Testovi za GET /api/ai-tutor/topics + list_topics (NPP, razredi 6–9).

Čita stvarne NPP master fajlove (offline). Nema OpenAI/mrežnih poziva. Očekivanja
se izvode iz samog mastera (bez brittle hardkodiranih lista), uz par stabilnih
sidra po razredu.
"""
import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl

TOPICS_URL = "/api/ai-tutor/topics"
ALL_GRADES = (6, 7, 8, 9)

# Stabilna sidra: prva oblast svakog razreda (area_order == 1).
FIRST_OBLAST = {
    6: "Skupovi i skupovne operacije",
    7: "Cijeli brojevi",
    8: "Stepeni",
    9: "ALGEBARSKI RAZLOMCI",
}


@pytest.fixture(scope="module")
def masters():
    return {g: cl.load_master_content(grade=g) for g in ALL_GRADES}


def _expected_oblast_order(master):
    seen: list[str] = []
    for r in master["topics"]:
        if r.get("oblast") and r["oblast"] not in seen:
            seen.append(r["oblast"])
    return seen


# --- list_topics (servis) -------------------------------------------------------

@pytest.mark.parametrize("grade", ALL_GRADES)
def test_list_topics_shape(masters, grade):
    master = masters[grade]
    data = svc.list_topics(master, grade=grade)
    assert data["grade"] == grade
    assert isinstance(data["topics"], list) and data["topics"]
    assert isinstance(data["grouped"], dict) and data["grouped"]
    assert isinstance(data["areas"], list) and data["areas"]
    for t in data["topics"]:
        assert set(t.keys()) == {"oblast", "topic", "display_name", "has_video"}
        assert t["topic"] and t["display_name"]
        assert isinstance(t["has_video"], bool)
        assert t["topic"].startswith(f"{grade}-")


@pytest.mark.parametrize("grade", ALL_GRADES)
def test_list_topics_oblast_first_order(masters, grade):
    master = masters[grade]
    data = svc.list_topics(master, grade=grade)
    expected = _expected_oblast_order(master)
    # NPP_TOPICS je poredan po area_order, pa oblast-first order == sheet order
    assert data["oblast_order"] == expected
    assert list(data["grouped"].keys()) == expected
    assert data["oblast_order"][0] == FIRST_OBLAST[grade]
    # areas nose isti redoslijed
    assert [a["oblast"] for a in data["areas"]] == expected


@pytest.mark.parametrize("grade", ALL_GRADES)
def test_list_topics_has_video_matches_master(masters, grade):
    master = masters[grade]
    data = svc.list_topics(master, grade=grade)
    videos = master["videos_by_topic"]
    for t in data["topics"]:
        assert t["has_video"] == bool(videos.get(t["topic"]))
    # bar jedna tema ima video (svaki razred ima video lekcije)
    assert any(t["has_video"] for t in data["topics"])


@pytest.mark.parametrize("grade", ALL_GRADES)
def test_list_topics_all_ready_and_in_master(masters, grade):
    master = masters[grade]
    data = svc.list_topics(master, grade=grade)
    assert len(data["topics"]) == len(master["topics"])
    assert all(t["topic"] in master["topic_ids"] for t in data["topics"])
    # teme unutar grupe u redoslijedu sheeta
    for oblast, items in data["grouped"].items():
        sheet_ids = [r["topic"] for r in master["topics"] if r.get("oblast") == oblast]
        assert [it["topic"] for it in items] == sheet_ids


# --- HTTP endpoint --------------------------------------------------------------

@pytest.mark.parametrize("grade", ALL_GRADES)
def test_topics_endpoint_ok(client, masters, grade):
    resp = client.get(f"{TOPICS_URL}?grade={grade}")
    assert resp.status_code == 200
    assert resp.is_json
    body = resp.get_json()
    assert body["grade"] == grade
    assert isinstance(body["topics"], list) and body["topics"]
    assert isinstance(body["oblast_order"], list) and body["oblast_order"]
    assert body["oblast_order"] == _expected_oblast_order(masters[grade])
    assert set(body["oblast_order"]) == set(body["grouped"].keys())
    assert len(body["topics"]) == len(masters[grade]["topics"])


def test_topics_endpoint_unsupported_grade(client):
    resp = client.get(f"{TOPICS_URL}?grade=10")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "unsupported_grade"
    assert "Nepodrzan razred" in body["detail"]


def test_topics_endpoint_no_secrets(client):
    text = client.get(TOPICS_URL).get_data(as_text=True).lower()
    for leaked in ("api_key", "openai", "secret", "password", "token"):
        assert leaked not in text
