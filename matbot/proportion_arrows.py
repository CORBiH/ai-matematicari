"""Školska METODA STRELICA za proporcionalnost — jedan renderer za sve puteve.

OBNOVLJENA IZGUBLJENA METODA (audit ovlašćenja pravila, odluka F): historijski
prompt je zahtijevao metodu strelica za pravilo trojno, ali kroz krhku ASCII
grafiku koja se lomila u chat prikazu, pa je poslije 74a6cc0 brisanja pravilo
zamijenjeno obeshrabrivanjem strelica. Ovdje se vraća METODA (smjer promjene
veličina određuje proporciju PRIJE računa), a ne krhki prikaz: redovi su
obični tekst s MathJax simbolima $\\uparrow$/$\\downarrow$/$\\rightarrow$,
bez ikakvog oslanjanja na poravnanje kolona ili HTML.

Školski raspored (kanonski): dvije veličine u dvije kolone, poznati par u
gornjem redu, traženi $x$ u DONJEM redu DESNE kolone; strelica uz $x$
pokazuje NAGORE. Direktna proporcionalnost: obje strelice u ISTOM smjeru
($\\uparrow\\uparrow$); obrnuta: u SUPROTNIM smjerovima ($\\uparrow\\downarrow$).
Proporcija se čita UZ SMJER strelica — kod direktne se lijeva razmjera čita
u istom smjeru kao desna, kod obrnute u suprotnom.

Renderer koriste: rules.py (blok oblasti proporcija u OBA prompta),
deterministički generator razmjere (obrazloženje prepoznavanja) i testovi.
Sve je lokalno — nula poziva modela.
"""

KIND_DIRECT = "direct"
KIND_INVERSE = "inverse"

_KIND_WORD = {KIND_DIRECT: "direktna", KIND_INVERSE: "obrnuta"}


def arrow_pair(kind):
    """MathJax par strelica za smjer proporcionalnosti."""
    return ("$\\uparrow\\uparrow$" if kind == KIND_DIRECT
            else "$\\uparrow\\downarrow$")


def direction_sentence(kind):
    """Jedna rečenica koja veže smjer strelica i vrstu proporcionalnosti."""
    if kind == KIND_DIRECT:
        return ("Strelice su u ISTOM smjeru (" + arrow_pair(KIND_DIRECT)
                + ") — proporcionalnost je direktna: kad jedna veličina "
                "raste, raste i druga.")
    return ("Strelice su u SUPROTNIM smjerovima (" + arrow_pair(KIND_INVERSE)
            + ") — proporcionalnost je obrnuta: kad jedna veličina raste, "
            "druga opada.")


def setup_rows(known_left, known_right, asked_left, unit_left, unit_right,
               unknown="x"):
    """Dva reda školskog rasporeda — traženi član u donjem redu desno.

    Svaka vrijednost ide u svoj $...$; između kolona je $\\rightarrow$, pa
    prikaz ne zavisi ni od kakvog poravnanja."""
    return (
        f"${known_left}$ {unit_left} $\\rightarrow$ ${known_right}$ {unit_right}",
        f"${asked_left}$ {unit_left} $\\rightarrow$ ${unknown}$ {unit_right}",
    )


def oriented_proportion(known_left, known_right, asked_left, kind,
                        unknown="x"):
    """Proporcija pročitana UZ SMJER strelica (strelica uz x gleda nagore).

    Direktna: obje kolone se čitaju odozdo nagore →
        x : known_right = asked_left : known_left
    Obrnuta: lijeva kolona se čita odozgo nadolje →
        x : known_right = known_left : asked_left
    """
    if kind == KIND_DIRECT:
        return f"{unknown} : {known_right} = {asked_left} : {known_left}"
    return f"{unknown} : {known_right} = {known_left} : {asked_left}"


def method_lines(known_left, known_right, asked_left, kind, unit_left,
                 unit_right, unknown="x"):
    """Kompletan zapis metode strelica za pravilo trojno — lista redova."""
    rows = setup_rows(known_left, known_right, asked_left, unit_left,
                      unit_right, unknown)
    proportion = oriented_proportion(known_left, known_right, asked_left,
                                     kind, unknown)
    return [
        rows[0],
        rows[1],
        direction_sentence(kind),
        f"Proporcija (čita se uz smjer strelica): ${proportion}$.",
    ]


def prompt_rule_text():
    """Blok oblasti proporcija za OBA prompta — jedina formulacija metode."""
    return (
        "OBLAST — PROPORCIJE I RAZMJERE:\n"
        "- Za primjenu direktne/obrnute proporcionalnosti (pravilo trojno) "
        "koristi ŠKOLSKU METODU STRELICA: zapiši dva reda (poznati par gore, "
        "traženi $x$ u donjem redu desne kolone), npr. u posebnim redovima "
        "„$4$ radnika $\\rightarrow$ $12$ dana“ pa „$6$ radnika $\\rightarrow$ "
        "$x$ dana“. Strelica uz $x$ pokazuje nagore.\n"
        "- PRIJE računa izričito utvrdi vrstu: direktna proporcionalnost — "
        "strelice u ISTOM smjeru ($\\uparrow\\uparrow$); obrnuta — u SUPROTNIM "
        "smjerovima ($\\uparrow\\downarrow$). Tek onda postavi proporciju "
        "čitajući razmjere UZ SMJER strelica i riješi je.\n"
        "- Redove piši kao običan tekst s $...$ simbolima ($\\rightarrow$, "
        "$\\uparrow$, $\\downarrow$) — NIKAD ASCII crteže, tabele koje zavise "
        "od poravnanja, ni HTML.\n"
        "- Metodu strelica koristi samo gdje je proporcionalnost stvarno tema; "
        "za skraćivanje razmjere i osnovno svojstvo proporcije koristi metodu "
        "koju lekcija imenuje.\n"
    )
