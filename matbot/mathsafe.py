"""Deterministička (ne-AI) zaštita od LaTeX-a koji bi izazvao "Math input error"
u MathJax-u na frontendu. Nema ambiciju da bude potpun LaTeX parser — provjerava
tri stvari koje su dovoljne da spriječe vidljivu grešku kod učenika:

0. JSON dvostruko-escape bug: model ponekad vrati LaTeX komandu bez ispravnog
   JSON escape-a backslasha (npr. piše "\frac" umjesto "\\frac" u JSON stringu).
   JSON parser to protumači kao poznat escape (\f, \b, \t, \r, \n) i rezultat
   je STVARNI kontrolni znak (npr. U+000C form feed) umjesto backslasha, praćen
   ostatkom komande kao običnim slovima (npr. "rac{3}{5}"). Prije bilo koje
   druge provjere, ovi kontrolni znakovi se unutar matematičkih segmenata
   rekonstruišu nazad u literalni "\f"/"\b"/"\t"/"\r"/"\n" — tako
   "<FORM FEED>rac{3}{5}" ponovo postaje "\frac{3}{5}".
1. broj neescaped '$' delimitera mora biti paran (svaki otvoren mora biti zatvoren)
2. unutar svakog matematičkog segmenta, vitičaste zagrade { } moraju biti
   balansirane (pokriva slomljen \frac{a}{b i slične greške)

Kad segment ne prođe provjeru, NE pravimo popravni AI poziv niti pokušavamo
pogoditi ispravan LaTeX — samo uklanjamo delimitere oko tog segmenta, tako da
MathJax uopšte ne pokuša da ga parsira i učenik dobije čitljiv obični tekst
umjesto crvene greške.

PODRŠKA ZA `$...$` I `$$...$$` (živi nalaz, Explain — vidi docs/CURRENT_STATE.md
C-3/C-5): stariji kod je dijelio tekst NAIVNIM alternating-splitom na SVAKI
pojedinačan `$` znak, pa je par susjednih `$$` razbijao na DVA odvojena para
umjesto da ih prepozna kao JEDAN display blok. Posljedica: sadržaj unutar
`$$...$$` je bio tretiran kao da je VAN matematike (npr. `$$P=\\frac{a\\cdot
h}{2}$$` je postajalo `$$P=$\\frac{a\\cdot h}{2}$$$` — ubačen je promašen
dodatan `$`), a numerička provjera (mathcheck.py) je za `$$...$$` segmente
uvijek vraćala prazan string (nikad nije provjeravala njihov sadržaj). Sav
delimiter-parsing u ovom modulu sada ide kroz matbot/mathsegments.py —
zajednički tokenizator koji ISPRAVNO razlikuje `$...$` (inline) od `$$...$$`
(display) jednim lijevo-desno prolazom, bez regex alternacije."""
import re

from matbot.mathsegments import (
    DISPLAY,
    INLINE,
    TEXT,
    ensure_even_dollar_count,
    join_segments,
    map_math_segments,
    map_text_segments,
    tokenize_math,
)

# ---------------------------------------------------------------------------
# BIJELA LISTA MathJax KOMANDI (živi nalaz D35-1, pozivi 10 i 12 kampanje od
# 35 poziva). Do sada nijedna provjera nije gledala DA LI je komanda unutar
# $...$ uopšte postojeća MathJax komanda — pa su „\ty“ i „\tdot“ (nastali u
# _repair_control_chars, vidi tamo) i udvostručen „\\cdot“ stigli do browsera i
# prikazali se kao crvena „Math input error“ / goli tekst.
#
# Lista je JEDAN izvor istine: iz nje se grade i regexi za skupljanje
# udvostručenog backslasha i skener nepoznatih komandi. Sadrži samo ono što se
# stvarno koristi u ovom projektu (osnovna škola 6-9) plus uobičajene školske
# komande — namjerno NIJE potpun LaTeX. Nepoznata komanda se NE pogađa i NE
# prepravlja: cio odgovor se odbija i vraća se postojeći sigurni fallback.
MATHJAX_COMMAND_ALLOWLIST = frozenset({
    # struktura i tekst
    "frac", "dfrac", "tfrac", "sqrt", "text", "textbf", "textit",
    "mathbb", "mathrm", "mathit", "mathbf", "left", "right",
    "overline", "underline", "vec", "hat", "bar",
    "begin", "end", "array", "cases", "aligned", "matrix", "pmatrix",
    # ŽIVI F5L NALAZ (I03 + M06/M18 klasa): model u punim rješenjima
    # uobičajeno piše "$\displaystyle \frac{...}$" i "\boxed{...}" — obje su
    # standardne, bezbjedne komande samo za prikaz, a njihovo odsustvo je
    # zatvaralo SVAKO takvo rješenje porukom „nebezbjedan matematički zapis
    # [full_solution_request]“. Dokazano novim kodovima u logu objave:
    # unknown_mathjax_command:displaystyle (dvaput u jednom živom rješenju),
    # unknown_mathjax_command:boxed (u živom nacrtu zadatka). "textstyle" je
    # simetrični par iste komande (isti rizik: nikakav) — dodan uz njih.
    "displaystyle", "textstyle", "boxed",
    # operatori i relacije
    "cdot", "times", "div", "pm", "mp", "approx", "neq", "ne", "not",
    "le", "leq", "ge", "geq", "lt", "gt", "equiv", "sim", "propto",
    "angle", "perp", "parallel", "cong", "colon",
    # simboli
    "pi", "Pi", "alpha", "beta", "gamma", "Gamma", "delta", "Delta",
    "epsilon", "varepsilon", "zeta", "eta", "theta", "Theta", "iota",
    "kappa", "lambda", "Lambda", "mu", "nu", "xi", "rho", "sigma", "Sigma",
    "tau", "phi", "varphi", "Phi", "chi", "psi", "Psi", "omega", "Omega",
    "nabla", "circ", "degree", "infty",
    "dots", "ldots", "cdots", "vdots", "prime", "square", "triangle",
    "in", "notin", "subset", "subseteq", "supset", "cup", "cap",
    "emptyset", "varnothing", "forall", "exists",
    "Rightarrow", "Leftarrow", "Leftrightarrow", "to", "rightarrow",
    "leftarrow", "mapsto",
    # Metoda strelica za proporcionalnost (audit ovlašćenja pravila, odluka
    # F): smjer promjene veličina se zapisuje s \uparrow/\downarrow — vidi
    # matbot/proportion_arrows.py.
    "uparrow", "downarrow",
    # funkcije i veliki operatori
    "sum", "prod", "int", "lim", "log", "ln", "lg",
    "sin", "cos", "tan", "cot", "sec", "csc", "min", "max", "gcd",
    # razmaci
    "quad", "qquad",
})

# Poredak od DUŽE ka KRAĆOJ komandi: regex alternacija inače „stane“ na kraćem
# prefiksu (npr. „ne“ prije „neq“) i granica ispod se pogrešno ne poklopi.
_ALLOWED_COMMAND_ALTERNATION = "|".join(
    sorted(MATHJAX_COMMAND_ALLOWLIST, key=lambda w: (-len(w), w))
)
# Granica komande: NIJE \b nego „nije slovo“. \b se NIKAD ne aktivira između
# dva word-znaka, a cifra JESTE word-znak — zato „\\cdot3“ (zapis bez razmaka
# koji je u ovom projektu uobičajen: „$2\cdot3=6$“) nije prolazio ni kroz jedan
# od donja dva regexa i udvostručen backslash je stizao do browsera netaknut
# (živi nalaz D35-1, poziv 12).
_COMMAND_BOUNDARY = r"(?![A-Za-z])"

# Shared model-output math transport repair. Some structured model outputs
# over-escape the MathJax delimiters themselves (``\$...\$``) or preserve two
# literal backslashes before a command after JSON parsing. Those strings are
# valid JSON, but ``\$`` means a literal dollar to MathJax and ``\\command``
# means a line break followed by raw text. Originally written for Quick only;
# Explain calls the SAME function (matbot/explain.py) — see docs/CURRENT_STATE.md
# C-10: the same over-escaping can come from any mode, and rejecting it outright
# in Explain (as opposed to repairing it, like Quick already did) cost the
# student the whole answer for no reason. Practice keeps its existing behavior
# (Practice never saw this failure mode in the live evidence that justified this
# repair, so it is not touched here).
_ESCAPED_DOLLAR_RE = re.compile(r"\\\$")
_RESULT_MATH_COMMAND_RE = re.compile(
    r"\\(?:frac|sqrt|text|cdot|times|mathbb|dots|ldots|neq|leq|geq|approx|"
    r"infty|pi|left|right)\b|\\[{}]"
)
_RESULT_OVERESCAPED_COMMAND_RE = re.compile(
    r"\\{2,}(?=(?:" + _ALLOWED_COMMAND_ALTERNATION + r")" + _COMMAND_BOUNDARY + r"|[{}])"
)


def _clearly_enclosed_math(value):
    r"""Usko prepoznaj sadržaj kojem ``\$...\$`` nedvosmisleno znači math."""
    text = (value or "").strip()
    if not text or "$" in text or "\n" in text or "\r" in text:
        return False
    if text.count("{") != text.count("}"):
        return False

    # Ukloni poznate komande prije provjere proznih riječi. Jednoslovne
    # varijable/skupovi (x, y, N, R) su dozvoljeni; višeslovna proza nije.
    residue = re.sub(r"\\text\{[^{}]*\}", "", text)
    residue = re.sub(r"\\(?:[A-Za-z]+|[{}])", "", residue)
    if any(len(word) > 1 for word in re.findall(r"[A-Za-z]+", residue)):
        return False
    has_command = bool(_RESULT_MATH_COMMAND_RE.search(text))
    has_operator = bool(re.search(r"[=<>+\-*/^_]", residue))
    return has_command or has_operator


def normalize_result_math_transport(text):
    """Normalize JSON/MathJax over-escaping shared by Explain and Quick.

    Returns ``(normalized, safe)``. Escaped dollars are converted only in
    balanced pairs whose content is clearly mathematical. Currency/plain
    escaped dollars remain byte-for-byte unchanged. A math-like dangling or
    nested escaped delimiter is rejected instead of guessed.
    """
    value = text or ""
    matches = list(_ESCAPED_DOLLAR_RE.finditer(value))
    if matches:
        if len(matches) % 2:
            tail = value[matches[-1].end():]
            return (value, False) if _clearly_enclosed_math(tail) else (value, True)
        out = []
        cursor = 0
        for index in range(0, len(matches), 2):
            opening, closing = matches[index], matches[index + 1]
            body = value[opening.end():closing.start()]
            out.append(value[cursor:opening.start()])
            if "$" in body:
                return value, False
            if _clearly_enclosed_math(body):
                out.append("$" + body + "$")
            else:
                out.append(value[opening.start():closing.end()])
            cursor = closing.end()
        out.append(value[cursor:])
        value = "".join(out)

    # Samo unutar stvarnih matematičkih segmenata (inline ILI display): dva
    # ili više backslasha pred poznatom komandom/kontrolnim simbolom svode se
    # na tačno jedan.
    value = map_math_segments(value, lambda content: _RESULT_OVERESCAPED_COMMAND_RE.sub("\\\\", content))
    return value, True

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
    """Popravlja SAMO unutar jednog $...$ segmenta — ne dira tekst van njega.

    Živi nalaz D35-1 (poziv 10): ranija verzija je SVAKI kontrolni znak slijepo
    vraćala u literalni „\\t“/„\\f“/… bez gledanja šta iza njega slijedi. Model
    je poslao JSON "x=3,\\ty=1" — pravi TAB kao obični razmak između dvije
    vrijednosti — a mi smo od njega NAPRAVILI nepostojeću komandu „\\ty“ koja je
    u browseru dala „Math input error“.

    Sada se rekonstrukcija radi SAMO kad kontrolni znak + slova koja slijede
    daju komandu iz MATHJAX_COMMAND_ALLOWLIST (TAB+„imes“ → „\\times“, form
    feed+„rac“ → „\\frac“). Kad iza kontrolnog znaka NE slijedi slovo, on nije
    mogao biti dio komande — tretira se kao razmak. Kad slijede slova koja ne
    daju poznatu komandu, NE pogađamo koja je komanda trebala biti: ostavljamo
    literalni escape, pa ga find_unknown_math_commands prijavi i cio odgovor
    bude odbijen."""
    out = []
    i = 0
    length = len(segment)
    while i < length:
        ch = segment[i]
        escape = _CONTROL_TO_LATEX_ESCAPE.get(ch)
        if escape is not None:
            end = i + 1
            while end < length and segment[end].isalpha():
                end += 1
            command = escape[1] + segment[i + 1:end]
            if command in MATHJAX_COMMAND_ALLOWLIST:
                out.append("\\" + command)
                i = end
                continue
            if end == i + 1:
                out.append(" ")  # iza njega nema slova — nije mogao biti komanda
            else:
                out.append(escape)  # nepoznato: ne pogađaj, pusti skener da odbije
            i += 1
            continue
        if ord(ch) < 0x20:
            i += 1  # ostali rijetki kontrolni znakovi: ukloni, ne pogađaj slovo
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def sanitize_math_text(text: str) -> str:
    if not text or "$" not in text:
        return text or ""

    segments = tokenize_math(text)
    if len(segments) == 1 and segments[0][0] == TEXT:
        return text  # nema validnih delimitera (npr. jedan nezatvoren $)

    out_segments = []
    for kind, content in segments:
        if kind == TEXT:
            out_segments.append((TEXT, content))
            continue
        content = _repair_control_chars(content)  # PRIJE provjere balansa
        # Nebalansirane zagrade ILI usamljen "$" UNUTAR sadržaja (ugniježđen/
        # nejednoznačan delimiter, npr. "$$P=1$ nekompletno$$" — tokenize_math
        # traži TAČNO "$$" da zatvori display, pa jedan "$" unutra ostaje kao
        # SADRŽAJ, ne kao delimiter) — u OBA slučaja delimitere uklanjamo ODMAH,
        # PRIJE finalne provjere parnosti ispod, da ta provjera ne bi slučajno
        # "riješila" nejednoznačnost brisanjem POGREŠNOG '$' i time sakrila
        # problem umjesto da ga odbije.
        if content.count("{") != content.count("}") or "$" in content:
            out_segments.append((TEXT, content))
        else:
            out_segments.append((kind, content))  # ispravan segment — zadrži tip i delimitere
    return ensure_even_dollar_count(join_segments(out_segments))


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

# NAPOMENA: granica MORA biti _COMMAND_BOUNDARY (ne \b) iz istog razloga kao
# gore (D35-1) — \b se ne aktivira između backslash-komande i cifre koja
# neposredno slijedi, pa je „\cdot2“ (bez razmaka, ovaj projekat ga piše baš
# tako: „$2\cdot3=6$“) IZVAN $...$ prije prolazio kroz ovu provjeru nezapaženo
# i stizao do browsera kao sirov, neomeđen tekst (backslash i sve) umjesto da
# padne zatvoreno. \b je ovdje bio identičan propust kao onaj koji je D35-1
# već ispravio za regexe udvostručenog backslasha.
_RAW_LATEX_COMMAND_RE = re.compile(
    r"\\(?:frac|sqrt|text|mathbb|cdot|begin|end)" + _COMMAND_BOUNDARY
)

# ---------------------------------------------------------------------------
# POLITIKA KOMANDI IZVAN MATEMATIKE (živi nalaz D35T-1, poziv 3 od 14)
# ---------------------------------------------------------------------------
# „Koje su komande VALIDNE unutar $...$“ i „šta smije stajati IZVAN $...$“ su
# DVA RAZLIČITA pitanja. Prethodni prolaz ih je spojio: cijela
# MATHJAX_COMMAND_ALLOWLIST je korištena i kao lista za odbijanje izvan
# matematike, pa je odgovor koji je bio matematički POTPUNO TAČAN (deklarisao
# π≈3,14 i izračunao 18,84 cm) odbijen samo zato što je u prozi pisalo
# „LEKCIJA: Broj \pi i obim kruga“. To je ponovilo poznatu C-10 klasu lažnog
# odbijanja: popravka je bolja od odbijanja kad je odgovor inače ispravan.
#
# Sada postoje tri jasno odvojene klase:
#
#   1. SAMOSTALNI SIMBOLI (bez argumenata) — „\pi“, grčka slova, relacije.
#      Izvan matematike se NE odbijaju nego se USKO umotaju u $...$
#      („Broj \pi i obim“ → „Broj $\pi$ i obim“). Simbol je jednoznačan, nema
#      argumenata koje bi trebalo pogađati, i nikad ne smije ostati sirov u
#      browseru.
#   2. STRUKTURNE KOMANDE — „\frac“, „\sqrt“, „\text“, „\mathbb“, „\cdot“,
#      „\begin“, „\end“. One nose argumente/operande; izvan matematike se NE
#      pogađaju nego padaju zatvoreno (osim izolovanog \frac{a}{b} koji
#      wrap_isolated_frac_tokens već umije sigurno umotati).
#   3. NEPOZNATE KOMANDE — uvijek padaju zatvoreno, i unutar i izvan.
#
# NAPOMENA O OBIMU: sve u ovom modulu radi ISKLJUČIVO nad izlazom modela.
# Poruka učenika ne prolazi kroz sanitizaciju i nikad se ne prepisuje.
_STANDALONE_SYMBOL_COMMANDS = frozenset({
    # grčka slova i konstante
    "pi", "Pi", "alpha", "beta", "gamma", "Gamma", "delta", "Delta",
    "epsilon", "varepsilon", "zeta", "eta", "theta", "Theta", "iota",
    "kappa", "lambda", "Lambda", "mu", "nu", "xi", "rho", "sigma", "Sigma",
    "tau", "phi", "varphi", "Phi", "chi", "psi", "Psi", "omega", "Omega",
    "infty",
    # relacije i jednoznačni operatorski simboli (bez argumenata)
    "approx", "neq", "ne", "le", "leq", "ge", "geq", "equiv", "sim",
    "pm", "mp", "times", "div", "circ", "degree",
    # skupovni i strelice
    "in", "notin", "subset", "subseteq", "supset", "cup", "cap",
    "emptyset", "varnothing", "to", "rightarrow", "leftarrow", "mapsto",
    "Rightarrow", "Leftarrow", "Leftrightarrow",
    # tri tačke
    "dots", "ldots", "cdots",
})
assert _STANDALONE_SYMBOL_COMMANDS <= MATHJAX_COMMAND_ALLOWLIST

_STANDALONE_SYMBOL_RE = re.compile(
    r"(?<!\\)\\(" + "|".join(
        sorted(_STANDALONE_SYMBOL_COMMANDS, key=lambda w: (-len(w), w))
    ) + r")" + _COMMAND_BOUNDARY
)
# Bilo koja komanda (poznata ili ne) koja je OSTALA izvan matematike nakon
# uskog umotavanja iznad. Koristi se samo za prijavu, nikad za pogađanje.
_ANY_COMMAND_OUTSIDE_MATH_RE = re.compile(r"(?<!\\)\\([A-Za-z]+)")


def wrap_standalone_symbols_outside_math(text: str) -> str:
    """Umota SAMOSTALAN simbol bez argumenata (npr. „\\pi“) koji stoji IZVAN
    matematike u vlastiti $...$ par. Ne dira sadržaj unutar $...$/$$...$$, ne
    umata okolnu prozu i ne dodaje ništa osim delimitera oko samog simbola."""
    if not text or "\\" not in text:
        return text or ""

    def _wrap(match):
        return "$\\" + match.group(1) + "$"

    if "$" not in text:
        return _STANDALONE_SYMBOL_RE.sub(_wrap, text)
    return map_text_segments(text, lambda part: _STANDALONE_SYMBOL_RE.sub(_wrap, part))
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
# — znak da je radikand/jedinica bila nejednoznačna i NIJE popravljena. NEMA
# granicu iza namjerno: "sqrtx"/"textabc" (nejednoznačan radikand/jedinica —
# repair iznad ih svjesno NE dira) mora ostati prijavljen čak i kad odmah iza
# slijedi još jedno slovo.
_BARE_COMMAND_RESIDUE_RE = re.compile(r"(?<!\\)(?:sqrt|text)")

# GENERIČKA verzija za SVE OSTALE komande iz MATHJAX_COMMAND_ALLOWLIST (živi
# nalaz: "4ecdot2"/"2ecdot2 + 3ecdot3" u produkciji — ispravan "$4 \\cdot 2$"
# je učeniku stigao BEZ backslasha ispred "cdot"). Bez obzira KOJIM mehanizmom
# backslash nestane (spori kontrolni znak koji _repair_control_chars ne zna
# rekonstruisati pa ga tiho obriše, model koji jednostavno zaboravi "\",
# bilo koji budući sličan bag), rezultat je uvijek isti oblik: poznata komanda
# ostaje kao GOLA riječ unutar matematičkog segmenta. Prijašnja provjera je
# gledala SAMO "sqrt"/"text" — "cdot", "times", "frac" i svaka druga komanda
# je mogla proći NEOPAŽENO. Ovo NIJE zakrpa za baš "cdot": to je jedan
# generički skener nad CIJELOM preostalom bijelom listom, nikad pogađanje koje
# je komanda "trebala" biti.
#
# "sqrt"/"text" su namjerno izuzete iz OVE alternacije — imaju SVOJU provjeru
# iznad, bez granice iza, iz istog razloga kao u tom komentaru. Granica ISPRED
# (ni backslash ni slovo) sprječava pogodak usred duže riječi/identifikatora;
# granica IZA je ista _COMMAND_BOUNDARY kao svuda u modulu (cifra iza JOŠ UVIJEK
# broji kao granica — to je baš slučaj koji je propuštao, npr. "cdot2").
_BARE_OTHER_COMMAND_ALTERNATION = "|".join(
    sorted(MATHJAX_COMMAND_ALLOWLIST - {"sqrt", "text"}, key=lambda w: (-len(w), w))
)
_BARE_OTHER_COMMAND_RESIDUE_RE = re.compile(
    r"(?<![\\A-Za-z])(?:" + _BARE_OTHER_COMMAND_ALTERNATION + r")" + _COMMAND_BOUNDARY
)

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
    r"\\{2,}(?=(?:" + _ALLOWED_COMMAND_ALTERNATION + r")" + _COMMAND_BOUNDARY + r"|[,;! ])"
)

# Preostao udvostručen backslash NEPOSREDNO ispred slova NAKON gornjeg
# skupljanja znači da komanda iza njega nije poznata — ne pogađamo je.
_RESIDUAL_DOUBLED_BACKSLASH_RE = re.compile(r"\\{2,}(?=[A-Za-z])")
# Jedan backslash + slova = kandidat za komandu. Negativan lookbehind isključuje
# udvostručene slučajeve (njih hvata regex iznad).
_MATH_COMMAND_TOKEN_RE = re.compile(r"(?<!\\)\\([A-Za-z]+)")


def find_unknown_math_commands(content: str) -> list:
    """Vrati BOUNDED dijagnostičke kodove za komande unutar JEDNOG matematičkog
    segmenta koje nisu u MATHJAX_COMMAND_ALLOWLIST. Kod sadrži ISKLJUČIVO ime
    komande i kategoriju — nikad tekst učenika ni ostatak odgovora."""
    issues = []
    if not content:
        return issues
    if _RESIDUAL_DOUBLED_BACKSLASH_RE.search(content):
        issues.append("doubled_backslash_before_command")
    for match in _MATH_COMMAND_TOKEN_RE.finditer(content):
        command = match.group(1)
        if command not in MATHJAX_COMMAND_ALLOWLIST:
            issues.append("unknown_mathjax_command:" + command[:24])
    return issues


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


# Živi nalaz (kampanja od 14 poziva, poziv 5): model je napisao „$180\circ$“
# umjesto „$180^\circ$“, pa se stepen renderuje kao prsten u liniji teksta
# umjesto kao znak stepena. Popravka je jednoznačna i USKA: samo kad \circ
# NEPOSREDNO slijedi cifru (i kad ispred već nije „^“). Validan „180^\circ“ se
# time nikad ne dira.
_DEGREE_WITHOUT_CARET_RE = re.compile(r"(?<!\^)(\d)\s*\\circ(?![A-Za-z])")


def _repair_malformed_math_content(content):
    fixed = _DOUBLED_BACKSLASH_COMMAND_RE.sub("\\\\", content)
    fixed = _BARE_SQRT_RE.sub(_repair_bare_sqrt_match, fixed)
    fixed = _BARE_TEXT_UNIT_RE.sub(_repair_bare_text_unit_match, fixed)
    fixed = _DEGREE_WITHOUT_CARET_RE.sub(r"\1^\\circ", fixed)
    return fixed


def repair_malformed_math_inside(text: str) -> str:
    """Popravlja bare \\sqrt i \\text{jedinica} zapise UNUTAR matematičkih
    segmenata — I inline ($...$) I display ($$...$$) — (i, kad $ uopšte ne
    postoji, u cijelom stringu — npr. MC opcija prije umotavanja) — jedini
    raspon koji ranije nijedna funkcija nije dirala (živi produkcijski nalaz:
    "sqrt2", "textcm" stigli su do MathJax-a nepromijenjeni jer je
    find_unsafe_math_issues provjeravao ISKLJUČIVO tekst IZVAN $...$).

    Primjenjuje se SAMO kad je radikand/jedinica nedvosmislen. Nejednoznačni
    oblici (npr. "sqrtx" bez brojčanog radikanda) se NE pogađaju — ostaju
    za find_unsafe_math_issues da ih prijavi kao nebezbjedne."""
    if not text:
        return text or ""
    if "$" not in text:
        return _repair_malformed_math_content(text)
    return map_math_segments(text, _repair_malformed_math_content)

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
    """Dijelovi teksta IZVAN bilo kojeg matematičkog segmenta (inline ILI
    display) — preko zajedničkog tokenizatora (matbot/mathsegments.py), pa
    sadržaj unutar $$...$$ NIKAD ne bude pogrešno tretiran kao da je van
    matematike (živi nalaz C-3, vidi docs/CURRENT_STATE.md)."""
    if "$" not in text:
        return [text]
    return [content for kind, content in tokenize_math(text) if kind == TEXT]


def _inside_math_parts(text):
    """Dijelovi teksta UNUTAR matematičkih segmenata — I inline ($...$) I
    display ($$...$$)."""
    if "$" not in text:
        return []
    return [content for kind, content in tokenize_math(text) if kind in (INLINE, DISPLAY)]


def replace_literal_newline_escapes(text: str) -> str:
    """Popravlja doslovan "\\n" (dva vidljiva znaka, backslash+n) I VAN I
    UNUTAR matematičkih segmenata — provjerava se preko
    _LITERAL_NEWLINE_ESCAPE_RE, koja NIKAD ne pogađa \\neq/\\ne/\\not/\\nu/
    \\nabla i slične ISPRAVNE komande (traži se da 'n' NIJE praćeno drugim
    slovom).

    Van matematike: pretvara se u stvaran prelom reda (prelom pasusa).
    Unutar $...$ ili $$...$$: UKLANJA se (živi nalaz: „$d = \\n\\sqrt{128}$“ —
    stvaran newline unutar matematike nema smisla i i dalje bi mogao lomiti
    MathJax; brisanje daje čist „$d = \\sqrt{128}$“)."""
    if not text or _LITERAL_NEWLINE_ESCAPE not in text:
        return text or ""
    if "$" not in text:
        return _LITERAL_NEWLINE_ESCAPE_RE.sub("\n", text)
    segments = tokenize_math(text)
    new_segments = [
        (kind, _LITERAL_NEWLINE_ESCAPE_RE.sub("\n" if kind == TEXT else "", content))
        for kind, content in segments
    ]
    return join_segments(new_segments)


def wrap_isolated_frac_tokens(text: str) -> str:
    """Usko siguran repair: umota SAMO izolovan \\frac{a}{b} token koji se
    nalazi IZVAN postojećih matematičkih segmenata, ne dirajući okolnu prozu i
    ne dirajući fracove koji su već unutar validnog $...$ ili $$...$$. Za sve
    složenije slučajeve (\\sqrt, \\text, \\cdot, \\begin/\\end izvan
    matematike) nema jednoznačno sigurnog uskog popravka — takvi ostaju za
    find_unsafe_math_issues da ih prijavi kao nebezbjedne."""
    if not text or "\\frac" not in text:
        return text or ""
    if "$" not in text:
        return _FRAC_TOKEN_RE.sub(lambda m: "$" + m.group(0) + "$", text)
    return map_text_segments(text, lambda t: _FRAC_TOKEN_RE.sub(lambda m: "$" + m.group(0) + "$", t))


def repair_stray_terminal_brace(text: str) -> str:
    """Ukloni TAČNO JEDNU zalutalu zatvarajuću vitičastu zagradu „}“ na SAMOM
    kraju vidljivog teksta (prije eventualnog praznog prostora), kad je
    sigurno da je zalutala — živi nalaz: model je znao vratiti hint koji se
    doslovno završava s „...$y=3$.}“ (višak „}“ bez ikakvog para).

    Popravka se primjenjuje SAMO kad su ISPUNJENI svi uslovi:
      1. Zagrada je IZVAN svakog matematičkog segmenta (nikad se ne dira
         sadržaj unutar $...$ ili $$...$$ — $x^{2}$, $\\frac{3}{4}$,
         $P_{DP}$ ostaju netaknuti).
      2. Nalazi se na samom kraju teksta (dozvoljen prateći razmak) —
         tj. tekst se MORA završavati van matematike.
      3. Nije dio „}}“ (dvije ili više zaredom) — to je složeniji slučaj,
         ostavlja se netaknut.
      4. Brojanje „{“/„}“ preko CIJELOG teksta IZVAN matematike pokazuje
         TAČNO jednu višak zatvarajuću zagradu (tj. uklanjanjem ove jedne
         zagrade cijeli tekst izvan matematike postaje uravnotežen) — ako je
         bilo koji drugi par u tekstu već neuravnotežen (npr. „Tekst {primjer“
         bez zatvaranja), ne pogađamo koja je „prava“ višak zagrada i ništa
         se ne dira.

    Balansirane proze zagrade (npr. „Tekst {primjer}“, „Skup je ${1,2,3}$“
    gdje su zagrade unutar $...$) i sve zagrade unutar matematike ostaju
    uvijek netaknute — ova funkcija NIKAD ne modifikuje sadržaj unutar
    $...$ ili $$...$$.
    """
    if not text or "}" not in text:
        return text or ""

    segments = tokenize_math(text)
    if not segments or segments[-1][0] != TEXT:
        return text  # tekst se ne završava van matematike — nije naš posao

    last = segments[-1][1]
    stripped = last.rstrip()
    if not stripped.endswith("}") or stripped.endswith("}}"):
        return text

    outside_text = "".join(content for kind, content in segments if kind == TEXT)
    if outside_text.count("{") != outside_text.count("}") - 1:
        return text  # ostatak teksta izvan matematike već nebalansiran na drugi način — ne pogađaj

    trailing_ws = last[len(stripped):]
    new_last = stripped[:-1] + trailing_ws
    if not new_last.strip():
        return text  # uklanjanje bi ostavilo prazan tekst — ne diraj
    segments = segments[:-1] + [(TEXT, new_last)]
    return join_segments(segments)


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


def find_unsafe_math_issues(text: str) -> list:
    """Vraća listu razloga zašto TEKST (već propušten kroz sanitize_math_text
    + gornje repair korake) NIJE bezbjedan za prikaz učeniku. Prazna lista =
    bezbjedno. Provjerava dijelove IZVAN matematike (sirove/oštećene LaTeX
    komande tu NIKAD nisu ispravne) I dijelove UNUTAR matematike (inline ILI
    display, nakon pokušaja repair_malformed_math_inside — preostao bare
    "sqrt"/"text" znači da je radikand/jedinica bila nejednoznačna i namjerno
    NIJE pogođena)."""
    if not text:
        return []
    issues = []
    # A terminal backslash cannot form a complete LaTex command or an escaped
    # character. It is a concrete truncation signal (for example, a response
    # ending in ``... 15\\``) and must never reach the student as a partial
    # worked solution.
    if text.rstrip().endswith("\\"):
        issues.append("dangling_terminal_backslash")
    for part in _outside_math_parts(text):
        # Strukturne komande izvan matematike (nose argumente) — ne pogađaju se.
        if _RAW_LATEX_COMMAND_RE.search(part):
            issues.append("raw_latex_command_outside_math")
        # Sve ostalo što je ostalo kao komanda izvan matematike NAKON umotavanja
        # samostalnih simbola: nepoznato ili nije bezbjedno umotati → odbij.
        for match in _ANY_COMMAND_OUTSIDE_MATH_RE.finditer(part):
            command = match.group(1)
            if command in MATHJAX_COMMAND_ALLOWLIST and command not in _STANDALONE_SYMBOL_COMMANDS:
                continue  # već pokriveno _RAW_LATEX_COMMAND_RE provjerom iznad
            if command not in MATHJAX_COMMAND_ALLOWLIST:
                issues.append("unknown_mathjax_command_outside_math:" + command[:24])
        if _DAMAGED_LATEX_FORM_RE.search(part):
            issues.append("damaged_latex_form")
        if _LITERAL_NEWLINE_ESCAPE_RE.search(part):
            issues.append("literal_newline_escape")
        if any(ord(ch) < 0x20 and ch not in ("\n", "\t") for ch in part):
            issues.append("control_character")
    for part in _inside_math_parts(text):
        issues.extend(find_unknown_math_commands(part))
        if _BARE_COMMAND_RESIDUE_RE.search(part):
            issues.append("unrepaired_bare_command_in_math")
        bare_other = _BARE_OTHER_COMMAND_RESIDUE_RE.search(part)
        if bare_other:
            issues.append("bare_command_in_math:" + bare_other.group(0)[:24])
        if _LITERAL_NEWLINE_ESCAPE_RE.search(part):
            issues.append("literal_newline_escape_in_math")
        if any(ord(ch) < 0x20 for ch in part):
            issues.append("control_character_in_math")
        # Faza A1 (docs/CURRENT_STATE.md): jedan neupareni "$" UNUTAR već
        # izdvojenog matematičkog segmenta (inline ili display) znači
        # ugniježđen/nejednoznačan delimiter — npr. "$$P=1$ nekompletno$$"
        # ostavlja doslovan "$" kao SADRŽAJ (tokenize_math traži TAČNO "$$" da
        # zatvori display, pa usamljen "$" unutra ne otvara/zatvara ništa).
        # Ovaj projekat "$" nikad ne koristi kao znak valute unutar matematike
        # (novčani iznosi su u "KM"), pa je doslovan "$" unutar matematike
        # UVIJEK sumnjiv — ne pogađamo namjeru, odbijamo cio odgovor.
        if "$" in part:
            issues.append("nested_dollar_in_math_segment")
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
    cleaned, issues = sanitize_and_validate_math_text_with_issues(
        text, allow_whole_expression_wrap=allow_whole_expression_wrap)
    return cleaned, (len(issues) == 0)


def sanitize_and_validate_math_text_with_issues(text: str, allow_whole_expression_wrap: bool = False):
    """Isti niz koraka kao `sanitize_and_validate_math_text`, ali vraća i LISTU
    internih kodova defekta umjesto samo boolean-a.

    ZAŠTO POSTOJI (živi A+B ab-5ac723e: 13 turnova s
    `unsafe_solution_notation: (solution)`, od toga 5 s `unchanged=True`):
    recenzent je u dijagnostici dobijao SAMO ime polja, ne i KOJI zapis je
    server odbio, pa je vraćao isto polje nepromijenjeno i turn je propadao.
    Kodovi (`unknown_mathjax_command:\\ty`, `damaged_latex_form`, …) su već
    sankcionisani log kodovi po CLAUDE.md pravilu 7 — nose najviše ime
    komande, nikad rečenicu sadržaja. Pravila se NE mijenjaju: is_safe je i
    dalje tačno `len(issues) == 0`."""
    cleaned = sanitize_math_text(text)
    cleaned = replace_literal_newline_escapes(cleaned)
    cleaned = repair_malformed_math_inside(cleaned)
    if allow_whole_expression_wrap and "$" not in cleaned and _looks_like_pure_math_expression(cleaned):
        cleaned = "$" + cleaned.strip() + "$"
    else:
        cleaned = wrap_isolated_frac_tokens(cleaned)
        # Uski popravak PRIJE provjere: samostalan simbol izvan matematike
        # (npr. „\pi“ u prozi) dobije svoje $...$ umjesto da sruši cio odgovor.
        cleaned = wrap_standalone_symbols_outside_math(cleaned)
    cleaned = repair_stray_terminal_brace(cleaned)
    return cleaned, find_unsafe_math_issues(cleaned)
