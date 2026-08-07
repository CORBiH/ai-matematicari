"""Masovno uključivanje determinističkih lekcija (kapacitetna ekspanzija).

    python scripts/bulk_onboard_deterministic.py            # generiši + kompajliraj
    python scripts/bulk_onboard_deterministic.py --check    # provjeri da su artefakti u koraku
    python scripts/bulk_onboard_deterministic.py --report   # + sažetak pokrivenosti

ULAZI:
    data/topics.json                                   (kanonskih 534 lekcija)
    reference/curriculum/semantics/MATBOT_Faza2_Mapiranje.xlsx   (dokazi, opciono)
    OVA DATOTEKA — tabela ACTIVATIONS je pregledani izvor istine o tome koja
    lekcija pripada kojoj porodici s kojim parametrima.

IZLAZI:
    data/semantic_families.json            (dodane kapacitetne porodice)
    data/lesson_semantic_assignments.json  (dodane dodjele lekcija)
    data/lesson_semantics.compiled.json    (preko postojećeg kompajlera)
    reference/curriculum/semantics/deterministic_coverage_report.json

PRINCIPI (isti kao serverski validatori):
  • fail closed: nepoznata lekcija, naslov koji se ne poklapa s očekivanim,
    porodica bez registrovanog generatora ili parametri koje generator NE
    podržava u potpunosti OBARAJU build — lekcija se nikad ne aktivira tiho;
  • lekcija je PODATAK: nijedan ID lekcije ne postoji u matbot/ kodu;
  • dokazi se čitaju iz Faze 2 (exact mapiranja); lekcija bez izvora dobija
    izričitu oznaku CANON-TITLE (aktivacija na osnovu jednoznačnog naslova),
    nikad izmišljeni izvor;
  • dvostruko pokretanje daje bajt-identične artefakte.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAMILIES_PATH = ROOT / "data" / "semantic_families.json"
ASSIGNMENTS_PATH = ROOT / "data" / "lesson_semantic_assignments.json"
TOPICS_PATH = ROOT / "data" / "topics.json"
FAZA2_XLSX = ROOT / "reference" / "curriculum" / "semantics" / "MATBOT_Faza2_Mapiranje.xlsx"
REPORT_PATH = (ROOT / "reference" / "curriculum" / "semantics"
               / "deterministic_coverage_report.json")

CONTRACT_VERSION = "5C.1"

_HEADER = "SEMANTIČKI UGOVOR LEKCIJE (server ga provjerava determinističkim putem):"
_CLOSING = "- ako prekršiš ovo, server odbija paket prije objave"


def _template(main_parameter=None, main_text=None, operations=None,
              fixed=(), parameter_lines=None):
    template = {"header": _HEADER}
    if operations:
        template["operations"] = operations
    if main_parameter:
        template["main_line"] = {"parameter": main_parameter, "text": main_text}
    if fixed:
        template["fixed_lines"] = list(fixed)
    if parameter_lines:
        template["parameter_lines"] = parameter_lines
    template["closing"] = _CLOSING
    return template


_ARITHMETIC_OPERATION_VALUES = ["add", "subtract", "multiply", "divide"]


def _arithmetic_family(domain, domain_label, operation_labels, fixed,
                       extra_schema=None, parameter_lines=None):
    schema = {
        "allowed_operations": {"kind": "enum_set",
                               "values": _ARITHMETIC_OPERATION_VALUES,
                               "required": True},
        "number_domain": {"kind": "enum", "values": [domain], "required": True},
        "expression_shape": {"kind": "enum",
                             "values": ["single_operation", "multi_factor",
                                        "order_of_operations"],
                             "required": False},
    }
    schema.update(extra_schema or {})
    return {
        "family_version": 1,
        "family_name": f"Direktan račun — {domain_label}",
        "detector": "numeric_arithmetic",
        "core_skill": f"izvesti imenovanu računsku operaciju ({domain_label})",
        "parameter_schema": schema,
        "enforced_parameters": ["allowed_operations", "number_domain"],
        "advisory_parameters": ["expression_shape"] + list((extra_schema or {})),
        "prompt_template": _template(
            operations="- glavna vidljiva operacija mora biti: {operations}",
            fixed=fixed, parameter_lines=parameter_lines),
        "operation_labels": operation_labels,
    }


def _concept_family(name, detector, core_skill, concept_values, value_labels,
                    fixed=(), main_text=None, extra_schema=None,
                    concept_parameter="concepts", parameter_lines=None):
    schema = {
        concept_parameter: {"kind": "enum_set", "values": concept_values,
                            "required": True},
    }
    schema.update(extra_schema or {})
    return {
        "family_version": 1,
        "family_name": name,
        "detector": detector,
        "core_skill": core_skill,
        "parameter_schema": schema,
        "enforced_parameters": [concept_parameter] + sorted(extra_schema or {}),
        "advisory_parameters": [],
        "prompt_template": _template(
            main_parameter=concept_parameter,
            main_text=main_text or "- glavna vidljiva radnja mora biti: {values}",
            fixed=fixed, parameter_lines=parameter_lines),
        "value_labels": value_labels,
    }


NEW_FAMILIES = {
    "natural_arithmetic_direct": _arithmetic_family(
        "natural", "prirodni brojevi",
        {"add": "sabiranje prirodnih brojeva",
         "subtract": "oduzimanje prirodnih brojeva",
         "multiply": "množenje prirodnih brojeva",
         "divide": "dijeljenje prirodnih brojeva"},
        ["- svi operandi i svi međurezultati moraju ostati prirodni brojevi "
         "(bez razlomaka i bez negativnih vrijednosti)"],
        parameter_lines={"expression_shape": {
            "order_of_operations": "- izraz mora tražiti poštovanje redoslijeda "
                                   "operacija (i zagrade gdje su zadate)",
            "multi_factor": "- izraz je proizvod više faktora"}}),
    "integer_arithmetic_direct": _arithmetic_family(
        "integer", "cijeli brojevi",
        {"add": "sabiranje cijelih brojeva",
         "subtract": "oduzimanje cijelih brojeva",
         "multiply": "množenje cijelih brojeva",
         "divide": "dijeljenje cijelih brojeva"},
        ["- operandi su cijeli brojevi (s negativnim vrijednostima gdje nivo "
         "traži) i rezultat je cio broj"],
        extra_schema={"sign_scope": {"kind": "enum",
                                     "values": ["any", "same_signs",
                                                "different_signs"],
                                     "required": False}},
        parameter_lines={"sign_scope": {
            "same_signs": "- svi sabirci moraju imati ISTE predznake",
            "different_signs": "- sabirci moraju imati RAZLIČITE predznake"},
            "expression_shape": {
                "multi_factor": "- izraz je proizvod više cijelih faktora"}}),
    "decimal_arithmetic_direct": _arithmetic_family(
        "decimal", "decimalni brojevi",
        {"add": "sabiranje decimalnih brojeva",
         "subtract": "oduzimanje decimalnih brojeva",
         "multiply": "množenje decimalnih brojeva",
         "divide": "dijeljenje decimalnih brojeva"},
        ["- operandi su decimalni brojevi i svaki vidljivi rezultat mora imati "
         "KONAČAN decimalan zapis"]),
    "rational_arithmetic_direct": _arithmetic_family(
        "rational_signed", "racionalni brojevi",
        {"add": "sabiranje racionalnih brojeva",
         "subtract": "oduzimanje racionalnih brojeva",
         "multiply": "množenje racionalnih brojeva",
         "divide": "dijeljenje racionalnih brojeva"},
        ["- operandi su racionalni brojevi (razlomci s predznakom) i rezultat "
         "je racionalan broj"]),
    "number_comparison_order": _concept_family(
        "Poređenje i uređenje brojeva", "number_ordering",
        "uporediti ili urediti brojeve po veličini",
        ["natural", "fraction", "decimal", "integer", "rational"],
        {"natural": "prirodnih brojeva", "fraction": "razlomaka",
         "decimal": "decimalnih brojeva", "integer": "cijelih brojeva",
         "rational": "racionalnih brojeva"},
        fixed=["- zadatak traži izbor najvećeg/najmanjeg, poredak ili položaj "
               "između datih brojeva — ne računanje nove vrijednosti"],
        main_text="- glavna vidljiva radnja mora biti poređenje ili uređenje: "
                  "{values}",
        concept_parameter="number_domain"),
    "absolute_value_opposite": _concept_family(
        "Apsolutna vrijednost i suprotan broj", "absolute_opposite",
        "odrediti apsolutnu vrijednost ili suprotan broj",
        ["absolute_value", "opposite"],
        {"absolute_value": "apsolutna vrijednost broja",
         "opposite": "suprotan broj"},
        extra_schema={"number_domain": {"kind": "enum",
                                        "values": ["integer", "rational"],
                                        "required": True}}),
    "divisibility_predicate_application": _concept_family(
        "Primjena pravila djeljivosti", "divisibility_predicate",
        "primijeniti pravila djeljivosti na dat broj",
        [2, 3, 4, 5, 6, 9, 10, 15, 25],
        {},
        fixed=["- pitanje glasi »djeljiv sa N« (bez negacije i bez ‘ili’); "
               "računanje količnika smije biti samo pomoćni korak"],
        main_text="- zadatak mora tražiti primjenu pravila djeljivosti sa: "
                  "{values}",
        concept_parameter="divisors"),
    "common_divisors_multiples": _concept_family(
        "Djelioci, sadržioci, NZD i NZS", "common_divisors_multiples",
        "odrediti djelioce/sadržioce, NZD ili NZS",
        ["divisor_membership", "multiple_membership", "gcd", "lcm"],
        {"divisor_membership": "prepoznavanje djelioca datog broja",
         "multiple_membership": "prepoznavanje sadržioca datog broja",
         "gcd": "najveći zajednički djelilac",
         "lcm": "najmanji zajednički sadržilac"}),
    "prime_structure": _concept_family(
        "Prosti brojevi i prosta struktura", "prime_structure",
        "prepoznati proste brojeve i rastaviti broj na proste faktore",
        ["prime_classification", "coprime_pairs", "prime_factorization"],
        {"prime_classification": "prepoznavanje prostog i složenog broja",
         "coprime_pairs": "prepoznavanje relativno prostih brojeva",
         "prime_factorization": "rastavljanje na proste faktore"}),
    "percent_basic": _concept_family(
        "Osnovni procentni račun", "percent_basic",
        "izračunati procenat broja ili postotni zapis razlomka",
        ["percent_of_number", "fraction_to_percent"],
        {"percent_of_number": "procenat datog broja",
         "fraction_to_percent": "postotni zapis razlomka"},
        fixed=["- znak % piše se u prozi, nikad unutar $...$"]),
    "arithmetic_mean_direct": _concept_family(
        "Aritmetička sredina", "arithmetic_mean",
        "izračunati aritmetičku sredinu datih brojeva",
        ["mean"],
        {"mean": "aritmetička sredina datih brojeva"}),
    "power_arithmetic_direct": _concept_family(
        "Stepeni i zakoni stepena", "power_arithmetic",
        "izračunati vrijednost stepena ili primijeniti zakon stepena",
        ["square_value", "power_value", "zero_negative_exponent",
         "same_base_product_quotient", "power_of_power_product"],
        {"square_value": "kvadrat racionalnog broja",
         "power_value": "vrijednost stepena s prirodnim izložiocem",
         "zero_negative_exponent": "nulti i negativni izložilac",
         "same_base_product_quotient": "množenje i dijeljenje stepena "
                                       "jednakih osnova",
         "power_of_power_product": "stepen stepena, proizvoda i količnika"}),
    "square_root_direct": _concept_family(
        "Kvadratni korijen", "square_root",
        "izračunati kvadratni korijen ili prepoznati savršeni kvadrat",
        ["square_root_value", "perfect_square_recognition"],
        {"square_root_value": "kvadratni korijen savršenog kvadrata",
         "perfect_square_recognition": "prepoznavanje savršenog kvadrata"},
        fixed=["- potkorjena vrijednost mora biti savršen kvadrat (prirodan "
               "broj, razlomak ili decimalni zapis) — bez približnih "
               "vrijednosti"]),
    "linear_equation_direct": _concept_family(
        "Jednostavne linearne jednačine", "linear_equation",
        "riješiti ili provjeriti jednostavnu linearnu jednačinu",
        ["one_step_additive", "one_step_multiplicative", "parentheses",
         "check_solution", "check_inequality"],
        {"one_step_additive": "jednačina s jednim sabiranjem/oduzimanjem",
         "one_step_multiplicative": "jednačina s jednim množenjem/dijeljenjem",
         "parentheses": "jednačina sa zagradom",
         "check_solution": "provjera ponuđenog rješenja jednačine",
         "check_inequality": "provjera ponuđenog rješenja nejednačine"},
        fixed=["- jedna nepoznata i tačno jedno rješenje"],
        extra_schema={"number_domain": {"kind": "enum",
                                        "values": ["integer", "rational"],
                                        "required": True}},
        concept_parameter="shapes",
        parameter_lines={"number_domain": {
            "integer": "- koeficijenti i rješenje su cijeli brojevi",
            "rational": "- koeficijenti i rješenje smiju biti razlomci"}}),
    "classical_probability_basic": _concept_family(
        "Klasična vjerovatnoća", "classical_probability",
        "izračunati klasičnu vjerovatnoću (povoljni kroz svi ishodi)",
        ["classical_probability"],
        {"classical_probability": "klasična vjerovatnoća: broj povoljnih kroz "
                                  "broj svih ishoda"},
        fixed=["- svi ishodi su konačni i jednako vjerovatni; vjerovatnoća se "
               "piše riječju, nikad simbolom P"]),
}


# Porodica poređenja ima JEDAN domen po lekciji — parametar je enum, ne skup.
NEW_FAMILIES["number_comparison_order"]["parameter_schema"]["number_domain"]["kind"] = "enum"


_LEVEL_BOUNDS = {
    "natural_arithmetic_direct": {
        "1": "jedna operacija malim brojevima",
        "2": "dvije povezane operacije ili veći brojevi",
        "3": "tri povezane operacije (odnosno prioritet i zagrade)"},
    "integer_arithmetic_direct": {
        "1": "jedna operacija malim brojevima",
        "2": "dvije povezane operacije ili strože pravilo znakova",
        "3": "tri povezane operacije s miješanim predznacima"},
    "decimal_arithmetic_direct": {
        "1": "jedna operacija, jedno decimalno mjesto",
        "2": "dvije povezane operacije ili dva decimalna mjesta",
        "3": "tri povezane operacije"},
    "rational_arithmetic_direct": {
        "1": "jedna operacija jednostavnim razlomcima",
        "2": "dvije operacije ili nejednaki imenioci s predznacima",
        "3": "tri povezane operacije"},
    "number_comparison_order": {
        "1": "male, jasno razdvojene vrijednosti",
        "2": "bliže vrijednosti ili miješani zapisi",
        "3": "vrlo bliske vrijednosti, položaj između"},
    "absolute_value_opposite": {
        "1": "jedno direktno očitavanje",
        "2": "dva člana ili dvostruka promjena predznaka",
        "3": "tri člana izraza"},
    "divisibility_predicate_application": {
        "1": "jedno pravilo djeljivosti",
        "2": "dva pravila istovremeno",
        "3": "dva-tri pravila, veći brojevi"},
    "common_divisors_multiples": {
        "1": "jedan dati broj, mali djelioci/sadržioci",
        "2": "dva data broja (zajednički djelilac/sadržilac)",
        "3": "veći brojevi ili tri data broja"},
    "prime_structure": {
        "1": "mali brojevi (< 50)",
        "2": "brojevi do 100, „varljivi“ složeni brojevi",
        "3": "faktorizacija s tri prosta faktora"},
    "percent_basic": {
        "1": "osnovni procenti (10 %, 20 %, 25 %, 50 %)",
        "2": "prošireni procenti (5 %, 15 %, 30 %, ...)",
        "3": "necjelobrojni postoci ili traženje cjeline"},
    "arithmetic_mean_direct": {
        "1": "tri broja, cjelobrojna sredina",
        "2": "četiri broja",
        "3": "pet brojeva, decimalna sredina"},
    "power_arithmetic_direct": {
        "1": "jedan stepen/zakon malim brojevima",
        "2": "negativna osnova, negativan izložilac ili količnik",
        "3": "kombinovana dva zakona ili zbir stepena"},
    "square_root_direct": {
        "1": "korijen malog savršenog kvadrata",
        "2": "korijen razlomka ili decimalnog broja",
        "3": "zbir dva korijena"},
    "linear_equation_direct": {
        "1": "jedan računski korak do rješenja",
        "2": "dva koraka ili negativni koeficijenti",
        "3": "tri koraka (sređivanje pa rješavanje)"},
    "classical_probability_basic": {
        "1": "dvije boje, direktan događaj",
        "2": "tri boje",
        "3": "komplementaran događaj"},
}

_REVIEWER_NOTES = {
    "natural_arithmetic_direct": "Invarijanta: svi operandi i međurezultati prirodni.",
    "integer_arithmetic_direct": "Invarijanta: cjelobrojni operandi i rezultat; pravilo znakova.",
    "decimal_arithmetic_direct": "Invarijanta: konačan decimalan zapis svake vidljive vrijednosti.",
    "rational_arithmetic_direct": "Invarijanta: egzaktan racionalan račun s predznacima.",
    "number_comparison_order": "Invarijanta: tačno jedna ekstremna/tražena vrijednost među opcijama.",
    "absolute_value_opposite": "Invarijanta: apsolutna vrijednost nenegativna; suprotan broj mijenja samo predznak.",
    "divisibility_predicate_application": "Invarijanta: tačno jedna opcija zadovoljava SVA navedena pravila (uski orakl to nezavisno dokazuje).",
    "common_divisors_multiples": "Invarijanta: NZD/NZS/djelilac/sadržilac dokazani dijeljenjem bez ostatka.",
    "prime_structure": "Invarijanta: tačno jedna opcija ima traženo svojstvo proste strukture.",
    "percent_basic": "Invarijanta: procenat je stoti dio; znak % nikad u $...$.",
    "arithmetic_mean_direct": "Invarijanta: sredina = zbir kroz broj podataka, egzaktno.",
    "power_arithmetic_direct": "Invarijanta: zakoni stepena nad jednakim osnovama; opcije različitih vrijednosti.",
    "square_root_direct": "Invarijanta: korijen savršenog kvadrata, provjerljiv kvadriranjem.",
    "linear_equation_direct": "Invarijanta: jedinstveno rješenje, provjera uvrštavanjem egzaktna.",
    "classical_probability_basic": "Invarijanta: vjerovatnoća = povoljni/svi, u [0,1].",
}


# ---------------------------------------------------------------------------
# PREGLEDANA TABELA AKTIVACIJA — jedina lista lekcija; kôd nikad ne grana po ID-ju
# ---------------------------------------------------------------------------
# (lesson_id, očekivani naslov, family_id, parametri, izvor_povjerenja)
# izvor_povjerenja: "pilot"  = Faza 2.5 pregled (READY),
#                   "exact"  = Faza 2 exact mapiranje + jednoznačan naslov,
#                   "title"  = jednoznačan kanonski naslov (bez NPP izvora).

ACTIVATIONS = [
    # --- prirodni brojevi (6. razred) -------------------------------------
    ("6-02-003", "Sabiranje i oduzimanje u skupu N0",
     "natural_arithmetic_direct",
     {"allowed_operations": ["add", "subtract"], "number_domain": "natural"},
     "exact"),
    ("6-02-004", "Množenje i dijeljenje u skupu N0",
     "natural_arithmetic_direct",
     {"allowed_operations": ["multiply", "divide"], "number_domain": "natural"},
     "exact"),
    ("6-02-007", "Redoslijed računskih operacija i zagrade",
     "natural_arithmetic_direct",
     {"allowed_operations": ["add", "subtract", "multiply", "divide"],
      "number_domain": "natural", "expression_shape": "order_of_operations"},
     "exact"),
    # --- djeljivost (6. razred, pilot Faze 2.5) ---------------------------
    ("6-03-001", "Djelilac/faktor i sadržilac/višekratnik prirodnog broja",
     "common_divisors_multiples",
     {"concepts": ["divisor_membership", "multiple_membership"]}, "pilot"),
    ("6-03-004", "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",
     "divisibility_predicate_application",
     {"divisors": [2, 3, 4, 5, 6, 9, 10, 15, 25]}, "pilot"),
    ("6-03-005", "Prosti i složeni brojevi",
     "prime_structure", {"concepts": ["prime_classification"]}, "pilot"),
    ("6-03-006", "Relativno prosti brojevi",
     "prime_structure", {"concepts": ["coprime_pairs"]}, "pilot"),
    ("6-03-007", "Rastavljanje složenih brojeva na proste faktore",
     "prime_structure", {"concepts": ["prime_factorization"]}, "pilot"),
    ("6-03-008", "Zajednički djelioci i najveći zajednički djelilac / NZD",
     "common_divisors_multiples", {"concepts": ["gcd"]}, "pilot"),
    ("6-03-009", "Zajednički sadržioci i najmanji zajednički sadržilac / NZS",
     "common_divisors_multiples", {"concepts": ["lcm"]}, "pilot"),
    # --- razlomci: poređenje (6. razred, pilot) ---------------------------
    ("6-04-008", "Upoređivanje razlomaka",
     "number_comparison_order", {"number_domain": "fraction"}, "pilot"),
    # --- decimalni brojevi (6. razred) ------------------------------------
    ("6-05-006", "Upoređivanje decimalnih brojeva",
     "number_comparison_order", {"number_domain": "decimal"}, "exact"),
    ("6-05-008", "Sabiranje i oduzimanje decimalnih brojeva",
     "decimal_arithmetic_direct",
     {"allowed_operations": ["add", "subtract"], "number_domain": "decimal"},
     "exact"),
    ("6-05-009", "Množenje decimalnih brojeva",
     "decimal_arithmetic_direct",
     {"allowed_operations": ["multiply"], "number_domain": "decimal"}, "exact"),
    ("6-05-010", "Dijeljenje decimalnih brojeva",
     "decimal_arithmetic_direct",
     {"allowed_operations": ["divide"], "number_domain": "decimal"}, "exact"),
    # --- postotak i sredina (6. razred) -----------------------------------
    ("6-06-001", "Postotni zapis razlomka",
     "percent_basic", {"concepts": ["fraction_to_percent"]}, "exact"),
    ("6-06-002", "Postotak/procenat i procenat broja",
     "percent_basic", {"concepts": ["percent_of_number", "fraction_to_percent"]},
     "exact"),
    ("6-06-004", "Aritmetička sredina danih brojeva / dva broja",
     "arithmetic_mean_direct", {"concepts": ["mean"]}, "exact"),
    # --- cijeli brojevi (7. razred) ---------------------------------------
    ("7-02-004", "Suprotni cijeli brojevi",
     "absolute_value_opposite",
     {"concepts": ["opposite"], "number_domain": "integer"}, "exact"),
    ("7-02-005", "Apsolutna vrijednost cijelog broja",
     "absolute_value_opposite",
     {"concepts": ["absolute_value"], "number_domain": "integer"}, "exact"),
    ("7-02-006", "Upoređivanje i uređenje cijelih brojeva",
     "number_comparison_order", {"number_domain": "integer"}, "exact"),
    ("7-02-007", "Sabiranje cijelih brojeva istih znakova",
     "integer_arithmetic_direct",
     {"allowed_operations": ["add"], "number_domain": "integer",
      "sign_scope": "same_signs"}, "exact"),
    ("7-02-008", "Sabiranje cijelih brojeva različitih znakova",
     "integer_arithmetic_direct",
     {"allowed_operations": ["add"], "number_domain": "integer",
      "sign_scope": "different_signs"}, "exact"),
    ("7-02-009", "Oduzimanje cijelih brojeva",
     "integer_arithmetic_direct",
     {"allowed_operations": ["subtract"], "number_domain": "integer"}, "exact"),
    ("7-02-011", "Množenje cijelih brojeva i pravilo znakova",
     "integer_arithmetic_direct",
     {"allowed_operations": ["multiply"], "number_domain": "integer"}, "exact"),
    ("7-02-012", "Dijeljenje cijelih brojeva i pravilo znakova",
     "integer_arithmetic_direct",
     {"allowed_operations": ["divide"], "number_domain": "integer"}, "exact"),
    ("7-02-013", "Proizvod više cijelih faktora",
     "integer_arithmetic_direct",
     {"allowed_operations": ["multiply"], "number_domain": "integer",
      "expression_shape": "multi_factor"}, "title"),
    ("7-02-015", "Brojevni izrazi sa cijelim brojevima",
     "integer_arithmetic_direct",
     {"allowed_operations": ["add", "subtract", "multiply"],
      "number_domain": "integer"}, "exact"),
    ("7-02-016", "Jednačine sa sabiranjem i oduzimanjem u Z",
     "linear_equation_direct",
     {"shapes": ["one_step_additive"], "number_domain": "integer"}, "exact"),
    ("7-02-017", "Jednačine sa množenjem i dijeljenjem u Z",
     "linear_equation_direct",
     {"shapes": ["one_step_multiplicative"], "number_domain": "integer"},
     "exact"),
    # --- racionalni brojevi (7. razred) -----------------------------------
    ("7-03-004", "Suprotan racionalni broj",
     "absolute_value_opposite",
     {"concepts": ["opposite"], "number_domain": "rational"}, "exact"),
    ("7-03-005", "Apsolutna vrijednost racionalnog broja",
     "absolute_value_opposite",
     {"concepts": ["absolute_value"], "number_domain": "rational"}, "exact"),
    ("7-03-006", "Upoređivanje racionalnih brojeva",
     "number_comparison_order", {"number_domain": "rational"}, "exact"),
    ("7-03-009", "Sabiranje racionalnih brojeva",
     "rational_arithmetic_direct",
     {"allowed_operations": ["add"], "number_domain": "rational_signed"},
     "exact"),
    ("7-03-010", "Oduzimanje racionalnih brojeva",
     "rational_arithmetic_direct",
     {"allowed_operations": ["subtract"], "number_domain": "rational_signed"},
     "exact"),
    ("7-03-011", "Množenje racionalnih brojeva",
     "rational_arithmetic_direct",
     {"allowed_operations": ["multiply"], "number_domain": "rational_signed"},
     "exact"),
    ("7-03-012", "Dijeljenje racionalnih brojeva",
     "rational_arithmetic_direct",
     {"allowed_operations": ["divide"], "number_domain": "rational_signed"},
     "exact"),
    ("7-03-016", "Jednačine u Q sa sabiranjem i oduzimanjem",
     "linear_equation_direct",
     {"shapes": ["one_step_additive"], "number_domain": "rational"}, "exact"),
    ("7-03-017", "Jednačine u Q sa množenjem i dijeljenjem",
     "linear_equation_direct",
     {"shapes": ["one_step_multiplicative"], "number_domain": "rational"},
     "exact"),
    # --- realni brojevi, stepeni, korijeni (8. razred) --------------------
    ("8-01-005", "Apsolutna vrijednost realnog broja",
     "absolute_value_opposite",
     {"concepts": ["absolute_value"], "number_domain": "rational"}, "exact"),
    ("8-01-006", "Kvadrat racionalnog broja",
     "power_arithmetic_direct", {"concepts": ["square_value"]}, "exact"),
    ("8-01-007", "Savršeni kvadrati i procjena",
     "square_root_direct", {"concepts": ["perfect_square_recognition"]},
     "title"),
    ("8-01-008", "Kvadratni korijen nenegativnog racionalnog broja",
     "square_root_direct", {"concepts": ["square_root_value"]}, "exact"),
    ("8-01-013", "Stepen sa cijelim izložiocem",
     "power_arithmetic_direct", {"concepts": ["power_value"]}, "exact"),
    ("8-01-014", "Nulti i negativni eksponent",
     "power_arithmetic_direct", {"concepts": ["zero_negative_exponent"]},
     "exact"),
    ("8-01-015", "Množenje i dijeljenje stepena jednakih osnova",
     "power_arithmetic_direct", {"concepts": ["same_base_product_quotient"]},
     "exact"),
    ("8-01-016", "Stepen proizvoda, količnika i stepena",
     "power_arithmetic_direct", {"concepts": ["power_of_power_product"]},
     "exact"),
    # --- podaci i vjerovatnoća (8. razred) --------------------------------
    ("8-06-009", "Aritmetička sredina",
     "arithmetic_mean_direct", {"concepts": ["mean"]}, "exact"),
    ("8-06-012", "Povoljni ishodi i klasična vjerovatnoća",
     "classical_probability_basic", {"concepts": ["classical_probability"]},
     "exact"),
    # --- 9. razred --------------------------------------------------------
    ("9-04-003", "Jednačina sa zagradama",
     "linear_equation_direct",
     {"shapes": ["parentheses"], "number_domain": "integer"}, "exact"),
    ("9-04-019", "Provjera rješenja jednačine i nejednačine",
     "linear_equation_direct",
     {"shapes": ["check_solution", "check_inequality"],
      "number_domain": "integer"}, "title"),
    ("9-08-002", "Vjerovatnoća slučajnog događaja",
     "classical_probability_basic", {"concepts": ["classical_probability"]},
     "exact"),
    ("9-08-010", "Aritmetička sredina i interpretacija podataka",
     "arithmetic_mean_direct", {"concepts": ["mean"]}, "exact"),
]


# ---------------------------------------------------------------------------
# KLASIFIKACIJA PREOSTALIH LEKCIJA (izvještaj, bez uticaja na ponašanje)
# ---------------------------------------------------------------------------

_VISUAL_KEYWORDS = ("konstrukc", "crtanje", "crtež", "dijagram", "grafič",
                    "prikaz", "koordinat", "simetri", "preslikav", "izometri",
                    "vektor", "mreža", "čitanje i kritičko")
_WORD_PROBLEM_KEYWORDS = ("tekstualni", "primjena", "problemi")
_PROOF_KEYWORDS = ("dokaz", "teorema", "diskusija")
_NEEDS_EXTENSION = {
    # Kandidati čiji bi zadaci stali u POSTOJEĆE motore uz proširenje
    # parametara/oblika — namjerno NEaktivirani u ovoj fazi.
    "6-02-002", "6-02-005", "6-02-008", "6-03-002", "6-03-003", "6-05-003",
    "6-05-007", "7-02-018", "7-02-019", "7-02-020", "7-03-008", "7-03-014",
    "7-03-015", "7-03-018", "7-03-019", "7-03-021", "8-01-004", "8-01-009",
    "8-01-010", "8-01-012", "8-01-017", "9-04-004", "9-04-014", "9-04-016",
    "8-06-013", "9-08-011",
}


def _classify(lesson_id, title, activated):
    lowered = title.lower()
    if lesson_id in activated:
        return "DETERMINISTIC_READY"
    if lesson_id in _NEEDS_EXTENSION:
        return "DETERMINISTIC_NEEDS_EXISTING_CAPABILITY_EXTENSION"
    if any(keyword in lowered for keyword in _PROOF_KEYWORDS):
        return "MODEL_ONLY_FOR_NOW"
    if any(keyword in lowered for keyword in _VISUAL_KEYWORDS):
        return "VISUAL_OR_CONSTRUCTION_REQUIRED"
    if any(keyword in lowered for keyword in _WORD_PROBLEM_KEYWORDS):
        return "MODEL_ONLY_FOR_NOW"
    if any(keyword in lowered for keyword in
           ("jednačin", "nejednačin", "razmjer", "proporc", "procent",
            "kamata", "stepen", "korijen", "polinom", "izraz", "funkcij",
            "frekvencij", "vjerovatno", "pretvaranje", "zaokruživanje")):
        return "DETERMINISTIC_NEEDS_NEW_CAPABILITY"
    return "MODEL_ONLY_FOR_NOW"


# ---------------------------------------------------------------------------
# DOKAZI IZ FAZE 2
# ---------------------------------------------------------------------------

def _load_exact_evidence():
    """lesson_id → sortirana lista item_id-jeva s relacijom `exact`."""
    if not FAZA2_XLSX.exists():
        return {}
    try:
        import openpyxl
    except ImportError:
        return {}
    workbook = openpyxl.load_workbook(FAZA2_XLSX, read_only=True, data_only=True)
    if "Mapiranje" not in workbook.sheetnames:
        return {}
    sheet = workbook["Mapiranje"]
    rows = sheet.iter_rows(values_only=True)
    header = [str(cell or "") for cell in next(rows)]
    index = {name: position for position, name in enumerate(header)}
    lesson_column = next((name for name in header if "lesson" in name.lower()
                          or "lekcij" in name.lower()), None)
    item_column = next((name for name in header if name.lower() == "item_id"), None)
    relation_column = next((name for name in header
                            if "relation" in name.lower()), None)
    if not (lesson_column and item_column and relation_column):
        return {}
    evidence = {}
    for row in rows:
        relation = str(row[index[relation_column]] or "")
        if relation != "exact":
            continue
        lesson_id = str(row[index[lesson_column]] or "")
        item_id = str(row[index[item_column]] or "")
        if lesson_id and item_id:
            evidence.setdefault(lesson_id, set()).add(item_id)
    workbook.close()
    return {key: sorted(values) for key, values in evidence.items()}


# ---------------------------------------------------------------------------
# GRADNJA ARTEFAKATA
# ---------------------------------------------------------------------------

class OnboardingError(RuntimeError):
    pass


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def _canonical_lessons():
    payload = _read(TOPICS_PATH)
    return {lesson["id"]: (grade_key, lesson["oblast"], lesson["title"])
            for grade_key, grade in payload["grades"].items()
            for lesson in grade["lessons"]}


def build_assignment_rows(lessons, exact_evidence):
    rows = []
    for lesson_id, expected_title, family_id, parameters, provenance in ACTIVATIONS:
        if lesson_id not in lessons:
            raise OnboardingError(f"{lesson_id}: lekcija ne postoji u kurikulumu")
        _grade, _oblast, title = lessons[lesson_id]
        if title != expected_title:
            raise OnboardingError(
                f"{lesson_id}: naslov se ne poklapa s pregledanim "
                f"({title!r} != {expected_title!r})")
        if family_id not in NEW_FAMILIES:
            raise OnboardingError(f"{lesson_id}: nepoznata porodica {family_id}")
        evidence = list(exact_evidence.get(lesson_id, ()))
        if provenance == "pilot":
            evidence.append("F25-PILOT-READY")
        if not evidence:
            # Izričita oznaka aktivacije na osnovu jednoznačnog naslova —
            # nikad izmišljen NPP izvor.
            evidence.append(f"CANON-TITLE-{lesson_id}")
        rows.append({
            "lesson_id": lesson_id,
            "family_id": family_id,
            "enforcement_mode": "blocking",
            "activation_class": "READY",
            "evidence_ids": sorted(set(evidence)),
            "parameters": parameters,
            "level_bounds": _LEVEL_BOUNDS[family_id],
            "forbidden_neighbour_skills": [],
            "reviewer_note": _REVIEWER_NOTES[family_id],
        })
    return rows


def merge_sources():
    lessons = _canonical_lessons()
    exact_evidence = _load_exact_evidence()

    families_payload = _read(FAMILIES_PATH)
    families = families_payload["families"]
    generated_rows = build_assignment_rows(lessons, exact_evidence)

    for family_id, definition in NEW_FAMILIES.items():
        families[family_id] = definition
    families_payload["families"] = dict(sorted(families.items()))

    assignments_payload = _read(ASSIGNMENTS_PATH)
    activated_ids = {row["lesson_id"] for row in generated_rows}
    kept = [row for row in assignments_payload["assignments"]
            if row["lesson_id"] not in activated_ids
            and row["family_id"] not in NEW_FAMILIES]
    overlap = {row["lesson_id"] for row in kept} & activated_ids
    if overlap:
        raise OnboardingError(f"dodjela postoji dvaput: {sorted(overlap)}")
    assignments_payload["assignments"] = sorted(
        kept + generated_rows, key=lambda row: row["lesson_id"])
    assignments_payload["contract_version"] = CONTRACT_VERSION
    return families_payload, assignments_payload, lessons


def verify_generator_support():
    """Svaka blocking dodjela nove porodice MORA imati potpun generator."""
    from matbot import deterministic as registry
    from matbot.semantics import contracts as semantic_contracts

    semantic_contracts.reset_cache()
    unsupported = []
    for lesson_id, contract in sorted(semantic_contracts.all_contracts().items()):
        module = registry.GENERATORS.get(contract.family_id)
        if module is None:
            unsupported.append((lesson_id, contract.family_id, "nema generatora"))
        elif not module.supports(dict(contract.parameters)):
            unsupported.append((lesson_id, contract.family_id,
                                "parametri nisu podržani"))
    if unsupported:
        raise OnboardingError(f"generator ne pokriva: {unsupported}")


def build_report(lessons):
    activated = {row[0]: row[2] for row in ACTIVATIONS}
    activated["6-04-009"] = "fraction_arithmetic_direct"
    activated["6-04-010"] = "fraction_arithmetic_direct"
    activated["6-04-011"] = "fraction_arithmetic_direct"
    activated["6-04-012"] = "fraction_arithmetic_direct"

    classes, by_grade, by_family, table = {}, {}, {}, []
    for lesson_id, (grade, oblast, title) in sorted(lessons.items()):
        classification = _classify(lesson_id, title, activated)
        classes[classification] = classes.get(classification, 0) + 1
        if classification == "DETERMINISTIC_READY":
            family = activated[lesson_id]
            by_family[family] = by_family.get(family, 0) + 1
            by_grade[grade] = by_grade.get(grade, 0) + 1
        table.append({"lesson_id": lesson_id, "grade": grade, "oblast": oblast,
                      "title": title, "class": classification,
                      "family": activated.get(lesson_id, "")})
    total = len(lessons)
    deterministic = classes.get("DETERMINISTIC_READY", 0)
    return {
        "contract_version": CONTRACT_VERSION,
        "total_lessons": total,
        "deterministic_lessons": deterministic,
        "deterministic_share": round(deterministic / total, 4),
        "class_totals": dict(sorted(classes.items())),
        "deterministic_by_grade": dict(sorted(by_grade.items())),
        "deterministic_by_family": dict(sorted(by_family.items())),
        "lessons": table,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Masovno determinističko uključivanje")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)

    families_payload, assignments_payload, lessons = merge_sources()
    report = build_report(lessons)

    if args.check:
        current_families = _read(FAMILIES_PATH)
        current_assignments = _read(ASSIGNMENTS_PATH)
        current_report = _read(REPORT_PATH) if REPORT_PATH.exists() else None
        if (current_families != families_payload
                or current_assignments != assignments_payload
                or current_report != report):
            print("Artefakti NISU u koraku s tabelom aktivacija.", file=sys.stderr)
            return 1
        verify_generator_support()
        print("OK: artefakti su u koraku s tabelom aktivacija.")
        return 0

    _write(FAMILIES_PATH, families_payload)
    _write(ASSIGNMENTS_PATH, assignments_payload)
    _write(REPORT_PATH, report)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_lesson_semantics", ROOT / "scripts" / "build_lesson_semantics.py")
    build_lesson_semantics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_lesson_semantics)
    build_lesson_semantics.write()
    verify_generator_support()

    if args.report:
        print(f"UKUPNO LEKCIJA:      {report['total_lessons']}")
        print(f"DETERMINISTIČKI:     {report['deterministic_lessons']} "
              f"({report['deterministic_share'] * 100:.1f} %)")
        for name, count in report["class_totals"].items():
            print(f"  {name:48s} {count}")
        print("Po razredu:", report["deterministic_by_grade"])
        for family, count in report["deterministic_by_family"].items():
            print(f"  {family:36s} {count}")
    print(f"OK: {len(assignments_payload['assignments'])} dodjela, "
          f"{len(families_payload['families'])} porodica")
    return 0


if __name__ == "__main__":
    sys.exit(main())
