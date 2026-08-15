"""Katalog KATEGORIJA GREŠKE — podaci ugovora, ne izvršni motor.

Do povlačenja starog jednopozivnog motora (2026-08-14) ovaj modul je i
STRUKTURNO IZVODIO kategoriju greške iz dva uzastopna koraka i renderovao
opcije za K1/K3 generator. Taj generator više ne postoji, pa je izvođenje
uklonjeno.

Ostaju TAČNO dva kataloga, i oba su i dalje živa: `registry.load_all()` kroz
`archetypes.assert_supported()` njima provjerava da uključen ugovor ne navede
kategoriju koju projekat ne poznaje. `error_category_set` iz tih ugovora ide
kao PODATAK u aktivni Luna prompt (matbot/tutor/lesson_context.py), pa
neprovjerena kategorija ne bi bila bezopasna.
"""

ERROR_CATEGORY_LABELS = {
    "combined_denominators":
        "Sabrao je i nazivnike, a nazivnik je trebao ostati isti.",
    "wrong_numerator":
        "Nazivnik je zadržao ispravno, ali je brojnik pogrešno izračunao.",
    "missed_reciprocal":
        "Pri dijeljenju nije pomnožio recipročnom vrijednošću drugog razlomka.",
    "wrong_operation":
        "Primijenio je pogrešnu računsku operaciju.",
    "wrong_product":
        "Proizvod razlomaka je pogrešno izračunao.",
    "unequal_scaling":
        "Brojnik i nazivnik nije pomnožio istim brojem.",
    "wrong_reduction":
        "Brojnik i nazivnik nije podijelio istim brojem.",
    "incorrect_conversion":
        "Razlomke je pogrešno sveo na zajednički nazivnik.",
}

# Kategorije za koje POSTOJI strukturni izvođač I projektni tekst. Ugovor koji
# navede bilo šta izvan ovoga pada na UČITAVANJU (archetypes.assert_supported) —
# jer bez teksta server ne bi mogao sam sastaviti opciju.
DERIVABLE_ERROR_CATEGORIES = frozenset({
    "combined_denominators", "wrong_numerator", "missed_reciprocal",
    "wrong_operation", "wrong_product", "unequal_scaling", "wrong_reduction",
    "incorrect_conversion",
})
