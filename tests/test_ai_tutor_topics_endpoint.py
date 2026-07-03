"""Testovi za GET /api/ai-tutor/topics + list_topics (Phase 4).

Čita stvarni master (offline). Nema OpenAI/mrežnih poziva.
"""
import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl

TOPICS_URL = "/api/ai-tutor/topics"


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


# --- list_topics (servis) -------------------------------------------------------

def test_list_topics_shape(master):
    data = svc.list_topics(master)
    assert data["grade"] == 6
    assert isinstance(data["topics"], list) and data["topics"]
    assert isinstance(data["grouped"], dict) and data["grouped"]
    for t in data["topics"]:
        assert set(t.keys()) == {"oblast", "topic", "display_name"}
        assert t["topic"] and t["display_name"]


def test_list_topics_only_ready(master):
    data = svc.list_topics(master)
    ready = [r for r in master["topics"] if (r.get("status", "").upper() == "READY")]
    assert len(data["topics"]) == len(ready)
    # sve teme moraju postojati u masteru
    assert all(t["topic"] in master["topic_ids"] for t in data["topics"])


def test_list_topics_sorted_and_grouped(master):
    data = svc.list_topics(master)
    # globalno sortirano po (oblast, display_name)
    pairs = [(t["oblast"], t["display_name"]) for t in data["topics"]]
    assert pairs == sorted(pairs)
    # grouped: ključevi su oblasti, svaka grupa sortirana po display_name
    for oblast, items in data["grouped"].items():
        assert all(it["oblast"] == oblast for it in items)
        names = [it["display_name"] for it in items]
        assert names == sorted(names)
    # ukupan broj u grupama == broj tema
    assert sum(len(v) for v in data["grouped"].values()) == len(data["topics"])


# --- HTTP endpoint --------------------------------------------------------------

def test_topics_endpoint_ok(client, master):
    resp = client.get(TOPICS_URL)
    assert resp.status_code == 200
    assert resp.is_json
    body = resp.get_json()
    assert body["grade"] == 6
    assert isinstance(body["topics"], list) and body["topics"]
    assert isinstance(body["grouped"], dict) and body["grouped"]
    first = body["topics"][0]
    for key in ("oblast", "topic", "display_name"):
        assert key in first
    # broj tema == broj READY tema u masteru
    ready = [r for r in master["topics"] if r.get("status", "").upper() == "READY"]
    assert len(body["topics"]) == len(ready)


def test_topics_endpoint_no_secrets(client):
    # trivijalna provjera da odgovor ne curi tajne
    text = client.get(TOPICS_URL).get_data(as_text=True).lower()
    for leaked in ("api_key", "openai", "secret", "password", "token"):
        assert leaked not in text
