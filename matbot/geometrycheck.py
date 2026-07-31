"""Deterministička (ne-AI) provjera GEOMETRIJSKE NOTACIJE u vidljivom tekstu.

ZAŠTO POSTOJI (živi nalaz, poziv 41 velike kampanje, 6. razred, lekcija
„Centar, poluprečnik/polumjer i prečnik/promjer“): model je vratio zadatak

    „Krug ima prečnik $D=10\\,\\text{cm}$. Izračunaj obim kruga.“
    expected_answer: „$O=\\pi D=3,14\\cdot10=31,4\\,\\text{cm}$“

Račun je NUMERIČKI TAČAN ($\\pi \\cdot 10 = 31,4$), pa ga mathcheck.py propušta;
MathJax je ispravan, pa ga mathsafe.py propušta; opcije su međusobno različite,
pa ih option_equivalence.py propušta; porodica je ispravna, pa prolazi i
FamilyContract. Ali oznaka je ZABRANJENA: po projektnoj konvenciji (vidi
matbot/geometry_rules.py:42-45) prečnik je $R$ (uz $R=2r$), a $d$/$D$ su
dijagonale — „$d$ NIKAD ne znači prečnik“, a $D$ je prostorna dijagonala tijela.

Do sada je ta konvencija postojala ISKLJUČIVO u promptu. Prompt nije garancija:
ovaj modul je deterministička izlazna zaštita, po istom principu kao
terminology.py (zabranjen termin) i mathcheck.py (nedosljedan račun).

ŠTA OVAJ MODUL JESTE: uzak čuvar NOTACIJE. Provjerava da li tekst SEMANTIČKI
DEFINIŠE, DODJELJUJE ili KORISTI simbol za veličinu kojoj taj simbol po
projektnoj konvenciji ne pripada.

ŠTA OVAJ MODUL NIJE: dokazivač geometrije. Ne provjerava da li je formula
matematički tačna, ne računa i ne pogađa. Nejasna ili nepodržana upotreba se
PRESKAČE (kao mathcheck.py: preskočeno nije dokaz ispravnosti).

KLJUČNA ZAŠTITA OD LAŽNIH POZITIVA — indeksi su „nevidljivi“ za \\b:
u Pythonu je `_` član \\w, pa `\\bS\\b` NE pogađa `S_n`, `\\bd\\b` ne pogađa
`d_1`, `\\bR\\b` ne pogađa `r_o`/`r_u`, `\\bP\\b` ne pogađa `P_{DP}`/`P_{OP}`,
`\\bO\\b` ne pogađa `O_B`, `\\bh\\b` ne pogađa `h_a`. Kanonske oznake s
indeksom time ostaju netaknute BEZ ijednog posebnog izuzetka.

Slova koja mogu biti oznake TAČAKA (npr. „Tačka D pripada kružnici“, „Duž CD
je tetiva“) se NIKAD ne odbijaju sama po sebi — svaki uzorak zahtijeva da je
uz simbol prisutna RIJEČ za veličinu (prečnik/poluprečnik/površina/...) ili
kanonski oblik formule. Zato „$AD$“ i „$CD$“ ne mogu okinuti nijednu provjeru
(`\\bD\\b` se ne poklapa unutar `AD`).

OPSEG SE NIKAD NE IZVODI IZ UČENIKOVOG TEKSTA — dolazi isključivo iz
canonical (oblast, lesson_title) preko geometry_rules.route_geometry_topic().
"""
import re

# ---------------------------------------------------------------------------
# INTERNI KODOVI — nikad se ne šalju u browser (idu u InvalidOutputError
# poruku, koju pozivalac pretvara u postojeći SAFE_ERROR_MESSAGE).
# ---------------------------------------------------------------------------
CIRCLE_DIAMETER_USES_D = "circle_diameter_uses_D"
CIRCLE_DIAMETER_USES_LOWER_D = "circle_diameter_uses_d"
CIRCLE_RADIUS_USES_R = "circle_radius_uses_R"
CIRCUMRADIUS_USES_R = "circumradius_uses_R"
PLANE_AREA_USES_S = "plane_area_uses_S"
PLANE_PERIMETER_AREA_SYMBOL_SWAP = "plane_perimeter_area_symbol_swap"
SOLID_SPACE_DIAGONAL_USES_D = "solid_space_diagonal_uses_d"
SOLID_FACE_DIAGONAL_USES_D = "solid_face_diagonal_uses_D"
SOLID_BASE_AREA_SYMBOL_MISMATCH = "solid_base_area_symbol_mismatch"
PYRAMID_APOTHEM_EDGE_CONFUSION = "pyramid_apothem_edge_confusion"
GEOMETRY_FORMULA_SYMBOL_CONFLICT = "geometry_formula_symbol_conflict"

ALL_ISSUE_CODES = (
    CIRCLE_DIAMETER_USES_D, CIRCLE_DIAMETER_USES_LOWER_D, CIRCLE_RADIUS_USES_R,
    CIRCUMRADIUS_USES_R, PLANE_AREA_USES_S, PLANE_PERIMETER_AREA_SYMBOL_SWAP,
    SOLID_SPACE_DIAGONAL_USES_D, SOLID_FACE_DIAGONAL_USES_D,
    SOLID_BASE_AREA_SYMBOL_MISMATCH, PYRAMID_APOTHEM_EDGE_CONFUSION,
    GEOMETRY_FORMULA_SYMBOL_CONFLICT,
)

# --- uloge sadržaja ---------------------------------------------------------
# AUTHORITATIVE: tekst koji tvrdi ISTINITU matematiku (pitanje, tačna opcija,
#   interni expected_answer, feedback/rješenje, Explain i Quick odgovor).
# DISTRACTOR: namjerno POGREŠNA ponuđena opcija — po dizajnu multiple-choice
#   zadatka smije sadržavati pogrešnu formulu/oznaku i NIKAD se ne provjerava
#   ovim modulom (isti princip kao numerička provjera distraktora u
#   matbot/practice.py: pogrešna opcija ne smije srušiti cio zadatak).
ROLE_AUTHORITATIVE = "authoritative"
ROLE_DISTRACTOR = "distractor"

# --- politike za TEKST PITANJA (server-derived, nikad iz modela) ------------
POLICY_CHECK = "check"
POLICY_ALLOW_INTENTIONAL = "allow_intentional_violation"

# Tijela s KRUŽNOM bazom — tamo konvencija $R=2r$ takođe važi.
_CIRCULAR_SOLIDS = frozenset({"valjak", "kupa", "lopta"})


# ---------------------------------------------------------------------------
# NORMALIZACIJA — spoji prozu i matematiku u jedan čitljiv red
# ---------------------------------------------------------------------------

_SPACING_RE = re.compile(r"\\,|\\;|\\!|\\quad|\\qquad|\\ |\\left|\\right")
_TEXT_CMD_RE = re.compile(r"\\(?:text|mathrm|mathit)\s*\{([^{}]*)\}")
_DOLLAR_RE = re.compile(r"(?<!\\)\$")
_WS_RE = re.compile(r"\s+")


def flatten(text):
    """Ukloni $ delimitere i LaTeX „šum“ tako da riječ i simbol postanu susjedni.

    „Krug ima prečnik $D=10\\,\\text{cm}$.“ → „Krug ima prečnik D=10 cm.“
    Indeksi (`d_1`, `r_o`, `P_{DP}`, `O_B`, `h_a`, `S_n`) ostaju netaknuti, pa
    ih granica riječi \\b i dalje štiti od svih provjera ispod.
    """
    if not text:
        return ""
    out = _TEXT_CMD_RE.sub(r" \1 ", text)
    out = _SPACING_RE.sub(" ", out)
    out = _DOLLAR_RE.sub(" ", out)
    return _WS_RE.sub(" ", out).strip()


# ---------------------------------------------------------------------------
# RIJEČI ZA VELIČINE (ijekavica/ekavica + oblici bez dijakritika)
#
# VELIČINA SLOVA JE KRITIČNA: cijela konvencija počiva na razlici $d$/$D$,
# $r$/$R$ i $S$/$s$. Zato uzorci NISU case-insensitive kao cjelina — samo
# BOSANSKE RIJEČI su umotane u lokalni `(?i:...)` flag, dok simboli ostaju
# case-sensitive. (Raniji `re.IGNORECASE` nad cijelim uzorkom je brisao upravo
# razliku koju modul treba da čuva: `$d=a\\sqrt{3}$` i `$D=a\\sqrt{3}$` su
# izgledali identično.)
# ---------------------------------------------------------------------------
_DIAM = r"(?i:pre[čc]nik\w*|promjer\w*)"
_RAD = r"(?i:polupre[čc]nik\w*|polumjer\w*)"
_AREA = r"(?i:povr[šs]in\w*)"
_PERIM = r"(?i:obim\w*)"
_OPISAN = r"(?i:opisan\w*)"
_UPISAN = r"(?i:upisan\w*)"
# Veznici između RIJEČI za veličinu i SIMBOLA. Osim kopule („je“, „iznosi“,
# „označen sa“), dozvoljena je i kratka GENITIVNA dopuna imenice — živi nalaz
# iz dry-runa: „Prečnik baze je $D=6\\,\\text{cm}$“ nije bio uhvaćen jer se
# između „Prečnik“ i „D“ našla imenica „baze“.
#
# Namjerno je to ZATVOREN spisak, a ne slobodan prozor od N znakova: slobodan
# prozor bi lažno okinuo na „Izračunaj prečnik ako je tačka $D$ na kružnici“,
# gdje je $D$ oznaka TAČKE, a ne prečnika. Riječi „ako“ i „tačka“ nisu veznici,
# pa takva rečenica ostaje ispravno neprijavljena.
_CONNECTOR_WORDS = (
    r"je|su|iznosi|ozna[čc]en\w*|oznake|oznaka|sa|s|"
    r"baze|baza|osnove|osnova|kruga|krug|kru[žz]nic\w*|"
    r"valjka|kupe|lopte|sfere|piramide|prizme|kocke|kvadra|"
    r"strane|figure|tijela|kvadrata|pravougaonika|trougla|romba|trapeza|"
    r"tog|ovog|te|toga|dat\w*|traž\w*"
)
_COP = r"(?:\s*(?i:" + _CONNECTOR_WORDS + r")\b)*\s*"


def _rx(pattern):
    """Case-SENSITIVE po dizajnu — vidi napomenu iznad."""
    return re.compile(pattern)


# --- KRUG: prečnik ---------------------------------------------------------
# „prečnik D=10 cm“, „prečnik je označen sa D“, „prečnik D“
_DIAM_IS_UPPER_D = _rx(_DIAM + _COP + r"\bD\b")
_UPPER_D_IS_DIAM = _rx(r"\bD\b\s*(?i:je|ozna[čc]ava|predstavlja)\s*" + _DIAM)
# „D=2r“ u kontekstu kruga — kanonski je $R=2r$.
_UPPER_D_EQ_2R = _rx(r"\bD\s*=\s*2\s*(?:\\cdot\s*)?r\b")
# „O=\pi D“ / „O=\pi\cdot D“ — obim kruga preko pogrešne oznake prečnika.
_PERIM_PI_UPPER_D = _rx(r"\bO\s*=\s*\\?pi\s*(?:\\cdot\s*)?\bD\b")

_DIAM_IS_LOWER_D = _rx(_DIAM + _COP + r"\bd\b")
_LOWER_D_IS_DIAM = _rx(r"\bd\b\s*(?i:je|ozna[čc]ava|predstavlja)\s*" + _DIAM)
_LOWER_D_EQ_2R = _rx(r"\bd\s*=\s*2\s*(?:\\cdot\s*)?r\b")
_PERIM_PI_LOWER_D = _rx(r"\bO\s*=\s*\\?pi\s*(?:\\cdot\s*)?\bd\b")

# --- KRUG: poluprečnik -----------------------------------------------------
# Namjerno isključuje „opisane/upisane“ — to je zaseban kod (r_o/r_u).
_RAD_IS_R = _rx(_RAD + r"(?!\s+(?i:opisan|upisan))" + _COP + r"\bR\b")
_R_IS_RAD = _rx(r"\bR\b\s*(?i:je|ozna[čc]ava|predstavlja)\s*" + _RAD
                + r"(?!\s+(?i:opisan|upisan))")

# --- opisana kružnica ------------------------------------------------------
_CIRCUMRADIUS_R = _rx(_RAD + r"\s+" + _OPISAN + r"(?:\s+(?i:kru[žz]nic\w*))?" + _COP + r"\bR\b")
_R_IS_CIRCUMRADIUS = _rx(r"\bR\b\s*(?i:je|ozna[čc]ava|predstavlja)\s*" + _RAD
                         + r"\s+" + _OPISAN)

# --- RAVAN: površina/obim --------------------------------------------------
# Kanonski oblici DESNE strane koji nedvosmisleno znače POVRŠINU ravne figure.
_AREA_RHS = _rx(
    r"^\s*(?:"
    r"a\s*\\cdot\s*b|ab\b|a\s*\*\s*b"
    r"|a\^\s*\{?2\}?"
    r"|\\frac\s*\{[^{}]*h[^{}]*\}\s*\{\s*2\s*\}"
    r"|\\frac\s*\{\s*d_?1?\s*(?:\\cdot)?\s*d_?2?\s*\}\s*\{\s*2\s*\}"
    r"|\\frac\s*\{\s*\(?\s*a\s*\+\s*c\s*\)?\s*h\s*\}\s*\{\s*2\s*\}"
    r"|\\?pi\s*r\^\s*\{?2\}?"
    r")"
)
_AREA_UNIT = _rx(r"\b(?:mm|cm|dm|m)\s*\^?\s*\{?\s*2\s*\}?")
_S_ASSIGN = _rx(r"\bS\s*=\s*(?P<rhs>[^.;]*)")


def _near(quantity_word, other_word, symbol, window=30):
    """„<veličina> [kratka imenička dopuna] <simbol>=“ — npr. „Obim kvadrata je
    $P=4a$“. Prozor NIKAD ne prelazi granicu rečenice/klauze (`.`, `;`, `,`)
    niti SUPROTNU riječ za veličinu, pa „Površina je $P=ab$, a obim $O=4a$“
    ostaje ispravno (zarez prekida prozor)."""
    blocked = r"(?:(?!" + other_word + r"|[.;,]).)"
    return _rx(quantity_word + blocked + r"{0,%d}?" % window + symbol)


_AREA_IS_S = _near(_AREA, r"(?i:obim)", r"\bS\s*=", window=40)
_AREA_IS_O = _near(_AREA, r"(?i:obim)", r"\bO\s*=")
_PERIM_IS_P = _near(_PERIM, r"(?i:povr[šs]in)", r"\bP\s*=")

# --- TIJELA: dijagonale ----------------------------------------------------
_SPACE_DIAG_LOWER_D = _rx(r"(?i:prostorn\w*\s+dijagonal\w*)" + _COP + r"\bd\b")
_FACE_DIAG_UPPER_D = _rx(r"(?i:dijagonal\w*\s+(?:strane|osnove|baze|bo[čc]ne\s+strane))"
                         + _COP + r"\bD\b")
# Kocka: d=a√2 je dijagonala STRANE, D=a√3 je PROSTORNA. Zamjena je nedvosmislena.
_CUBE_LOWER_D_SQRT3 = _rx(r"\bd\s*=\s*a\s*\\?sqrt\s*\{?\s*3\s*\}?")
_CUBE_UPPER_D_SQRT2 = _rx(r"\bD\s*=\s*a\s*\\?sqrt\s*\{?\s*2\s*\}?")

# --- TIJELA: baza ----------------------------------------------------------
_BASE = r"(?i:baze|osnove|baza|osnova)"
_BASE_AREA_WRONG = _rx(_AREA + r"\s+" + _BASE + _COP + r"\b[PS]\s*=")
_BASE_PERIM_WRONG = _rx(_PERIM + r"\s+" + _BASE + _COP + r"\bO\s*=")

# --- PIRAMIDA: apotema vs bočna ivica --------------------------------------
_APOTHEM_IS_S = _rx(r"(?i:apotem\w*)" + _COP + r"\bs\b")
_LATERAL_EDGE_IS_HA = _rx(r"(?i:bo[čc]n\w*\s+ivic\w*)" + _COP + r"h_a\b")
_SLANT_IS_HA = _rx(r"(?i:izvodnic\w*)" + _COP + r"h_a\b")

# --- formula konflikti (SAMO ravan krug) -----------------------------------
# $R$ je PREČNIK, pa je obim $\pi R$ (ne $2\pi R$), a površina $\pi r^2$
# (ne $\pi R^2$). U tijelu „lopta“ je $P=\pi R^2$ ISPRAVNO, zato je ova
# provjera ograničena na ravan krug.
_PERIM_2PI_R = _rx(r"\bO\s*=\s*2\s*(?:\\cdot\s*)?\\?pi\s*(?:\\cdot\s*)?\bR\b")
_AREA_PI_R2 = _rx(r"\bP\s*=\s*\\?pi\s*(?:\\cdot\s*)?\bR\s*\^\s*\{?\s*2\s*\}?")


def _circle_active(scope, figures):
    figs = set(figures or ())
    if scope == "plane" and "krug" in figs:
        return True
    if scope == "solid" and (figs & _CIRCULAR_SOLIDS):
        return True
    return False


def _pyramid_active(figures):
    return any(str(f).startswith("piramida") or str(f) == "kupa" for f in (figures or ()))


def _cube_like(figures):
    return bool({"kocka", "kvadar"} & set(figures or ()))


def find_geometry_issues(text, scope, figures=(), role=ROLE_AUTHORITATIVE,
                         policy=POLICY_CHECK):
    """Vrati listu INTERNIH kodova (prazno = nema dokazane povrede notacije).

    text     — VIDLJIV tekst NAKON sanitizacije i normalizacije terminologije
    scope    — "plane" | "solid" | "" (iz route_geometry_topic; nikad iz učenika)
    figures  — figure ID-jevi iz route_geometry_topic
    role     — ROLE_AUTHORITATIVE ili ROLE_DISTRACTOR (distraktor se preskače)
    policy   — POLICY_CHECK ili POLICY_ALLOW_INTENTIONAL (porodice čiji je
               predmet ispitivanja BAŠ pogrešna oznaka)

    Nikad ne mijenja tekst, nikad ne poziva model.
    """
    if not text or not scope:
        return []
    if role == ROLE_DISTRACTOR:
        return []
    if policy == POLICY_ALLOW_INTENTIONAL:
        return []

    flat = flatten(text)
    if not flat:
        return []

    issues = []
    circle = _circle_active(scope, figures)

    if circle:
        if (_DIAM_IS_UPPER_D.search(flat) or _UPPER_D_IS_DIAM.search(flat)
                or _UPPER_D_EQ_2R.search(flat) or _PERIM_PI_UPPER_D.search(flat)):
            issues.append(CIRCLE_DIAMETER_USES_D)
        if (_DIAM_IS_LOWER_D.search(flat) or _LOWER_D_IS_DIAM.search(flat)
                or _LOWER_D_EQ_2R.search(flat) or _PERIM_PI_LOWER_D.search(flat)):
            issues.append(CIRCLE_DIAMETER_USES_LOWER_D)
        if _RAD_IS_R.search(flat) or _R_IS_RAD.search(flat):
            issues.append(CIRCLE_RADIUS_USES_R)

    # $r_o$ konvencija važi i van same lekcije o krugu (trougao/mnogougao).
    if scope in ("plane", "solid"):
        if _CIRCUMRADIUS_R.search(flat) or _R_IS_CIRCUMRADIUS.search(flat):
            issues.append(CIRCUMRADIUS_USES_R)

    if scope == "plane":
        if _AREA_IS_S.search(flat):
            issues.append(PLANE_AREA_USES_S)
        else:
            # „$S=ab$“ bez riječi „površina“: prihvati kao površinu SAMO kad je
            # desna strana kanonski oblik površine ili nosi kvadratnu jedinicu.
            for m in _S_ASSIGN.finditer(flat):
                rhs = m.group("rhs") or ""
                if _AREA_RHS.search(rhs.strip()) or _AREA_UNIT.search(rhs):
                    issues.append(PLANE_AREA_USES_S)
                    break
        if _AREA_IS_O.search(flat) or _PERIM_IS_P.search(flat):
            issues.append(PLANE_PERIMETER_AREA_SYMBOL_SWAP)
        if circle and (_PERIM_2PI_R.search(flat) or _AREA_PI_R2.search(flat)):
            issues.append(GEOMETRY_FORMULA_SYMBOL_CONFLICT)

    if scope == "solid":
        if _SPACE_DIAG_LOWER_D.search(flat) or (_cube_like(figures)
                                                and _CUBE_LOWER_D_SQRT3.search(flat)):
            issues.append(SOLID_SPACE_DIAGONAL_USES_D)
        if _FACE_DIAG_UPPER_D.search(flat) or (_cube_like(figures)
                                               and _CUBE_UPPER_D_SQRT2.search(flat)):
            issues.append(SOLID_FACE_DIAGONAL_USES_D)
        if _BASE_AREA_WRONG.search(flat) or _BASE_PERIM_WRONG.search(flat):
            issues.append(SOLID_BASE_AREA_SYMBOL_MISMATCH)
        if _pyramid_active(figures) and (
                _APOTHEM_IS_S.search(flat) or _LATERAL_EDGE_IS_HA.search(flat)
                or _SLANT_IS_HA.search(flat)):
            issues.append(PYRAMID_APOTHEM_EDGE_CONFUSION)

    # Stabilan, deduplicirani redoslijed za testove i logove.
    seen = []
    for code in issues:
        if code not in seen:
            seen.append(code)
    return seen


def is_geometry_clean(text, scope, figures=(), role=ROLE_AUTHORITATIVE,
                      policy=POLICY_CHECK):
    return not find_geometry_issues(text, scope, figures, role=role, policy=policy)
