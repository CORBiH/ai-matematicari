r"""JEDNA ISTINA O SPOSOBNOSTI RAZREDA — prompt i validator se ne smiju razići.

PRETKOMITNI NALAZ (popravka blokatora FINAL40, FW-G06). Nova politika 6. razreda
zabranjuje zapis korijena i objava ga odbija kao `grade_capability_mismatch`.
Ali blokovi figura u `matbot/geometry_rules.py` su GRADE-AGNOSTIČNI: isti
`mnogougao` blok dobija uvodna lekcija 6. razreda „Mnogougao/mnogokut" i
dvanaest lekcija 8. razreda iz oblasti „Mnogougao, kružnica i krug". Jedan
njegov red

    - Pravilni šestougao: $O = 6a$, $P = \frac{3a^2\sqrt{3}}{2}$,
      $r_u = \frac{a\sqrt{3}}{2}$, $r_o = a$, $d_1 = a\sqrt{3}$, $d_2 = 2a$.

je gradivo 8. razreda. Tutor je za tu jednu lekciju istovremeno dobijao FORMULU
s korijenom i razredno pravilo koje korijen ZABRANJUJE — dvije suprotne
instrukcije za istu lekciju, pa bi svaki paket koji poslušanjem formule nastane
bio odbijen u objavi. To se ne smije isporučiti.

KURIKULARNI DOKAZ da red NIJE gradivo 6. razreda (data/topics.json):
  • 6. razred ima TAČNO JEDNU lekciju o mnogouglu („Mnogougao/mnogokut", u
    oblasti „Skupovi tačaka, kružnica i krug" — dakle uvodna, pojmovna);
  • nijedna lekcija 6. razreda ne govori o PRAVILNOM mnogouglu ni o površini
    mnogougla (jedini naslov 6. razreda s riječi „površina" su MJERNE JEDINICE
    za površinu);
  • pravilni mnogougao i obim/površina mnogougla su lekcije 8. razreda, u
    vlastitoj oblasti od dvanaest lekcija;
  • 6. razred nema nijednu lekciju o korijenu (korijen se uvodi u 8. razredu),
    pa je formula tamo neupotrebljiva po konstrukciji.

POPRAVKA: renderovanje geometrijskog bloka poštuje VEĆ RAZRIJEŠENU politiku
razreda i izostavlja SAMO redove koji traže zapis korijena. Filtrira se RED, ne
blok — ostalih pet redova (`mnogougao`) jesu gradivo 6. razreda i ostaju.

Nema grananja po ID-ju lekcije, nema imena figure, nema razrednog `if` u
`geometry_rules`: modul dobija boolean koji dolazi iz jedine tabele
`practice_policy.radical_notation_allowed_for_grade`.

ZERO poziva modela.
"""
import json
import pathlib

import pytest

from matbot import geometry_rules, practice_policy, rules
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import prompts as tutor_prompts

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOPICS = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))

RADICAL_MARKERS = ("\\sqrt", "√")


def _radical_lines(text):
    return {line.strip() for line in (text or "").splitlines()
            if line.strip() and any(m in line for m in RADICAL_MARKERS)}


def _declared_reference_lines():
    """Redovi koji o zapisu korijena SAMO GOVORE — iz samog izvora, ne iz procjene.

    Tri porodice, i nijedna ne daje matematičku METODU:
      • `rules._MATH_NOTATION_RULES` — higijena MathJax zapisa („kad pišeš
        korijen, piši ga ovako"); isti redovi jednako govore o \\frac i \\cdot;
      • `prompts._HELP_NOTATION_RULE` — allowlista bezbjednih LaTeX komandi
        (KOJE komande smiju biti ispisane, ne šta računati);
      • sama prohibicijska rečenica politike razreda.

    Sve ostalo što nosi korijen je POZITIVAN RECEPT i za razred bez te
    sposobnosti je protivrječnost."""
    lines = _radical_lines(rules._MATH_NOTATION_RULES)
    lines |= _radical_lines(tutor_prompts._HELP_NOTATION_RULE)
    for grade in (6, 7, 8, 9):
        policy = practice_policy.resolve(grade=grade, lesson_id="")
        lines |= _radical_lines(
            practice_policy.radical_capability_rule_text(policy))
    return lines


REFERENCE_LINES = _declared_reference_lines()
GRADE6_LESSONS = TOPICS["grades"]["6"]["lessons"]


def _positive_radical_recipes(prompt):
    """Redovi s korijenom koji NISU deklarisani kao referentni."""
    return sorted(_radical_lines(prompt) - REFERENCE_LINES)


# ===========================================================================
# 1) REVIZIJA SVIH 119 LEKCIJA 6. RAZREDA — nad CIJELIM Tutor promptom
# ===========================================================================

def test_grade_six_lesson_count_matches_the_curriculum():
    """Sidro revizije: ako se kurikulum promijeni, brojevi u izvještaju lažu."""
    assert len(GRADE6_LESSONS) == 121


def test_every_grade_six_lesson_has_the_radical_prohibition():
    for lesson in GRADE6_LESSONS:
        context = lesson_context_module.build(6, lesson["id"])
        assert context is not None, lesson["id"]
        assert not context.practice_policy.radical_notation_allowed, lesson["id"]


@pytest.mark.parametrize("lesson_id", [lesson["id"] for lesson in GRADE6_LESSONS])
def test_no_grade_six_prompt_offers_a_radical_recipe(lesson_id):
    """POSITIVE_RADICAL_PROMPT_CONFLICTS_AFTER = 0, mjereno nad CIJELIM promptom.

    Renderuje se `build_tutor_instructions` — kompletan sistemski prompt prvog
    poziva — a ne izolovana pomoćna funkcija, jer se protivrječnost može
    pojaviti u BILO KOJOJ porodici pravila koja u prompt uđe."""
    context = lesson_context_module.build(6, lesson_id)
    prompt = tutor_prompts.build_tutor_instructions(context)
    assert _positive_radical_recipes(prompt) == [], lesson_id


def test_the_prohibition_itself_is_present_and_is_not_counted_as_a_recipe():
    """Zabrana SMIJE spominjati zapis — test mora razlikovati te dvije stvari."""
    context = lesson_context_module.build(6, "6-08-004")
    prompt = tutor_prompts.build_tutor_instructions(context)
    prohibition = practice_policy.radical_capability_rule_text(
        context.practice_policy)
    assert prohibition.strip() in prompt
    assert any(m in prohibition for m in RADICAL_MARKERS)   # zaista ga spominje
    assert _positive_radical_recipes(prompt) == []          # a ipak nije recept


def test_the_measure_would_actually_catch_a_reintroduced_recipe():
    """Kontrola samog mjerača: da nije slijep, dokazuje se namjernim padom."""
    context = lesson_context_module.build(6, "6-08-004")
    prompt = tutor_prompts.build_tutor_instructions(context)
    injected = prompt + "\n- Pravilni šestougao: $P = \\frac{3a^2\\sqrt{3}}{2}$.\n"
    assert _positive_radical_recipes(injected) == [
        "- Pravilni šestougao: $P = \\frac{3a^2\\sqrt{3}}{2}$."]


# ===========================================================================
# 2) TAČAN DEFEKT — 6-08-004 i njegov `mnogougao` blok
# ===========================================================================

def test_6_08_004_still_routes_to_the_polygon_block():
    """Ruta figure NIJE bila pogrešna i ne smije se dirati: lekcija JESTE o
    mnogouglu i treba svoj blok formula."""
    lesson = next(l for l in GRADE6_LESSONS if l["id"] == "6-08-004")
    scope, figures = geometry_rules.route_geometry_topic(
        lesson["oblast"], lesson["title"])
    assert scope == "plane"
    assert list(figures) == ["mnogougao"]


def test_only_the_radical_line_is_dropped_from_the_polygon_block():
    full = geometry_rules._FIGURE_RULES["mnogougao"]
    filtered = geometry_rules._without_radical_formulas(full)
    dropped = [line for line in full.splitlines()
               if line not in filtered.splitlines()]
    assert len(dropped) == 1
    assert "šestougao" in dropped[0] and "\\sqrt" in dropped[0]
    # Pet redova koji JESU gradivo 6. razreda ostaju netaknuti.
    assert filtered.startswith("FORMULE — MNOGOUGAO:")
    assert len(filtered.splitlines()) == 6
    for expected in ("broj dijagonala", "Zbir unutrašnjih uglova",
                     "Pravilan $n$-tougao", "Broj stranica",
                     "Obim i površina pravilnog mnogougla"):
        assert expected in filtered, expected


def test_a_block_left_with_only_its_header_is_dropped_entirely():
    assert geometry_rules._without_radical_formulas(
        "FORMULE — X:\n- $a\\sqrt{2}$\n") == ""


def test_grade_six_polygon_prompt_keeps_the_usable_formulas():
    """Lekcija ne smije postati neupotrebljiva — samo tačna, uska ograda."""
    context = lesson_context_module.build(6, "6-08-004")
    prompt = tutor_prompts.build_tutor_instructions(context)
    assert "FORMULE — MNOGOUGAO:" in prompt
    assert "Zbir unutrašnjih uglova: $S_n = (n-2) \\cdot 180^\\circ$" in prompt
    assert "Pravilni šestougao" not in prompt


# ===========================================================================
# 3) OPSEG PROMJENE — ništa izvan 6. razreda se ne pomjera
# ===========================================================================

def test_grade_eight_polygon_lessons_keep_the_hexagon_formula():
    """Isti blok, razred koji korijen JESTE upoznao — mora ostati potpun."""
    for lesson_id in ("8-08-005", "8-08-007"):
        context = lesson_context_module.build(8, lesson_id)
        prompt = tutor_prompts.build_tutor_instructions(context)
        assert "Pravilni šestougao" in prompt, lesson_id
        assert "\\sqrt" in prompt, lesson_id
    assert practice_policy.resolve(
        grade=8, lesson_id="8-08-005").radical_notation_allowed


def test_grade_seven_is_deliberately_untouched_by_this_repair():
    """Uputa §7: granica 7. razreda se u ovom zadatku NE dira.

    7-06-004 je ista lekcija „Mnogougao/mnogokut" u 7. razredu i mora zadržati
    zatečeni blok, bajt za bajt."""
    seven = lesson_context_module.build(7, "7-06-004")
    assert seven.practice_policy.radical_notation_allowed
    prompt = tutor_prompts.build_tutor_instructions(seven)
    assert "Pravilni šestougao" in prompt
    assert geometry_rules._FIGURE_RULES["mnogougao"] in prompt


def test_only_grade_six_prompts_changed_at_all():
    """Tačan opseg: nijedna druga kombinacija (razred, lekcija, mod) se ne mijenja.

    Zatečeno ponašanje je `build_geometry_rules` s podrazumijevanim
    `allow_radical_notation=True` — bajt za bajt funkcija prije izmjene."""
    changed = []
    for grade_key in ("6", "7", "8", "9"):
        grade = int(grade_key)
        for lesson in TOPICS["grades"][grade_key]["lessons"]:
            for mode in ("practice", "explain", "quick"):
                before = geometry_rules.build_geometry_rules(
                    lesson["oblast"], lesson["title"], mode=mode)
                allow = (mode == "quick"
                         or practice_policy.radical_notation_allowed_for_grade(grade))
                after = geometry_rules.build_geometry_rules(
                    lesson["oblast"], lesson["title"], mode=mode,
                    allow_radical_notation=allow)
                if before != after:
                    changed.append((grade, lesson["id"], mode))
    assert changed == [(6, "6-08-004", "practice"), (6, "6-08-004", "explain")]


def test_quick_mode_is_untouched_because_it_carries_no_grade_rules():
    """Prohibicija i filtriranje moraju ići ZAJEDNO ili nikako.

    Quick namjerno ne dobija razredna kurikularna ograničenja (vidi
    rules.build_shared_math_rules), pa tamo nema protivrječnosti koju bi
    trebalo rješavati — i formule mu se ne diraju."""
    lesson = next(l for l in GRADE6_LESSONS if l["id"] == "6-08-004")
    quick = rules.build_shared_math_rules(
        6, lesson["title"], lesson["oblast"], "quick")
    assert "Pravilni šestougao" in quick
    assert practice_policy.radical_capability_rule_text(
        practice_policy.resolve(grade=6, lesson_id="6-08-004")).strip() not in quick

    practice = rules.build_shared_math_rules(
        6, lesson["title"], lesson["oblast"], "practice")
    assert "Pravilni šestougao" not in practice


# ===========================================================================
# 4) JEDNA ISTINA — nijedna druga kopija tabele razreda
# ===========================================================================

def test_the_grade_capability_table_has_exactly_one_owner():
    """`rules.py` NE smije ponovo izvoditi „koji razred smije korijen"."""
    source = (ROOT / "matbot" / "rules.py").read_text(encoding="utf-8")
    assert "radical_notation_allowed_for_grade" in source
    assert "_RADICAL_FORBIDDEN_GRADES" not in source
    geometry_source = (ROOT / "matbot" / "geometry_rules.py").read_text(
        encoding="utf-8")
    # geometry_rules dobija BOOLEAN; ne zna ni za razred ni za tabelu.
    assert "_RADICAL_FORBIDDEN_GRADES" not in geometry_source
    assert "grade" not in geometry_source.split(
        "def build_geometry_rules")[1].split("def ")[0].replace(
        "allow_radical_notation", "")


def test_policy_field_and_the_table_never_disagree():
    for grade in (6, 7, 8, 9):
        assert (practice_policy.resolve(grade=grade, lesson_id="")
                .radical_notation_allowed
                == practice_policy.radical_notation_allowed_for_grade(grade))


def test_the_prompt_filter_uses_the_same_measure_as_publication():
    """Prag prompta i prag objave su ISTA funkcija — ne dvije kopije."""
    source = (ROOT / "matbot" / "geometry_rules.py").read_text(encoding="utf-8")
    assert "practice_policy.find_radical_notation(" in source


# ===========================================================================
# 5) SERVERSKA GRANICA JE NETAKNUTA — prompt nije bezbjednosna granica
# ===========================================================================

def test_publication_still_rejects_radicals_in_grade_six():
    """Filtriranje prompta NE zamjenjuje validator (CLAUDE.md doktrina)."""
    six = practice_policy.resolve(grade=6, lesson_id="6-08-004")
    assert practice_policy.text_policy_failures(six, "$\\sqrt{3}$") == (
        practice_policy.GRADE_CAPABILITY_CODE,)


def test_grade_eight_publication_still_allows_radicals():
    eight = practice_policy.resolve(grade=8, lesson_id="8-08-005")
    assert practice_policy.text_policy_failures(eight, "$a\\sqrt{3}$") == ()
