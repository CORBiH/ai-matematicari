"""Deterministička (ne-AI) zaštita od LaTeX-a koji bi izazvao "Math input error"
u MathJax-u na frontendu. Nema ambiciju da bude potpun LaTeX parser — provjerava
tri stvari koje su dovoljne da spriječe vidljivu grešku kod učenika:

0. JSON dvostruko-escape bug: model ponekad vrati LaTeX komandu bez ispravnog
   JSON escape-a backslasha (npr. piše "\frac" umjesto "\\frac" u JSON stringu).
   JSON parser to protumači kao poznat escape (\f, \b, \t, \r, \n) i rezultat
   je STVARNI kontrolni znak (npr. U+000C form feed) umjesto backslasha, praćen
   ostatkom komande kao običnim slovima (npr. "rac{3}{5}"). Prije bilo koje
   druge provjere, ovi kontrolni znakovi se unutar $...$ segmenata rekonstruišu
   nazad u literalni "\f"/"\b"/"\t"/"\r"/"\n" — tako "<FORM FEED>rac{3}{5}"
   ponovo postaje "\frac{3}{5}".
1. broj neescaped '$' delimitera mora biti paran (svaki otvoren mora biti zatvoren)
2. unutar svakog $...$ segmenta, vitičaste zagrade { } moraju biti balansirane
   (pokriva slomljen \frac{a}{b i slične greške)

Kad segment ne prođe provjeru, NE pravimo popravni AI poziv niti pokušavamo
pogoditi ispravan LaTeX — samo uklanjamo $ delimitere oko tog segmenta, tako
da MathJax uopšte ne pokuša da ga parsira i učenik dobije čitljiv obični tekst
umjesto crvene greške.
"""
import re

_DOLLAR_SPLIT = re.compile(r"(?<!\\)\$")

# JSON escape sekvence čije se dekodirane kontrolne znakove najčešće brka sa
# backslash-om ispred LaTeX komande (\frac, \begin, \times, \neq, \right, ...).
# Mapiranje ide OBRNUTO od JSON dekodera: kontrolni znak → JEDAN literalni
# backslash + slovo (dva Python znaka: '\' i slovo — NE dva backslasha).
# Namjerno raw string literali (r"\f") da izbjegnemo bilo kakvu zabunu oko
# broja backslasheva: r"\f" i "\\f" su identični (jedan backslash + 'f'), ali
# raw zapis je nedvosmislen na prvi pogled. json.dumps() će kasnije SAM
# prikazati taj jedan Python backslash kao "\\f" u JSON tekstu — to je ispravno
# JSON escapovanje jednog backslasha, NE dupliranje.
_CONTROL_TO_LATEX_ESCAPE = {
    "\x0c": r"\f",  # form feed   (JSON \f)  — npr. \frac
    "\x08": r"\b",  # backspace   (JSON \b)  — npr. \begin
    "\t":   r"\t",  # tab         (JSON \t)  — npr. \times
    "\r":   r"\r",  # carriage return (JSON \r) — npr. \right
    "\n":   r"\n",  # newline     (JSON \n)  — npr. \neq, \newcommand
}


def _repair_control_chars(segment: str) -> str:
    """Popravlja SAMO unutar jednog $...$ segmenta — ne dira tekst van njega."""
    out = []
    for ch in segment:
        if ch in _CONTROL_TO_LATEX_ESCAPE:
            out.append(_CONTROL_TO_LATEX_ESCAPE[ch])
        elif ord(ch) < 0x20:
            continue  # ostali rijetki kontrolni znakovi: ukloni, ne pogađaj slovo
        else:
            out.append(ch)
    return "".join(out)


def sanitize_math_text(text: str) -> str:
    if not text or "$" not in text:
        return text or ""

    parts = _DOLLAR_SPLIT.split(text)
    if len(parts) == 1:
        return text

    # Paran broj '$' delimitera => neparan broj dijelova nakon splita.
    # Ako je broj dijelova PARAN, zadnji dio je matematički segment koji
    # nikad nije zatvoren (stray '$' na kraju teksta).
    unterminated_tail = (len(parts) % 2 == 0)
    last_index = len(parts) - 1

    out = []
    for i, part in enumerate(parts):
        is_math_segment = (i % 2 == 1)
        if not is_math_segment:
            out.append(part)  # tekst VAN $...$: nikad se ne dira
            continue
        part = _repair_control_chars(part)  # PRIJE provjere balansa
        if unterminated_tail and i == last_index:
            out.append(part)  # nikad zatvoren $ — prikaži kao obični tekst
            continue
        if part.count("{") != part.count("}"):
            out.append(part)  # nebalansirane vitičaste zagrade — ukloni delimitere
        else:
            out.append("$" + part + "$")  # ispravan segment — zadrži kako jeste
    return "".join(out)


# ---------------------------------------------------------------------------
# PROŠIRENJE (live produkcijski nalaz): sanitize_math_text gore rješava SAMO
# ono što je VEĆ unutar prepoznatljivog $...$ para. Tri stvarna live bagova
# dijele isti korijenski uzrok — matematički/LaTeX sadržaj koji model vrati
# BEZ ijednog $ (ili s $ tek oko dijela izraza) sanitize_math_text uopšte ne
# dotiče (rano vraćanje kad "$" not in text, ili "tekst VAN $...$: nikad se ne
# dira"), pa sirovi \frac/\sqrt/\text ili slomljen zapis stigne do browsera.
#
# Ne pokušavamo pogoditi/izmisliti nedostajuće zagrade ili operande. Umjesto
# toga:
#   1. sigurna, nedvosmislena reparacija: doslovno "\n" (backslash+n) IZVAN
#      matematike postaje stvaran novi red (model je precesto-escapeovao
#      prelom pasusa umjesto stvarnog newlinea ili $...$ zapisa)
#   2. usko, sigurno umotavanje: IZOLOVAN \frac{a}{b} token izvan $...$ (jedini
#      slučaj koji ne mijenja okolnu prozu i eksplicitno je dozvoljen)
#   3. za KRATKE, atomske stringove (opcije): cijeli string se umota u $...$
#      SAMO kad je strukturno potvrđeno da je cio string matematički izraz
#      (whitelist skup znakova + prisustvo poznate LaTeX komande)
#   4. sve što i dalje sadrži sirovu zabranjenu komandu izvan $...$, vidljiv
#      "\n", zabranjen kontrolni znak ili prepoznat oštećen oblik (npr.
#      "sqrt3", "textcm") nakon koraka 1-3 → NEBEZBJEDNO: pozivalac odbija
#      cijeli generisani zadatak/tekst i vraća postojeći sigurni fallback
#      (bez drugog AI poziva).
# ---------------------------------------------------------------------------

_RAW_LATEX_COMMAND_RE = re.compile(r"\\(?:frac|sqrt|text|cdot|begin|end)\b")
_DAMAGED_LATEX_FORM_RE = re.compile(r"\dsqrt|sqrt\d|\btext[a-zA-Z]")
_LITERAL_NEWLINE_ESCAPE = "\\n"
# Doslovan "\n" (backslash+n) je ambiguan: identičan je prefiksu \neq, \ne,
# \not, \nu, \nabla i sličnih ISPRAVNIH LaTeX komandi. Popravlja se SAMO kad
# NE nastavlja neku od tih POZNATIH komandi (provjereno kao CIJELA riječ —
# nakon nastavka ne smije slijediti još jedno slovo). Obična riječ/rečenica
# koja slijedi (npr. "\nDrugi", "\nNovi") NIJE ovaj slučaj i ispravno se
# pretvara — provjera je usko ograničena na eq/e/ot/u/abla, ne na "bilo koje
# slovo", jer bi to blokiralo svaki prelom pred novom rečenicom.
_LITERAL_NEWLINE_ESCAPE_RE = re.compile(r"\\n(?!(?:eq|e|ot|u|abla)(?![A-Za-z]))")
_FRAC_TOKEN_RE = re.compile(r"\\frac\{[^{}]*\}\{[^{}]*\}")
_PURE_MATH_EXPRESSION_RE = re.compile(r"^[\d\s+\-*/=,.:;()\[\]{}^_°'\"\\a-zA-Z]+$")

# Bare (bez backslasha) "sqrt" iza kojeg slijedi NEDVOSMISLEN radikand — jedan
# brojčani token, ili sadržaj u {} ili (). Ambiguozni oblici (npr. "sqrtx" bez
# brojčanog radikanda) namjerno NE prolaze ovaj regex i ostaju za
# find_unsafe_math_issues da ih prijavi kao nebezbjedne, a ne pogode.
_BARE_SQRT_RE = re.compile(r"(?<!\\)sqrt\s*(\{[^{}]*\}|\([^()]*\)|\d+)")

# Bare (bez backslasha) "text" iza kojeg slijedi POZNATA (bijela lista)
# mjerna jedinica, opciono u {}/() i opciono s eksponentom ^2/^3. Poredak je
# namjerno od dužih ka kraćim skraćenicama (mm/km/kg/ml/kom PRIJE cm/dm/m/l/g/t)
# da regex alternacija ne "pojede" samo prvo slovo duže jedinice (npr. da
# "textmm" ne bi pogrešno stao na "m").
_UNIT_WORDS_ORDERED = ("mm", "km", "kg", "ml", "kom", "cm", "dm", "m", "l", "g", "t")
_BARE_TEXT_UNIT_RE = re.compile(
    r"(?<!\\)text\s*\{?\s*(" + "|".join(_UNIT_WORDS_ORDERED) + r")\s*\}?"
    r"(\^\s*\{?\s*[23]\s*\}?)?"
)

# Preostao bare "sqrt"/"text" (bez backslasha) NAKON pokušaja gornje reparacije
# — znak da je radikand/jedinica bila nejednoznačna i NIJE popravljena.
_BARE_COMMAND_RESIDUE_RE = re.compile(r"(?<!\\)(?:sqrt|text)")

# Živi nalaz (Phase 7 live test, "Dijagonala kvadrata"): model je vratio
# UDVOSTRUČEN backslash neposredno ispred poznate LaTeX komande — npr.
# doslovno "\\\\sqrt{2}" (DVA backslash znaka, ne jedan) umjesto "\\sqrt{2}".
# _RAW_LATEX_COMMAND_RE/_BARE_COMMAND_RESIDUE_RE ovo NE hvataju (znak
# neposredno ispred "sqrt" JESTE backslash, provjera prolazi), a MathJax "\\"
# (dva backslasha) tumači kao naredbu za prelom reda, pa "sqrt{2}" ostaje
# neprevedeno kao goli tekst — identičan vizuelni bag kao bare sqrt/text.
# Nedvosmisleno i sigurno: 2+ backslasha NEPOSREDNO ispred POZNATE komande se
# uvijek svode na TAČNO jedan (nikad legitiman kao stvaran prelom reda usred
# jedne kratke inline formule/opcije).
_DOUBLED_BACKSLASH_COMMAND_RE = re.compile(
    r"\\{2,}(?=(?:frac|sqrt|text|cdot|times|begin|end|pi|neq|ne|not|nu|nabla|"
    r"le|ge|leq|geq|approx|circ|degree|div|pm|infty|left|right|square|dots|"
    r"ldots|sum|int|lim|log|ln|sin|cos|tan)\b|[,;! ])"
)


def _repair_bare_sqrt_match(m):
    radicand = m.group(1)
    if radicand.startswith("("):
        radicand = "{" + radicand[1:-1] + "}"
    elif not radicand.startswith("{"):
        radicand = "{" + radicand + "}"
    return "\\sqrt" + radicand


def _repair_bare_text_unit_match(m):
    unit = m.group(1)
    exp = m.group(2)
    out = "\\text{" + unit + "}"
    if exp:
        digit_match = re.search(r"[23]", exp)
        if digit_match:
            out += "^" + digit_match.group(0)
    return out


def repair_malformed_math_inside(text: str) -> str:
    """Popravlja bare \\sqrt i \\text{jedinica} zapise UNUTAR $...$ segmenata
    (i, kad $ uopšte ne postoji, u cijelom stringu — npr. MC opcija prije
    umotavanja) — jedini raspon koji ranije nijedna funkcija nije dirala
    (živi produkcijski nalaz: "sqrt2", "textcm" stigli su do MathJax-a
    nepromijenjeni jer je find_unsafe_math_issues provjeravao ISKLJUČIVO
    tekst IZVAN $...$).

    Primjenjuje se SAMO kad je radikand/jedinica nedvosmislen. Nejednoznačni
    oblici (npr. "sqrtx" bez brojčanog radikanda) se NE pogađaju — ostaju
    za find_unsafe_math_issues da ih prijavi kao nebezbjedne."""
    if not text:
        return text or ""
    if "$" not in text:
        fixed = _DOUBLED_BACKSLASH_COMMAND_RE.sub("\\\\", text)
        fixed = _BARE_SQRT_RE.sub(_repair_bare_sqrt_match, fixed)
        fixed = _BARE_TEXT_UNIT_RE.sub(_repair_bare_text_unit_match, fixed)
        return fixed
    parts = _DOLLAR_SPLIT.split(text)
    for i in range(1, len(parts), 2):  # SAMO math segmenti (neparni indeksi)
        parts[i] = _DOUBLED_BACKSLASH_COMMAND_RE.sub("\\\\", parts[i])
        parts[i] = _BARE_SQRT_RE.sub(_repair_bare_sqrt_match, parts[i])
        parts[i] = _BARE_TEXT_UNIT_RE.sub(_repair_bare_text_unit_match, parts[i])
    return "$".join(parts)

# Ukloni poznate LaTeX komande (SA njihovim argumentima) iz kopije stringa da
# bismo provjerili šta OSTAJE — koristi se SAMO da otkrijemo da li je "ostatak"
# obična prozna riječ (npr. "Rezultat je ", "Izaberi ") umiješana s jednom
# LaTeX komandom. Whitelist charset sam po sebi NE razlikuje prozu bez
# dijakritika ("Rezultat je \frac{3}{4}") od čistog izraza ("(0,\frac{8}{3})")
# — obje prolaze charset provjeru jer koriste samo a-zA-Z/razmak/backslash/
# zagrade. Provjera ostatka to razlikuje: nakon uklanjanja komande+argumenata,
# čist izraz ne ostavlja ništa osim cifara/zagrada/operatora, dok prozna
# rečenica ostavlja prepoznatljive riječi.
_LATEX_COMMAND_WITH_ARGS_RE = re.compile(
    r"\\frac\{[^{}]*\}\{[^{}]*\}"
    r"|\\sqrt\{[^{}]*\}"
    r"|\\text\{[^{}]*\}"
    r"|\\cdot"
    r"|\\begin\{[^{}]*\}"
    r"|\\end\{[^{}]*\}"
)


def _outside_math_parts(text):
    """Dijelovi teksta IZVAN bilo kojeg $...$ para. Pretpostavlja da je broj
    '$' u tekstu PARAN (tačno stanje nakon sanitize_math_text, koja uvijek
    ili zadrži oba $ jednog segmenta ili ukloni oba) — sigurno za ponovno
    dijeljenje jer se svaki preostali $ garantovano uparuje."""
    if "$" not in text:
        return [text]
    parts = _DOLLAR_SPLIT.split(text)
    return [part for i, part in enumerate(parts) if i % 2 == 0]


def replace_literal_newline_escapes(text: str) -> str:
    """Popravlja doslovan "\\n" (dva vidljiva znaka, backslash+n) I VAN I
    UNUTAR $...$ — provjerava se preko _LITERAL_NEWLINE_ESCAPE_RE, koja NIKAD
    ne pogađa \\neq/\\ne/\\not/\\nu/\\nabla i slične ISPRAVNE komande (traži se
    da 'n' NIJE praćeno drugim slovom).

    Van $...$: pretvara se u stvaran prelom reda (prelom pasusa).
    Unutar $...$: UKLANJA se (živi nalaz: „$d = \\n\\sqrt{128}$“ — stvaran
    newline unutar inline matematike nema smisla i i dalje bi mogao lomiti
    MathJax; brisanje daje čist „$d = \\sqrt{128}$“)."""
    if not text or _LITERAL_NEWLINE_ESCAPE not in text:
        return text or ""
    if "$" not in text:
        return _LITERAL_NEWLINE_ESCAPE_RE.sub("\n", text)
    parts = _DOLLAR_SPLIT.split(text)
    for i, part in enumerate(parts):
        if i % 2 == 0:  # van $...$
            parts[i] = _LITERAL_NEWLINE_ESCAPE_RE.sub("\n", part)
        else:  # unutar $...$
            parts[i] = _LITERAL_NEWLINE_ESCAPE_RE.sub("", part)
    return "$".join(parts)


def wrap_isolated_frac_tokens(text: str) -> str:
    """Usko siguran repair: umota SAMO izolovan \\frac{a}{b} token koji se
    nalazi IZVAN postojećih $...$ segmenata, ne dirajući okolnu prozu i ne
    dirajući fracove koji su već unutar validnog $...$. Za sve složenije
    slučajeve (\\sqrt, \\text, \\cdot, \\begin/\\end izvan $...$) nema
    jednoznačno sigurnog uskog popravka — takvi ostaju za
    find_unsafe_math_issues da ih prijavi kao nebezbjedne."""
    if not text or "\\frac" not in text:
        return text or ""
    if "$" not in text:
        return _FRAC_TOKEN_RE.sub(lambda m: "$" + m.group(0) + "$", text)
    parts = _DOLLAR_SPLIT.split(text)
    for i in range(0, len(parts), 2):
        parts[i] = _FRAC_TOKEN_RE.sub(lambda m: "$" + m.group(0) + "$", parts[i])
    return "$".join(parts)


def repair_stray_terminal_brace(text: str) -> str:
    """Ukloni TAČNO JEDNU zalutalu zatvarajuću vitičastu zagradu „}“ na SAMOM
    kraju vidljivog teksta (prije eventualnog praznog prostora), kad je
    sigurno da je zalutala — živi nalaz: model je znao vratiti hint koji se
    doslovno završava s „...$y=3$.}“ (višak „}“ bez ikakvog para).

    Popravka se primjenjuje SAMO kad su ISPUNJENI svi uslovi:
      1. Zagrada je IZVAN svakog $...$ segmenta (nikad se ne dira sadržaj
         unutar $...$ — $x^{2}$, $\\frac{3}{4}$, $P_{DP}$ ostaju netaknuti).
      2. Nalazi se na samom kraju teksta (dozvoljen prateći razmak).
      3. Nije dio „}}“ (dvije ili više zaredom) — to je složeniji slučaj,
         ostavlja se netaknut.
      4. Brojanje „{“/„}“ preko CIJELOG teksta IZVAN $...$ pokazuje TAČNO
         jednu višak zatvarajuću zagradu (tj. uklanjanjem ove jedne zagrade
         cijeli tekst izvan $...$ postaje uravnotežen) — ako je bilo koji
         drugi par u tekstu već neuravnotežen (npr. „Tekst {primjer“ bez
         zatvaranja), ne pogađamo koja je „prava“ višak zagrada i ništa se
         ne dira.

    Balansirane proze zagrade (npr. „Tekst {primjer}“, „Skup je ${1,2,3}$“
    gdje su zagrade unutar $...$) i sve zagrade unutar $...$ ostaju uvijek
    netaknute — ova funkcija NIKAD ne modifikuje sadržaj unutar $...$.
    """
    if not text or "}" not in text:
        return text or ""

    parts = _DOLLAR_SPLIT.split(text)
    if len(parts) % 2 == 0:
        return text  # neparan broj '$' (nezatvoren segment) — nije naš posao

    last = parts[-1]
    stripped = last.rstrip()
    if not stripped.endswith("}") or stripped.endswith("}}"):
        return text

    outside_text = "".join(parts[i] for i in range(0, len(parts), 2))
    if outside_text.count("{") != outside_text.count("}") - 1:
        return text  # ostatak teksta izvan $...$ već nebalansiran na drugi način — ne pogađaj

    trailing_ws = last[len(stripped):]
    new_last = stripped[:-1] + trailing_ws
    if not new_last.strip():
        return text  # uklanjanje bi ostavilo prazan tekst — ne diraj
    parts[-1] = new_last
    return "$".join(parts)


def _looks_like_pure_math_expression(text: str) -> bool:
    """True SAMO kad je cio (stripovan) string sastavljen isključivo od
    znakova koji se očekuju u matematičkom izrazu (cifre, osnovni operatori,
    zagrade, slova latinice, backslash...) I sadrži bar jednu od ciljanih
    LaTeX komandi — dovoljno usko da NE zahvati obične brojeve/razlomke bez
    LaTeX-a (npr. "5/8" ostaje netaknuto, kao i danas).

    Whitelist charset SAM PO SEBI nije dovoljan: bosanska prozna rečenica BEZ
    dijakritika (npr. "Rezultat je \\frac{3}{4}", "Izaberi \\sqrt{5}") koristi
    isključivo a-zA-Z/razmak/backslash/zagrade — isti skup kao pravi izraz
    poput "(0,\\frac{8}{3})" — pa bi je charset provjera pogrešno prihvatila.
    Zato se DODATNO provjerava da nakon uklanjanja poznatih LaTeX komandi (sa
    argumentima) iz stringa NE ostaje nijedno slovo — prava prozna riječ
    (npr. "Rezultat", "je", "Izaberi") uvijek ostavi slova u ostatku."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if not _PURE_MATH_EXPRESSION_RE.match(stripped):
        return False
    if not _RAW_LATEX_COMMAND_RE.search(stripped):
        return False
    residue = _LATEX_COMMAND_WITH_ARGS_RE.sub("", stripped)
    return not re.search(r"[A-Za-z]", residue)


def _inside_math_parts(text):
    """Dijelovi teksta UNUTAR $...$ parova (vidi _outside_math_parts za
    pretpostavku o parnom broju '$')."""
    if "$" not in text:
        return []
    parts = _DOLLAR_SPLIT.split(text)
    return [part for i, part in enumerate(parts) if i % 2 == 1]


def find_unsafe_math_issues(text: str) -> list:
    """Vraća listu razloga zašto TEKST (već propušten kroz sanitize_math_text
    + gornje repair korake) NIJE bezbjedan za prikaz učeniku. Prazna lista =
    bezbjedno. Provjerava dijelove IZVAN $...$ (sirove/oštećene LaTeX komande
    tu NIKAD nisu ispravne) I dijelove UNUTAR $...$ (nakon pokušaja
    repair_malformed_math_inside — preostao bare "sqrt"/"text" znači da je
    radikand/jedinica bila nejednoznačna i namjerno NIJE pogođena)."""
    if not text:
        return []
    issues = []
    for part in _outside_math_parts(text):
        if _RAW_LATEX_COMMAND_RE.search(part):
            issues.append("raw_latex_command_outside_math")
        if _DAMAGED_LATEX_FORM_RE.search(part):
            issues.append("damaged_latex_form")
        if _LITERAL_NEWLINE_ESCAPE_RE.search(part):
            issues.append("literal_newline_escape")
        if any(ord(ch) < 0x20 and ch not in ("\n", "\t") for ch in part):
            issues.append("control_character")
    for part in _inside_math_parts(text):
        if _BARE_COMMAND_RESIDUE_RE.search(part):
            issues.append("unrepaired_bare_command_in_math")
        if _LITERAL_NEWLINE_ESCAPE_RE.search(part):
            issues.append("literal_newline_escape_in_math")
        if any(ord(ch) < 0x20 for ch in part):
            issues.append("control_character_in_math")
    return issues


def sanitize_and_validate_math_text(text: str, allow_whole_expression_wrap: bool = False):
    """Glavna ulazna tačka za USER-VISIBLE matematički tekst (task pitanje,
    opcije, feedback/reply). Vraća (sanitizovan_tekst, is_safe).

    allow_whole_expression_wrap=True samo za KRATKE atomske stringove (MC
    opcije) gdje je sigurno umotati CIO string u $...$ kad je strukturno
    potvrđen kao čist matematički izraz (vidi _looks_like_pure_math_expression).
    Za pitanje/reply (proza) ostaje False — tamo se pokušava samo usko
    umotavanje izolovanog \\frac{a}{b} tokena.

    is_safe=False znači: pozivalac MORA odbaciti CIO generisani zadatak/odgovor
    i vratiti postojeći sigurni fallback, bez drugog AI poziva — ovaj modul
    namjerno ne izmišlja zagrade/operande za nejednoznačne slučajeve.
    """
    cleaned = sanitize_math_text(text)
    cleaned = replace_literal_newline_escapes(cleaned)
    cleaned = repair_malformed_math_inside(cleaned)
    if allow_whole_expression_wrap and "$" not in cleaned and _looks_like_pure_math_expression(cleaned):
        cleaned = "$" + cleaned.strip() + "$"
    else:
        cleaned = wrap_isolated_frac_tokens(cleaned)
    cleaned = repair_stray_terminal_brace(cleaned)
    issues = find_unsafe_math_issues(cleaned)
    return cleaned, (len(issues) == 0)
