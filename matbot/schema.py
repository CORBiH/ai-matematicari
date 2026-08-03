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

    # NAPOMENA (poslije Live96): polja `archetype` i `evidence` su UKLONJENA.
    # Za lekciju s uključenim ugovorom matematiku sada KONSTRUIŠE server
    # (matbot/contracts/generator.py) i modelov new_task sadržaj se ionako
    # ignoriše — model nema šta da dokazuje o zadatku koji nije njegov.

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


# Granice INTERNIH polja slike (nikad vidljivih učeniku). Namjerno male: ova
# polja služe serverskoj provjeri, ne prepisivanju cijelog zadatka.
MAX_VISIBLE_PROBLEM_TEXT_CHARS = 300
MAX_VISIBLE_MATH_CHARS = 120
MAX_UNCERTAINTY_REASON_CHARS = 200
MAX_VISIBLE_VALUE_FIELD_CHARS = 24
MAX_VISIBLE_VALUES = 8


class VisibleValue(BaseModel):
    """JEDAN podatak koji je STVARNO vidljiv na slici."""

    model_config = ConfigDict(extra="forbid")

    symbol: str   # npr. "a", "b", "x"
    value: str    # npr. "8" — string da bi zapis ostao onakav kakav je na slici
    unit: str     # npr. "cm"; prazno kad jedinice nema


class QuickImageTurnOutput(BaseModel):
    """Quick mod KAD JE PRILOŽENA SLIKA — jedina šema s internim poljima.

    ZAŠTO POSTOJI (živi nalaz D35-5/D35-6, pozivi 33 i 35 kampanje od 35):
    tekstualna šema je bila samo `{reply}`, pa je slika bila potpuno neprovjerljiva
    crna kutija. Za pravougaonik s $a=8$ cm i $b=5$ cm model je vratio
    „$P=26\\,\\text{cm}$“ (obim, s linearnom jedinicom, na zahtjev za površinu), a
    za jednačinu s namjerno prekrivenom desnom stranom „$x=5$“ — pogodio je broj
    koji na slici uopšte nije bio vidljiv. Nijedna postojeća provjera to nije
    mogla uhvatiti: mathcheck.py provjerava lanac jednakosti (ovdje ga nema),
    geometrycheck.py provjerava OZNAKE (a `P` je bila ispravna oznaka, samo s
    pogrešnom vrijednošću), a istina sa slike nigdje ne postoji u tekstu.

    Sada model MORA prijaviti šta je vidio prije nego što odgovori: čitljivost,
    tip zadatka, vidljive vrijednosti i sopstvenu sigurnost. Server na osnovu
    toga (matbot/quick.py + matbot/imagecheck.py) ili nezavisno provjeri račun
    ili odbije odgovor. Deklaracija sama po sebi NIJE dokaz — zato se, gdje god
    je moguće, nad prijavljenim vrijednostima radi nezavisan račun.

    SVA polja osim `reply` su INTERNA: nikad ne idu u browser, u historiju
    razgovora, u localStorage ni u log s punim sadržajem (vidi quick.py).
    Tekstualni (bez slike) Quick put i dalje koristi QuickTurnOutput, bajt za
    bajt kao ranije.
    """

    model_config = ConfigDict(extra="forbid")

    reply: str
    readability: Literal[
        "clear", "partially_unreadable", "unreadable", "multiple_tasks", "non_math",
    ]
    all_required_symbols_visible: bool
    task_type: Literal[
        "arithmetic", "linear_equation", "fraction_expression",
        "rectangle_area", "rectangle_perimeter",
        "square_area", "square_perimeter", "other",
    ]
    # SAMO matematički izraz/jednačina koja je STVARNO vidljiva na slici —
    # nikad naslov („Riješi“, „Izračunaj“, „Zadatak“, „Odredi“), nikad rezultat
    # koji model predlaže, nikad pretpostavljena vrijednost. Prazno kad se izraz
    # ne može pročitati TAČNO.
    #
    # ZAŠTO POSTOJI ODVOJENO OD visible_problem_text (živi nalaz D35T-2, pozivi
    # 12 i 13 kampanje od 14): model je u visible_problem_text stavljao naslov
    # zadatka („Rijesi jednacinu:“), pa deterministički provjeravači za izraz i
    # jednačinu nisu imali šta parsirati i TIHO su preskakali. Prazna lista
    # problema je tada značila i „provjereno“ i „preskočeno“, a pozivalac je to
    # čitao kao „provjereno“ — pogrešan rezultat je mogao biti objavljen.
    visible_math: str
    # Slobodan opis zadatka. Koristi se SAMO za nepodržane/opšte slike i NIKAD
    # kao deterministički dokaz za podržane porodice.
    visible_problem_text: str
    requested_quantity: Literal[
        "area", "perimeter", "value_of_unknown", "numeric_result", "other",
    ]
    visible_values: list[VisibleValue]
    unit: str
    answer_confidence: Literal["high", "medium", "low"]
    uncertainty_reason: str


class InvalidOutputError(ValueError):
    """AI odgovor je strukturno validan JSON, ali sadržajno neupotrebljiv."""


def validate_output(out: PracticeTurnOutput, require_reply: bool = True,
                    ignore_new_task_content: bool = False) -> None:
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

    ignore_new_task_content=True: SAMO za lekciju s UKLJUČENIM ugovorom —
    server je zadatak već konstruisao i modelov new_task sadržaj u cijelosti
    ODBACUJE (matbot/practice.py), pa se njegova struktura ne provjerava:
    new_task tada služi isključivo kao signal „u ovom turnu se izdaje novi
    zadatak“, a nesavršena kopija ne smije srušiti turn koji server ionako
    objavljuje iz vlastitog kostura.
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
        if ignore_new_task_content:
            return
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


def validate_quick_output(out) -> None:
    """Server-side provjere Quick outputa povrh strict šeme.

    Prima OBJE Quick šeme (tekstualnu i sliku) — zajedničko je samo `reply`;
    interna polja slike provjerava validate_quick_image_output."""
    if not (out.reply or "").strip():
        raise InvalidOutputError("prazan reply")
    if len(out.reply) > config.MAX_QUICK_REPLY_CHARS:
        raise InvalidOutputError("predug reply")


def validate_quick_image_output(out: QuickImageTurnOutput) -> None:
    """Ograničenja INTERNIH polja slike. Ona nikad ne stižu do učenika, ali
    ulaze u serversku logiku i log, pa moraju biti kratka i brojčano ograničena
    (bez transkripcije cijele strane udžbenika)."""
    validate_quick_output(out)
    if len(out.visible_problem_text) > MAX_VISIBLE_PROBLEM_TEXT_CHARS:
        raise InvalidOutputError("predug visible_problem_text")
    if len(out.visible_math) > MAX_VISIBLE_MATH_CHARS:
        raise InvalidOutputError("predug visible_math")
    if len(out.uncertainty_reason) > MAX_UNCERTAINTY_REASON_CHARS:
        raise InvalidOutputError("predug uncertainty_reason")
    if len(out.unit) > MAX_VISIBLE_VALUE_FIELD_CHARS:
        raise InvalidOutputError("preduga unit")
    if len(out.visible_values) > MAX_VISIBLE_VALUES:
        raise InvalidOutputError("previše visible_values")
    for item in out.visible_values:
        if max(len(item.symbol), len(item.value), len(item.unit)) > MAX_VISIBLE_VALUE_FIELD_CHARS:
            raise InvalidOutputError("predugo polje u visible_values")
