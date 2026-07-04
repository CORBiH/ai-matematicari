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


def _legacy_block(html):
    """Sadržaj SKRIVENOG legacy dijela (Phase 6.2: nema više <details>)."""
    start = html.index('id="advancedLegacy"')
    end = html.index("<!-- /legacy-holder -->")
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
    # …a legacy chat-container je u SKRIVENOM legacy dijelu
    assert 'id="chat-container"' in _legacy_block(html)


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


def test_legacy_form_preserved_but_hidden(client):
    """Phase 6.2: legacy markup POSTOJI (JS/backend netaknuti) ali je skriven."""
    html = _html(client)
    legacy = _legacy_block(html)
    # legacy forma i upload žive (backend /submit ostaje), ali NE kao vidljiv bot
    assert 'id="ask-form"' in legacy
    assert 'action="/submit"' in legacy
    assert 'id="sendBtn"' in legacy
    assert 'id="slika"' in legacy and 'name="file"' in legacy
    # holder je skriven hidden atributom
    assert 'id="advancedLegacy" class="legacy-holder" hidden' in html
    # nema više vidljivog <details>/summary dvojnika
    assert "<details" not in html
    assert "Imam sliku zadatka / napredni način" not in html


def test_single_visible_tutor_card(client):
    html = _html(client)
    # samo jedna vidljiva kartica; legacy je unutar skrivenog holdera
    assert html.count('<div class="card') == 1


def test_empty_state_helper_in_tutor_card(client):
    block = _tutor_block(_html(client))
    assert 'id="tutorEmptyState"' in block
    assert "Izaberi temu ili samo upiši pitanje" in block
    assert "aritmetička sredina brojeva 4, 6 i 8" in block


def test_topic_label_optional(client):
    assert "Tema ako znaš (opcionalno):" in _tutor_block(_html(client))


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


# --- Phase 6.1: hardening/UX markeri ----------------------------------------------

def test_topic_change_resets_practice_state(client):
    html = _html(client)
    # change listener na topic selectu resetuje fazu + briše zadnji zadatak
    assert "topicSel.addEventListener('change'" in html
    idx = html.index("topicSel.addEventListener('change'")
    snippet = html[idx:idx + 400]
    assert "interactionPhase = null" in snippet
    assert "LASTTASK_KEY" in snippet
    assert "DEFAULT_PLACEHOLDER" in snippet


def test_clear_chat_clears_tutor_keys(client):
    html = _html(client)
    assert "k.startsWith('matbot_tutor_history_')" in html
    assert "k.startsWith('matbot_tutor_lasttask_')" in html


def test_enter_to_send_markers(client):
    html = _html(client)
    assert "e.key === 'Enter'" in html
    assert "e.shiftKey" in html
    assert "if (!tutorBusy) sendTutorMsg()" in html


def test_topics_load_failure_message(client):
    html = _html(client)
    assert 'id="tutorTopicError"' in html
    assert "Teme trenutno nisu dostupne" in html


# --- Phase 6.2: slika zadatka u glavnom tutoru --------------------------------------

def _composer_row(html):
    """Sadržaj composer reda (.tutor-inputrow) — + dugme | textarea | send."""
    start = html.index('class="tutor-inputrow"')
    end = html.index("</div>", start)
    return html[start:end]


def test_image_upload_ui_inside_tutor_card(client):
    html = _html(client)
    block = _tutor_block(html)
    assert 'id="tutorImage"' in block
    assert 'accept="image/*"' in block
    assert 'id="tutorImageChip"' in block
    assert 'id="tutorImageRemove"' in block
    # dostupnost: + dugme ima aria-label/title
    assert 'aria-label="Dodaj sliku zadatka"' in block


def test_plus_button_in_composer_row(client):
    """textarea, + dugme i send su u istom composer redu."""
    row = _composer_row(_html(client))
    assert 'class="tutor-plus"' in row
    assert 'for="tutorImage"' in row          # + otvara postojeći file input
    assert 'id="tutorMessage"' in row         # textarea
    assert 'id="tutorSend"' in row            # send dugme
    # redoslijed: textarea prije + dugmeta prije send-a
    assert row.index("tutorMessage") < row.index("tutor-plus") < row.index("tutorSend")


def test_old_standalone_upload_button_gone(client):
    html = _html(client)
    # stari veliki samostalni upload dugme/red više ne postoji
    assert "tutor-imagerow" not in html
    assert "📷 Dodaj sliku zadatka" not in html
    # hidden file input i dalje postoji i povezan je
    assert 'id="tutorImage"' in html and 'accept="image/*"' in html


def test_image_send_uses_multipart(client):
    html = _html(client)
    assert "fd.append('payload', JSON.stringify(payload))" in html
    assert "fd.append('image', imgFile, imgFile.name)" in html
    # default poruke po modu za sliku bez teksta
    assert "Daj mi samo rezultat zadatka sa slike." in html
    assert "Objasni mi zadatak sa slike." in html
