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
ANGLE_DIVIDER_VERTEX_MISMATCH = "angle_divider_vertex_mismatch"
ANGLE_DIVIDER_BOUNDARY_RAY = "angle_divider_boundary_ray"
# Protivrječna geometrijska PREMISA (ciljani blokator FW-G03) — vidi odjeljak
# „KOHERENTNOST GEOMETRIJSKE PREMISE" niže. Kod nosi i ograničen RAZLOG,
# npr. `geometry_relation_contradiction:coincident_rays_nonzero_angle`.
GEOMETRY_RELATION_CONTRADICTION = "geometry_relation_contradiction"
COINCIDENT_RAYS_NONZERO_ANGLE = "coincident_rays_nonzero_angle"

ALL_ISSUE_CODES = (
    CIRCLE_DIAMETER_USES_D, CIRCLE_DIAMETER_USES_LOWER_D, CIRCLE_RADIUS_USES_R,
    CIRCUMRADIUS_USES_R, PLANE_AREA_USES_S, PLANE_PERIMETER_AREA_SYMBOL_SWAP,
    SOLID_SPACE_DIAGONAL_USES_D, SOLID_FACE_DIAGONAL_USES_D,
    SOLID_BASE_AREA_SYMBOL_MISMATCH, PYRAMID_APOTHEM_EDGE_CONFUSION,
    GEOMETRY_FORMULA_SYMBOL_CONFLICT, ANGLE_DIVIDER_VERTEX_MISMATCH,
    ANGLE_DIVIDER_BOUNDARY_RAY, GEOMETRY_RELATION_CONTRADICTION,
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


# ---------------------------------------------------------------------------
# KOHERENTNOST TVRDNJE „KRAK DIJELI UGAO“ (DISC-D005, Task 2) — ČISTA NOTACIJA
# ---------------------------------------------------------------------------
# ŽIVI DISC NALAZ (D005, lekcija o pojmu ugla, 6. razred, dva objavljena
# zadatka u istom lancu):
#   • korak 2: tvrdnja „krak $\overrightarrow{BA}$ dijeli ugao $\angle BAC$“ —
#     zrak BA POČINJE u B, a ugao BAC ima tjeme A. Zrak koji ne polazi iz
#     tjemena ne može biti unutrašnji djelilac tog ugla. Nemoguće po notaciji.
#   • korak 4: tvrdnje „$\overrightarrow{BC}$ dijeli $\angle ABC$“ i
#     „$\overrightarrow{BD}$ dijeli $\angle ABD$“ — BC/BD su GRANIČNI kraci
#     tih uglova (C odnosno D je krajnja tačka kraka ugla), pa ne mogu biti i
#     NOVI unutrašnji djelilac istog ugla.
#
# Ovo NIJE geometrijski dokazivač: pravilo čita ISKLJUČIVO notaciju i pali se
# SAMO kad proza izričito TVRDI odnos dijeljenja (zatvoren skup glagola).
# Obična konstatacija „ugao ABC je određen kracima BA i BC“ NEMA glagol
# dijeljenja i nikad ne okida. Kad je notacija saglasna (zrak iz tjemena ka
# tački koja nije na granici), pravilo NE tvrdi da je tačka stvarno u
# unutrašnjosti — to se ne može dokazati bez slike, pa se preskače.
#
# Provjera je NEZAVISNA OD SCOPE-a: lekcije o uglovima rutiraju scope "" (nisu
# ni „plane“ ni „solid“ konvencija simbola), a kontradikcija je čisto
# notacijska — zato se pokreće prije scope kapije u find_geometry_issues.
_RAY_TOKEN = r"\\overrightarrow\s*\{\s*([A-Z])\s*([A-Z])\s*\}"
_ANGLE_TOKEN = r"\\(?:angle|measuredangle)\s*\{?\s*([A-Z])\s*([A-Z])\s*([A-Z])\s*\}?"
# Zatvoren skup glagola tvrdnje dijeljenja; „polovi/prepolavlja/raspolavlja“
# (simetrala) su strožija tvrdnja istog tipa — unutrašnji djelilac.
_DIVIDER_VERB = r"(?:dijeli\w*|podijel\w*|polovi\w*|prepolavlja\w*|raspolavlja\w*)"
# Prozor tvrdnje ne prelazi granicu rečenice/klauze niti DRUGI zrak/ugao —
# u D005 koraku 4 dvije tvrdnje stoje u istoj rečenici vezane veznikom „i“.
_DIVIDER_GAP = r"(?:(?!\\overrightarrow|\\angle|\\measuredangle|[.?!;]).)"
_DIVIDER_CLAIM_RE = re.compile(
    _RAY_TOKEN
    + r"(?P<gap>" + _DIVIDER_GAP + r"{0,80}?)"
    + r"\b(?i:" + _DIVIDER_VERB + r")\b"
    + _DIVIDER_GAP + r"{0,60}?"
    + _ANGLE_TOKEN)
_DIVIDER_NEGATION_RE = re.compile(r"(?i)\bne\s*$")


def _divider_coherence_issues(flat):
    """Kodovi dokazanih notacijskih kontradikcija tvrdnji o djeliocu ugla."""
    issues = []
    for match in _DIVIDER_CLAIM_RE.finditer(flat):
        if _DIVIDER_NEGATION_RE.search(match.group("gap") or ""):
            # „…NE dijeli…“ nije tvrdnja dijeljenja — preskoči.
            continue
        ray_start, ray_end = match.group(1), match.group(2)
        first_arm, vertex, second_arm = match.group(4), match.group(5), match.group(6)
        if ray_start == ray_end:
            continue                     # degenerisan zapis — ne dokazuje se
        if ray_start != vertex:
            issues.append(ANGLE_DIVIDER_VERTEX_MISMATCH)
        elif ray_end in (first_arm, second_arm):
            issues.append(ANGLE_DIVIDER_BOUNDARY_RAY)
    return issues


# ---------------------------------------------------------------------------
# KOHERENTNOST GEOMETRIJSKE PREMISE: POKLOPLJENI ZRACI I NENULTI UGAO
# ---------------------------------------------------------------------------
# ŽIVI BLOKATOR IZDANJA (ciljana kampanja b9151fc, FW-G03, lekcija o pojmu
# ugla). Objavljen je zadatak:
#
#   „Ugao $\angle ABC$ iznosi $60^\circ$. NA KRAKU $BA$ nalaze se četiri
#    zadata kraka iz tačke $B$: $BD$, $BE$, $BF$ i $BG$. Poznato je da su
#    uglovi mjereni od kraka $BA$ redom: $\angle ABD=10^\circ$,
#    $\angle ABE=30^\circ$, $\angle ABF=20^\circ$ i $\angle ABG=40^\circ$.
#    Koji od navedenih krakova dijeli ugao $\angle ABC$ na dva jednaka djela?“
#
# Označeno je $BE$ i to JESTE broj koji je autor htio ($60:2=30$). Ali premisa
# je neistinita: ako zrak $BD$ leži NA zraku $BA$, onda je to ISTI zrak i
# $\angle ABD$ je nužno $0^\circ$, nikad $10^\circ$. Zadatak time traži od
# učenika da rasuđuje o konfiguraciji koja ne postoji.
#
# Nijedna postojeća kapija to nije mogla vidjeti: `mathcheck` nema jednakost
# koju bi oborio, opcije su različite, označena opcija je „tačna“, oznake su
# kanonske, a koherentnost djelioca (D005 iznad) traži TVRDNJU o dijeljenju
# uz `\overrightarrow` zapis — ovdje je zapis goli par slova i tvrdnja je o
# POLOŽAJU, ne o dijeljenju.
#
# ŠTA SE OVDJE DOKAZUJE — jedna egzaktna Euklidska činjenica i ništa više:
#
#     zrak VP i zrak VQ su ISTI zrak  ⟹  ugao između njih je tačno 0°.
#
# Dakle: tekst koji IZRIČITO tvrdi poklapanje dvaju zraka iz istog tjemena, a
# istom paru pripiše NENULTI ugao, protivrječi sam sebi. Nema procjene, nema
# praga, nema slike.
#
# ŠTA SE NE DOKAZUJE: da li zrak zaista jeste unutar ugla, da li su mjere
# međusobno konzistentne, da li konfiguracija postoji. Sve što se ne može
# pročitati zatvorenom gramatikom se PRESKAČE (doktrina modula).
#
# GRANICA KOJU JE LAKO PREKORAČITI — „NA KRAKU“ NIJE „UNUTAR UGLA“:
# „krak $BE$ leži UNUTAR ugla $ABC$“ je ispravna i uobičajena premisa i nikad
# se ne smije normalizovati u „leži na kraku“. Zato je sidro isključivo
# predlog „na/sa“ + IMENICA ZRAKA, nikad „unutar“, „u“ ni „između“.
#
# ZAŠTO NE „PRAVA“: tačka na PRAVOJ $BA$ smije biti i sa suprotne strane
# tjemena, pa je ugao tada $180^\circ$, a ne $0^\circ$. Imenica prave se zato
# NIKAD ne prihvata kao sidro — samo krak/zrak/poluprava/duž, koji svi počinju
# u tjemenu.
#
# Kod nalaza i ograničen razlog stoje uz ostale kodove na vrhu modula
# (CLAUDE.md pravilo 7: u dijagnostiku ide kod i razlog, nikad sadržaj).

# Par velikih slova kao oznaka zraka/kraka ($BA$, $BD$). PRVO slovo je tjeme.
# Negativni lookaround čuva od hvatanja „AB“ ili „BD“ unutar troslovne oznake
# ugla „ABD“ — tamo par nikad ne smije biti pročitan kao zrak.
_RELATION_PAIR_RE = re.compile(r"(?<![A-Za-z])([A-Z])\s*([A-Z])(?![A-Za-z])")

# Imenice koje označavaju ZRAK IZ TJEMENA. „Prava“ NIJE među njima (vidi gore).
_RAY_NOUN = r"(?:krak\w*|zrak\w*|poluprav\w*|du[žz]\w*)"
# Glagol položaja — zatvoren skup.
_ON_VERB_RE = re.compile(r"(?i)\b(?:nalaz\w*|le[žz]\w*|pripada\w*)\b")
# Sidro „na kraku BA“ / „sa krakom BA“ — PREDLOG je obavezan i zatvoren.
_ON_RAY_ANCHOR_RE = re.compile(
    r"(?i)\b(?:na|sa|sa\s+istim|s)\s+" + _RAY_NOUN + r"\s*"
    r"(?<![A-Za-z])([A-Z])\s*([A-Z])(?![A-Za-z])")
# Izričito poklapanje: „BD se poklapa sa BA“, „BD je isti zrak kao BA“.
_COINCIDE_RE = re.compile(
    r"(?<![A-Za-z])([A-Z])\s*([A-Z])(?![A-Za-z])"
    r"(?:(?![.?!;]).){0,40}?"
    r"(?i:se\s+poklapa\w*|poklapaju\s+se|je\s+isti\s+" + _RAY_NOUN
    + r"\s+kao|jeste\s+isti\s+" + _RAY_NOUN + r"\s+kao)"
    r"(?:(?![.?!;]).){0,30}?"
    r"(?<![A-Za-z])([A-Z])\s*([A-Z])(?![A-Za-z])")
# Tačka na zraku: „Tačka $D$ leži na kraku $BA$“ → zrak BD JESTE zrak BA.
_POINT_ON_RAY_RE = re.compile(
    r"(?i)\bta[čc]k\w*\s*(?<![A-Za-z])([A-Z])(?![A-Za-z])"
    r"(?:(?![.?!;]).){0,40}?"
    r"\b(?:nalaz\w*|le[žz]\w*|pripada\w*)\b"
    r"(?:(?![.?!;]).){0,20}?"
    r"\bna\s+" + _RAY_NOUN + r"\s*"
    r"(?<![A-Za-z])([A-Z])\s*([A-Z])(?![A-Za-z])")

# Dodjela mjere uglu: `\angle ABD=10^\circ`, `\angle ABD je 30 stepeni`.
_ANGLE_MEASURE_RE = re.compile(
    _ANGLE_TOKEN
    + r"\s*(?:=|\b(?i:je|iznosi)\b)\s*"
    + r"(\d+(?:[.,]\d+)?)\s*(?:\^\s*\\circ|\\circ|°|(?i:stepen\w*|stupnj\w*))")

_SENTENCE_SPLIT_RE = re.compile(r"[.?!;]")


def _coincident_ray_claims(sentence):
    """Skup dokazanih tvrdnji poklapanja: {(tjeme, krajA, krajB)}.

    Par je NEUREĐEN po krajevima (poklapanje je simetrično) i uvijek dijeli
    tjeme — dva zraka bez zajedničkog početka se ovdje nikad ne porede."""
    claims = set()

    def add(vertex_a, end_a, vertex_b, end_b):
        if vertex_a != vertex_b or end_a == end_b:
            return                       # različito tjeme ili isti zrak
        claims.add((vertex_a, *sorted((end_a, end_b))))

    # 1) SIDRO „na kraku BA“ + glagol položaja + ostali zraci iz ISTOG tjemena.
    #    Živi oblik je nabrajanje: „Na kraku BA nalaze se … BD, BE, BF i BG“.
    if _ON_VERB_RE.search(sentence):
        for anchor in _ON_RAY_ANCHOR_RE.finditer(sentence):
            vertex, base_end = anchor.group(1), anchor.group(2)
            for pair in _RELATION_PAIR_RE.finditer(sentence):
                if pair.start() == anchor.start(1):
                    continue             # samo sidro
                add(vertex, base_end, pair.group(1), pair.group(2))

    # 2) IZRIČITO POKLAPANJE dvaju imenovanih zraka.
    for match in _COINCIDE_RE.finditer(sentence):
        add(match.group(1), match.group(2), match.group(3), match.group(4))

    # 3) TAČKA NA ZRAKU: tačka X na zraku VE znači da je zrak VX isti zrak.
    for match in _POINT_ON_RAY_RE.finditer(sentence):
        point, vertex, base_end = match.group(1), match.group(2), match.group(3)
        if point != vertex:
            add(vertex, base_end, vertex, point)
    return claims


def _angle_measures(text):
    """{(tjeme, krakA, krakB): [mjere]} iz dodjela `\\angle XYZ = θ`."""
    measures = {}
    for match in _ANGLE_MEASURE_RE.finditer(text):
        first, vertex, second = match.group(1), match.group(2), match.group(3)
        if first == vertex or second == vertex or first == second:
            continue                     # degenerisan zapis — ne dokazuje se
        try:
            value = float(match.group(4).replace(",", "."))
        except ValueError:
            continue
        measures.setdefault((vertex, *sorted((first, second))), []).append(value)
    return measures


# ---------------------------------------------------------------------------
# TAČKE NABROJANE NA KRACIMA IMENOVANOG UGLA (živi FINAL40 FW-G03, 1df3852)
# ---------------------------------------------------------------------------
# ŽIVI BLOKATOR IZDANJA:
#
#   „Ugao je ∠ABC i mjera ∠ABC je 40°. NA KRAKU AB I KRAKU BC NALAZE SE TAČKE
#    D, E, F, G tako da su mjere uglova … m∠ABD=20°, m∠ABE=10°, … Koji krak
#    dijeli ugao ∠ABC na dva jednaka dijela?"        označeno: krak BD
#
# Tačka na kraku ugla NE MOŽE zatvarati unutrašnji ugao s tim istim krakom:
# ako je D na kraku BA, zrak BD JESTE zrak BA i m∠ABD = 0; ako je D na kraku
# BC, zrak BD je zrak BC i m∠ABD = m∠ABC. Nijedna od objavljenih mjera
# (20/10/15/5 uz ∠ABC=40) nije ni 0 ni 40 — premisa je nemoguća, pa označeni
# „unutrašnji djelilac" nema koherentnu podlogu.
#
# ZAŠTO POSTOJEĆI DETEKTOR ĆUTI: `_POINT_ON_RAY_RE` traži red „tačka X … na
# kraku VE", a živi tekst ga OKREĆE („na kraku … nalaze se tačke …") i nabraja
# VIŠE tačaka odjednom. `_ON_RAY_ANCHOR_RE` pri tome čita par „AB" kao zrak
# čije je tjeme A — a krak AB ugla ABC ima tjeme B. Orijentacija se zato ovdje
# izvodi ISKLJUČIVO iz tjemena imenovanog ugla, nikad iz redoslijeda slova.
#
# GRANICE (namjerno uske, sve moraju vrijediti):
#   • u tekstu postoji TAČNO JEDAN različit imenovani ugao (inače tjeme nije
#     jednoznačno i ništa se ne tvrdi);
#   • svaki imenovani krak mora biti krak BAŠ tog ugla (dijeli mu tjeme);
#   • tačke su pojedinačna velika slova i nisu oznake samog ugla;
#   • kad su imenovana OBA kraka, tačka je na jednom ILI drugom, pa je
#     dozvoljen skup mjera {0, m∠ugla} i mjera CIJELOG ugla mora biti
#     zapisana — bez nje se NIŠTA ne tvrdi (NOT_PROVEN);
#   • kad je imenovan SAMO JEDAN krak, dozvoljena mjera je isključivo 0.
# „Prava" nije krak (vidi `_RAY_NOUN`), a „unutar ugla" nikad ne stiže ovamo.
_ARM_ANCHOR_RE = re.compile(
    r"(?i)\bna\s+" + _RAY_NOUN + r"\s*"
    r"(?<![A-Za-z])([A-Z])\s*([A-Z])(?![A-Za-z])"
    r"(?:\s*(?:,|\bi\b)\s*(?:" + _RAY_NOUN + r"\s*)?"
    r"(?<![A-Za-z])([A-Z])\s*([A-Z])(?![A-Za-z]))?")
_POINT_LIST_RE = re.compile(
    r"(?i)\bta[čc]k\w*\s+((?:(?<![A-Za-z])[A-Z](?![A-Za-z])\s*(?:,|\bi\b)?\s*)+)")
_POINT_LETTER_RE = re.compile(r"(?<![A-Za-z])([A-Z])(?![A-Za-z])")


def _arm_point_contradiction(flat, measures):
    """True kad su tačke NA OBA KRAKA ugla, a njihove mjere to poriču.

    TJEME SE NE POGAĐA: izvodi se iz ZAJEDNIČKOG slova dva imenovana kraka
    („krak AB" i „krak BC" dijele B), pa redoslijed slova u zapisu nikad nije
    pretpostavka. Zato se traže OBA kraka — s jednim imenovanim krakom tjeme
    ostaje dvosmisleno i ništa se ne tvrdi."""
    for sentence in _SENTENCE_SPLIT_RE.split(flat):
        if not _ON_VERB_RE.search(sentence):
            continue
        anchor = _ARM_ANCHOR_RE.search(sentence)
        if anchor is None or not anchor.group(3):
            continue                     # oba kraka moraju biti imenovana
        first = {anchor.group(1), anchor.group(2)}
        second = {anchor.group(3), anchor.group(4)}
        shared = first & second
        if len(shared) != 1:
            continue                     # kraci ne dijele tačno jedno tjeme
        vertex = shared.pop()
        ends = (first | second) - {vertex}
        if len(ends) != 2:
            continue
        end_a, end_b = sorted(ends)
        full = measures.get((vertex, end_a, end_b)) or []
        if len(full) != 1:
            continue                     # mjera cijelog ugla nije zapisana
        allowed = {0.0, full[0]}
        listed = _POINT_LIST_RE.search(sentence)
        if listed is None:
            continue
        points = {letter for letter in _POINT_LETTER_RE.findall(listed.group(1))
                  if letter not in {vertex, end_a, end_b}}
        for point in points:
            for reference in (end_a, end_b):
                key = (vertex, *sorted((reference, point)))
                for value in measures.get(key, ()):
                    if value not in allowed:
                        return True
    return False


def geometry_relation_contradictions(text):
    """Ograničeni razlozi dokazanih geometrijskih protivrječnosti u tekstu.

    Prazna torka znači „nije dokazano" — uključujući svaku formulaciju koju
    zatvorena gramatika ne pročita. To NIJE dokaz da je geometrija ispravna.

    Čista funkcija: bez modela, bez stanja, bez ijedne oznake lekcije. Radi s
    proizvoljnim slovima — nijedna tačka, zrak ni mjera nije konstanta."""
    flat = flatten(text)
    if not flat:
        return ()
    measures = _angle_measures(flat)
    if not measures:
        return ()                        # bez ijedne mjere nema šta protivrječiti
    claims = set()
    for sentence in _SENTENCE_SPLIT_RE.split(flat):
        claims |= _coincident_ray_claims(sentence)
    for key in sorted(claims & set(measures)):
        if any(value != 0 for value in measures[key]):
            # Razlog je KLASA protivrječnosti, nikad slova ni brojevi iz
            # sadržaja (pravilo 7) — klasa je dovoljna i za log i za recept.
            return (COINCIDENT_RAYS_NONZERO_ANGLE,)
    # Ista KLASA protivrječnosti (poklopljeni zraci uz nenulti ugao), samo
    # iskazana obrnutim redom i nad NABROJANIM tačkama — vidi odjeljak iznad.
    if _arm_point_contradiction(flat, measures):
        return (COINCIDENT_RAYS_NONZERO_ANGLE,)
    return ()


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
    if not text:
        return []
    if role == ROLE_DISTRACTOR:
        return []
    if policy == POLICY_ALLOW_INTENTIONAL:
        return []

    flat = flatten(text)
    if not flat:
        return []

    issues = []
    # DISC-D005: koherentnost tvrdnje „krak dijeli ugao“ je čisto notacijska i
    # NE zavisi od scope-a (lekcije o uglovima rutiraju scope "") — pokreće se
    # prije scope kapije, uz iste role/policy izuzetke iznad.
    issues.extend(_divider_coherence_issues(flat))
    # Ciljani blokator FW-G03: protivrječna geometrijska premisa (poklopljeni
    # zraci uz nenulti ugao). Isti razlog za mjesto poziva kao iznad — lekcije
    # o uglovima nemaju scope, a protivrječnost je egzaktna bez konvencije
    # simbola. Prosljeđuje se IZVORNI tekst: funkcija sama zove `flatten`.
    for reason in geometry_relation_contradictions(text):
        issues.append(f"{GEOMETRY_RELATION_CONTRADICTION}:{reason}")
    if not scope:
        # Bez scope-a nema konvencije simbola — ostale provjere se preskaču.
        seen = []
        for code in issues:
            if code not in seen:
                seen.append(code)
        return seen

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
