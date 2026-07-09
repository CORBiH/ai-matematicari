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


def test_grade_dropdown_grades_6_to_9_enabled(client):
    home = _home_block(_html(client))
    assert 'id="homeGrade"' in home
    assert '<option value="6" selected>6. razred</option>' in home
    assert '<option value="7">7. razred</option>' in home
    assert '<option value="8">8. razred</option>' in home
    assert '<option value="9">9. razred</option>' in home
    # nijedan razred nije više zaključan ("uskoro")
    assert "uskoro" not in home


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
    assert 'id="homeOblastSelect"' in home
    assert 'id="homeTopicSelect"' in home
    assert 'id="homeTopicGroup"' in home
    assert html.index('id="homeOblastSelect"') < html.index('id="homeTopicSelect"')
    # "Nastavi" bez izabrane lekcije NE ulazi u chat
    assert "Prvo izaberi oblast." in html
    assert "Prvo izaberi lekciju." in html
    # explain/practice otvaraju picker umjesto direktnog ulaska
    assert "showPicker(state.mode)" in html


def test_exam_requires_oblast_only(client):
    html = _html(client)
    home = _home_block(html)
    assert 'id="homeOblastSelect"' in home
    assert "Izaberi oblast za kontrolni" in html
    assert "Prvo izaberi oblast." in html
    assert "kontrolni je iz cijele oblasti" in html
    # auto-poruka pri ulasku u exam chat
    assert "Sutra imam kontrolni iz ove oblasti. Pripremi me." in html


def test_quick_path_enters_chat_directly(client):
    html = _html(client)
    idx = html.index("if (state.mode === 'quick')")
    snippet = html[idx:idx + 650]
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
    assert "grade=' + encodeURIComponent(grade)" in html
    assert "gradeSel.addEventListener('change'" in html
    assert "loadTopicsForGrade(state.grade)" in html
    assert "data.grouped" in html
    assert "data.oblast_order" in html
    assert "oblastOrderCache = oblastOrder.slice()" in html
    assert "topicsByOblast[oblast] = (grouped[oblast] || []).slice()" in html
    assert "function populateLessonOptions(oblast)" in html
    assert "topicSelect.appendChild(opt)" in html
    assert "oblastSelect.appendChild(ob)" in html


def test_lesson_selector_updates_after_oblast_selection(client):
    html = _html(client)
    assert "oblastSelect.addEventListener('change'" in html
    idx = html.index("oblastSelect.addEventListener('change'")
    snippet = html[idx:idx + 520]
    assert "populateLessonOptions(ob)" in snippet
    assert "resetLessonOptions('— kontrolni je iz cijele oblasti —')" in snippet
    pidx = html.index("function populateLessonOptions(oblast)")
    psnippet = html[pidx:pidx + 900]
    assert "const lessons = topicsByOblast[oblast] || []" in psnippet
    assert "topicSelect.disabled = lessons.length === 0" in psnippet
    assert "topicGroup.classList.toggle('hidden', lessons.length === 0)" in psnippet


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


def test_topbar_has_single_visible_nazad_action(client):
    """Chat topbar ima jednu jasnu akciju: Nazad."""
    html = _html(client)
    block = _tutor_block(html)
    assert 'id="topbarGrade"' in block
    assert 'id="topbarMode"' in block
    assert 'id="topbarTopic"' in block
    assert 'id="tutorBackBtn"' in block
    assert 'class="btn sm tutor-back-btn"' in block
    assert block.count(">Nazad<") == 1
    assert 'id="tutorChangeBtn"' not in block
    assert 'id="tutorNewBtn"' not in block
    assert "Promijeni" not in block
    assert "Nova konverzacija" not in block


def test_internal_fresh_conversation_reset_still_available(client):
    """Interni reset briše historiju, zadnji zadatak, kontekst i transcript."""
    html = _html(client)
    idx = html.index("function startFreshTutorConversation()")
    snippet = html[idx:idx + 300]
    assert "resetTutorConversation()" in snippet
    assert "localStorage.removeItem(CTX_KEY)" in snippet
    assert "showHome()" in snippet
    ridx = html.index("function resetTutorConversation()")
    rsnippet = html[ridx:ridx + 900]
    assert "localStorage.removeItem(TKEY)" in rsnippet
    assert "localStorage.removeItem(LASTTASK_KEY)" in rsnippet
    assert "interactionPhase = null" in rsnippet
    assert "lastTutorMessage = ''" in rsnippet
    assert "clearNextState()" in rsnippet
    assert "querySelectorAll('.tmsg, .tutor-video-hint')" in rsnippet  # briše transcript + hintove
    assert "hideChips()" in rsnippet                        # i quick-reply chips


def test_history_scoped_by_context(client):
    """Phase 1 (audit): promjena razred/mod/tema/oblast briše staru historiju;
    isti kontekst poslije reload-a iscrtava postojeću (bez nevidljive memorije)."""
    html = _html(client)
    assert "matbot_tutor_ctx_" in html
    assert "function tutorContextKey()" in html
    assert "[state.grade, state.mode, state.topic || '', state.oblast || ''].join('|')" in html
    idx = html.index("function enterChat(autoMessage)")
    snippet = html[idx:idx + 1200]
    assert "prevCtx !== ctx" in snippet
    assert "resetTutorConversation()" in snippet
    assert "replayTutorHistory()" in snippet
    assert "if (autoMessage && !resuming)" in snippet       # bez duple auto-poruke


def test_fetch_timeout_with_friendly_message(client):
    """Phase 1 (audit): AbortController prekida zahtjev na 60s + poruka djetetu."""
    html = _html(client)
    assert "new AbortController()" in html
    assert "60000" in html
    assert "signal: ac.signal" in html
    assert "Odgovor traje duže nego obično" in html


def test_plotly_not_loaded_upfront(client):
    """Phase 1 (audit): Plotly (~3.5MB) se ne učitava unaprijed — lijeno tek
    kad legacy graf tok stvarno zatraži crtanje."""
    html = _html(client)
    assert '<script src="https://cdn.plot.ly' not in html
    assert "function ensurePlotly()" in html
    assert "ensurePlotly().then" in html
    # MathJax OSTAJE učitan unaprijed (formule su u svakom odgovoru)
    assert "mathjax@3/es5/tex-mml-chtml.js" in html


def test_nazad_resets_practice_state_keeps_history(client):
    """Nazad: vraća na izbor, čisti fazu/zadnji zadatak/sliku/placeholder,
    ali NE briše historiju razgovora (TKEY ostaje)."""
    html = _html(client)
    idx = html.index("chatBackBtn.addEventListener")
    snippet = html[idx:idx + 500]
    assert "showHome()" in snippet
    assert "interactionPhase = null" in snippet
    assert "localStorage.removeItem(LASTTASK_KEY)" in snippet
    assert "clearTutorImage()" in snippet
    assert "DEFAULT_PLACEHOLDER" in snippet
    assert "localStorage.removeItem(TKEY)" not in snippet   # historija OSTAJE


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
    # Result/Quick mod: tema/lekcija se NE šalju kao ograničenje (kontekst-slobodno).
    assert "const resultMode = (state.mode === 'quick');" in html
    assert "selected_topic: resultMode ? '' : state.topic" in html
    assert "entry_source: resultMode ? 'free_chat'" in html
    assert "state.mode === 'exam' ? state.oblast : ''" in html
    assert "grade: parseInt(state.grade, 10) || 6" in html


def test_result_mode_hides_grade_and_topic_pills(client):
    """Result/Quick mod: topbar skriva razred i temu, ostaje samo mod."""
    html = _html(client)
    assert "topbarGrade.classList.toggle('hidden', resultMode);" in html
    assert "const ctx = resultMode ? '' : (state.topicName || state.oblast || '');" in html
    # donji status: u result modu samo "Režim", nikad "Tema: ..."
    assert "if (resultMode){                         // kontekst-slobodno: samo režim" in html


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
    assert "matbot_tutor_nextstate_" in html       # explicit next-turn state contract


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
    assert "k.startsWith('matbot_tutor_lasttopic_')" in html
    assert "k.startsWith('matbot_tutor_lastimagectx_')" in html
    assert "k.startsWith('matbot_tutor_nextstate_')" in html


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
    assert 'class="composer-plus-icon"' in row
    assert 'for="tutorImage"' in row           # + otvara postojeći file input
    assert 'id="tutorMessage"' in row          # sredina: input/textarea
    assert 'placeholder="Upiši pitanje ili zadatak..."' in row
    assert 'class="composer-send"' in row      # desno: send strelica
    assert 'class="composer-send-icon"' in row
    assert 'id="tutorSend"' in row
    assert "↑" in row
    # redoslijed: + prije textarea prije send strelice
    assert row.index("composer-plus") < row.index("tutorMessage") < row.index("composer-send")


def test_composer_and_close_button_polish_styles(client):
    html = _html(client)
    assert ".composer{display:flex;align-items:center;" in html
    assert ".composer-plus{flex:0 0 auto;display:grid;place-items:center;" in html
    assert ".composer-send{flex:0 0 auto;display:grid;place-items:center;" in html
    assert ".composer-plus-icon,.composer-send-icon" in html
    assert ".modal-close" in html
    assert 'class="modal-close" data-close="#confirm-clear"' in html
    assert 'class="modal-close" data-close="#confirm-grade"' in html
    assert ".file-chip .remove{display:grid;place-items:center;width:28px;height:28px;" in html


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


def test_image_context_persisted_for_followups(client):
    html = _html(client)
    assert "const LASTIMAGECTX_KEY = 'matbot_tutor_lastimagectx_'" in html
    assert "function storedLastImageContext()" in html
    assert "function isImageFollowupMessage(t)" in html
    assert "payload.last_image_context = savedImageContext.slice(0, 2000)" in html
    assert "if (j.image_context) setLastImageContext(j.image_context)" in html
    assert "localStorage.removeItem(LASTIMAGECTX_KEY)" in html


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


# --- Phase 7.1: kompaktan renderer + CSS ---------------------------------------------

def test_renderer_compacts_short_display_math(client):
    r"""Kratki jednoredni $$...$$ se pretvara u inline \(...\) prije MathJax-a."""
    html = _html(client)
    assert r"\$\$([^$\n]{1,60}?)\$\$" in html      # regex za kratke display blokove
    assert "e.length > 40" in html                 # dugi blokovi ostaju display
    assert r"'\\begin'" in html                    # \begin okruženja ostaju display
    assert r"'\\(' + e + '\\)'" in html            # konverzija u inline \(...\)


def test_renderer_merges_interrupted_ordered_lists(client):
    """Prazan red između numerisanih stavki NE prekida <ol> (nema ponovljenih '1.')."""
    html = _html(client)
    assert "(!t && list)" in html


def test_renderer_collapses_blank_lines_aggressively(client):
    html = _html(client)
    assert "(<br>){2,}" in html                    # prazni redovi → jedan <br>
    assert "(<(?:ul|ol)>)" in html                 # bez <br> viška prije listi


def test_bubble_spacing_compact(client):
    html = _html(client)
    assert ".tbubble mjx-container{margin:.15rem 0 !important;}" in html
    assert ".tbubble ul,.tbubble ol{margin:.25rem 0 .25rem 1.15rem;padding:0;}" in html
    assert ".tbubble li{margin:.1rem 0;}" in html


# --- Phase 7.2: follow-up nastavak + robusnije practice stanje ----------------------

def test_followup_detection_present(client):
    """JS prepoznaje kratke potvrde ("može", "hocu", "nastavi") kao nastavak."""
    html = _html(client)
    assert "function isFollowupMessage(t)" in html
    for token in ("moze|može", "hocu|hoću", "nastavi|dalje", "jos|još", "primjer"):
        assert token in html
    # detekcija se koristi samo za upisane poruke, uz postojanje zadnje poruke
    assert "lastTutorMessage && isFollowupMessage(typed)" in html


def test_followup_payload_fields(client):
    html = _html(client)
    assert "'continuing_explanation'" in html
    assert "payload.last_tutor_message = lastTutorMessage.slice(0, 600)" in html
    assert "if (j && j.next_state) setNextState(j.next_state)" in html
    # zadnja poruka tutora se pamti nakon ready odgovora
    assert "lastTutorMessage = (answer || '').slice(0, 600)" in html


def test_short_answer_with_stored_task_becomes_practice_answer(client):
    html = _html(client)
    assert "function storedLastTask()" in html
    assert "function isShortPracticeAnswer(t)" in html
    assert "function isNewPracticeTaskRequest(t)" in html
    assert "savedTask && isShortPracticeAnswer(typed)" in html
    assert "!asksNewTask && (interactionPhase === 'awaiting_practice_answer'" in html
    assert "payload.mode = 'practice'" in html
    assert "payload.last_tutor_task = savedTask.slice(0, 600)" in html
    assert "ne kontam|ne razumijem|pomozi|pomoc|hint|daj hint|objasni" in html
    assert "jos\\s+jedan|sljedeci|slican" in html


def test_detected_topic_adopted_for_next_payload(client):
    html = _html(client)
    assert "const LASTTOPIC_KEY = 'matbot_tutor_lasttopic_'" in html
    assert "function adoptResponseTopic(j)" in html
    assert "state.topic = tid" in html
    assert "state.topicSource = 'detected_topic'" in html
    assert "if (j.status === 'ready') adoptResponseTopic(j)" in html
    assert "topicOblasti[t.topic] = oblast" in html


def test_tutor_task_detection_updates_last_task(client):
    html = _html(client)
    assert "function looksLikeTutorTask(t)" in html
    assert "function extractTutorTask(j, answer)" in html
    assert "function structuredTutorTask(j)" in html
    assert "last_tutor_task', 'practice_task', 'task_text" in html
    assert "uporedi|usporedi|poredi|odredi" in html
    assert "prethodnik|sljedbenik" in html
    assert "setAwaitingPracticeTask(taskText)" in html
    assert "clearAwaitingPracticeTask()" in html
    assert "restoreActiveTopic()" in html
    assert "storedLastTask()" in html


def test_tutor_task_saved_before_history_for_stream_and_json(client):
    html = _html(client)
    idx = html.index("function applyTutorResponse")
    snippet = html[idx:idx + 1200]
    # image_test tok NE pretvara prozu odgovora u last_tutor_task
    assert "const taskText = (j && j.status === 'ready' && !inImageTest) ? extractTutorTask(j, answer) : ''" in snippet
    assert "j.next_state.active_task_kind === 'image_test'" in snippet
    assert snippet.index("setAwaitingPracticeTask(taskText)") < snippet.index("pushTutor('assistant', answer)")
    assert "sačuvaj zadatak prije historije" in snippet


def test_practice_new_task_request_bypasses_answer_phase(client):
    html = _html(client)
    idx = html.index("const asksNewTask = isNewPracticeTaskRequest(typed);")
    snippet = html[idx:idx + 1200]
    assert "!asksNewTask &&" in snippet
    assert "answering_practice_task" in snippet
    assert "!asksNewTask && lastTutorMessage && isFollowupMessage(typed)" in snippet
    assert "Daj mi još jedan zadatak." in html


def test_next_state_confirmation_takes_precedence_over_practice_answer(client):
    """Explicit continue_confirmation state wins before short answer heuristics."""
    html = _html(client)
    assert "function storedNextState()" in html
    assert "payload.previous_next_state = previousNextState" in html
    assert "payload.intent = confirmationIntent" in html
    assert "'continue_confirmation'" in html
    assert "'confirmation_declined'" in html
    assert "function isAffirmativeConfirmation(t)" in html
    assert "function isNegativeConfirmation(t)" in html
    state_idx = html.index("previousNextState && previousNextState.expected_user_action === 'continue_confirmation'")
    idx = html.index("interactionPhase === 'awaiting_practice_answer'", state_idx)
    followup_idx = html.index("lastTutorMessage && isFollowupMessage(typed)", idx)
    assert state_idx < idx < followup_idx


def test_mode_topic_oblast_change_resets_practice_state(client):
    html = _html(client)
    # klik na mode karticu resetuje fazu
    idx = html.index("modeGrid.querySelectorAll('.home-mode-card').forEach(btn=>{")
    snippet = html[idx:idx + 700]
    assert "interactionPhase = null" in snippet
    # izbor lekcije, oblasti i quick ulaz čiste zadnji zadatak
    assert html.count("localStorage.removeItem(LASTTASK_KEY)") >= 4


def test_answer_send_does_not_clear_last_task_upfront(client):
    """Slanje odgovora NE briše zadnji zadatak; ažurira se tek nakon feedbacka."""
    html = _html(client)
    idx = html.index("if (answerPhase){")
    snippet = html[idx:idx + 400]
    assert "localStorage.removeItem" not in snippet
    assert "payload.last_tutor_task = savedTask.slice(0, 600)" in snippet


# --- Phase 2: streaming + chips + video hint + token -----------------------------

def test_streaming_client_present_with_json_fallback(client):
    """SSE klijent postoji; pad streama pada nazad na non-streaming JSON put."""
    html = _html(client)
    assert "/api/ai-tutor/chat/stream" in html
    assert "function streamTutorRequest(payload, ac)" in html
    assert "text/event-stream" in html
    assert "r.body.getReader()" in html
    # fallback grana: bez streama → jsonTutorRequest
    assert "jsonTutorRequest(payload, ac, imgFile)" in html
    # MathJax se tipografiše TEK na kraju (finalni render jednom, ne po tokenu)
    idx = html.index("function streamTutorRequest")
    snippet = html[idx:html.index("function applyTutorResponse")]
    assert snippet.count("MathJax.typesetPromise([made.bubble])") == 1
    assert "made.bubble.textContent = acc" in snippet       # progresivni tekst bez HTML-a


def test_streaming_not_used_for_images(client):
    html = _html(client)
    idx = html.index("if (!imgFile){")
    snippet = html[idx:idx + 400]
    assert "streamTutorRequest" in snippet                  # stream SAMO bez slike


def test_quick_reply_chips_present(client):
    html = _html(client)
    block = _tutor_block(html)
    assert 'id="tutorChips"' in block
    assert "function chipDefs(j)" in html
    assert "function renderChips(j)" in html
    # dječiji, korisni chipovi
    for label in ("Daj mi zadatak", "Objasni jednostavnije", "Još jedan primjer",
                  "Ne znam — daj mi hint", "Objasni postupak"):
        assert label in html, label
    # chip ide kroz NORMALNI typed tok (poštuje practice/followup logiku)
    assert "msgBox.value = c.msg;" in html


def test_video_hint_present_and_guarded(client):
    html = _html(client)
    assert "function maybeVideoHint(j)" in html
    assert "recommend_video" in html
    assert "Možda ti pomogne da prvo pogledaš video lekciju" in html
    assert "videoHintShown.has(tid)" in html                # samo JEDNOM po temi
    assert "tid === 'unknown'" in html                      # bez izmišljenih preporuka


def test_embed_token_meta_and_header(client):
    html = _html(client)
    assert 'name="matbot-embed-token"' in html
    assert "function tutorHeaders(extra)" in html
    assert "h['X-Tutor-Token'] = EMBED_TOKEN" in html


# --- Audit: auto-scroll, chips bez duplikata, anti-ponavljanje zadataka -----------

def test_autoscroll_after_mathjax_typeset(client):
    """Scroll na dno se ponavlja NAKON MathJax typeseta — typeset je asinhron i
    naknadno mijenja visinu poruke (bez ovoga odgovor ostane van ekrana)."""
    html = _html(client)
    assert "function scrollTutorToBottom()" in html
    # i za obicne poruke i za finalni render streama
    assert html.count(".then(scrollTutorToBottom)") >= 2


def test_repeat_task_chip_removed_new_task_chip_present(client):
    """"Ponovi zadatak" chip je uklonjen (dupliralo je obican chat); tokom
    cekanja odgovora ostaju hint + novi zadatak."""
    html = _html(client)
    assert "Ponovi zadatak" not in html
    assert "Ne znam — daj mi hint" in html
    assert "Novi zadatak" in html
    assert "Daj mi novi zadatak." in html       # ide kroz isNewPracticeTaskRequest


def test_recent_tasks_tracked_and_sent(client):
    """Zadnji dati zadaci se pamte lokalno i salju backendu (recent_tasks) —
    zastita od ponavljanja istih zadataka."""
    html = _html(client)
    assert "matbot_tutor_recent_" in html
    assert "function storedRecentTasks()" in html
    assert "function pushRecentTask(t)" in html
    assert "payload.recent_tasks = recentTasks" in html
    # pamti se tek kad odgovor stvarno sadrzi zadatak
    idx = html.index("function applyTutorResponse")
    snippet = html[idx:idx + 900]
    assert "pushRecentTask(taskText)" in snippet
    # cisti se i pri resetu konverzacije i u "Ocisti chat"
    ridx = html.index("function resetTutorConversation()")
    assert "RECENTTASKS_KEY" in html[ridx:ridx + 900]
    didx = html.index("doClear")
    assert "matbot_tutor_recent_" in html[didx:html.index("promjena razreda kroz meni")]


# --- image_test tok: perzistencija konteksta + state-driven rutiranje -------------

def test_image_test_frontend_contract(client):
    html = _html(client)
    # kontekst slike se salje i uz potvrde i dok je image_test aktivan
    assert "previousNextState.active_task_kind === 'image_test'" in html
    assert "isImageFollowupMessage(text) || confirmationIntent || imageTestActive" in html
    # prosireni follow-up rjecnik hvata "uradi mi korak po korak", "nastavi", "sve"
    assert r"korak|nastav\w*|dalje|sljedec\w*|sve" in html
    # greska bez statusa NE brise sacuvano stanje (next_state ostaje)
    assert "else if (j && j.status) clearNextState()" in html


def test_transition_text_never_becomes_task_frontend(client):
    html = _html(client)
    assert "function looksLikeTransitionText(t)" in html
    assert "if (looksLikeTransitionText(s)) return false;" in html
    # tokom image_test toka proza odgovora se ne pretvara u last_tutor_task
    assert "const inImageTest = !!(j && j.next_state && j.next_state.active_task_kind === 'image_test')" in html
    assert "if (inImageTest) clearAwaitingPracticeTask();" in html


def test_nastavi_is_not_new_task_request(client):
    html = _html(client)
    idx = html.index("function isNewPracticeTaskRequest(t)")
    snippet = html[idx:idx + 700]
    assert "|objasni|nastavi)" in snippet
