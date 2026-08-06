"""KO ODLUČUJE O ČEMU u recenzentovom konačnom paketu (Faza 4C).

ZAŠTO POSTOJI. `validate_reviewer` je do sada tretirao SVIH jedanaest
`checks.*` polja kao smrtonosna: jedna netačna samoprijavljena zastavica
obarala je cio turn i kad je vraćen KOMPLETAN paket koji prolazi sve
determinističke validatore. Živi nalazi:

    kampanja F4A  correct + language_age_appropriate=false → turn izgubljen
    kampanja A+B  correct + marked_option_correct=false    → turn izgubljen

Te dvije tvrdnje NISU iste težine: prva je pitanje tona, druga je pitanje
tačnosti označenog odgovora. Zato blanket pravilo („ignoriši svaku false
provjeru“) nije dozvoljeno — potrebna je izričita matrica.

HIJERARHIJA AUTORITETA (redom):

  1. DETERMINISTIČKI SERVERSKI VALIDATOR, gdje postoji, je mjerodavan.
     Recenzentov boolean je tada samo dijagnostika: server ionako ponovo
     pokreće isti validator nad KONAČNIM paketom (package_preflight +
     objava), pa netačan boolean ne smije sam obarati turn — a netačan
     `true` ionako nikad ništa ne dokazuje.

  2. STRUKTURA ODLUKE je mjerodavna i nepromijenjena: `correct` traži
     kompletan konačan paket, `fail_closed` nikad ne objavljuje, a odobren
     ili ispravljen paket se UVIJEK nezavisno revalidira.

  3. SAMOPRIJAVLJENA PROVJERA NIJE DOKAZ. Nikad se ne koristi kao potvrda.

  4. SAVJETODAVNA provjera sama po sebi ne stavlja veto na kompletan
     ispravljen paket koji je prošao sve mjerodavne validatore.

  5. Provjera BEZ determinističke zamjene, a sigurnosno kritična, OSTAJE
     blokirajuća. Ništa ovdje ne postaje savjetodavno zato da bi gate
     češće prolazio.

Sve četiri klase zajedno moraju pokriti svako boolean polje šeme, bez
preklapanja — čuva `tests/test_reviewer_check_authority.py`.
"""

# --- 1) Mjerodavan je SERVER: boolean je samo dijagnostika ------------------
# Za svaku od ovih provjera postoji deterministički validator koji se pokreće
# nad KONAČNIM paketom, prije objave i prije ijedne mutacije sesije.
DETERMINISTIC_AUTHORITY_CHECKS = frozenset({
    "intent_handled",
    "mathjax_valid",
    "task_package_consistent",
    "difficulty_evidence_valid",
})

AUTHORITATIVE_VALIDATOR = {
    "intent_handled":
        "schema.validate_final — pravilo polja po namjeri (server, ne model, "
        "odlučuje šta smije biti popunjeno)",
    "mathjax_valid":
        "mathsafe.sanitize_and_validate_math_text preko package_preflight "
        "(unsafe_task_text_notation / unsafe_option_notation / "
        "unsafe_solution_notation) i ponovo u objavi",
    "task_package_consistent":
        "package_preflight: validate_task (ID/indeks označene opcije, broj "
        "opcija) + expected_answer_not_marked_option + option_equivalence",
    "difficulty_evidence_valid":
        "schema.difficulty_evidence_errors nad recenzentovim VLASTITIM "
        "`reviewed_difficulty_evidence` i deklarisanim ciljnim nivoom",
}

# --- 2) SIGURNOSNO KRITIČNO bez determinističke zamjene: OSTAJE blokirajuće -
# Nijedan serverski validator ne može ovo dokazati za proizvoljnu lekciju, a
# netačna tvrdnja ovdje znači da bi učenik dobio pogrešnu matematiku ili
# zadatak izvan lekcije. Fail-closed je ispravan ishod.
MODEL_ONLY_BLOCKING_CHECKS = frozenset({
    "math_correct",
    "marked_option_correct",
    "inside_lesson",
    "task_solvable_and_unambiguous",
})

# --- 3) SAVJETODAVNO: kvalitet, ne ispravnost -------------------------------
# Netačna vrijednost ovdje opisuje stil ili metapodatak, nikad matematiku koju
# učenik vidi. Izgubiti cio zadatak zbog toga je lošije za učenika nego
# objaviti paket koji je prošao sve mjerodavne validatore. Vrijednost se i
# dalje LOGUJE (vidi pipeline), pa se signal ne gubi.
ADVISORY_CHECKS = frozenset({
    "language_age_appropriate",     # ton i uzrast — bez serverskog oraklа
    "response_addresses_student",   # kvalitet obraćanja
    "task_signature_consistent",    # metapodatak za otkrivanje ponavljanja;
                                    # samo ponavljanje hvata deterministički
                                    # potpis u pipeline-u
})

# --- 4) STRUKTURNE tvrdnje koje `validate_reviewer` provjerava posebno ------
STRUCTURAL_CHECKS = frozenset({
    "independently_solved",        # obavezno za namjere koje prave zadatak
    "difficulty_direction_correct",  # obavezno samo za stvarnu promjenu smjera
})

ADVISORY_REASON = {
    "language_age_appropriate":
        "ton/uzrast nema serverski orakl; pogrešan ton ne objavljuje pogrešnu "
        "matematiku, a gubitak zadatka je veća šteta za učenika",
    "response_addresses_student":
        "kvalitet obraćanja nema serverski orakl i ne utiče na tačnost paketa",
    "task_signature_consistent":
        "potpis je interni metapodatak; stvarno ponavljanje deterministički "
        "hvata potpis zadatka u pipeline-u, ne ovaj boolean",
}


def blocking_failed_checks(checks):
    """Imena SAMO onih netačnih provjera koje same po sebi obaraju paket.

    Deterministički pokrivene i savjetodavne provjere se ovdje NE pojavljuju —
    o njima odlučuje serverski validator, odnosno ništa."""
    return tuple(sorted(
        name for name in MODEL_ONLY_BLOCKING_CHECKS
        if getattr(checks, name, True) is not True
    ))


def diagnostic_failed_checks(checks):
    """Netačne provjere koje NE obaraju turn — isključivo za log."""
    names = DETERMINISTIC_AUTHORITY_CHECKS | ADVISORY_CHECKS
    return tuple(sorted(
        name for name in names if getattr(checks, name, True) is not True
    ))
