"""Phase 2 (audit) — slika zadatka: OCR-only vs. OCR+Vision odluka.

Sve mockirano (fake OCR + fake OpenAI) — nikad stvarni Mathpix/OpenAI poziv.
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl

TOPIC = "skupovi_uvod"
GOOD_OCR = "Izračunaj presjek skupova A = {1,2,3} i B = {2,3,4}."


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _chat_recorder(reply="Odgovor."):
    calls = {"messages": [], "models": []}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["messages"].append(messages)
        calls["models"].append(model)
        msg = types.SimpleNamespace(content=reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    chat.calls = calls
    return chat


def _run(master, tmap, *, ocr_result, payload=None):
    chat = _chat_recorder()
    out = svc.handle_chat(
        dict(payload or {"selected_topic": TOPIC, "mode": "quick"}),
        chat, master, tmap,
        model="text-model", timeout=1,
        image_bytes=b"fake-bytes", image_data_url="data:image/png;base64,AAA=",
        ocr_image=lambda b: ocr_result, vision_model="vision-model",
    )
    return out, chat


def _last_user_content(chat):
    return chat.calls["messages"][-1][-1]["content"]


# --- OCR-only put (siguran, dugačak, ne-geometrijski tekst) --------------------------

def test_confident_ocr_stays_text_only(master, tmap):
    out, chat = _run(master, tmap, ocr_result=(GOOD_OCR, 0.97))
    assert out["status"] == "ready"
    content = _last_user_content(chat)
    assert isinstance(content, str)                     # NEMA slike u poruci
    assert GOOD_OCR in content                          # OCR tekst je u promptu
    assert chat.calls["models"][-1] == "text-model"     # jeftiniji tekst model


# --- OCR + Vision (nepouzdan/nepotpun OCR ili geometrija) -----------------------------

def _assert_multimodal_with_ocr(chat, expect_ocr=None):
    content = _last_user_content(chat)
    assert isinstance(content, list)
    kinds = {p.get("type") for p in content}
    assert "image_url" in kinds and "text" in kinds
    if expect_ocr:
        text_part = next(p["text"] for p in content if p.get("type") == "text")
        assert expect_ocr in text_part                  # OCR tekst ide ZAJEDNO sa slikom
    assert chat.calls["models"][-1] == "vision-model"


def test_low_confidence_ocr_sends_image_too(master, tmap):
    out, chat = _run(master, tmap, ocr_result=(GOOD_OCR, 0.30))
    assert out["status"] == "ready"
    _assert_multimodal_with_ocr(chat, expect_ocr=GOOD_OCR)


def test_short_ocr_text_sends_image_too(master, tmap):
    out, chat = _run(master, tmap, ocr_result=("x =", 0.95))
    _assert_multimodal_with_ocr(chat)


def test_geometry_keywords_send_image_too(master, tmap):
    """Visok confidence, ali zadatak je geometrijski → figura nosi informaciju."""
    ocr = "U trouglu ABC dat je ugao od 40 stepeni. Izračunaj ostale uglove."
    out, chat = _run(master, tmap, ocr_result=(ocr, 0.96))
    _assert_multimodal_with_ocr(chat, expect_ocr=ocr)


def test_geometric_topic_sends_image_too(master, tmap):
    """Tema (kružnica) je geometrijska → slika ide i uz pouzdan OCR."""
    out, chat = _run(
        master, tmap,
        ocr_result=("Izracunaj vrijednost iz date figure: r = 5 cm, O je centar.", 0.95),
        payload={"selected_topic": "kruznica_i_krug", "mode": "explain"},
    )
    _assert_multimodal_with_ocr(chat)


def test_failed_ocr_falls_back_to_vision(master, tmap):
    def _boom(b):
        raise RuntimeError("mathpix pao")

    chat = _chat_recorder()
    out = svc.handle_chat(
        {"selected_topic": TOPIC, "mode": "quick"}, chat, master, tmap,
        model="text-model", timeout=1,
        image_bytes=b"x", image_data_url="data:image/png;base64,AAA=",
        ocr_image=_boom, vision_model="vision-model",
    )
    assert out["status"] == "ready"
    _assert_multimodal_with_ocr(chat)


def test_empty_ocr_falls_back_to_vision(master, tmap):
    out, chat = _run(master, tmap, ocr_result=(None, 0.0))
    _assert_multimodal_with_ocr(chat)


# --- heuristika _looks_geometric -------------------------------------------------------

def test_looks_geometric_unit():
    for txt in ("nacrtaj trougao ABC", "ugao od 60°", "obim kružnice",
                "ŠTA JE NA SLICI PRIKAZANO", "vektori a i b", "površina kvadrata"):
        assert svc._looks_geometric(txt) is True, txt
    for txt in ("izračunaj 1/2 + 1/3", "NZD brojeva 12 i 18",
                "koliko je 25% od 200", ""):
        assert svc._looks_geometric(txt) is False, txt
