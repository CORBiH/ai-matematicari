"""Minimalna strict šema AI odgovora za jedan Practice turn + server validacija.

Model smije reći SAMO ovo — svaka promjena stanja izvodi se serverski iz ovih
polja. Model nikad ne postavlja ID-jeve, brojače, verzije ni state patcheve.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from matbot import config


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class NewTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    expected_answer: str
    difficulty: Literal["easy", "standard", "hard"]
    options: list[Option]
    correct_option_index: int

    # --- INTERNI metapodaci o pedagoškom obliku (server-only) ---------------
    # Model ih deklariše, server ih UNAKRSNO provjerava s dodijeljenom
    # porodicom I sa stvarnim vidljivim tekstom (matbot/task_family_validation.py).
    # NIKAD ne idu u browser (vidi practice._next_state). Deklaracija sama po
    # sebi NIJE dokaz — model može tvrditi ispravnu porodicu a generisati
    # pogrešan zadatak, pa strukturna provjera ostaje obavezna.
    # Opcionalni su radi kompatibilnosti sa starijim odgovorima; kad izostanu,
    # preskače se samo unakrsna provjera metapodataka.
    task_family: Optional[str] = None
    student_must_find: Optional[Literal[
        "expanded_fraction", "expansion_factor", "missing_numerator", "missing_denominator",
        "equivalent_fraction", "incorrect_step", "variable_value", "ordered_pair",
        "formula", "missing_dimension", "method", "number_of_solutions",
        "value", "statement", "comparison", "unit_value", "next_step",
    ]] = None
    answer_kind: Optional[Literal[
        "integer", "decimal", "fraction", "ordered_pair", "expression",
        "formula", "option_label", "short_text",
    ]] = None
    task_form: Optional[Literal[
        "direct_calculation", "missing_value", "recognition", "error_detection",
        "method_selection", "interpretation", "word_problem", "construction_step",
    ]] = None


class PracticeTurnOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    evaluation: Optional[Literal["correct", "partially_correct", "incorrect"]]
    gave_hint: bool
    new_task: Optional[NewTask]
    # Sažet sljedeći korak, odvojen od 'reply'. Server ga koristi da SAM sastavi
    # kratak feedback na prvi pogrešan klik („Netačno.“ + hint) — vidi
    # matbot/feedback.py. Opcionalno: stari klijenti šeme i dalje prolaze, a
    # kad izostane, server pada nazad na 'reply'. Nikad ne ide u browser sirov.
    hint: Optional[str] = None


class ExplainTurnOutput(BaseModel):
    """Explain mod: namjerno NAJMANJA moguća šema — samo vidljivi tekst.
    Bez evaluation/gave_hint/new_task: Explain ništa ne ocjenjuje, ne daje
    hintove po nivou i ne mijenja nikakav aktivni zadatak, pa svako dodatno
    polje samo otvara prostor za kontradikciju."""

    model_config = ConfigDict(extra="forbid")

    reply: str


class QuickTurnOutput(BaseModel):
    """Quick mod ("Samo rezultat"): namjerno NAJMANJA moguća šema — samo
    vidljivi tekst. Bez evaluation/gave_hint/new_task/options: Quick ništa
    ne ocjenjuje, ne daje hintove po nivou, ne stvara Practice zadatak i ne
    nudi opcije, pa svako dodatno polje samo otvara prostor za kontradikciju."""

    model_config = ConfigDict(extra="forbid")

    reply: str


class InvalidOutputError(ValueError):
    """AI odgovor je strukturno validan JSON, ali sadržajno neupotrebljiv."""


def validate_output(out: PracticeTurnOutput, require_reply: bool = True) -> None:
    """Server-side provjere povrh strict JSON šeme. Baca InvalidOutputError.

    NAPOMENA O OBIMU: server ovdje provjerava SAMO strukturu (neprazna polja,
    dužinska ograničenja, dozvoljene enum vrijednosti). NE postoji nikakva
    provjera da li 'new_task' stvarno pripada istoj lekciji/oblasti — nema
    lesson ID-ja u šemi niti matematičkog/verifier motora koji bi to mogao
    deterministički dokazati bez dodatnog AI poziva (što je van dizajna Faze 1).
    Pripadnost lekciji je ISKLJUČIVO prompt instrukcija (matbot/prompts.py:
    "Novi zadatak ostaje u ISTOJ lekciji...") — fake testovi mogu dokazati samo
    da se prompt instrukcija ŠALJE modelu, ne da je model poštuje. Da li model
    stvarno ostaje u temi je pitanje za live eval set, ne za ovaj validator.

    require_reply=False: SAMO za prvi pogrešan klik (vidi
    practice._handle_choice_answer) — tamo server SAM sastavlja vidljiv
    odgovor iz 'hint' („Netačno.“ + hint, matbot/feedback.py), pa je prazan
    'reply' bezopasan DOK GOD je 'hint' prisutan i neprazan. Svaki drugi put
    (novi zadatak, tačan odgovor, drugi pogrešan klik/reveal, obično pitanje)
    poziva se s podrazumijevanim require_reply=True — tu reply nosi stvarni
    sadržaj koji učenik čita, pa ostaje obavezan kao i prije.
    """
    reply_present = bool((out.reply or "").strip())
    hint_present = bool((out.hint or "").strip())
    if not reply_present:
        if require_reply:
            raise InvalidOutputError("prazan reply")
        if not hint_present:
            raise InvalidOutputError("prazan reply i prazan hint")
    if len(out.reply) > config.MAX_REPLY_CHARS:
        raise InvalidOutputError("predug reply")
    if out.hint is not None and len(out.hint) > config.MAX_REPLY_CHARS:
        raise InvalidOutputError("predug hint")
    if out.new_task is not None:
        if not (out.new_task.text or "").strip():
            raise InvalidOutputError("novi zadatak bez teksta")
        if len(out.new_task.text) > config.MAX_TASK_CHARS:
            raise InvalidOutputError("predug tekst zadatka")
        if not (out.new_task.expected_answer or "").strip():
            raise InvalidOutputError("novi zadatak bez očekivanog odgovora")
        if len(out.new_task.expected_answer) > config.MAX_EXPECTED_ANSWER_CHARS:
            raise InvalidOutputError("predug očekivani odgovor")
        _validate_options(out.new_task.options, out.new_task.correct_option_index)


def _validate_options(options, correct_index) -> None:
    """Deterministička provjera OBLIKA 4 ponuđene opcije (bez novog AI poziva).

    OBIM: ovdje se provjerava samo STRUKTURA (broj, praznina, dužina, indeks).
    JEDINSTVENOST se NAMJERNO provjerava kasnije, u practice._apply_new_task,
    nad SANITIZOVANIM tekstom — vidi tamo. Dva razloga:

      1. Ovdje je tekst još SIROV; dvije različite sirove opcije mogu se nakon
         sanitizacije svesti na isti vidljivi tekst („sqrt2“ i „\\sqrt{2}“), pa
         bi provjera na ovom mjestu propustila stvarni duplikat u browseru.
      2. Ranija provjera je radila `text.lower()` i time spajala `$R=2r$` i
         `$r=2R$` u isti ključ — u ovom projektu veličina slova nosi značenje
         (r/R, d/D, P/p, O/o, B/b, H/h), pa je to lažno odbijalo validne
         zadatke (živi nalaz, poziv 12 fokusiranog testa).
    """
    if len(options) != 4:
        raise InvalidOutputError("mora postojati tačno 4 opcije")
    if not (0 <= correct_index < 4):
        raise InvalidOutputError("correct_option_index van opsega")
    for opt in options:
        text = (opt.text or "").strip()
        if not text:
            raise InvalidOutputError("prazna opcija")
        if len(text) > config.MAX_OPTION_TEXT_CHARS:
            raise InvalidOutputError("preduga opcija")


def validate_explain_output(out: ExplainTurnOutput) -> None:
    """Server-side provjere Explain outputa povrh strict šeme."""
    if not (out.reply or "").strip():
        raise InvalidOutputError("prazan reply")
    if len(out.reply) > config.MAX_EXPLAIN_REPLY_CHARS:
        raise InvalidOutputError("predug reply")


def validate_quick_output(out: QuickTurnOutput) -> None:
    """Server-side provjere Quick outputa povrh strict šeme."""
    if not (out.reply or "").strip():
        raise InvalidOutputError("prazan reply")
    if len(out.reply) > config.MAX_QUICK_REPLY_CHARS:
        raise InvalidOutputError("predug reply")
