"""Statički ugovor zaglavlja razgovora, potvrde izlaska i composer dugmadi.

templates/index.html se ne izvršava u pytest-u, a CSS se ne izvršava NIGDJE u
ovom repou (ni DOM stub nema layout). Ova provjera zato štiti tri stvari koje
su bile žive greške i koje nijedan drugi test ne bi uhvatio:

  1. `.hidden` nad `.tutor-pill` — dva prazna pilla u zaglavlju. Uzrok je bio
     čisto CSS redoslijed: `.tutor-pill{display:inline-flex}` je deklarisan
     KASNIJE od `.hidden{display:none}`, pa je pri jednakoj specifičnosti
     pobjeđivao on i klasa `hidden` je bila potpuno bez efekta. Ista klasa
     greške kao kod `.exam-card [data-exam-state]` sekcija.
  2. Jedna akcijska grupa u zaglavlju — „Nazad“ se ranije sudarao sa susjednim
     kontrolama jer grupa nije mogla ni da se skupi (`flex:0 0 auto`) ni da se
     prelomi (`flex-wrap` nije bio postavljen).
  3. Markup potvrde izlaska — DOM test (tests/frontend/exit_confirmation.test.js)
     rekonstruiše dijalog u stubu, pa mora postojati provjera da rekonstrukcija
     odgovara stvarnom markupu.
"""
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def _read():
    return INDEX_HTML.read_text(encoding="utf-8")


def _css():
    html = _read()
    return html[html.index("<style>"):html.index("</style>")]


def _topbar_actions(html, marker):
    """Akcijska grupa iz zaglavlja koje sadrži `marker` (chat ili kontrolni)."""
    start = html.index(marker)
    open_at = html.index('<div class="tutor-topbar-actions">', start)
    return html[open_at:html.index("</div>", open_at)]


# --- 1: prazni pillovi ------------------------------------------------------


def test_hidden_beats_pill_display_by_specificity():
    css = _css()
    assert ".tutor-pill.hidden" in css
    # `.home-picker-group{display:grid}` je isti propust na početnom ekranu
    assert ".home-picker-group.hidden" in css
    rule = css[css.index(".tutor-pill.hidden"):]
    assert rule[:rule.index("}")].endswith("display:none;")


def test_long_pill_truncates_with_an_ellipsis_not_a_hard_cut():
    """`text-overflow` djeluje samo na blok kontejner.

    Uz `display:inline-flex` je pill kao flex stavka imao korišteni `display:flex`,
    pa je dugačak naziv lekcije bio TVRDO odsječen — bez „…“.
    """
    # Pravilo, ne komentar: `.tutor-pill{` se u komentaru iznad pojavljuje kao
    # citat starog (pokvarenog) oblika, pa se traži na POČETKU reda.
    css = _css()
    rule = css[css.index("\n  .tutor-pill{"):]
    rule = rule[:rule.index("}")]
    assert "display:inline-block" in rule
    assert "display:inline-flex" not in rule
    for prop in ("white-space:nowrap", "overflow:hidden", "text-overflow:ellipsis"):
        assert prop in rule, prop
    # bez fiksnih širina — granica je uvijek relativna prema roditelju
    assert "max-width:100%" in rule
    assert "width:" not in rule.replace("max-width:", "").replace("min-width:", "")


def test_truncated_pills_keep_their_full_text_available():
    """Skraćivanje je čisto vizuelno: sadržaj elementa ostaje pun (čitači ekrana),
    a `title` isti tekst daje i mišu."""
    html = _read()
    assert "if (ctx) topbarTopic.title = ctx; else topbarTopic.removeAttribute('title');" in html
    assert "examEls.oblast.title = oblastName" in html


def test_empty_pill_can_never_render():
    """Konstrukcijska brava: pill bez teksta se ne crta ni bez klase `hidden`."""
    css = _css()
    assert ".tutor-pill:empty{display:none;}" in css


def test_grade_pill_is_no_longer_hidden_in_result_mode():
    """Razred je jedini stalni orijentir u zaglavlju — vidljiv u SVIM modovima.

    Popravkom CSS-a bi se staro (nikad izvršeno) skrivanje odjednom aktiviralo,
    pa je namjera povučena eksplicitno, a ne prepuštena regresiji.
    """
    html = _read()
    assert "topbarGrade.classList.toggle('hidden'" not in html


# --- 2: jedna akcijska grupa, bez preklapanja -------------------------------


def test_topbar_actions_can_shrink_and_wrap():
    css = _css()
    block = css[css.index(".tutor-topbar-actions{"):]
    block = block[:block.index("}")]
    assert "flex:0 1 auto" in block          # smije se skupiti
    assert "flex-wrap:wrap" in block         # i prelomiti prije nego izađe iz kartice
    assert "align-items:center" in block     # vertikalno centrirano
    assert "gap:" in block


def test_topbar_never_forces_flex_start_on_narrow_screens():
    """Staro `align-items:flex-start` je razbijalo vertikalno poravnanje."""
    assert ".tutor-topbar{align-items:flex-start;}" not in _css()


def test_three_chat_actions_live_in_one_group_with_clear_hierarchy():
    html = _read()
    actions = _topbar_actions(html, 'id="tutorTopbar"')
    assert 'id="tutorClearBtn"' in actions and "topbar-btn--danger" in actions
    assert 'id="tutorBackBtn"' in actions and "topbar-btn--nav" in actions
    assert 'id="tutorExitBtn"' in actions and "topbar-btn--exit" in actions
    # sva tri dijele istu geometriju; razlikuje ih samo modifikator
    assert actions.count("topbar-btn ") == 3


def test_exam_screen_offers_the_same_exit():
    """Bez ovoga bi „Zatvori“ nestao tačno na ekranu kontrolnog."""
    actions = _topbar_actions(_read(), 'class="exam-topbar"')
    assert 'id="examBackBtn"' in actions
    assert 'id="examExitBtn"' in actions


def test_home_screen_offers_the_same_exit():
    """Početni ekran je jedini bez zaglavlja — a učenik tu provodi prvi minut.

    Bez izlaza ovdje, skrivanje Thinkific-ovog dugmeta bi ostavilo učenika bez
    ijedne kontrole za napuštanje aplikacije prije ulaska u razgovor.
    """
    html = _read()
    bar = html[html.index('class="home-topbar"'):]
    bar = bar[:bar.index("</div>\n        <h1")]
    assert 'class="home-brand"' in bar          # marka ostaje, ekran se ne redizajnira
    assert 'id="homeExitBtn"' in bar
    assert "topbar-btn--exit" in bar            # ista klasa = isti izgled i isto skupljanje
    assert 'aria-label="Zatvori MAT-BOT"' in bar


def test_exit_has_exactly_one_implementation_for_all_three_screens():
    html = _read()
    for btn in ("chatExitBtn", "homeExitBtn"):
        assert "%s.addEventListener('click', requestExitMatbot)" % btn in html
    assert "examEls.exit.addEventListener('click', requestExitMatbot)" in html
    # nijedan drugi put ka izlasku: tačno jedan otvarač dijaloga i jedan pošiljalac
    assert html.count("openModal('#confirm-exit')") == 1
    assert html.count("function performExitMatbot()") == 1


def test_narrow_screen_collapses_labels_instead_of_overlapping():
    css = _css()
    narrow = css[css.index("@media (max-width:430px)"):]
    narrow = narrow[:narrow.index("\n  }")]
    assert ".topbar-btn--danger .topbar-btn-label" in narrow
    assert ".topbar-btn--exit .topbar-btn-label" in narrow
    assert "clip-path:inset(50%)" in narrow          # sakriven vizuelno, ne za čitač
    assert "min-width:38px" in narrow                # dodirna zona ostaje


def test_every_topbar_button_keeps_an_accessible_name():
    html = _read()
    for btn_id in ("tutorClearBtn", "tutorBackBtn", "tutorExitBtn",
                   "examBackBtn", "examExitBtn"):
        start = html.index('id="%s"' % btn_id)
        tag = html[html.rindex("<button", 0, start):html.index(">", start)]
        assert "aria-label=" in tag, btn_id
        assert "title=" in tag, btn_id


# --- 3: potvrda izlaska -----------------------------------------------------


def test_exit_dialog_uses_the_existing_modal_system_and_exact_wording():
    html = _read()
    box = html[html.index('id="confirm-exit"'):]
    box = box[:box.index("</div>\n  </div>")]
    assert 'class="modal" id="confirm-exit"' in html
    assert 'role="dialog"' in box and 'aria-modal="true"' in box
    assert 'aria-labelledby="confirmExitTitle"' in box
    assert "Izaći iz MAT-BOT-a?" in box
    assert "Da li ste sigurni da želite izaći iz MAT-BOT-a?" in box
    assert ">Ostani<" in box and ">Izađi<" in box
    # „Ostani“ gasi dijalog kroz isti delegirani mehanizam kao ostale potvrde
    assert 'id="stayInMatbot" data-close="#confirm-exit"' in box
    # početni fokus je na SIGURNOJ radnji — Enter nikad ne izlazi
    assert "data-modal-initial-focus" in box[:box.index(">Izađi<")]


def test_close_button_opens_the_dialog_and_never_exits_directly():
    html = _read()
    assert "chatExitBtn.addEventListener('click', requestExitMatbot)" in html
    assert "function requestExitMatbot(){ openModal('#confirm-exit'); }" in html
    # jedini poziv postMessage je iza potvrde
    assert html.count("postMessage(") == 1
    exit_fn = html[html.index("function performExitMatbot()"):]
    exit_fn = exit_fn[:exit_fn.index("\n    }")]
    assert "if (exitInFlight) return;" in exit_fn      # bez dvostrukog izvršavanja
    assert "'matbot:close'" in exit_fn


def test_escape_closes_a_dialog_without_running_its_action():
    html = _read()
    handler = html[html.index("document.addEventListener('keydown'"):]
    handler = handler[:handler.index("});")]
    assert "e.key !== 'Escape'" in handler
    assert "closeAllModals()" in handler
    assert "performExit" not in handler


def test_modal_focus_returns_to_the_trigger():
    html = _read()
    assert "function restoreModalTrigger()" in html
    assert "data-modal-initial-focus" in html
    open_fn = html[html.index("function openModal(id){"):]
    assert "lastModalTrigger" in open_fn[:open_fn.index("\n}")]


# --- composer: + i kamera su različite radnje -------------------------------


def test_plus_picks_a_file_and_camera_captures_a_new_photo():
    html = _read()
    plus_at = html.index('id="tutorImageBtn"')
    plus = html[html.rindex("<label", 0, plus_at):html.index("</label>", plus_at)]
    cam_at = html.index('id="tutorCameraBtn"')
    camera = html[html.rindex("<label", 0, cam_at):html.index("</label>", cam_at)]
    assert 'for="tutorImage"' in plus and 'aria-label="Dodaj sliku"' in plus
    assert 'for="tutorCameraImage"' in camera
    assert 'aria-label="Fotografiši zadatak"' in camera
    # dva ODVOJENA polja; samo kamerino nosi `capture`
    assert 'id="tutorCameraImage"' in html and 'capture="environment"' in html
    image_input = html[html.index('id="tutorImage"'):]
    assert "capture=" not in image_input[:image_input.index(">")]


def test_both_image_buttons_are_reachable_by_keyboard():
    """<label> nije fokusabilan, a pripadni <input> je display:none."""
    html = _read()
    for btn_id in ("tutorImageBtn", "tutorCameraBtn"):
        tag = html[html.index('id="%s"' % btn_id):]
        tag = tag[:tag.index(">")]
        assert 'tabindex="0"' in tag, btn_id
        assert 'role="button"' in tag, btn_id
    assert "el.getAttribute('aria-disabled') === 'true'" in html   # busy stanje vrijedi i za tastaturu


def test_newest_selection_wins_across_the_two_image_inputs():
    """Izbor iz galerije poslije fotografije je slao staru fotografiju."""
    html = _read()
    fn = html[html.index("function handleTutorImageChange(input){"):]
    fn = fn[:fn.index("\n    }")]
    assert "const other = (input === tutorCameraImg) ? tutorImg : tutorCameraImg;" in fn
    # otkazan dijalog (bez fajla) ne smije skinuti već priložen prilog
    assert "if (!f) { if (!tutorImageFile()) clearTutorImage(); return; }" in fn


def test_composer_controls_share_one_hit_area_and_show_focus():
    css = _css()
    plus = css[css.index(".composer-plus{"):]
    plus = plus[:plus.index("}")]
    assert "width:38px" in plus and "height:38px" in plus
    send = css[css.index(".composer-send{"):]
    send = send[:send.index("}")]
    assert "width:38px" in send and "height:38px" in send
    assert ".composer:focus-within{" in css
    assert ".composer-plus:focus-visible,.composer-send:focus-visible{" in css


def test_empty_state_is_centred_in_the_empty_conversation():
    css = _css()
    rule = css[css.index(".tutor-empty{"):]
    rule = rule[:rule.index("}")]
    assert "margin:auto" in rule
    assert "text-align:center" in rule
