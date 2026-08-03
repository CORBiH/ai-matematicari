"""Univerzalni AI Tutor Pipeline — JEDAN aktivni put za svih 534 lekcije.

Zamjenjuje raniju podjelu na dva aktivna Practice puta (6 lekcija s ugovorom
kroz deterministički motor + 528 lekcija kroz legacy porodice). Sada svaka
lekcija ide kroz isti niz:

    poruka učenika → LessonContext → Tutor poziv → Reviewer poziv
    → zajednička serverska validacija → objava → copy-on-write stanje

Granica poziva: TAČNO DVA modela poziva po neblokiranom turnu. Bez retryja,
bez repair petlje, bez trećeg poziva, bez skrivene zamjene.

Ulazne tačke:
    lesson_context.build(grade, topic_id)  → LessonContext (svih 534)
    pipeline.run_turn(store, llm, turn)    → JSON-spreman odgovor
"""
