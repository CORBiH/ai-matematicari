"""Phase 4.1 — smoke testovi renderovanog widget templatea.

Server uvijek renderuje puni index.html (iframe gate je klijentski JS), pa GET '/'
vraća sav HTML. Provjeravamo da su mode dugmad AKCIJSKA i da legacy /submit forma
i dalje postoji. Bez OpenAI/mreže.
"""
import re

MODES = ("explain", "practice", "exam", "quick")


def _html(client):
    r = client.get("/")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_index_renders(client):
    assert client.get("/").status_code == 200


def test_mode_buttons_present_with_data_mode(client):
    html = _html(client)
    for mode in MODES:
        assert f'data-mode="{mode}"' in html


def test_mode_buttons_are_action_buttons(client):
    html = _html(client)
    # sva 4 mode dugmeta su označena kao akcijska (odmah šalju)
    assert html.count('data-action="tutor-send"') == 4
    # svako akcijsko dugme je ujedno mode-btn sa data-mode
    action_btns = re.findall(r"<button[^>]*data-action=\"tutor-send\"[^>]*>", html)
    assert len(action_btns) == 4
    for b in action_btns:
        assert "mode-btn" in b
        assert "data-mode=" in b


def test_mode_buttons_wired_to_send(client):
    html = _html(client)
    # klik na mode dugme poziva tutor send funkciju (akcija, ne samo selekcija)
    assert "sendTutorMsg()" in html
    # ručni "Pošalji tutoru" i dalje postoji
    assert 'id="tutorSend"' in html


def test_quick_empty_validation_message_present(client):
    html = _html(client)
    assert "Unesi zadatak za koji želiš samo rezultat." in html


def test_legacy_submit_form_preserved(client):
    html = _html(client)
    assert 'id="ask-form"' in html
    assert 'action="/submit"' in html
    # legacy send dugme i upload i dalje postoje
    assert 'id="sendBtn"' in html
    assert 'name="file"' in html
