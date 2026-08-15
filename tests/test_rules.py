"""Testovi za matbot/rules.py: deterministički router + zajednički blokovi
+ integracija u sva tri build_*_instructions u matbot/prompts.py.

Bez AI poziva — sve provjere su na TEKSTU koji se sastavlja i šalje modelu,
u skladu sa ostalim testovima u ovom repou (fake/deterministički, ne live).
"""
from matbot.prompts import (
    build_explain_instructions,
    build_quick_instructions,
)
from matbot.rules import (
    OFF_TOPIC_ANSWER,
    _is_construction_topic,
    build_shared_math_rules,
    route_topic_rules,
)


def build_instructions(grade, lesson_title="", oblast=""):
    """Practice instrukcije više NE gradi matbot/prompts.py.

    Stari jednopozivni motor je povučen (2026-08-14) zajedno sa svojim
    graditeljem prompta; Practice prompt sada sastavlja `matbot/tutor/prompts.py`
    iz konteksta lekcije. Ono što OVI testovi provjeravaju su ZAJEDNIČKA
    matematička/jezička pravila, a njih i dalje isporučuje isti izvor za sva tri
    moda — pa se provjeravaju direktno na njemu."""
    return build_shared_math_rules(grade, lesson_title, oblast, mode="practice")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def test_router_matches_razlomci():
    assert "razlomci" in route_topic_rules("Razlomci", "Proširivanje razlomaka")


def test_router_matches_nejednacine_not_jednacine_when_title_says_nejednacine():
    ids = route_topic_rules("Cijeli brojevi", "Nejednačine sa cijelim brojevima")
    assert "nejednacine" in ids
    assert "jednacine" not in ids  # nejednačina nije i jednačina


def test_router_matches_jednacine_for_plain_equation_title():
    ids = route_topic_rules("Cijeli brojevi", "Jednačine sa cijelim brojevima")
    assert "jednacine" in ids


def test_router_matches_equations_nested_in_non_equation_oblast():
    """7. razred nema zasebnu 'Jednačine' oblast — jednačine su UNUTAR
    'Racionalni brojevi'. Router mora pogoditi po naslovu lekcije, ne po oblasti."""
    ids = route_topic_rules(
        "Racionalni brojevi", "Jednačine sa množenjem i dijeljenjem racionalnih brojeva"
    )
    assert "jednacine" in ids


def test_router_matches_sistemi():
    ids = route_topic_rules(
        "Sistem linearnih jednačina sa 2 nepoznate",
        "Metoda suprotnih koeficijenata (gausova metoda)",
    )
    assert "sistemi" in ids


def test_router_matches_korijeni():
    assert "korijeni" in route_topic_rules(
        "Realni brojevi", "KORIJENI - Pravila za računske operacije"
    )


def test_router_matches_uglovi():
    assert "uglovi" in route_topic_rules("Uglovi", "Ugaone jedinice: stepen, minuta, sekunda")


def test_slicni_trouglovi_lesson_does_not_route_uglovi():
    """Live nalaz iz novog kurikuluma: 'trouglova' sadrži 'ugao' kao goli
    podstring pa je pogrešno povlačio 'uglovi' blok za lekcije o sličnosti
    trouglova koje nemaju veze sa stepen/minuta/sekunda računom uglova."""
    ids = route_topic_rules(
        "Proporcionalnost, Talesova teorema i sličnost", "Površine sličnih trouglova"
    )
    assert "uglovi" not in ids


def test_pravougaonik_lesson_does_not_route_uglovi():
    ids = route_topic_rules("Pitagorina teorema i primjene u ravni", "Dijagonala pravougaonika")
    assert "uglovi" not in ids


def test_ugao_oblique_case_still_routes_uglovi():
    assert "uglovi" in route_topic_rules("Uglovi", "Pojam ugla, elementi i označavanje")


def test_router_matches_koordinatna_geometrija():
    assert "koordinatna_geometrija" in route_topic_rules(
        "Relacije, preslikavanja i koordinatni sistem", "Pravougli koordinatni sistem u ravni"
    )


def test_router_matches_linearna_funkcija():
    assert "linearna_funkcija" in route_topic_rules(
        "Linearna funkcija", "Eksplicitni i implicitni oblik linearne funkcije"
    )


def test_router_matches_nzd_nzs():
    ids = route_topic_rules("Djeljivost brojeva", "Zajednički djelioci i najveći zajednički djelilac / NZD")
    assert "nzd_nzs" in ids


def test_router_no_match_for_unrelated_lesson():
    ids = route_topic_rules("Vektori", "Pojam vektora, kolinearni i nula vektor")
    assert ids == []


def test_construction_topic_detected_by_keyword():
    assert _is_construction_topic("Trougao", "Konstrukcija opisane kružnice trougla")
    assert not _is_construction_topic("Razlomci", "Proširivanje razlomaka")


# ---------------------------------------------------------------------------
# build_shared_math_rules — filtriranje (ne šalje sve razrede/oblasti odjednom)
# ---------------------------------------------------------------------------

def test_shared_rules_include_only_selected_grade():
    text6 = build_shared_math_rules(6, "", "", mode="practice")
    text9 = build_shared_math_rules(9, "", "", mode="practice")
    assert "PRAVILA ZA 6. RAZRED" in text6
    assert "PRAVILA ZA 7. RAZRED" not in text6
    assert "PRAVILA ZA 9. RAZRED" not in text6
    assert "PRAVILA ZA 9. RAZRED" in text9
    assert "PRAVILA ZA 6. RAZRED" not in text9


def test_shared_rules_exclude_irrelevant_topic_blocks():
    text = build_shared_math_rules(7, "Sabiranje i oduzimanje cijelih brojeva", "Cijeli brojevi", mode="practice")
    assert "OBLAST — RAZLOMCI" not in text
    assert "OBLAST — KORIJENI" not in text
    assert "OBLAST — SISTEMI" not in text


def test_shared_rules_include_relevant_topic_block():
    text = build_shared_math_rules(6, "Proširivanje razlomaka", "Razlomci", mode="practice")
    assert "OBLAST — RAZLOMCI" in text


def test_construction_block_only_when_construction_topic():
    with_construction = build_shared_math_rules(
        6, "Konstrukcija tangente na kružnicu", "Skupovi tačaka, kružnica i krug", mode="explain"
    )
    without_construction = build_shared_math_rules(6, "Proširivanje razlomaka", "Razlomci", mode="explain")
    assert "GEOMETRIJSKE KONSTRUKCIJE" in with_construction
    assert "GEOMETRIJSKE KONSTRUKCIJE" not in without_construction


def test_construction_explain_gets_analiza_structure_block():
    text = build_shared_math_rules(7, "Konstrukcija simetrale ugla", "Osnovne geometrijske konstrukcije i postupci", mode="explain")
    assert "ANALIZA, POTREBAN PRIBOR, POSTUPAK KONSTRUKCIJE, PROVJERA" in text


def test_construction_practice_gets_multiple_choice_instruction():
    text = build_shared_math_rules(7, "Konstrukcija simetrale ugla", "Osnovne geometrijske konstrukcije i postupci", mode="practice")
    assert "MORA ostati multiple-choice" in text


def test_construction_quick_gets_no_extra_structure_block():
    text = build_shared_math_rules(7, "Konstrukcija simetrale ugla", "Osnovne geometrijske konstrukcije i postupci", mode="quick")
    assert "GEOMETRIJSKE KONSTRUKCIJE" in text
    assert "ANALIZA, POTREBAN PRIBOR" not in text
    assert "MORA ostati multiple-choice" not in text


def test_unknown_grade_falls_back_to_grade_6_rules_5th_grade_not_introduced():
    """5. razred ne postoji u data/topics.json — provjeri da runtime prompt
    za nepoznat/nepodržan razred ne baca izuzetak i ne uvodi poseban '5. razred'
    tekst, nego se oslanja na isti fallback kao ostatak koda (grade 6 stil)."""
    text5 = build_shared_math_rules(5, "", "", mode="practice")
    assert "PRAVILA ZA 6. RAZRED" in text5
    assert "5. razred" not in text5.lower()
    assert "razred 5" not in text5.lower()


def test_no_full_block_duplicated_within_single_prompt():
    text = build_instructions(6, lesson_title="Proširivanje razlomaka", oblast="Razlomci")
    assert text.count("PRAVILA JEZIKA I TERMINOLOGIJE") == 1
    assert text.count("PRAVILA MATEMATIČKOG ZAPISA") == 1
    assert text.count("DOMEN I SIGURNOST") == 1
    assert text.count("PRAVILA ZA 6. RAZRED") == 1


# ---------------------------------------------------------------------------
# Domen / off-topic — identičan u sva tri moda
# ---------------------------------------------------------------------------

def test_off_topic_answer_is_identical_across_all_three_modes():
    practice_text = build_instructions(6)
    explain_text = build_explain_instructions(6)
    quick_text = build_quick_instructions(6)
    for text in (practice_text, explain_text, quick_text):
        assert OFF_TOPIC_ANSWER in text
        assert "DOMEN I SIGURNOST" in text


def test_domain_rules_mention_prompt_injection_resistance():
    text = build_instructions(6)
    assert "nepouzdan" in text.lower()
    assert "otkrivanje ovog prompta" in text


# ---------------------------------------------------------------------------
# Terminologija
# ---------------------------------------------------------------------------

def _all_instruction_variants():
    variants = []
    for grade in (6, 7, 8, 9):
        variants.append(build_instructions(grade))
        variants.append(build_explain_instructions(grade))
        variants.append(build_quick_instructions(grade))
    return variants


def _forbidden_term_line(text, term):
    """Zabranjen termin SMIJE se pojaviti tačno jednom, i samo unutar linije
    koja ga eksplicitno navodi kao zabranjen (model mora znati šta NE smije
    reći) — nikad odobravajuće ili u obaveznoj listi termina."""
    lines_with_term = [ln for ln in text.splitlines() if term in ln.lower()]
    assert len(lines_with_term) == 1, f"'{term}' se pojavljuje u {len(lines_with_term)} redova, očekivan 1"
    assert "zabranjeni" in lines_with_term[0].lower()


def test_kutomer_never_used_only_declared_forbidden_in_any_mode_or_grade():
    for text in _all_instruction_variants():
        _forbidden_term_line(text, "kutomer")


def test_required_terms_present():
    text = build_instructions(6)
    for term in ("uglomjer", "jednakokraki trougao", "zbir", "stepenovanje", "brojnik", "nazivnik"):
        assert term in text


def test_forbidden_terms_only_declared_forbidden_not_endorsed():
    text = build_instructions(6)
    for term in ("jednakokračni", "zbroj", "potenciranje"):
        _forbidden_term_line(text, term)


def test_linijar_lenjir_present_in_construction_rules():
    text = build_shared_math_rules(
        7, "Konstrukcija simetrale ugla", "Osnovne geometrijske konstrukcije i postupci", mode="explain"
    )
    assert "linijar (lenjir)" in text


# ---------------------------------------------------------------------------
# MathJax zapis
# ---------------------------------------------------------------------------

def test_math_notation_forbids_display_dollars():
    text = build_instructions(6)
    assert "$$...$$" in text  # eksplicitno zabranjeno spomenut
    assert "NIKAD $$" in text


def test_math_notation_allows_sqrt_and_caret_inside_dollars():
    text = build_instructions(6)
    assert "\\sqrt" in text
    assert "x^2" in text


def test_math_notation_forbids_raw_visible_sqrt_and_caret_examples():
    text = build_instructions(6)
    assert "sqrt(20)" in text  # spomenut kao PRIMJER ZABRANJENOG zapisa
    assert "1/2" in text       # spomenut kao PRIMJER ZABRANJENOG zapisa


def test_math_notation_uses_comma_decimal_separator():
    text = build_instructions(6)
    assert "$2,5$" in text


def test_math_notation_uses_cdot_for_multiplication():
    text = build_instructions(6)
    assert "\\cdot" in text


# ---------------------------------------------------------------------------
# Razredi — matematička pravila
# ---------------------------------------------------------------------------

def test_grade_6_gets_unknown_member_method_not_z():
    text = build_instructions(6)
    assert "nepoznati sabirak" in text
    assert "NEMA negativnih brojeva i NEMA skupa Z" in text


def test_grades_7_to_9_allow_moving_terms_across_equals_sign():
    for grade in (7, 8, 9):
        text = build_instructions(grade)
        assert "prebacivanje" in text.lower()


def test_inequality_sign_flip_rule_tied_to_negative_multiplication_only():
    for grade in (7, 8, 9):
        text = build_instructions(grade, lesson_title="Nejednačine", oblast="Nejednačine")
        assert "množe ili dijele negativnim brojem" in text or "množenja/dijeljenja obje strane" in text


def test_grade_9_forbids_rational_inequalities():
    text = build_instructions(9)
    assert "racionalne" in text.lower()
    assert "ne generiši" in text.lower()


# ---------------------------------------------------------------------------
# Oblasti — sadržaj bloka
# ---------------------------------------------------------------------------

def test_decimal_division_gets_scaling_rule():
    text = build_shared_math_rules(6, "Dijeljenje decimalnog broja cijelim brojem", "Razlomci u decimalnom obliku i decimalni brojevi", mode="practice")
    assert "proširi I djeljenik I djelilac" in text


def test_fractions_get_reduction_and_improper_fraction_rules():
    text = build_shared_math_rules(6, "Sabiranje i oduzimanje razlomaka jednakih imenilaca", "Razlomci", mode="practice")
    assert "neprave razlomke" in text
    assert "skrati" in text


def test_roots_get_partial_root_extraction_rule():
    text = build_shared_math_rules(8, "KORIJENI - Pravila za računske operacije", "Realni brojevi", mode="explain")
    assert "\\sqrt{4}\\cdot\\sqrt{5}" in text


def test_angles_get_degrees_minutes_seconds():
    text = build_shared_math_rules(6, "Ugaone jedinice: stepen, minuta, sekunda", "Uglovi", mode="explain")
    assert "35^\\circ 20' 15''" in text


def test_coordinate_geometry_uses_xs_ys():
    text = build_shared_math_rules(9, "Pravougli (dekartov) koordinatni sistem", "Linearna funkcija", mode="explain")
    assert "$x_s$" in text
    assert "$y_s$" in text


def test_linear_function_uses_explicit_form():
    text = build_shared_math_rules(9, "Eksplicitni i implicitni oblik linearne funkcije", "Linearna funkcija", mode="explain")
    assert "y=kx+n" in text


def test_systems_get_named_methods_not_forced_gauss_label():
    text = build_shared_math_rules(9, "Metoda suprotnih koeficijenata (gausova metoda)", "Sistem linearnih jednačina sa 2 nepoznate", mode="explain")
    assert "TAČNO kako glasi u izabranoj lekciji" in text
    assert "ne izmišljaj naziv" in text.lower()


def test_plain_fraction_task_does_not_get_construction_block():
    text = build_shared_math_rules(6, "Proširivanje razlomaka", "Razlomci", mode="practice")
    assert "GEOMETRIJSKE KONSTRUKCIJE" not in text


# ---------------------------------------------------------------------------
# Modovi — mode-specific override na zajednička pravila
# ---------------------------------------------------------------------------

def test_quick_instructions_relativize_shared_procedure_rules():
    text = build_quick_instructions(6, lesson_title="Dijeljenje decimalnog broja cijelim brojem", oblast="Razlomci u decimalnom obliku i decimalni brojevi")
    assert "SAMO ako učenik izričito zatraži postupak" in text


def test_explain_and_practice_do_not_get_quick_relativization_line():
    practice_text = build_instructions(6)
    explain_text = build_explain_instructions(6)
    marker = "SAMO ako učenik izričito zatraži postupak"
    assert marker not in practice_text
    assert marker not in explain_text


# ---------------------------------------------------------------------------
# FIX A (live smoke test nalaz): "sistemi" ne smije se aktivirati na goli
# "sistem" (npr. "koordinatni sistem") — samo na stvarne sisteme
# jednačina/nejednačina ili imenovane metode iz canonical lesson_title.
# ---------------------------------------------------------------------------

def test_coordinate_system_lesson_does_not_route_sistemi():
    """Live nalaz: 'Pravougli (dekartov) koordinatni sistem' je pogrešno
    povlačio 'sistemi' blok (metoda suprotnih koeficijenata/Gaus) jer je
    stari router tražio goli 'sistem'."""
    ids = route_topic_rules("Linearna funkcija", "Pravougli (dekartov) koordinatni sistem")
    assert "sistemi" not in ids


def test_other_coordinate_system_lesson_does_not_route_sistemi():
    ids = route_topic_rules(
        "Relacije, preslikavanja i koordinatni sistem", "Pravougli koordinatni sistem u ravni"
    )
    assert "sistemi" not in ids


def test_number_system_phrase_does_not_route_sistemi():
    """Hipotetički 'brojevni sistem' (ako bi ikad postojao u kurikulumu) ne
    smije aktivirati sistemi blok — provjera generičke fraze, ne stvarne lekcije."""
    ids = route_topic_rules("Neka oblast", "Brojevni sistem i osnovni pojmovi")
    assert "sistemi" not in ids


def test_sistem_linearnih_jednacina_routes_sistemi():
    ids = route_topic_rules("Sistem linearnih jednačina sa 2 nepoznate", "Neka lekcija")
    assert "sistemi" in ids


def test_sistemi_linearnih_jednacina_plural_routes_sistemi():
    ids = route_topic_rules("", "Sistemi linearnih jednačina — uvod")
    assert "sistemi" in ids


def test_sistem_nejednacina_routes_sistemi():
    ids = route_topic_rules(
        "Linearne nejednačine sa jednom nepoznatom", "Sistem linearnih nejednačina sa jednom nepoznatom"
    )
    assert "sistemi" in ids
    assert "nejednacine" in ids  # i dalje treba dobiti i opšte nejednačine pravilo


def test_metoda_suprotnih_koeficijenata_gaus_routes_sistemi():
    ids = route_topic_rules(
        "Sistem linearnih jednačina sa 2 nepoznate", "Metoda suprotnih koeficijenata (Gausova metoda)"
    )
    assert "sistemi" in ids


def test_metoda_zamjene_routes_sistemi():
    ids = route_topic_rules("Sistem linearnih jednačina sa 2 nepoznate", "Metoda zamjene ili supstitucije")
    assert "sistemi" in ids


def test_genitive_sistema_jednacina_routes_sistemi():
    """Stvarna 9. razred oblast/lekcija koristi genitiv 'sistemA jednačina'."""
    ids = route_topic_rules("Sistem linearnih jednačina sa 2 nepoznate", "Odnosi koeficijenata i grafičko rješavanje sistema jednačina")
    assert "sistemi" in ids


def test_coordinate_geometry_routing_unaffected_by_sistemi_fix():
    ids = route_topic_rules("Linearna funkcija", "Pravougli (dekartov) koordinatni sistem")
    assert "koordinatna_geometrija" in ids


def test_linear_function_routing_unaffected_by_sistemi_fix():
    ids = route_topic_rules("Linearna funkcija", "Eksplicitni i implicitni oblik linearne funkcije")
    assert ids == ["linearna_funkcija"]


# ---------------------------------------------------------------------------
# FIX B (live smoke test nalaz): construction Practice opcije moraju biti
# kratke — live poziv je stvarno vratio opciju > 200 znakova ("preduga
# opcija"), pa je server odbio cijeli zadatak i vratio sigurni fallback.
# ---------------------------------------------------------------------------

def test_construction_practice_instructions_require_concise_options():
    text = build_shared_math_rules(
        7, "Konstrukcija simetrale ugla", "Osnovne geometrijske konstrukcije i postupci", mode="practice"
    )
    assert "KRATKA" in text
    assert "140-160 znakova" in text
    assert "NIKAD ne stavljaj cijeli postupak" in text


def test_construction_explain_still_gets_full_analiza_structure():
    text = build_shared_math_rules(
        7, "Konstrukcija simetrale ugla", "Osnovne geometrijske konstrukcije i postupci", mode="explain"
    )
    assert "ANALIZA, POTREBAN PRIBOR, POSTUPAK KONSTRUKCIJE, PROVJERA" in text


def test_construction_quick_not_forced_full_structure_or_concise_option_rule():
    """Quick ne dobija ni ANALIZA-strukturu ni Practice-opcijsko pravilo —
    obje su irelevantne za Quick (nema new_task.options, nema detaljne strukture)."""
    text = build_shared_math_rules(
        7, "Konstrukcija simetrale ugla", "Osnovne geometrijske konstrukcije i postupci", mode="quick"
    )
    assert "ANALIZA, POTREBAN PRIBOR" not in text
    assert "new_task.options" not in text


def test_construction_remains_multiple_choice_instruction_present():
    text = build_shared_math_rules(
        7, "Konstrukcija simetrale ugla", "Osnovne geometrijske konstrukcije i postupci", mode="practice"
    )
    assert "MORA ostati multiple-choice" in text


def test_other_practice_topics_unchanged_by_construction_fix():
    text = build_shared_math_rules(6, "Proširivanje razlomaka", "Razlomci", mode="practice")
    assert "GEOMETRIJSKE KONSTRUKCIJE" not in text
    assert "140-160 znakova" not in text
