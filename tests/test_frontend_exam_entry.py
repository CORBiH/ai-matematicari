# -*- coding: utf-8 -*-
"""Kontrolni ulazni tok — regresije živog produkcijskog kvara (2026-08-15).

DVA SLOJA ISTOG NALAZA:

(1) FRONTEND: učenik je u produkciji dobijao STARI chat-based kontrolni
    (auto-poruka „Sutra imam kontrolni iz ove oblasti. Pripremi me.“ →
    generički /chat → canned odbijenica + stara dugmad „Daj još zadataka…“).
    Ovi testovi strukturno dokazuju da izvorni frontend TAJ put više nema:
    exam grana Nastavi dugmeta ide na enterExam/​/exam/start i VRAĆA SE prije
    ikakvog enterChat poziva; auto-poruka i stare prečice ne postoje.

(2) DEPLOY: pravi uzrok je bio u samom deploy workflowu — `docker compose
    run` bez `< /dev/null` je POJEO ostatak SSH heredoc skripte, pa se
    `docker compose up -d` od 2026-08-14 više nikad nije izvršio: 30+
    „success“ deployova ostavilo je zamrznuti stari kontejner (healthz
    zelen!). Testovi ispod drže tri prisile koje taj razred kvara čine
    GLASNIM: stdin izolaciju container poziva, tvrdnju checkout==pushed SHA,
    tvrdnju APP_VERSION živog kontejnera i javnu domensku provjeru verzije.

Bez browser automatizacije u repou, ovo je najjači dostupan dokaz na nivou
izvora; ponašanje API sloja (/exam/start → 5 pitanja → /exam/submit) pokriva
tests/test_kontrolni.py, a živi dokaz ide preko produkcijskog domena.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (1) Frontend: jedini kontrolni ulaz je vlastiti ekran + /exam/start
# ---------------------------------------------------------------------------

def _continue_handler_body():
    """Tijelo continueBtn click handlera (od registracije do sljedećeg
    addEventListener) — dovoljno za strukturne tvrdnje o grananju."""
    start = INDEX.index("continueBtn.addEventListener('click'")
    end = INDEX.index("addEventListener", start + 40)
    return INDEX[start:end]


def test_exam_branch_enters_dedicated_screen_and_returns_before_chat():
    body = _continue_handler_body()
    exam_branch_start = body.index("state.mode === 'exam'")
    enter_exam = body.index("enterExam(", exam_branch_start)
    branch_return = body.index("return;", enter_exam)
    # enterChat poslije exam grane pripada explain/practice putu — exam grana
    # se mora ZAVRŠITI (return) prije nego što se do njega uopšte može doći.
    enter_chat = body.index("enterChat(", exam_branch_start)
    assert exam_branch_start < enter_exam < branch_return < enter_chat


def test_no_automatic_exam_chat_message_exists():
    assert "Sutra imam kontrolni iz ove oblasti. Pripremi me." not in INDEX
    auto_block = INDEX[INDEX.index("const AUTO_MESSAGES"):INDEX.index("};", INDEX.index("const AUTO_MESSAGES"))]
    assert "exam" not in auto_block


def test_frontend_calls_dedicated_exam_endpoints():
    assert "/api/ai-tutor/exam/start" in INDEX
    assert "/api/ai-tutor/exam/submit" in INDEX
    assert 'id="exam-card"' in INDEX
    assert "Pitanje ' + (exam.index + 1) + ' od '" in INDEX


def test_old_exam_chat_buttons_are_gone():
    assert "Daj još zadataka" not in INDEX
    assert "Objasni prvi zadatak" not in INDEX
    assert "exam_state" not in INDEX
    # Chip definicije ne smiju imati exam granu — kontrolni nikad nije u chatu.
    chip_zone = INDEX[INDEX.index("function chipDefs"):INDEX.index("function renderChips")]
    assert "mode === 'exam'" not in chip_zone


def test_practice_explain_quick_entry_flows_unchanged():
    body = _continue_handler_body()
    # Explain/Practice i dalje ulaze u chat s auto-porukom.
    assert "enterChat(AUTO_MESSAGES[state.mode] || AUTO_MESSAGES.explain)" in body
    auto_block = INDEX[INDEX.index("const AUTO_MESSAGES"):INDEX.index("};", INDEX.index("const AUTO_MESSAGES"))]
    assert "Objasni mi ovu temu." in auto_block
    assert "Daj mi jedan zadatak za vježbu iz ove teme." in auto_block
    # Quick ulazi u chat bez auto-poruke.
    assert "enterChat('')" in INDEX


# ---------------------------------------------------------------------------
# (1b) Layout ugovor kontrolnog ekrana (živi screenshot 2026-08-16):
#      preklopljena stanja, ugniježđen scroll, previše praznine.
#      GRANICA: ovo nije pregledač — mjeri se CSS ugovor koji kvar čini
#      nemogućim, ne rendering. Ponašanje rukovaoca dokazuje
#      tests/frontend/exam_state_machine.test.js, a piksele živa provjera.
# ---------------------------------------------------------------------------

def _exam_css():
    start = INDEX.index(".exam-card{")
    end = INDEX.index("/* Phase 2: mala preporuka")
    return re.sub(r"/\*[\s\S]*?\*/", "", INDEX[start:end])


def test_exam_sections_are_state_driven_not_attribute_driven():
    css = _exam_css()
    assert ".exam-card [data-exam-state]{display:none;}" in css
    for state in ("generating", "question", "submitting", "result", "error"):
        assert f'.exam-card[data-state="{state}"] [data-exam-state="{state}"]' in css
    # Svaka sekcija u markupu mora nositi svoje stanje — nijedna se ne oslanja
    # na `hidden` (mehanizam koji je u produkciji pao).
    card = INDEX[INDEX.index('id="exam-card"'):INDEX.index("<!-- /exam-card -->")]
    sections = re.findall(r'<div class="exam-section[^"]*" data-exam-state="(\w+)"[^>]*>', card)
    assert sorted(sections) == ["error", "generating", "question", "result", "submitting"]
    # NIJEDNA sekcija stanja ne smije se oslanjati na atribut `hidden` — to je
    # tačno mehanizam koji je u produkciji bio bez efekta. (`examFinishBtn` ga
    # legitimno koristi: to je dugme unutar jednog stanja, ne stanje.)
    for tag in re.findall(r"<div[^>]*data-exam-state=[^>]*>", card):
        assert " hidden" not in tag, tag


def test_exam_has_no_nested_scroll_container():
    css = _exam_css()
    assert "overflow-y:auto" not in css
    # Kartica ide u dokumentni tok; pravilo mora nadjačati `tutor-fullscreen`
    # ljusku (veća specifičnost, ne samo redoslijed).
    assert "body.exam-open.tutor-fullscreen main{height:auto;min-height:100dvh;}" in INDEX
    assert "body.exam-open{overflow:auto;}" in INDEX


def test_exam_covers_every_required_viewport_class():
    css = _exam_css()
    # E (uski mobitel), C (tablet), B (laptop), A (veliki desktop). D (mobitel
    # 390–767) je osnovni, mobile-first sloj bez upita.
    for query in ("@media (max-width:389px)", "@media (min-width:768px)",
                  "@media (min-width:1024px)", "@media (min-width:1440px)"):
        assert query in css, query
    # Dvije kolone tek od laptopa naviše i samo za kratke opcije.
    assert ".exam-options{display:grid;grid-template-columns:1fr;" in css
    assert ".exam-options.exam-options-2col{grid-template-columns:1fr 1fr;}" in css
    assert "EXAM_TWO_COL_MAX_CHARS" in INDEX


def test_exam_cannot_overflow_horizontally():
    css = _exam_css()
    for rule in (".exam-qtext", ".exam-option-text", ".exam-options", ".exam-section"):
        block = css[css.index(rule + "{"):css.index("}", css.index(rule + "{"))]
        assert "min-width:0" in block or "overflow-wrap:anywhere" in block, rule
    # Duga formula smije kliziti SAMA, stranica nikad.
    assert 'mjx-container[display="true"]{overflow-x:auto' in css
    assert "flex-wrap:wrap" in css[css.index(".exam-topbar{"):]


def test_exam_touch_targets_and_focus_states():
    css = _exam_css()
    assert "min-height:52px" in css                      # opcija
    assert ".exam-next .btn{min-height:44px" in css       # akcije rezultata
    assert ".exam-option:focus-visible{outline:3px solid" in css
    # Onemogućeno dugme mora ostati čitljivo (ne skoro nevidljivo).
    disabled = css[css.index(".exam-next .btn:disabled"):]
    opacity = float(re.search(r"opacity:([\d.]+)", disabled).group(1))
    assert opacity >= 0.45, opacity
    # Izbor se ne oslanja samo na boju: ima i prsten i istaknuto slovo opcije.
    assert ".exam-option.selected{" in css and "box-shadow:0 0 0 2px" in css
    assert ".exam-option.selected .exam-option-key{" in css


def test_result_screen_is_compact_and_conditional():
    # Prazna sekcija „ponovi“ se ne prikazuje za 5/5 (uslovno u JS-u).
    assert "if (rec.lessons && rec.lessons.length){" in INDEX
    assert "Ponovi ove lekcije" in INDEX
    assert 'examEls.score.textContent = j.score' in INDEX
    assert 'examEls.scorePct.textContent = j.percentage' in INDEX
    # Rezultat ne smije ostati u ogromnoj kutiji: kompaktan padding.
    css = _exam_css()
    recommend = css[css.index(".exam-recommend{"):css.index("}", css.index(".exam-recommend{"))]
    assert "padding:.65rem .85rem" in recommend


def test_loading_copy_does_not_promise_a_minute():
    assert "Ovo može potrajati do minute" not in INDEX
    assert "Ovo obično traje nekoliko sekundi." in INDEX


# ---------------------------------------------------------------------------
# (2) Deploy workflow: tihi no-op deploy mora biti nemoguć
# ---------------------------------------------------------------------------

def test_every_container_invocation_in_ssh_script_isolates_stdin():
    """`docker compose run`/`exec` unutar SSH heredoca MORA imati `< /dev/null`
    — bez toga docker klijent pojede ostatak skripte (izmjereni uzrok 30+
    tihih no-op deployova)."""
    for line in WORKFLOW.splitlines():
        stripped = line.strip()
        if stripped.startswith("docker compose run") or stripped.startswith("docker compose exec"):
            # Komanda može biti prelomljena u više redova — provjeri logički red.
            logical = stripped
            if logical.endswith("\\"):
                index = WORKFLOW.splitlines().index(line)
                logical = " ".join(
                    l.strip().rstrip("\\") for l in WORKFLOW.splitlines()[index:index + 3])
            assert "< /dev/null" in logical, f"stdin nije izolovan: {stripped}"


def test_deploy_asserts_checkout_matches_pushed_sha():
    assert 'git rev-parse HEAD)" != "${{ github.sha }}"' in WORKFLOW
    assert "DEPLOY REFUSED" in WORKFLOW


def test_deploy_asserts_running_container_version():
    assert "printenv APP_VERSION" in WORKFLOW
    assert 'RUNNING_VERSION" != "$VERSION"' in WORKFLOW


def test_deploy_verifies_public_domain_version():
    assert "Verify public domain serves this deploy" in WORKFLOW
    assert "bot.matematicari.com/healthz" in WORKFLOW


# ---------------------------------------------------------------------------
# healthz nosi identitet deploya
# ---------------------------------------------------------------------------

def test_healthz_reports_app_version(client, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "abc1234")
    for path in ("/healthz", "/_healthz"):
        payload = client.get(path).get_json()
        assert payload["ok"] is True
        assert payload["version"] == "abc1234"
