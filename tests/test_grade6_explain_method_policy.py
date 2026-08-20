"""Metoda nepoznatog člana (6. razred) u modu „Objasni mi“ — JEDAN izvor istine.

ZAŠTO POSTOJI (forenzički trag modova, 2026-08-20). Trag je pokazao troje:

  1. Explain JESTE dobijao PP-1 tabelu relacija, ali formulisanu kao pravilo o
     tome kako se jednačina „rješava“ i vezanu zagradom za JEDNU oblast
     („Jednačine, nejednačine i izrazi u Q+“) — dakle upućenu drugoj radnji i
     užem opsegu nego što ograničenje stvarno ima.
  2. Isti prompt je dvadesetak redova niže, kroz blok oblasti koji ne zna za
     razred, šestašu izričito saopštavao da „7-9. razred smiju koristiti
     prebacivanje uz promjenu znaka“.
  3. Quick je isti nalaz već imao i zatvorio ga RUČNO PREPISANOM tabelom
     (`prompts._QUICK_GRADE_METHOD`), koja se od kanonske razišla — nedostajala
     joj je uloga `unknown_minuend`.

Ovi testovi zaključavaju popravku: jedna funkcija (`practice_policy.
equation_method_rule_text`) gradi metodu iz `UNKNOWN_ROLE_RELATIONS`, stiže u
svaki mod koji dobija razredna pravila, stoji PRIJE svakog teksta oblasti, a
šestaš nigdje ne dobija tvrdnju da je metoda starijih razreda dozvoljena.

Regresija Vježbajma je ovdje NAMJERNO uz Explain: `rules.py` je zajednički, pa
izmjena zbog Explaina mora biti dokazana i na Practice strani, u istom fajlu.
"""
import logging
import random

import pytest

from matbot import practice_policy as pp
from matbot import prompts, rules
from matbot.deterministic import equations
from matbot.explain import run_explain_turn
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import prompts as tutor_prompts
from tests.conftest import FakeLLM, make_explain_output

GRADE6_EQUATION_LESSON = ("Jednačine s razlomcima oblika x ± a = b i a ± x = b",
                          "Jednačine, nejednačine i izrazi u Q+")
GRADE6_NON_EQUATION_LESSON = ("Proširivanje razlomaka", "Razlomci")

ALL_RELATIONS = tuple(relation for _name, relation in pp.UNKNOWN_ROLE_RELATIONS.values())
ALL_ROLE_NAMES = tuple(name for name, _relation in pp.UNKNOWN_ROLE_RELATIONS.values())


def explain_text(grade, lesson=GRADE6_EQUATION_LESSON, strong=True):
    return prompts.build_explain_instructions(
        grade, lesson_title=lesson[0], oblast=lesson[1],
        lesson_context_strong=strong)


# ---------------------------------------------------------------------------
# 1) EXPLAIN — 6. razred dobija metodu upućenu OBJAŠNJAVANJU
# ---------------------------------------------------------------------------

def test_grade_six_explain_carries_the_explanation_directed_method_block():
    text = explain_text(6)
    assert "METODA ZA 6. RAZRED" in text
    # Blok mora govoriti o OBJAŠNJAVANJU, ne samo o rješavanju — to je tačno
    # ono što je zatečenoj formulaciji nedostajalo.
    assert "OBJAŠNJAVAŠ" in text
    assert "koji je član nepoznat" in text


@pytest.mark.parametrize("relation", ALL_RELATIONS)
def test_grade_six_explain_carries_every_canonical_relation(relation):
    assert relation in explain_text(6)


@pytest.mark.parametrize("role_name", ALL_ROLE_NAMES)
def test_grade_six_explain_names_every_unknown_role(role_name):
    """Uključujući `unknown_minuend` — uloga koja je nedostajala Quick kopiji."""
    assert role_name in explain_text(6)


def test_grade_six_explain_forbids_older_grade_transposition_language():
    text = explain_text(6)
    assert "prebacivanjem preko znaka jednakosti" in text
    assert "prebaci na drugu stranu" in text
    assert "uradi isto s obje strane" in text


def test_grade_six_explain_never_advertises_the_older_grade_method():
    """Zatečena rečenica bloka oblasti je nestala iz ŠESTOG razreda."""
    for lesson in (GRADE6_EQUATION_LESSON, GRADE6_NON_EQUATION_LESSON):
        for strong in (True, False):
            text = explain_text(6, lesson, strong)
            assert "7-9. razred smiju koristiti prebacivanje" not in text
            assert "Dozvoljeno je prebacivanje" not in text


def test_the_method_block_precedes_every_area_block_in_explain():
    """Autoritet je i pitanje REDOSLIJEDA: metoda ide prije teksta oblasti."""
    text = explain_text(6)
    method_at = text.find("METODA ZA 6. RAZRED")
    assert method_at != -1
    for area_header in ("OBLAST — JEDNAČINE", "OBLAST — NEJEDNAČINE",
                        "OBLAST — RAZLOMCI"):
        assert method_at < text.index(area_header), area_header
    # Ne smije biti zakopan pri dnu 12 KB prompta: stoji odmah uz razredna
    # pravila, u prvoj trećini pošiljke.
    assert method_at < 0.35 * len(text), (method_at, len(text))
    assert text.index("PRAVILA ZA 6. RAZRED") < method_at


def test_the_method_block_ships_exactly_once_per_explain_prompt():
    assert explain_text(6).count("METODA ZA 6. RAZRED") == 1


@pytest.mark.parametrize("grade", (7, 8, 9))
def test_higher_grades_do_not_receive_the_unknown_member_block(grade):
    text = explain_text(grade)
    assert "METODA ZA" not in text
    for relation in ALL_RELATIONS:
        assert relation not in text


def test_unsupported_grade_falls_back_to_grade_six_labelling():
    """5. razred ne postoji u kurikulumu: razredni blok i METODA moraju
    govoriti o ISTOM razredu, inače prompt izmisli nepostojeći razred."""
    text = rules.build_shared_math_rules(5, "", "", mode="practice")
    assert "PRAVILA ZA 6. RAZRED" in text and "METODA ZA 6. RAZRED" in text
    assert "5. razred" not in text.lower()


# ---------------------------------------------------------------------------
# 2) AKTIVACIJA — dokumentovana, mjerena, bez skrivenih uslova
# ---------------------------------------------------------------------------

def test_activation_is_grade_scoped_not_lesson_scoped():
    """DOKUMENTOVANO PONAŠANJE: blok se aktivira po RAZREDU (razriješena PP-1
    politika), ne po izabranoj lekciji i ne po poruci učenika.

    ZAŠTO NE PO PORUCI: `rules.build_shared_math_rules` izričito odbacuje
    `student_message` (`del student_message`) — rutiranje ide isključivo po
    pouzdanom serverskom lesson_title/oblast. Aktivacija po poruci bila bi NOV
    autoritet rutiranja i nije dio ove popravke.

    ZAŠTO TO NIJE NOVO ZAGAĐENJE PROMPTA: zatečeni `_GRADE_RULES[6]` je istu
    tabelu relacija VEĆ slao u svaki prompt 6. razreda, bez obzira na lekciju.
    Otisak ostaje isti; mijenja se formulacija i mjesto, ne opseg."""
    equation = explain_text(6, GRADE6_EQUATION_LESSON)
    non_equation = explain_text(6, GRADE6_NON_EQUATION_LESSON)
    assert "METODA ZA 6. RAZRED" in equation
    assert "METODA ZA 6. RAZRED" in non_equation
    # Aktivacija ne zavisi ni od toga je li kontekst lekcije jak ili slab.
    assert "METODA ZA 6. RAZRED" in explain_text(6, GRADE6_NON_EQUATION_LESSON,
                                                 strong=False)


def test_the_method_block_is_a_bounded_addition_to_the_prompt():
    """„Ne napuhuj prompt“ je mjerljiv zahtjev — mjeri ga."""
    block = pp.equation_method_rule_text(pp.resolve(grade=6))
    assert 0 < len(block) < 1400, len(block)
    assert len(explain_text(6)) < 14000


# ---------------------------------------------------------------------------
# 3) QUICK — duplikat obrisan, isti kanonski izvor
# ---------------------------------------------------------------------------

def test_the_quick_duplicate_table_is_gone():
    assert not hasattr(prompts, "_QUICK_GRADE_METHOD")


def test_quick_explanation_intent_uses_the_canonical_renderer():
    text = prompts.build_quick_instructions(6, intent="explain")
    assert pp.equation_method_rule_text(pp.resolve(grade=6)) in text


@pytest.mark.parametrize("role_name", ALL_ROLE_NAMES)
def test_quick_now_carries_every_role_including_the_one_that_had_drifted(role_name):
    """`unknown_minuend` je ono što je ručno prepisana kopija bila izgubila."""
    assert role_name in prompts.build_quick_instructions(6, intent="explain")


def test_quick_keeps_its_existing_activation_contract():
    """Popravka NE proširuje Quick: blok i dalje ide samo uz postupak/provjeru
    i samo razredu čija politika traži metodu nepoznatog člana."""
    assert "METODA ZA" in prompts.build_quick_instructions(6, intent="explain")
    assert "METODA ZA" in prompts.build_quick_instructions(6, intent="verify")
    assert "METODA ZA" not in prompts.build_quick_instructions(6, intent="result")
    assert "METODA ZA" not in prompts.build_quick_instructions(9, intent="explain")


# ---------------------------------------------------------------------------
# 4) JEDNA ISTINA — nijedna druga kopija tabele
# ---------------------------------------------------------------------------

# JEDAN doslovno prepisan PRIMJER relacije nije kopija tabele — kvar koji je
# popravljen bila je DRUGA TABELA koja je tiho odlutala (Quick kopija bez uloge
# `unknown_minuend`). Ova dva mjesta su izmjerena i namjerna:
#   • equations.py    — relacija stoji u DOCSTRINGU, kao ilustracija toga šta
#                       `_role_sentence()` vraća; izvršni kod je čita iz PP-1.
#   • package_preflight.py — recept za recenzenta navodi JEDAN primjer relacije
#                       uz `forbidden_method_language`; tabelu ne prepisuje.
# Inventar je zamrznut: nov unos mora biti svjesna izmjena, kao i kod kapije
# nedosežnih blokova pravila (tests/test_prompt_architecture_gate.py).
KNOWN_SINGLE_RELATION_MENTIONS = {"equations.py", "package_preflight.py"}


def test_no_second_copy_of_the_relation_table_exists_in_the_codebase():
    """Tabela relacija smije biti ZAPISANA samo u practice_policy.py.

    Kapija protiv ponavljanja tačno onog kvara koji je popravljen: druga kopija
    koja tiho odluta od prve. Mjeri se KOPIJA TABELE (dvije ili više relacija u
    istom fajlu), a pojedinačni ilustrativni primjeri se drže u zamrznutom
    inventaru — da i oni ostanu vidljivi kad se pojavi nov."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "matbot"
    owner = root / "practice_policy.py"
    tables, singles = [], set()
    for path in root.rglob("*.py"):
        if path == owner:
            continue
        source = path.read_text(encoding="utf-8")
        hits = [relation for relation in ALL_RELATIONS if relation in source]
        if len(hits) >= 2:
            tables.append((path.name, hits))
        elif hits:
            singles.add(path.name)
    assert tables == [], tables
    assert singles == KNOWN_SINGLE_RELATION_MENTIONS, singles


@pytest.mark.parametrize("mode", ("practice", "explain", "kontrolni"))
def test_every_grade_rule_mode_receives_the_identical_method_text(mode):
    expected = pp.equation_method_rule_text(pp.resolve(grade=6))
    text = rules.build_shared_math_rules(
        6, GRADE6_EQUATION_LESSON[0], GRADE6_EQUATION_LESSON[1], mode=mode)
    assert expected in text


@pytest.mark.parametrize("grade", (7, 8, 9))
def test_higher_grades_keep_their_own_method_rules(grade):
    text = rules.build_shared_math_rules(grade, "Linearne jednačine",
                                         "Cijeli brojevi", mode="practice")
    assert "Dozvoljeno je prebacivanje" in text
    assert pp.equation_method_rule_text(pp.resolve(grade=grade)) == ""


# ---------------------------------------------------------------------------
# 5) MJERENJE POLITIKE NAD EXPLAIN ODGOVOROM (log-only)
# ---------------------------------------------------------------------------

CASE_A_TRANSPOSITION = "Prebacimo član na drugu stranu i promijenimo znak."
CASE_B_NEGATION = ("Ne prebacujemo član na drugu stranu; gledamo koji je član "
                   "nepoznat.")
CASE_C_UNKNOWN_MEMBER = ("$x$ je nepoznati umanjilac, pa je nepoznati umanjilac "
                         "jednak umanjeniku minus razlika.")


def explain_payload(message="Objasni mi ovu temu.", topic="6-07-002", grade=6):
    return {
        "session_id": "g6-method-obs",
        "grade": grade,
        "selected_topic": topic,
        "selected_oblast": "",
        "student_message": message,
        "intent": "",
        "difficulty_request": "",
        "interaction_phase": "",
        "last_tutor_task": "",
        "last_tutor_message": "",
        "conversation_history": [],
    }


def run_explain_with(reply, caplog, topic="6-07-002", grade=6):
    fake = FakeLLM()
    fake.queue(make_explain_output(reply=reply))
    with caplog.at_level(logging.INFO, logger="matbot.explain"):
        result = run_explain_turn(fake, explain_payload(topic=topic, grade=grade))
    observations = [r.getMessage() for r in caplog.records
                    if "explain_method_policy_observed" in r.getMessage()]
    return result, observations


def test_case_a_transposition_is_observed_as_a_lexical_hit_without_roles(caplog):
    result, observations = run_explain_with(CASE_A_TRANSPOSITION, caplog)
    assert len(observations) == 1
    assert "lexical=forbidden_method_language" in observations[0]
    assert "roles_named=-" in observations[0]
    # LOG-ONLY: odgovor učeniku je nepromijenjen i turn nije pao.
    assert result["answer"] == CASE_A_TRANSPOSITION
    assert result["status"] == "ready"


def test_case_b_negation_is_INDISTINGUISHABLE_from_case_a(caplog):
    """IZMJERENA GRANICA, NE PRIKRIVENA — ovo je NEGATIVAN rezultat.

    `_FORBIDDEN_METHOD_PROSE_RE` traži stem „prebac“ i zato jednako pogađa
    rečenicu koja metodu ZABRANJUJE. Za doslovno ovu rečenicu ni pozitivan
    signal ne pomaže: ona ne imenuje nijednu ULOGU nepoznatog člana („koji je
    član nepoznat“ je opšta riječ, ne uloga). Dijagnostika je zato bajt za bajt
    ista kao za slučaj A.

    Uzorak se NE proširuje negacijom: isti uzorak je živa kapija Vježbajma
    (`package_policy_failures`), a izuzetak za negaciju bi je oslabio —
    „Ne zaboravi: prebacimo član…“ bi tada prošlo.

    Zaključak koji ovaj test zaključava: dok se ova granica ne izmjeri na živim
    odgovorima, `forbidden_method_language` u Explainu NE SMIJE postati kapija."""
    _result, observations = run_explain_with(CASE_B_NEGATION, caplog)
    assert len(observations) == 1
    assert "lexical=forbidden_method_language" in observations[0]
    assert "roles_named=-" in observations[0]


def test_a_complete_negation_that_names_the_role_is_separable(caplog):
    """Ono što pozitivan signal STVARNO razdvaja: potpuno objašnjenje.

    Odgovor koji metodu odbija I imenuje ulogu (dakle stvarno predaje gradivo
    6. razreda) razlikuje se od slučaja A po `roles_named` — i to je jedina
    razlika koju detekcija danas može dokazati."""
    complete = ("Ne prebacujemo član na drugu stranu. Ovdje je $x$ nepoznati "
                "sabirak, pa je nepoznati sabirak zbir minus poznati sabirak.")
    _result, observations = run_explain_with(complete, caplog)
    assert len(observations) == 1
    assert "lexical=forbidden_method_language" in observations[0]
    assert "roles_named=unknown_addend" in observations[0]


def test_case_c_correct_unknown_member_explanation_has_no_lexical_hit(caplog):
    _result, observations = run_explain_with(CASE_C_UNKNOWN_MEMBER, caplog)
    assert len(observations) == 1
    assert "lexical=-" in observations[0]
    assert "structural=-" in observations[0]
    assert "unknown_subtrahend" in observations[0]


def test_the_detector_limit_is_stated_as_an_invariant():
    """Granica detekcije, iskazana kao provjerljiva tvrdnja — ne kao komentar.

    Ako neko kasnije „popravi“ regex tako da razdvoji A i B, ovaj test pada i
    tjera na svjesnu odluku: je li ista izmjena dokazano bezbjedna i za kapiju
    Vježbajma, koja isti uzorak koristi kao blokirajući."""
    policy = pp.resolve(grade=6, lesson_id="6-07-002",
                        lesson_title=GRADE6_EQUATION_LESSON[0],
                        oblast=GRADE6_EQUATION_LESSON[1])
    assert (pp.text_policy_failures(policy, CASE_A_TRANSPOSITION)
            == pp.text_policy_failures(policy, CASE_B_NEGATION)
            == (pp.FORBIDDEN_METHOD_CODE,))
    # Ni pozitivan signal ih ne razdvaja: nijedna od dvije rečenice ne imenuje
    # ulogu. Razdvaja tek POTPUNO objašnjenje (test iznad) i slučaj C.
    assert pp.unknown_member_role_mentions(CASE_A_TRANSPOSITION) == ()
    assert pp.unknown_member_role_mentions(CASE_B_NEGATION) == ()
    assert pp.unknown_member_role_mentions(CASE_C_UNKNOWN_MEMBER) != ()


def test_observation_never_reaches_the_student_payload(caplog):
    result, _observations = run_explain_with(CASE_A_TRANSPOSITION, caplog)
    serialized = repr(result)
    for code in (pp.FORBIDDEN_METHOD_CODE, pp.VISIBLE_DOMAIN_CODE,
                 pp.ADVANCED_SCOPE_CODE, pp.GRADE_CAPABILITY_CODE):
        assert code not in serialized
    assert "explain_method_policy_observed" not in serialized


def test_a_non_equation_lesson_is_not_scanned_for_method_prose(caplog):
    """Uska primjena: „prebaci“ u lekciji o razlomcima nije metodski prekršaj."""
    _result, observations = run_explain_with(
        "Prebaci mješoviti broj u nepravi razlomak.", caplog, topic="6-04-002")
    assert observations == []


@pytest.mark.parametrize("grade", (7, 8, 9))
def test_higher_grades_are_never_observed_for_the_unknown_member_method(grade,
                                                                       caplog):
    lesson = {7: "7-05-001", 8: "8-05-001", 9: "9-04-001"}[grade]
    from matbot.topics import lesson_info
    if lesson_info(grade, lesson) is None:      # kurikulum se smije mijenjati
        pytest.skip("lekcija ne postoji u ovom kurikulumu")
    _result, observations = run_explain_with(
        CASE_A_TRANSPOSITION, caplog, topic=lesson, grade=grade)
    for line in observations:
        assert "method=transposition" in line


# ---------------------------------------------------------------------------
# 6) REGRESIJA VJEŽBAJMA — rules.py je zajednički, pa se dokazuje ovdje
# ---------------------------------------------------------------------------

DETERMINISTIC_LESSONS = ("6-07-001", "6-07-002", "6-07-004", "6-07-006")
MODEL_BACKED_LESSON = "6-07-007"


@pytest.mark.parametrize("lesson_id", DETERMINISTIC_LESSONS)
def test_practice_keeps_the_zero_call_unknown_member_route(lesson_id):
    context = lesson_context_module.build(6, lesson_id)
    assert context is not None
    assert context.practice_policy.equation_method == pp.METHOD_UNKNOWN_MEMBER
    generator = tutor_pipeline._deterministic_generator_for(context)
    assert generator is equations, lesson_id


@pytest.mark.parametrize("lesson_id", DETERMINISTIC_LESSONS)
@pytest.mark.parametrize("level", (1, 2, 3))
def test_deterministic_packages_still_teach_the_role_and_pass_policy(lesson_id,
                                                                    level):
    """Provenijencija metode i proza uloge poslije izmjene rules.py.

    METODSKI NEUTRALAN OBLIK (`classification`, `check_solution`) po zatečenom
    ugovoru nosi PRAZAN `method_id` i nema postupka rješavanja u prozi — vidi
    `equations._unknown_member_package`. Takav paket se ovdje ne tjera da
    imenuje ulogu; tjera se samo da nikad ne nosi ZABRANJENU metodu. Lekcija
    6-07-001 je upravo taj slučaj (jedini oblik joj je `classification`)."""
    context = lesson_context_module.build(6, lesson_id)
    policy = context.practice_policy
    parameters = context.semantic_contract.parameters
    produced = 0
    for seed in range(12):
        try:
            package = equations.generate_package(
                lesson_id=lesson_id, lesson_title=context.title,
                parameters=parameters, level=level,
                rng=random.Random(seed), policy=policy)
        except Exception:                       # degenerisan uzorak → novi seed
            continue
        produced += 1
        assert package.method_id not in policy.forbidden_method_ids
        assert pp.package_policy_failures(
            policy, package.question, package.option_texts, package.hints,
            package.solution, package.method_id) == ()
        if package.method_id == pp.METHOD_UNKNOWN_MEMBER:
            prose = " ".join([package.solution, *package.hints])
            assert pp.unknown_member_role_mentions(prose), (lesson_id, seed)
    assert produced > 0, (lesson_id, level)


def test_solving_lessons_actually_produce_unknown_member_provenance():
    """Prethodni test dozvoljava prazan `method_id` samo neutralnom obliku —
    ovaj dokazuje da lekcije koje STVARNO rješavaju jednačinu i dalje nose
    `unknown_member`, pa dozvola iznad ne može ništa sakriti."""
    for lesson_id in ("6-07-002", "6-07-004", "6-07-006"):
        context = lesson_context_module.build(6, lesson_id)
        seen = set()
        for seed in range(12):
            try:
                package = equations.generate_package(
                    lesson_id=lesson_id, lesson_title=context.title,
                    parameters=context.semantic_contract.parameters, level=1,
                    rng=random.Random(seed), policy=context.practice_policy)
            except Exception:
                continue
            seen.add(package.method_id)
        assert seen == {pp.METHOD_UNKNOWN_MEMBER}, (lesson_id, seen)


@pytest.mark.parametrize("lesson_id", DETERMINISTIC_LESSONS)
def test_forbidden_transposition_still_fails_the_practice_validators(lesson_id):
    """Kapija NIJE oslabljena — dokaz na oba načina na koja pada."""
    policy = lesson_context_module.build(6, lesson_id).practice_policy
    assert pp.FORBIDDEN_METHOD_CODE in pp.package_policy_failures(
        policy, "Riješi jednačinu: $x + 2 = 5$", ["$3$"],
        ["Prebaci poznati član na drugu stranu."], "", pp.METHOD_UNKNOWN_MEMBER)
    assert pp.METHOD_PROVENANCE_CODE in pp.package_policy_failures(
        policy, "Riješi jednačinu: $x + 2 = 5$", ["$3$"], ["Uloga je sabirak."],
        "", pp.METHOD_TRANSPOSITION)


def test_model_backed_grade6_lesson_keeps_the_policy_in_both_prompts():
    """6-07-007 ide na model — prompt Tutora i Recenzenta mora nositi metodu."""
    context = lesson_context_module.build(6, MODEL_BACKED_LESSON)
    assert tutor_pipeline._deterministic_generator_for(context) is None
    expected = pp.equation_method_rule_text(context.practice_policy)
    assert expected
    for text in (tutor_prompts.build_tutor_instructions(context),
                 tutor_prompts.build_reviewer_instructions(context),
                 tutor_prompts.build_help_instructions(context)):
        assert expected in text
        assert "7-9. razred smiju koristiti prebacivanje" not in text
