"""Phase 7 — smoke testovi renderovanog tutor UI-a (onboarding + fokusirani chat).

Server renderuje puni index.html (iframe gate je klijentski JS), pa GET '/' vraća
sav HTML. Novi tok: SCREEN 1 (home/onboarding: razred → 4 kartice izbora →
lekcija/oblast po potrebi) → SCREEN 2 (čist chat sa malim topbar-om). Legacy
/submit markup ostaje skriven. Bez OpenAI/mreže.
"""
import re

MODES = ("explain", "practice", "exam", "quick")


def _html(client):
    r = client.get("/")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _home_block(html):
    """Sadržaj home/onboarding ekrana (SCREEN 1)."""
    start = html.index('id="tutorHome"')
    end = html.index("<!-- /tutor-home -->")
    assert start < end
    return html[start:end]


def _tutor_block(html):
    """Sadržaj chat kartice (SCREEN 2, do markera kraja)."""
    start = html.index('id="tutor-card"')
    end = html.index("<!-- /tutor-card -->")
    assert start < end
    return html[start:end]


def _legacy_block(html):
    """Sadržaj SKRIVENOG legacy dijela."""
    start = html.index('id="advancedLegacy"')
    end = html.index("<!-- /legacy-holder -->")
    assert start < end
    return html[start:end]


def _composer_row(html):
    """Sadržaj composer pilla (.composer) — + dugme | textarea | send strelica."""
    start = html.index('id="tutorComposer"')
    end = html.index("</div>", start)
    return html[start:end]


def test_index_renders(client):
    assert client.get("/").status_code == 200


# --- SCREEN 1: home/onboarding -----------------------------------------------------

def test_home_screen_present_and_chat_hidden_at_start(client):
    html = _html(client)
    home = _home_block(html)
    assert 'class="home-card"' in home
    assert "Koji si razred?" in home
    assert 'id="homeModes"' in home
    # home dolazi prije chat kartice; chat startuje skriven (hidden atribut)
    assert html.index('id="tutorHome"') < html.index('id="tutor-card"')
    assert 'id="tutor-card" hidden' in html


def test_grade_dropdown_only_grade6_enabled(client):
    home = _home_block(_html(client))
    assert 'id="homeGrade"' in home
    assert '<option value="6" selected>6. razred</option>' in home
    for g in ("7", "8", "9"):
        assert f'<option value="{g}" disabled>{g}. razred (uskoro)</option>' in home


def test_four_mode_cards_with_subtitles(client):
    home = _home_block(_html(client))
    cards = re.findall(r'<button[^>]*class="home-mode-card"[^>]*>', home)
    assert len(cards) == 4
    for mode in MODES:
        assert f'data-mode="{mode}"' in home
    for title, sub in (
        ("Objasni mi", "Izaberi lekciju i dobij kratko objašnjenje."),
        ("Vježbaj sa mnom", "Izaberi lekciju i dobij jedan zadatak za vježbu."),
        ("Sutra imam kontrolni", "Izaberi oblast i dobij pripremu za kontrolni."),
        ("Samo rezultat", "Pošalji zadatak ili sliku i dobij kratak odgovor."),
    ):
        assert title in home
        assert sub in home


def test_explain_practice_require_topic_selection(client):
    html = _html(client)
    home = _home_block(html)
    assert 'id="homeTopicSelect"' in home
    assert "Koju lekciju radiš?" in html
    # "Nastavi" bez izabrane lekcije NE ulazi u chat
    assert "Prvo izaberi lekciju." in html
    # explain/practice otvaraju picker umjesto direktnog ulaska
    assert "showPicker(state.mode)" in html


def test_exam_requires_oblast_only(client):
    html = _html(client)
    home = _home_block(html)
    assert 'id="homeOblastSelect"' in home
    assert "Iz koje oblasti je kontrolni?" in html
    assert "Prvo izaberi oblast." in html
    # auto-poruka pri ulasku u exam chat
    assert "Sutra imam kontrolni iz ove oblasti. Pripremi me." in html


def test_quick_path_enters_chat_directly(client):
    html = _html(client)
    idx = html.index("if (state.mode === 'quick')")
    snippet = html[idx:idx + 350]
    # quick: bez picker-a, bez teme/oblasti, odmah u chat bez auto-poruke
    assert "hidePicker()" in snippet
    assert "enterChat('')" in snippet
    assert "Upiši zadatak ili dodaj sliku..." in html


def test_auto_messages_for_topic_modes(client):
    html = _html(client)
    assert "Objasni mi ovu temu." in html
    assert "Daj mi jedan zadatak za vježbu iz ove teme." in html


def test_topics_and_oblasti_loaded_from_backend(client):
    """Ništa hardkodirano: lekcije i oblasti dolaze iz /api/ai-tutor/topics."""
    html = _html(client)
    assert "/api/ai-tutor/topics" in html
    assert "data.grouped" in html
    assert "topicSelect.appendChild(og)" in html
    assert "oblastSelect.appendChild(ob)" in html


def test_topics_load_failure_message(client):
    html = _html(client)
    assert 'id="homeTopicsError"' in html
    assert "Teme trenutno nisu dostupne" in html


# --- SCREEN 2: chat ----------------------------------------------------------------

def test_chat_screen_contains_everything(client):
    block = _tutor_block(_html(client))
    for needle in (
        'id="tutorTopbar"',
        'id="tutor-fallback"',
        'id="tutorChat"',
        'id="tutorTyping"',
        'id="tutorMessage"',
        'id="tutorSend"',
        'id="tutorMeta"',
        'id="tutorComposer"',
    ):
        assert needle in block, f"nedostaje {needle} unutar chat kartice"


def test_topbar_context_and_controls(client):
    block = _tutor_block(_html(client))
    assert 'id="topbarGrade"' in block
    assert 'id="topbarMode"' in block
    assert 'id="topbarTopic"' in block
    assert 'id="tutorChangeBtn"' in block and "Promijeni" in block
    assert 'id="tutorNewBtn"' in block and "Nova konverzacija" in block


def test_change_returns_home_and_keeps_history(client):
    html = _html(client)
    idx = html.index("changeBtn.addEventListener")
    snippet = html[idx:idx + 300]
    assert "showHome()" in snippet
    # Promijeni NE briše historiju (to radi samo Nova konverzacija)
    assert "localStorage.removeItem" not in snippet


def test_new_conversation_clears_state(client):
    html = _html(client)
    idx = html.index("newBtn.addEventListener")
    snippet = html[idx:idx + 800]
    assert "localStorage.removeItem(TKEY)" in snippet
    assert "localStorage.removeItem(LASTTASK_KEY)" in snippet
    assert "interactionPhase = null" in snippet
    assert "clearTutorImage()" in snippet
    assert "showHome()" in snippet


def test_mode_cards_not_inside_chat_card(client):
    """Velike opcije žive SAMO na home ekranu — ne u aktivnom chat pogledu."""
    html = _html(client)
    block = _tutor_block(html)
    assert "home-mode-card" not in block
    # stara mode dugmad i stalni topic selector više ne postoje
    assert 'id="tutorModes"' not in html
    assert 'id="tutorTopic"' not in html
    assert 'data-action="tutor-send"' not in html
    assert "Tema ako znaš (opcionalno):" not in html


def test_navbar_hidden_for_focused_layout(client):
    html = _html(client)
    assert 'id="main-header" aria-hidden="true"' in html
    assert "display:none;align-items:center;justify-content:center;" in html
    assert '<body class="dark tutor-fullscreen">' in html
    # bez velikog naslova/podnaslova u chat kartici
    block = _tutor_block(html)
    assert "AI Tutor" not in block


def test_single_visible_card(client):
    html = _html(client)
    # samo jedna .card (chat); home koristi .home-card; legacy je skriven
    assert html.count('<div class="card') == 1


def test_screen_switch_helpers_present(client):
    html = _html(client)
    assert "#tutorHome[hidden],#tutor-card[hidden]{display:none !important;}" in html
    assert "home.hidden = true" in html and "card.hidden = false" in html
    assert "card.hidden = true" in html and "home.hidden = false" in html


# --- chat ponašanje (payload, practice tok, Enter, renderer) ------------------------

def test_payload_uses_home_state(client):
    html = _html(client)
    assert "session_id: sessionId" in html
    assert "selected_topic: state.topic" in html
    assert "state.mode === 'exam' ? state.oblast : ''" in html
    assert "grade: parseInt(state.grade, 10) || 6" in html


def test_session_id_auto_created(client):
    html = _html(client)
    assert "matbot_session_id" in html
    assert "crypto.randomUUID" in html


def test_practice_followup_state_present(client):
    html = _html(client)
    assert "awaiting_practice_answer" in html      # JS stanje
    assert "answering_practice_task" in html       # interaction_phase u payloadu
    assert "last_tutor_task" in html               # zadnji zadatak se šalje backendu
    assert "matbot_tutor_lasttask_" in html        # localStorage ključ


def test_practice_answer_placeholder_present(client):
    assert "Upiši svoj odgovor na zadatak..." in _html(client)


def test_quick_empty_validation_message_present(client):
    assert "Unesi zadatak za koji želiš samo rezultat." in _html(client)


def test_enter_to_send_markers(client):
    html = _html(client)
    assert "e.key === 'Enter'" in html
    assert "e.shiftKey" in html
    assert "if (!tutorBusy) sendTutorMsg()" in html


def test_send_click_does_not_pass_event_as_message(client):
    html = _html(client)
    assert "sendTutor.addEventListener('click', ()=>{ sendTutorMsg(); });" in html


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


def test_fallback_banner_present(client):
    html = _html(client)
    assert 'id="tutor-fallback"' in html
    assert "Ne mogu automatski prepoznati lekciju." in html
    assert "Pronašao sam više sličnih lekcija." in html


def test_clear_chat_clears_tutor_keys(client):
    html = _html(client)
    assert "k.startsWith('matbot_tutor_history_')" in html
    assert "k.startsWith('matbot_tutor_lasttask_')" in html


# --- slika zadatka u composer-u ------------------------------------------------------

def test_image_upload_ui_inside_tutor_card(client):
    block = _tutor_block(_html(client))
    assert 'id="tutorImage"' in block
    assert 'accept="image/*"' in block
    assert 'id="tutorImageChip"' in block
    assert 'id="tutorImageRemove"' in block
    # dostupnost: + dugme ima aria-label/title
    assert 'aria-label="Dodaj sliku zadatka"' in block


def test_composer_pill_layout(client):
    """Jedan zaobljeni composer pill: [ + ] [ textarea ] [ ↑ ]."""
    html = _html(client)
    assert 'class="composer"' in html and 'id="tutorComposer"' in html
    row = _composer_row(html)
    assert 'class="composer-plus"' in row      # lijevo: + (upload)
    assert 'for="tutorImage"' in row           # + otvara postojeći file input
    assert 'id="tutorMessage"' in row          # sredina: input/textarea
    assert 'placeholder="Upiši pitanje ili zadatak..."' in row
    assert 'class="composer-send"' in row      # desno: send strelica
    assert 'id="tutorSend"' in row
    assert "↑" in row
    # redoslijed: + prije textarea prije send strelice
    assert row.index("composer-plus") < row.index("tutorMessage") < row.index("composer-send")


def test_no_big_send_text_in_composer(client):
    row = _composer_row(_html(client))
    assert "Pošalji tutoru" not in row
    assert ">Pošalji<" not in row


def test_image_send_uses_multipart(client):
    html = _html(client)
    assert "fd.append('payload', JSON.stringify(payload))" in html
    assert "fd.append('image', imgFile, imgFile.name)" in html
    # default poruke po modu za sliku bez teksta
    assert "Daj mi samo rezultat zadatka sa slike." in html
    assert "Objasni mi zadatak sa slike." in html


# --- legacy /submit ostaje skriven ---------------------------------------------------

def test_legacy_form_preserved_but_hidden(client):
    """Legacy markup POSTOJI (JS/backend netaknuti) ali NIJE vidljiv kao drugi bot."""
    html = _html(client)
    legacy = _legacy_block(html)
    assert 'id="ask-form"' in legacy
    assert 'action="/submit"' in legacy
    assert 'id="sendBtn"' in legacy
    assert 'id="slika"' in legacy and 'name="file"' in legacy
    # holder je skriven hidden atributom
    assert 'id="advancedLegacy" class="legacy-holder" hidden' in html
    # nema vidljivog <details>/summary dvojnika
    assert "<details" not in html
