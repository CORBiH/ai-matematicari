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


@pytest.fixture(scope="module")
def master7():
    return cl.load_master_content(grade=7)


# --- list_topics (servis) -------------------------------------------------------

def test_list_topics_shape(master):
    data = svc.list_topics(master)
    assert data["grade"] == 6
    assert isinstance(data["topics"], list) and data["topics"]
    assert isinstance(data["grouped"], dict) and data["grouped"]
    for t in data["topics"]:
        assert set(t.keys()) == {"oblast", "topic", "display_name"}
        assert t["topic"] and t["display_name"]


def test_list_topics_grade_7_shape(master7):
    data = svc.list_topics(master7, grade=7)
    assert data["grade"] == 7
    assert isinstance(data["topics"], list) and data["topics"]
    assert isinstance(data["grouped"], dict) and data["grouped"]
    assert "Cijeli brojevi" in data["grouped"]


def test_list_topics_only_ready(master):
    data = svc.list_topics(master)
    ready = [r for r in master["topics"] if (r.get("status", "").upper() == "READY")]
    assert len(data["topics"]) == len(ready)
    # sve teme moraju postojati u masteru
    assert all(t["topic"] in master["topic_ids"] for t in data["topics"])


def test_list_topics_preserves_sheet_order(master):
    """Phase 1 (audit): redoslijed tema = nastavni redoslijed iz TOPICS sheeta,
    NE abecedni (abeceda je razbijala redoslijed učenja u dropdownu)."""
    data = svc.list_topics(master)
    ready_ids = [
        r["topic"] for r in master["topics"]
        if r.get("topic") and (not r.get("status") or r["status"].upper() == "READY")
    ]
    assert [t["topic"] for t in data["topics"]] == ready_ids
    # oblast_order = redoslijed prvog pojavljivanja u sheetu (kroz JSON kao niz)
    seen: list[str] = []
    for r in master["topics"]:
        if r.get("oblast") and r["oblast"] not in seen:
            seen.append(r["oblast"])
    assert data["oblast_order"] == seen
    assert list(data["grouped"].keys()) == seen
    # grupe: sve stavke pripadaju oblasti, redoslijed unutar grupe = sheet
    for oblast, items in data["grouped"].items():
        assert all(it["oblast"] == oblast for it in items)
        sheet_ids = [r["topic"] for r in master["topics"] if r.get("oblast") == oblast]
        assert [it["topic"] for it in items] == [tid for tid in sheet_ids if tid in ready_ids]
    # ukupan broj u grupama == broj tema
    assert sum(len(v) for v in data["grouped"].values()) == len(data["topics"])


def test_topics_endpoint_oblast_order_in_json(client, master):
    """oblast_order stiže kroz HTTP JSON kao NIZ (objektni ključevi mogu biti
    presortirani od strane serializera — frontend koristi niz)."""
    body = client.get(f"{TOPICS_URL}?grade=6").get_json()
    assert isinstance(body.get("oblast_order"), list) and body["oblast_order"]
    expected = []
    for r in master["topics"]:
        if r.get("oblast") and r["oblast"] not in expected:
            expected.append(r["oblast"])
    assert body["oblast_order"] == expected
    # prva oblast u sheetu NIJE nužno prva po abecedi — upravo to štitimo
    assert set(body["oblast_order"]) == set(body["grouped"].keys())


# --- HTTP endpoint --------------------------------------------------------------

def test_topics_endpoint_ok(client, master):
    resp = client.get(f"{TOPICS_URL}?grade=6")
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


def test_topics_endpoint_grade_7_ok(client, master7):
    resp = client.get(f"{TOPICS_URL}?grade=7")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["grade"] == 7
    expected = {
        "Cijeli brojevi",
        "Racionalni brojevi",
        "Vektori",
        "Izometrijska preslikavanja",
        "Operacije sa uglovima",
        "Trougao",
        "Četverougao",
    }
    assert expected.issubset(set(body["grouped"]))
    assert any(k.startswith("Osnovne geometrijske konstrukcije") for k in body["grouped"])
    ready = [r for r in master7["topics"] if r.get("status", "").upper() == "READY"]
    assert len(body["topics"]) == len(ready)


def test_topics_endpoint_unsupported_grade(client):
    resp = client.get(f"{TOPICS_URL}?grade=8")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "unsupported_grade"
    assert "Nepodrzan razred" in body["detail"]


def test_topics_endpoint_no_secrets(client):
    # trivijalna provjera da odgovor ne curi tajne
    text = client.get(TOPICS_URL).get_data(as_text=True).lower()
    for leaked in ("api_key", "openai", "secret", "password", "token"):
        assert leaked not in text
