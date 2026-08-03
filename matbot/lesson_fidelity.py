"""Recenzent VJERNOSTI LEKCIJI — jedan dodatni poziv, samo za nove zadatke.

ZAŠTO POSTOJI (živi nalaz, 5 prijavljenih slučajeva): generisan zadatak je
često pogađao samo ŠIRU OBLAST, a ne tačno izabranu lekciju — „Upoređivanje
decimalnih brojeva“ je dobilo oduzimanje razlomaka, „Pravila djeljivosti“ obično
dijeljenje, „Upoređivanje uglova“ oduzimanje mjera, a „Tekstualni zadatak sa
sistemom“ gotov sistem bez priče.

Dio toga je bio defekt ROUTINGA i popravljen je u podacima
(`task_families._promote_declared_task_form`) — recenzent ne služi da sakrije
mapiranje. Ostatak je slobodna interpretacija modela i to hvata ovaj sloj.

GRANICE (namjerno uske):
  • poziva se ISKLJUČIVO kad turn pravi ili mijenja zadatak;
  • odgovori, klikovi, hintovi, „Ne znam“, objašnjenja i obična konverzacija
    NIKAD ne plaćaju ovaj poziv i zadržavaju zatečeno ponašanje;
  • vraća approve / correct(jedan kompletan zadatak) / fail_closed;
  • nema trećeg poziva, retryja, repair petlje ni prelaska na drugu lekciju.

Nijedan ID lekcije ne postoji u ovom modulu. Recenzent dobija TAČAN NASLOV
lekcije, oblast, razred i porodicu kao podatke — konkretni primjeri iz nalaza
žive u promptu (kao ilustracija principa) i u testovima.
"""
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from matbot.schema import NewTask

# Namjere turna koje SMIJU platiti recenzenta. Sve ostalo ide zatečenim putem.
TASK_GENERATING = frozenset({
    "generate_task", "next_task", "easier_task", "harder_task",
})

FAIL_REASON_CODES = (
    "math_incorrect",
    "wrong_lesson",
    "wrong_task_form",
    "not_grade_appropriate",
    "ambiguous_or_unsolvable",
    "wrong_marked_option",
    "duplicate_options",
    "difficulty_direction_wrong",
)


class FidelityChecks(BaseModel):
    """Svaka provjera je ODVOJENA — „djeluje u redu“ nije provjera.

    `tests_exact_lesson` je stroži od „ista oblast“: zadatak mora ispitivati
    baš vještinu iz naslova lekcije."""

    model_config = ConfigDict(extra="forbid")

    math_correct: bool
    tests_exact_lesson: bool
    required_task_form: bool
    grade_appropriate: bool
    solvable_and_unambiguous: bool
    answer_correct: bool
    marked_option_correct: bool
    options_unique: bool
    difficulty_direction_correct: bool
    # Kratko obrazloženje ZA LOG (nikad se ne prikazuje učeniku).
    lesson_skill_summary: str


class LessonFidelityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "correct", "fail_closed"]
    checks: FidelityChecks
    fail_reason_code: Optional[Literal[FAIL_REASON_CODES]] = None
    # Popunjen SAMO kod 'correct' — kompletan ispravljen zadatak koji se objavljuje.
    corrected_task: Optional[NewTask] = None


class FidelityRejected(ValueError):
    """Zadatak nije prošao recenziju vjernosti. Poruka je INTERNA (log).

    `failed_checks`: lista OBAVEZNIH provjera koje su bile oborene kad je do
    odbijanja došlo baš zbog toga (prazno za fail_closed ili nedostajući
    ispravljen zadatak — tamo razlog nije "oborena provjera"). Isključivo za
    strukturisan interni log, nikad se ne šalje u browser."""

    def __init__(self, message, failed_checks=()):
        super().__init__(message)
        self.failed_checks = tuple(failed_checks)


def is_task_generating(new_task, difficulty_request=""):
    """Da li OVAJ turn pravi/mijenja zadatak (i time zaslužuje recenzenta).

    Odluka se donosi iz SERVERSKE činjenice (model je vratio `new_task`), ne iz
    modelove izjave o namjeri — server ionako mijenja zadatak samo tada."""
    return new_task is not None


_PRINCIPLE = """Ti si stroga kontrola VJERNOSTI LEKCIJI za zadatak iz matematike
(osnovna škola, Bosna i Hercegovina). Dobijaš NACRT zadatka koji je napisao drugi
nastavnik i moraš ga provjeriti prije nego što ga učenik vidi.

NAJVAŽNIJE PRAVILO: zadatak mora ispitivati TAČNO IZABRANU LEKCIJU, a ne samo
istu širu oblast. „Ista oblast“ NIJE dovoljno. Ako naslov lekcije imenuje
određenu radnju (upoređivanje, primjena pravila, prepoznavanje, tekstualni
zadatak…), zadatak MORA tražiti baš tu radnju.

Ilustracije principa (nisu spisak lekcija — princip vrijedi za svaku):
- lekcija o UPOREĐIVANJU brojeva traži da učenik poredi/uredi vrijednosti ili
  izabere veći/manji ($<$, $>$, $=$); puka računska operacija s tim brojevima
  NE ispituje tu lekciju;
- lekcija o PRAVILIMA DJELJIVOSTI traži da učenik primijeni, obrazloži ili
  prepozna djeljivost po imenovanim pravilima; obično dijeljenje NE ispituje tu
  lekciju;
- lekcija o UPOREĐIVANJU UGLOVA traži poređenje ili uređivanje mjera uglova;
  računanje njihove razlike samo po sebi NE ispituje tu lekciju;
- lekcija o TEKSTUALNOM ZADATKU traži stvarnu, uzrastu primjerenu priču iz koje
  se postavlja ili koristi model (npr. sistem); gotov zapis bez priče NE
  ispituje tu lekciju;
- lekcija o SABIRANJU CIJELIH BROJEVA RAZLIČITIH ZNAKOVA je ispravno ispitana
  izrazom poput $-7+12$ — kad naslov imenuje baš tu operaciju, direktan račun
  JESTE tražena radnja.

ODLUKA:
- `approve` — nacrt ispituje tačno tu lekciju i matematički je ispravan;
- `correct` — popravljivo: u `corrected_task` vrati JEDAN KOMPLETAN ispravljen
  zadatak (tekst, 4 opcije, tačan indeks, očekivani odgovor). To je konačna
  verzija koju učenik vidi;
- `fail_closed` — ne može se sigurno objaviti; navedi `fail_reason_code`.

NIKAD ne vraćaj `approve` dok je ijedna od OBAVEZNIH provjera (`math_correct`,
`tests_exact_lesson`, `answer_correct`, `marked_option_correct`,
`solvable_and_unambiguous`, `options_unique`, `grade_appropriate`) oborena —
to je kontradikcija koju server odbija bez obzira na tvoju odluku. Ako
prepoznaš problem: ili ga POPRAVI i vrati `correct` s KOMPLETNIM zamjenskim
zadatkom (nikad polovičan opis šta fali), ili — ako se ne može sigurno
popraviti — vrati `fail_closed`. Sama dijagnoza problema bez popravke ili bez
`fail_closed` nikad nije dovoljna.

Kad ispravljaš, NE MIJENJAJ izabranu lekciju, razred ni oblast — popravi zadatak
tako da odgovara TOJ lekciji. Ne postoji treći poziv: tvoja odluka je konačna.
Ako nisi siguran u matematiku ili je zadatak dvosmislen, biraj `fail_closed`.

Nacrt ponekad nosi napomenu „DETERMINISTIČKA PROVJERA UGOVORA JE ODBILA NACRT: ...“
— to je serverski, nepregovorljiv ugovor dodijeljene porodice zadatka (npr. za
tekstualni zadatak: mora postojati stvarna životna situacija, ne gola računska
operacija). Kad ta napomena postoji, `corrected_task` MORA otkloniti TAČNO taj
nedostatak — gola računska operacija, „Evo zadatka.“ bez priče, ili četiri
neobjašnjene brojčane opcije NIKAD ne zadovoljavaju zahtjev za životni kontekst.
Ako ne možeš popraviti zadatak tako da ugovor bude zadovoljen, biraj `fail_closed`."""


def build_instructions(grade):
    from matbot.prompts import _GRADE_STYLE

    style = _GRADE_STYLE.get(grade, _GRADE_STYLE[6])
    return f"{_PRINCIPLE}\n\nUZRAST: {style}"


def _option_lines(new_task):
    lines = []
    for index, option in enumerate(new_task.options):
        marker = "  <-- OZNAČENA KAO TAČNA" if index == new_task.correct_option_index else ""
        lines.append(f"  {index}) {option.text}{marker}")
    return lines


def build_input(context, new_task, student_message, family="",
                family_description="", prior_task="", difficulty_request="",
                family_contract_mismatch="", duplicate_reason="",
                duplicate_task_text=""):
    """`context` je matbot.tutor.lesson_context.LessonContext (dijeli se s
    univerzalnim putem — isti kanonski identitet, bez duplikata).

    `family_contract_mismatch`: poruka determinističke provjere ugovora
    (matbot/task_family_validation.py) POKRENUTE na SIROVOM Tutorovom nacrtu
    PRIJE ovog poziva — prazan string kad nacrt tu provjeru već zadovoljava.
    Ovo NE zamjenjuje autoritativnu provjeru (ona se ponavlja NAD sanitizovanim
    tekstom poslije ovog poziva, u practice._apply_new_task), nego recenzentu
    daje TAČAN razlog kršenja da bi `corrected_task` mogao ciljano popraviti baš
    taj nedostatak (živi nalaz: „fraction_word_problem: nedostaje obavezan oblik
    'ima_zivotni_kontekst'“ dvaput odbijeno u produkciji jer recenzent nije znao
    šta konkretno nedostaje).

    `duplicate_reason`/`duplicate_task_text`: ISTA logika kao gore, ali za
    ponavljanje teksta u sesiji (matbot/task_families.py, PROVJERENO na
    SIROVOM nacrtu prije ovog poziva) — prazno kad nacrt nije ponavljanje.
    Autoritativna provjera se identično ponavlja NAD ispravljenim zadatkom u
    practice._apply_new_task — ova zaštita se ovdje NIKAD ne slabi ni
    zaobilazi, samo se recenzentu unaprijed kaže TAČNO koji tekst mora
    izbjeći (živi nalaz: „ponovljen tekst zadatka u istoj sesiji“ odbijeno tek
    NAKON Tutora i recenzenta, iako je recenzent mogao ispraviti da je znao)."""
    lines = [
        "IZABRANA LEKCIJA (nepromjenjiva):",
        f"- razred: {context.grade}",
        f"- oblast: {context.oblast} ({context.oblast_id})",
        f"- kanonski ID lekcije: {context.topic_id}",
        f"- TAČAN NASLOV LEKCIJE: {context.title}",
    ]
    if family:
        label = f"{family} — {family_description}" if family_description else family
        lines.append(f"- dodijeljena porodica zadatka: {label}")
    lines.append("")
    lines.append(f"PORUKA UČENIKA: „{(student_message or '').strip()[:400]}“")
    if difficulty_request:
        lines.append(f"TRAŽENA PROMJENA TEŽINE: {difficulty_request}")
    if prior_task:
        lines.append(f"PRETHODNI ZADATAK (za poređenje težine): {prior_task}")
    if family_contract_mismatch:
        lines.append("")
        lines.append(
            f"DETERMINISTIČKA PROVJERA UGOVORA JE ODBILA NACRT: {family_contract_mismatch}"
        )
        lines.append(
            "Ovaj nedostatak MORAŠ otkloniti u `corrected_task` (decision=`correct`) "
            "ili odbiti (`fail_closed`) ako ne možeš — nacrt se NE SMIJE odobriti "
            "(`approve`) dok ovaj nedostatak stoji."
        )
    if duplicate_reason:
        lines.append("")
        lines.append(f"DETERMINISTIČKA PROVJERA PONAVLJANJA JE ODBILA NACRT: {duplicate_reason}")
        if duplicate_task_text:
            lines.append(f"TEKST KOJI SE NE SMIJE PONOVITI: {duplicate_task_text}")
        lines.append(
            "Vrati `correct` s KOMPLETNIM zamjenskim zadatkom koji se ZNAČAJNO "
            "razlikuje od gornjeg teksta — drugi brojevi, druga formulacija i, "
            "gdje je primjenjivo, drugačiji odgovor/opcije — dok zadržavaš ISTU "
            "izabranu lekciju, ISTU dodijeljenu porodicu i traženu težinu. "
            "Promjena samo interpunkcije, razmaka ili redoslijeda riječi NIJE "
            "dovoljna i i dalje se broji kao isti zadatak. Ako ne možeš "
            "smisliti stvarno drugačiji zadatak, biraj `fail_closed` — nacrt "
            "se NE SMIJE odobriti (`approve`) dok ponavljanje stoji."
        )
    lines.append("")
    lines.append("NACRT ZADATKA (provjeri ga, ne vjeruj mu):")
    lines.append(f"- tekst: {new_task.text}")
    lines.extend(_option_lines(new_task))
    lines.append(f"- očekivani odgovor: {new_task.expected_answer}")
    lines.append(f"- težina: {new_task.difficulty}")
    lines.append("")
    lines.append("Vrati strukturisanu odluku prema šemi.")
    return "\n".join(lines)


# OBAVEZNE provjere za PUBLIKACIJU (živi nalaz VPS): "required_task_form" je
# NAMJERNO izostavljen — isti princip kao task_family_validation.FamilyContract
# (task_form je informativan, nikad razlog odbijanja, vidi taj docstring);
# "difficulty_direction_correct" ostaje ODVOJENA, uslovna provjera ispod (samo
# kad je učenik tražio lakše/teže), ne dio ovog fiksnog skupa.
_MANDATORY_CHECKS = (
    "math_correct", "tests_exact_lesson", "answer_correct",
    "marked_option_correct", "solvable_and_unambiguous",
    "options_unique", "grade_appropriate",
)


def mandatory_checks_failed(checks):
    """Imena obaveznih provjera koje su oborene (prazna lista = sve prošle)."""
    return [name for name in _MANDATORY_CHECKS if not getattr(checks, name)]


@dataclass(frozen=True)
class ResolvedReview:
    """Ishod resolve(): `task` je None kad se objavljuje ORIGINALNI nacrt
    (čist `approve`), ili konkretan NewTask kad se objavljuje ispravljen
    zadatak (čist `correct`, ili `approve` PREKLOPLJEN u `correct` — vidi
    `normalized_from_approve`). Isključivo za interni log, nikad u browser."""
    task: object
    normalized_from_approve: bool = False


def resolve(review, requested_difficulty=""):
    """Vrati ResolvedReview koji se objavljuje. Baca FidelityRejected — fail
    closed, BEZ mutacije sesije i BEZ trećeg poziva.

    ŽIVI NALAZ (VPS): recenzent je vratio `approve` dok su obavezne provjere
    (`math_correct`, `tests_exact_lesson`, `answer_correct`,
    `marked_option_correct`) bile OBORENE — kontradikcija koju je server
    ispravno odbio, ali bez ijednog objavljenog zadatka iako su OBA poziva već
    potrošena. Pravilo normalizacije:

      • `approve` uz SVE obavezne provjere tačne → objavljuje se nacrt
        (bez izmjene, `task=None`).
      • `approve` uz BAREM JEDNU oborenu obaveznu provjeru:
          - ako recenzent ipak nosi KOMPLETAN `corrected_task` → odluka se
            PREKLAPA (normalizuje) u `correct` i ta zamjena se validira
            potpuno isto kao svaki drugi `correct` (ista downstream provjera:
            schema, mathsafe, mathcheck, ugovor porodice, jedinstvenost
            opcija — vidi practice._apply_new_task, poziva se odmah poslije);
          - inače (nema zamjenskog zadatka) → fail closed, ISTO kao dosad.
      • `correct` (izvorno ili preklopljeno) zahtijeva SAMO da
        `corrected_task` nije prazan — obavezne provjere iznad opisuju
        NEDOSTATKE NACRTA koji su i doveli do ispravke, ne ispravljen zadatak,
        pa se nad njim više ne provjeravaju ovdje (to radi downstream sloj).
      • `fail_closed` uvijek odbija, bez obzira na provjere."""
    if review.decision == "fail_closed":
        raise FidelityRejected(f"fail_closed:{review.fail_reason_code or 'nepoznato'}")

    decision = review.decision
    normalized = False

    if decision == "approve":
        failed = mandatory_checks_failed(review.checks)
        if failed:
            if review.corrected_task is not None:
                decision = "correct"
                normalized = True
            else:
                raise FidelityRejected(
                    f"odobreno uprkos oborenim provjerama: {failed}",
                    failed_checks=failed,
                )

    if (requested_difficulty or "").strip().lower() in ("easier", "harder"):
        if not review.checks.difficulty_direction_correct:
            raise FidelityRejected("smjer promjene težine nije potvrđen")

    if decision == "correct":
        if review.corrected_task is None:
            raise FidelityRejected("odluka 'correct' bez ispravljenog zadatka")
        return ResolvedReview(review.corrected_task, normalized_from_approve=normalized)
    return ResolvedReview(None, normalized_from_approve=False)
