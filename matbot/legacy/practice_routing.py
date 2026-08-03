"""Zatečeno routiranje porodica po KANONSKOM ID-ju lekcije (samo legacy put).

ZAŠTO POSTOJI: živi nalaz je pokazao da routing samo po širokoj oblasti
„Razlomci“ svakoj lekciji daje isti prvi zadatak (proširivanje), čak i kad
lekcija traži sabiranje, množenje ili brojevni izraz. Ove liste to popravljaju
za konkretne lekcije šestog razreda.

ZAŠTO JE OVDJE, A NE U `matbot/task_families.py`: grananje po ID-ju lekcije je
tačno ono što univerzalni motor ukida. Dok te lekcije ne dobiju ugovor, njihovo
ponašanje se mora sačuvati NEPROMIJENJENO — ali izolovano, da bi bilo očigledno
šta je privremeno i šta Faza D briše.

NE DODAVATI NOVE UNOSE. Nova lekcija se opisuje ugovorom u
`data/lesson_contracts.json` (vidi docs/LESSON_CONTRACTS.md). Ova mapa se samo
prazni kako lekcije prelaze na motor.
"""

# Lekcije šestog razreda iz oblasti Razlomci s ručno određenim redoslijedom
# porodica. Šest pilot lekcija (6-04-005/006/009/010/011/012) OSTAJE u mapi radi
# doslovnog očuvanja zatečenog ponašanja, ali ih produkcija nikad ne rutira
# ovuda — one imaju uključen ugovor i idu kroz motor.
GRADE6_FRACTION_FAMILIES_BY_TOPIC = {
    "6-04-001": ["recognize_correct_statement", "find_missing_value", "detect_student_error"],
    "6-04-002": ["fraction_word_problem", "find_missing_value", "recognize_correct_statement"],
    "6-04-003": ["recognize_correct_statement", "direct_computation", "detect_student_error"],
    "6-04-004": ["compare_fractions", "find_missing_value", "recognize_correct_statement"],
    "6-04-005": [
        "expand_to_given_denominator", "find_expansion_factor",
        "find_missing_numerator", "recognize_equivalent_fraction", "detect_student_error",
    ],
    "6-04-006": [
        "find_expansion_factor", "recognize_equivalent_fraction", "detect_student_error",
    ],
    "6-04-007": [
        "expand_to_given_denominator", "find_missing_numerator",
        "recognize_equivalent_fraction", "compare_fractions",
    ],
    "6-04-008": ["compare_fractions", "recognize_equivalent_fraction", "detect_student_error"],
    "6-04-009": ["fraction_add_subtract_equal", "detect_student_error", "fraction_word_problem"],
    "6-04-010": ["fraction_add_subtract_unlike", "detect_student_error", "fraction_word_problem"],
    "6-04-011": ["fraction_multiplication", "detect_student_error", "fraction_word_problem"],
    "6-04-012": ["fraction_division", "detect_student_error", "fraction_word_problem"],
    "6-04-013": ["fraction_operation", "recognize_correct_statement", "detect_student_error"],
    "6-04-014": ["fraction_expression", "detect_student_error", "compare_fractions"],
    "6-04-015": ["fraction_word_problem", "fraction_operation", "detect_student_error"],
}

# Ostale lekcije šestog razreda koje samo dijele širu oblast razlomaka (npr.
# decimalni zapis) ne dobijaju proširivanje kao tihi podrazumijevani početak.
GRADE6_NON_EXPANSION_FRACTION_FAMILIES = [
    "fraction_operation", "compare_fractions", "detect_student_error",
    "fraction_word_problem",
]


def grade6_fraction_families(grade, lesson_id):
    """Zatečena lista porodica za lekciju razlomaka 6. razreda, ili None.

    None znači „nema posebnog pravila“ — pozivalac tada koristi opšti domenski
    routing, tačno kao i prije uvođenja motora ugovora."""
    if grade != 6:
        return None
    if lesson_id in GRADE6_FRACTION_FAMILIES_BY_TOPIC:
        return list(GRADE6_FRACTION_FAMILIES_BY_TOPIC[lesson_id])
    if lesson_id:
        return list(GRADE6_NON_EXPANSION_FRACTION_FAMILIES)
    return None


def topic_ids():
    """ID-jevi lekcija koje ovaj privremeni sloj još drži (za izvještaje)."""
    return sorted(GRADE6_FRACTION_FAMILIES_BY_TOPIC)
