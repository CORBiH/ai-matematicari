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


class KontrolniQuestionOutput(BaseModel):
    """JEDNO pitanje batch-generisanog kontrolnog testa („Sutra imam kontrolni“).

    Model radi nad serverskim SLOTOVIMA (lekcija + težina po slotu) i mora
    svaki slot vratiti NEPROMIJENJEN (echo `slot`/`lesson_id`/`difficulty` se
    unakrsno provjerava u matbot/kontrolni.py — zamjena lekcije pada). Opcije
    su goli tekstovi BEZ slova: slova (a–d) dodjeljuje ISKLJUČIVO server
    poslije miješanja, pa nijedno slovo koje bi model napisao ne može biti
    tačno (isti princip kao Practice pravilo 5f)."""

    model_config = ConfigDict(extra="forbid")

    slot: int
    lesson_id: str
    text: str
    options: list[str]
    correct_option_index: int
    expected_answer: str
    # Kratko rješenje — SERVER-ONLY dokazni materijal (mathcheck) i osnova za
    # kratku ispravku na ekranu rezultata. Nikad ne ide u browser prije predaje.
    solution: str
    difficulty: Literal["easy", "medium", "hard", "demanding"]


class KontrolniTestOutput(BaseModel):
    """Kompletan batch: model vraća pitanje za SVAKI traženi slot, ništa više.

    Broj slotova varira (5 u prvom pozivu; manje u uslovnoj popravci), pa
    dužinu liste provjerava server po traženim slotovima, ne šema."""

    model_config = ConfigDict(extra="forbid")

    questions: list[KontrolniQuestionOutput]


# Granice INTERNIH polja slike (nikad vidljivih učeniku). Namjerno male: ova
# polja služe serverskoj provjeri, ne prepisivanju cijelog zadatka.
# Granice su kalibrisane 2026-08-15 uz migraciju slike na gpt-5.6-sol (živa
# produkcijska proba kroz run_quick_turn): Sol kao temeljitiji čitač prijavljuje
# POTPUNIJU evidenciju od ranijeg modela — duži visible_math (npr. kurzivna
# algebarska jednačina u LaTeX-u lako pređe 120 znakova) i više visible_values.
# Stare, uže granice su obarale odgovor kodovima `predug visible_math` /
# `previše visible_values` PRIJE nego što bi kapija čitljivosti uopšte rekla
# svoje. Granice i dalje postoje s istom svrhom (interna polja moraju ostati
# ograničena — nikad transkripcija cijele strane udžbenika), samo primaju
# evidenciju jednog stvarnog zadatka u cjelini.
MAX_VISIBLE_PROBLEM_TEXT_CHARS = 500
MAX_VISIBLE_MATH_CHARS = 300
MAX_UNCERTAINTY_REASON_CHARS = 300
MAX_VISIBLE_VALUE_FIELD_CHARS = 64
MAX_VISIBLE_VALUES = 20
# Inventar zadataka sa stranice (samo za `multiple_tasks`). Granice postoje da
# slika stranice nikad ne postane pun OCR transkript udžbenika.
MAX_DETECTED_TASKS = 8
MAX_DETECTED_TASK_TEXT_CHARS = 400
MAX_DETECTED_TASK_LABEL_CHARS = 12


class VisibleValue(BaseModel):
    """JEDAN podatak koji je STVARNO vidljiv na slici."""

    model_config = ConfigDict(extra="forbid")

    symbol: str   # npr. "a", "b", "x"
    value: str    # npr. "8" — string da bi zapis ostao onakav kakav je na slici
    unit: str     # npr. "cm"; prazno kad jedinice nema


class DetectedTask(BaseModel):
    """JEDAN zadatak prepoznat na stranici s više zadataka.

    `fully_readable` je granica povjerenja: samo zadatak koji je model pročitao
    U CIJELOSTI smije kasnije biti riješen iz zapamćenog konteksta, bez nove
    slike. Za sve ostalo server traži jasniju sliku umjesto da pogađa."""

    model_config = ConfigDict(extra="forbid")

    label: str            # oznaka sa stranice: „1“, „2“, „a“, „b“…
    text: str             # POTPUNA transkripcija TOG zadatka
    fully_readable: bool


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
    # STRUKTURNI signal MATEMATIČKI BITNE nesigurnosti (kalibracija za Sol,
    # 2026-08-15): temeljit model istinito napominje i BEZAZLENE stvari o kadru
    # („izrez blizu ivice“), a raniji gate je blokirao na SVAKI neprazan
    # `uncertainty_reason` — pošten opis kadra ubijao je objavu iako je svaki
    # potreban simbol bio jasan. `True` znači: neki matematički simbol,
    # vrijednost, predznak, eksponent, nazivnik, znak nejednakosti ili jedinica
    # POTREBNA za rješenje je nečitljiva/dvosmislena → objava se blokira.
    # Napomena o kadru uz sve čitljive simbole ide u `uncertainty_reason` uz
    # `False` — informativna je, ne blokira. Definicija polja živi u promptu
    # (matbot/prompts.py, _QUICK_IMAGE_RULES).
    math_content_uncertain: bool
    # INVENTAR ZADATAKA — popunjava se SAMO kad je `readability="multiple_tasks"`.
    #
    # ZAŠTO POSTOJI (živi baseline 2026-08-16): na stranici s pet zadataka
    # server je ispravno tražio „napiši koji da riješim“, ali je sve što je
    # model pročitao nestajalo s turnom — pa je na „Treći.“ odgovarao
    # „Pošalji sliku ili napiši tekst trećeg zadatka.“ Ovdje se pamti tačno
    # onoliko koliko treba da se izabrani zadatak kasnije riješi BEZ nove
    # slike i BEZ drugog poziva modela.
    #
    # Nije transkript cijele stranice: prazna lista je uredan ishod, a zadatak
    # čiji `fully_readable` nije `true` server NIKAD ne rješava iz sjećanja.
    detected_tasks: list[DetectedTask]


class InvalidOutputError(ValueError):
    """AI odgovor je strukturno validan JSON, ali sadržajno neupotrebljiv."""


def validate_explain_output(out: ExplainTurnOutput) -> None:
    """Server-side provjere Explain outputa povrh strict šeme."""
    if not (out.reply or "").strip():
        raise InvalidOutputError("prazan reply")
    if len(out.reply) > config.MAX_EXPLAIN_REPLY_CHARS:
        raise InvalidOutputError("predug reply")


def validate_quick_output(out, max_reply_chars=None) -> None:
    """Server-side provjere Quick outputa povrh strict šeme.

    Prima OBJE Quick šeme (tekstualnu i sliku) — zajedničko je samo `reply`;
    interna polja slike provjerava validate_quick_image_output.

    `max_reply_chars`: granica ZAVISI OD NAMJERE (v2, 2026-08-16). Rezultat
    ostaje kratak (config.MAX_QUICK_REPLY_CHARS), ali objašnjenje koje je
    učenik IZRIČITO tražio ne smije pasti samo zato što je duže od rezultata —
    ono ima svoju, i dalje čvrstu granicu (config.MAX_QUICK_EXPLANATION_CHARS).
    Bez argumenta važi zatečena granica, pa stariji pozivaoci ostaju isti."""
    if not (out.reply or "").strip():
        raise InvalidOutputError("prazan reply")
    limit = max_reply_chars or config.MAX_QUICK_REPLY_CHARS
    if len(out.reply) > limit:
        raise InvalidOutputError("predug reply")


def validate_quick_image_output(out: QuickImageTurnOutput, max_reply_chars=None) -> None:
    """Ograničenja INTERNIH polja slike. Ona nikad ne stižu do učenika, ali
    ulaze u serversku logiku i log, pa moraju biti kratka i brojčano ograničena
    (bez transkripcije cijele strane udžbenika)."""
    validate_quick_output(out, max_reply_chars=max_reply_chars)
    if len(out.detected_tasks) > MAX_DETECTED_TASKS:
        raise InvalidOutputError("previše detected_tasks")
    for task in out.detected_tasks:
        if len(task.label) > MAX_DETECTED_TASK_LABEL_CHARS:
            raise InvalidOutputError("preduga oznaka zadatka")
        if len(task.text) > MAX_DETECTED_TASK_TEXT_CHARS:
            raise InvalidOutputError("preduga transkripcija zadatka")
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
