"""Prompt blok za lekciju s ugovorom — server je VEĆ sastavio zadatak.

Ranija faza je ovdje modelu opisivala šemu strukturisanog dokaza i tražila da
sam smisli zadatak unutar ograničenja ugovora. Nakon Live96 smjer je obrnut:
kostur (tekst, opcije, tačan odgovor) konstruiše server PRIJE poziva, pa blok
sada modelu PRIKAZUJE gotov zadatak i sužava njegov posao na bosansku prozu
(reply/hint/feedback). Model ne smije mijenjati, prepričavati ni ponovo
računati matematiku — server njegov new_task sadržaj ionako IGNORIŠE i
objavljuje vlastiti render (matbot/practice.py), a proza prolazi kapiju
vjernosti (pipeline.verify_prose_fidelity).

Ništa ovdje nije po lekciji: blok se izvodi iz ugovora i kostura.
"""

_OPERATION_WORDS = {
    "add": "sabiranje",
    "subtract": "oduzimanje",
    "multiply": "množenje",
    "divide": "dijeljenje",
    "power": "stepenovanje",
    "root": "korjenovanje",
    "compare": "upoređivanje",
    "convert": "pretvaranje jedinica",
    "factorize": "rastavljanje na faktore",
    "substitute": "uvrštavanje",
    "construct": "konstruisanje",
}


def _operations_line(contract):
    if not contract.allowed_operations:
        return ""
    words = [_OPERATION_WORDS.get(op, op) for op in contract.allowed_operations]
    return "Vještina lekcije koristi: " + ", ".join(words) + "."


def build_block(contract, archetype, skeleton):
    """Blok za DODIJELJENI, već generisan zadatak ove lekcije.

    `skeleton` je generator.TaskSkeleton — server-owned istina. Opcije se
    prikazuju predshuffle redoslijedom; miješanje radi server tek pri objavi,
    pa model nikad ne zna koje će slovo biti tačno u browseru."""
    lines = [
        f"UGOVOR LEKCIJE (obavezno): vještina ostaje „{contract.skill}“.",
    ]
    operations = _operations_line(contract)
    if operations:
        lines.append(operations)

    if skeleton is not None:
        lines.append(
            "SERVER JE VEĆ SASTAVIO NOVI ZADATAK (koristi se SAMO ako učenikova "
            "poruka traži novi zadatak ili ako aktivni zadatak ne postoji):"
        )
        lines.append(f"- tekst zadatka: {skeleton.question_text}")
        for index, option in enumerate(skeleton.option_texts):
            marker = " (TAČNA)" if index == skeleton.correct_index else ""
            lines.append(f"- opcija {index + 1}{marker}: {option}")
        lines.append(f"- interni tačan odgovor: {skeleton.expected_answer}")
        if archetype is not None and archetype.prompt_unknown:
            lines.append(f"- učenik određuje: {archetype.prompt_unknown}")
        lines.append(
            "KAD IZDAJEŠ NOVI ZADATAK: vrati new_task s TAČNO ovim tekstom, ovim "
            "opcijama istim redoslijedom, correct_option_index i expected_answer "
            "kako je navedeno — bez ijedne izmjene. Server objavljuje SVOJU "
            "verziju zadatka; svaka tvoja izmjena brojeva se odbacuje."
        )
        lines.append(
            "TVOJ POSAO JE SAMO PROZA: reply/hint/feedback na prirodnom "
            "bosanskom (ijekavica). NIKAD ne uvodi brojeve kojih nema u "
            "zadatku, ne računaj drugačiji rezultat i ne otkrivaj tačnu opciju "
            "prije vremena."
        )
    return "\n".join(lines)
