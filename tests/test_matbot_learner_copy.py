"""Učeniku vidljiva kopija MAT-BOT-a: „Mala pomoć" i mala napomena.

DVIJE SITNE IZMJENE, I NIŠTA VIŠE. Ovaj fajl postoji da se kasnije ne bi
pomiješalo šta je promijenjeno:

  1. LABELA koju učenik vidi je „Mala pomoć" umjesto „Daj mi hint". Poruka koja
     se šalje (`Ne znam.`) i namjera (`hint_request`) ostaju BAJT U BAJT iste —
     mijenja se natpis, ne ponašanje. Interni pojam „hint" (`hint_request`,
     `hint_level`, rute, funkcije) NAMJERNO ostaje u kodu: preimenovanje
     tehničkih naziva zbog terminologije bi bilo mijenjanje ugovora, ne kopije.

  2. NAPOMENA ispod composera: „MAT-BOT može pogriješiti. Provjeri važne
     informacije." Statična je i pasivna — ne ulazi u historiju razgovora, ne
     šalje se modelu, ne traži potvrdu i ne pravi nijedan zahtjev prema serveru.

GRANICA: napomena pripada SAMO učeničkom sučelju. Ni administratorska stranica,
ni PDF, ni izvještaj roditelju, ni prompt je ne smiju vidjeti.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "templates" / "index.html"

VISIBLE_LABEL = "Mala pomoć"
OLD_LABEL = "Daj mi hint"
DISCLAIMER = "MAT-BOT može pogriješiti. Provjeri važne informacije."


@pytest.fixture(scope="module")
def html():
    return INDEX.read_text(encoding="utf-8")


# ===========================================================================
# 1-5) LABELA POMOĆI
# ===========================================================================
def test_1_the_learner_facing_label_is_exactly_mala_pomoc(html):
    assert "'🙋 %s'" % VISIBLE_LABEL in html


def test_2_the_old_label_is_no_longer_rendered(html):
    assert OLD_LABEL not in html


def test_3_clicking_it_sends_the_unchanged_payload(html):
    """Natpis je nov, ugovor je isti: ista poruka, ista namjera."""
    chip = [line for line in html.splitlines() if VISIBLE_LABEL in line][0]
    assert "msg: 'Ne znam.'" in chip
    assert "intent: 'hint_request'" in chip


def test_4_no_api_payload_field_changed(html):
    """`hint_request` je i dalje jedina namjera pomoći koju klijent šalje."""
    assert html.count("'hint_request'") >= 3
    for forbidden in ("'mala_pomoc'", '"mala_pomoc"', "'small_help'"):
        assert forbidden not in html, forbidden


def test_5_hint_progression_is_untouched():
    """Ljestvica pomoći živi na serveru i ovaj commit je nije dodirnuo."""
    from matbot import hint_policy

    assert hasattr(hint_policy, "session_task_class")
    source = (ROOT / "matbot" / "hint_policy.py").read_text(encoding="utf-8")
    assert VISIBLE_LABEL not in source, "kopija sučelja je procurila u politiku"


def test_5b_the_internal_vocabulary_is_deliberately_unchanged(html):
    """Tehnički „hint" ostaje: preimenovanje bi mijenjalo ugovor, ne kopiju."""
    assert "hint_request" in html
    assert "hintPhase" in html or "hint" in html


# ===========================================================================
# 6-12) NAPOMENA
# ===========================================================================
def test_6_the_exact_disclaimer_text_is_present(html):
    assert DISCLAIMER in html


def test_7_the_disclaimer_appears_exactly_once(html):
    assert html.count(DISCLAIMER) == 1


def test_8_the_disclaimer_sits_under_the_composer(html):
    """Ispod unosa, unutar učeničke kartice — ne kao traka ni dijalog."""
    composer_at = html.index('id="tutorComposer"')
    disclaimer_at = html.index(DISCLAIMER)
    assert composer_at < disclaimer_at
    block = html[composer_at:disclaimer_at]
    assert 'id="tutorMeta"' in block, "napomena nije odmah ispod composera"
    # Nije toast/dijalog/banner i ne traži potvrdu.
    line = [l for l in html.splitlines() if DISCLAIMER in l][0]
    for forbidden in ("role=\"alert\"", "role=\"dialog\"", "banner", "toast",
                      "onclick", "aria-modal"):
        assert forbidden not in line, forbidden


def test_9_the_disclaimer_is_static_markup_not_a_message(html):
    """Ne ubacuje se u razgovor: nije u `tutor-chat`, nije mjehurić."""
    line = [l for l in html.splitlines() if DISCLAIMER in l][0]
    assert line.strip().startswith("<p class=\"tutor-disclaimer\"")
    assert "tbubble" not in line and "tmsg" not in line
    # Nijedan JS ga ne dodaje u historiju.
    assert "pushMessage(%r" % DISCLAIMER not in html
    assert "history.push" not in html.split(DISCLAIMER)[0][-400:]


def test_10_the_disclaimer_is_never_sent_to_the_model_or_api(html):
    """Statični tekst nije ni u jednom payloadu."""
    for marker in ("body: JSON.stringify", "fetch("):
        assert marker in html          # postoji transport...
    sends = re.findall(r"JSON\.stringify\((\{[^;]{0,400})", html)
    for payload in sends:
        assert DISCLAIMER not in payload
    # Ni na serverskoj strani.
    for module in ("report_prompt.py", "prompts.py", "rules.py", "api.py"):
        text = (ROOT / "matbot" / module).read_text(encoding="utf-8")
        assert DISCLAIMER not in text, module


def test_11_and_12_it_stays_readable_on_mobile_and_desktop(html):
    """Prigušena, mala, ali ne ispod čitljive granice — na oba rasporeda."""
    desktop = re.search(r"\.tutor-disclaimer\{([^}]*)\}", html).group(1)
    assert "color:var(--muted)" in desktop
    assert "text-align:center" in desktop
    desktop_size = float(re.search(r"font-size:\.(\d+)rem", desktop).group(1))
    assert 70 <= desktop_size <= 90, "prevelika ili presitna na desktopu"

    mobile_block = html[html.index("@media"):]
    mobile = re.search(r"\.tutor-disclaimer\{([^}]*)\}", mobile_block).group(1)
    mobile_size = float(re.search(r"font-size:\.(\d+)rem", mobile).group(1))
    assert mobile_size >= 68, "ispod čitljive granice na mobitelu"
    # Ne preklapa dugmad: statičan blok u toku stranice, bez apsolutnog polozaja.
    assert "position:absolute" not in desktop and "position:fixed" not in desktop


# ===========================================================================
# 13-14) GRANICE
# ===========================================================================
def test_13_no_admin_page_or_report_carries_the_disclaimer():
    for path in sorted((ROOT / "templates").glob("admin_*.html")):
        assert DISCLAIMER not in path.read_text(encoding="utf-8"), path.name
        assert VISIBLE_LABEL not in path.read_text(encoding="utf-8"), path.name
    for module in ("report_pdf.py", "report_facts.py", "parent_report.py",
                   "report_validation.py"):
        text = (ROOT / "matbot" / module).read_text(encoding="utf-8")
        assert DISCLAIMER not in text, module


def test_14_rendering_introduces_no_backend_call(html):
    """Napomena je čist markup: nema `fetch`, nema slušača, nema stanja."""
    line = [l for l in html.splitlines() if DISCLAIMER in l][0]
    for forbidden in ("fetch(", "addEventListener", "XMLHttpRequest",
                      "localStorage", "sendBeacon"):
        assert forbidden not in line, forbidden
    assert "tutorDisclaimer" not in html.split("</style>")[1].split("<script")[-1] \
        or "getElementById('tutorDisclaimer')" not in html


def test_14b_the_page_still_serves_and_shows_both_strings(client):
    """Stvarno iscrtavanje, ne samo predložak."""
    page = client.get("/")
    assert page.status_code == 200
    body = page.data.decode("utf-8")
    assert VISIBLE_LABEL in body
    assert DISCLAIMER in body
    assert OLD_LABEL not in body
