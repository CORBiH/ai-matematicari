"""Finding A: kanonske lekcije o kružnici/krugu moraju dobiti autoritativnu
konvenciju $R=2r$ (matbot/geometry_rules.py).

Živi nalaz: Quick na temi 6-08-006 („Centar, poluprečnik/polumjer i
prečnik/promjer“) vratio je $D=14$ cm za prečnik, jer je
route_geometry_topic() vratio PRAZAN opseg — model nikad nije ni dobio pravilo
da je $R$ prečnik.

Ključ ispravke: krug se prepoznaje iz NAZIVA LEKCIJE, nikad iz oblasti — dvije
cijele oblasti se zovu „... kružnica i krug“, a većina njihovih lekcija nije o
krugu.
"""
import json
import re
from pathlib import Path

from matbot import geometry_rules as gr, prompts
from matbot.topics import lesson_info

ROOT = Path(__file__).resolve().parent.parent

LIVE_GRADE, LIVE_TOPIC = 6, "6-08-006"


def live_lesson():
    info = lesson_info(LIVE_GRADE, LIVE_TOPIC)
    return info["oblast"], info["title"]


# ---------------------------------------------------------------------------
# 1. Exact live topic routes to plane/circle
# ---------------------------------------------------------------------------

def test_live_topic_routes_to_plane_circle():
    oblast, title = live_lesson()
    scope, figures = gr.route_geometry_topic(oblast, title)
    assert scope == "plane"
    assert "krug" in figures


def test_live_topic_produces_non_empty_geometry_rules():
    oblast, title = live_lesson()
    assert gr.build_geometry_rules(oblast, title) != ""


# ---------------------------------------------------------------------------
# 2-5. Full Quick prompt content for the live topic
# ---------------------------------------------------------------------------

def live_quick_prompt():
    oblast, title = live_lesson()
    return prompts.build_quick_instructions(LIVE_GRADE, lesson_title=title, oblast=oblast)


def test_full_quick_prompt_defines_R_as_diameter_and_r_as_radius():
    full = live_quick_prompt()
    assert "PREČNIK" in full
    assert "$R=2r$" in full.replace(" ", "")
    assert "$r$ = poluprečnik" in full


def test_full_quick_prompt_never_defines_d_or_D_as_diameter():
    full = live_quick_prompt()
    # Namjerno case-sensitive: $r$ i $R$ su različite veličine.
    assert not re.search(r"\$?d\$?\s*(?:=|je|označava)\s*prečnik", full)
    assert not re.search(r"\$?D\$?\s*(?:=|je|označava)\s*prečnik", full)
    assert "$d$ NIKAD ne znači prečnik" in full


def test_full_quick_prompt_never_defines_R_as_circumscribed_radius():
    full = live_quick_prompt()
    assert not re.search(r"\$?R\$?\s*(?:=|je|označava)\s*poluprečnik", full)


def test_full_quick_prompt_contains_circumference_formula():
    full = live_quick_prompt()
    assert "$O = 2\\pi r = \\pi R$" in full


def test_full_quick_prompt_contains_circle_area_formula():
    full = live_quick_prompt()
    assert "$P = \\pi r^2$" in full


def test_practice_and_explain_prompts_also_carry_the_convention():
    oblast, title = live_lesson()
    for builder in (prompts.build_instructions, prompts.build_explain_instructions):
        full = builder(LIVE_GRADE, lesson_title=title, oblast=oblast)
        assert "$R=2r$" in full.replace(" ", ""), builder.__name__


# ---------------------------------------------------------------------------
# 6. Triangle lessons with inscribed/circumscribed circle keep r_u / r_o
# ---------------------------------------------------------------------------

def test_triangle_with_circumscribed_circle_uses_ro():
    text = gr.build_geometry_rules("Ugao i trougao", "Simetrale stranica i centar opisane kružnice")
    assert "$r_o$ = poluprečnik OPISANE kružnice" in text
    assert "$r_u$ = poluprečnik UPISANE kružnice" in text
    assert not re.search(r"\$?R\$?\s*(?:=|je|označava)\s*poluprečnik", text)


def test_triangle_with_inscribed_circle_still_routes_triangle_too():
    scope, figures = gr.route_geometry_topic("Ugao i trougao", "Simetrale uglova i centar upisane kružnice")
    assert scope == "plane"
    assert "trougao" in figures


# ---------------------------------------------------------------------------
# 7. Solid lessons keep D as a spatial diagonal
# ---------------------------------------------------------------------------

def test_solid_lessons_keep_D_as_spatial_diagonal():
    text = gr.build_geometry_rules("Geometrijska tijela", "Dijagonalni presjek prizme")
    assert "$D$, $D_1$ = prostorne dijagonale" in text
    assert "$d$, $d_1$ = dijagonale osnove" in text


def test_circle_named_solid_lessons_stay_solid():
    """Valjak/kupa/lopta imaju kružnu osnovu, ali važi konvencija TIJELA."""
    for lesson in ("Osni presjek valjka", "Izvodnica kupe", "Presjek lopte ravni"):
        scope, _ = gr.route_geometry_topic("Geometrijska tijela", lesson)
        assert scope == "solid", lesson


# ---------------------------------------------------------------------------
# 8. No false positives across all 534 canonical lessons
# ---------------------------------------------------------------------------

def _all_lessons():
    data = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    for grade, grade_data in data["grades"].items():
        for lesson in grade_data["lessons"]:
            yield int(grade), lesson["oblast"], lesson["title"]


def _circle_routed():
    return [(g, o, t) for g, o, t in _all_lessons()
            if "krug" in gr.route_geometry_topic(o, t)[1]]


def test_every_circle_routed_lesson_actually_mentions_a_circle_term():
    term = re.compile(r"kružnic|kružn|\bkrug|kruga|kruž|poluprečnik|polumjer|prečnik|"
                      r"promjer|tetiv|tangent|sječic|sekant", re.IGNORECASE)
    for grade, oblast, title in _circle_routed():
        assert term.search(title), f"{grade} {oblast} / {title}"


def test_pie_chart_statistics_lessons_are_not_circle_geometry():
    """Živi audit: „Kružni dijagram“ i „Tabele, stupčasti i kružni dijagrami“
    su statistika, ne geometrija kruga."""
    for oblast, title in [("Podaci i vjerovatnoća", "Kružni dijagram"),
                          ("Relacije, preslikavanja i koordinatni sistem",
                           "Tabele, stupčasti i kružni dijagrami")]:
        _, figures = gr.route_geometry_topic(oblast, title)
        assert "krug" not in figures, title


def test_non_circle_lessons_inside_circle_named_oblasti_are_not_routed():
    """Dvije oblasti se ZOVU „... kružnica i krug“, ali ove lekcije nisu o krugu."""
    for title in ["Izlomljena linija", "Mnogougao/mnogokut",
                  "Tačka, prava, ravan, poluprava i duž",
                  "Geometrijske figure kao skupovi tačaka"]:
        _, figures = gr.route_geometry_topic("Skupovi tačaka, kružnica i krug", title)
        assert "krug" not in figures, title
    for title in ["Vrste mnogouglova", "Zbir unutrašnjih uglova mnogougla",
                  "Broj dijagonala mnogougla", "Pravilni mnogougao"]:
        _, figures = gr.route_geometry_topic("Mnogougao, kružnica i krug", title)
        assert "krug" not in figures, title


def test_centar_alone_never_triggers_circle_routing():
    """„ortocentar“, „centar rotacije“, „centar simetrije“ nisu krug."""
    for oblast, title in [("Ugao i trougao", "Visine trougla i ortocentar"),
                          ("Vektori i izometrijska preslikavanja", "Rotacija: centar i ugao rotacije"),
                          ("Vektori i izometrijska preslikavanja", "Osa i centar simetrije figure")]:
        _, figures = gr.route_geometry_topic(oblast, title)
        assert "krug" not in figures, title


def test_algebra_lessons_never_route_to_circle():
    for oblast, title in [("Razlomci", "Proširivanje razlomaka"),
                          ("Sistemi linearnih jednačina", "Metoda supstitucije"),
                          ("Polinomi", "Kvadrat binoma")]:
        scope, figures = gr.route_geometry_topic(oblast, title)
        assert "krug" not in figures, title


def test_expected_canonical_circle_lessons_are_all_routed():
    expected_ids = ["6-08-005", "6-08-006", "6-08-007", "6-08-008", "6-08-009",
                    "6-08-010", "6-08-011", "8-08-008", "8-08-009", "8-08-010",
                    "8-08-011", "8-08-012", "8-04-012"]
    for topic_id in expected_ids:
        grade = int(topic_id[0])
        info = lesson_info(grade, topic_id)
        _, figures = gr.route_geometry_topic(info["oblast"], info["title"])
        assert "krug" in figures, f"{topic_id} {info['title']}"


def test_circle_block_passes_the_mathjax_safety_boundary():
    from matbot.mathsafe import sanitize_and_validate_math_text
    _, is_safe = sanitize_and_validate_math_text(gr._FIGURE_RULES["krug"])
    assert is_safe
