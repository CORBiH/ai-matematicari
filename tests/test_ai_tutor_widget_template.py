"""Phase 4.2 — smoke testovi renderovanog tutor UI-a.

Server renderuje puni index.html (iframe gate je klijentski JS), pa GET '/' vraća
sav HTML. Provjeravamo: jedan glavni tutor panel sa transkriptom UNUTAR kartice,
akcijska mode dugmad, i legacy /submit forma očuvana ali sklopljena u <details>.
Bez OpenAI/mreže.
"""
import re

MODES = ("explain", "practice", "exam", "quick")


def _html(client):
    r = client.get("/")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _tutor_block(html):
    """Sadržaj glavne tutor kartice (do markera kraja)."""
    start = html.index('id="tutor-card"')
    end = html.index("<!-- /tutor-card -->")
    assert start < end
    return html[start:end]


def _details_block(html):
    """Sadržaj sklopljene (advanced/legacy) sekcije."""
    start = html.index("<details")
    end = html.index("</details>")
    assert start < end
    return html[start:end]


def test_index_renders(client):
    assert client.get("/").status_code == 200


def test_main_tutor_card_contains_everything(client):
    block = _tutor_block(_html(client))
    # header + subtitle
    assert "AI Tutor" in block
    assert 'class="muted tutor-sub"' in block
    # topic selector, mode dugmad, fallback, transkript, typing, input, send, meta
    for needle in (
        'id="tutorTopic"',
        'id="tutorModes"',
        'id="tutor-fallback"',
        'id="tutorChat"',
        'id="tutorTyping"',
        'id="tutorMessage"',
        'id="tutorSend"',
        'id="tutorMeta"',
    ):
        assert needle in block, f"nedostaje {needle} unutar tutor kartice"


def test_transcript_inside_tutor_card_not_legacy(client):
    html = _html(client)
    # tutor transkript je u tutor kartici…
    assert 'id="tutorChat"' in _tutor_block(html)
    # …a legacy chat-container je u sklopljenoj advanced sekciji
    assert 'id="chat-container"' in _details_block(html)


def test_mode_buttons_are_action_buttons(client):
    html = _html(client)
    block = _tutor_block(html)
    assert block.count('data-action="tutor-send"') == 4
    action_btns = re.findall(r"<button[^>]*data-action=\"tutor-send\"[^>]*>", block)
    assert len(action_btns) == 4
    for b in action_btns:
        assert "mode-btn" in b and "data-mode=" in b
    for mode in MODES:
        assert f'data-mode="{mode}"' in block


def test_mode_buttons_wired_to_send_and_renderer_present(client):
    html = _html(client)
    assert "sendTutorMsg()" in html           # akcijska dugmad odmah šalju
    assert "renderTutorHTML" in html          # mini bezbjedni renderer
    assert "Tutor razmišlja" in html          # loading unutar tutor chata


def test_quick_empty_validation_message_present(client):
    assert "Unesi zadatak za koji želiš samo rezultat." in _html(client)


def test_practice_answer_placeholder_present(client):
    assert "Upiši svoj odgovor na zadatak..." in _html(client)


def test_legacy_form_preserved_inside_details(client):
    html = _html(client)
    details = _details_block(html)
    # legacy forma i upload žive, ali sklopljeni u <details>
    assert 'id="ask-form"' in details
    assert 'action="/submit"' in details
    assert 'id="sendBtn"' in details
    assert 'id="slika"' in details and 'name="file"' in details
    assert "Upload slike / napredni način" in html


def test_legacy_form_not_in_tutor_card(client):
    block = _tutor_block(_html(client))
    assert 'id="ask-form"' not in block
    assert 'action="/submit"' not in block


# --- Phase 4.3: practice follow-up stanje + renderer ------------------------------

def test_practice_followup_state_present(client):
    html = _html(client)
    assert "awaiting_practice_answer" in html      # JS stanje
    assert "answering_practice_task" in html       # interaction_phase u payloadu
    assert "last_tutor_task" in html               # zadnji zadatak se šalje backendu
    assert "matbot_tutor_lasttask_" in html        # localStorage ključ


def test_renderer_handles_headings_and_bold(client):
    html = _html(client)
    # ### naslovi se pretvaraju u h3/h2 (ne prikazuje se sirovi markdown)
    assert "'<h3>'+m[1]+'</h3>'" in html
    assert "'<h2>'+m[1]+'</h2>'" in html
    # **bold** → <strong>
    assert "<strong>$1</strong>" in html
    # linije sa samo "." se uklanjaju
    assert "t === '.'" in html


def test_friendly_meta_present(client):
    html = _html(client)
    assert "Režim:" in html
    assert "topicNames" in html                    # display_name umjesto sirovog id-a
