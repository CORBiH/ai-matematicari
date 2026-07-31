"""Kanonske geometrijske oznake, terminologija i formule (BiH osnovna škola).

IZVOR ISTINE (referentni dokumenti, NE parsiraju se u runtime-u — sadržaj je
ovdje pretočen u determinističke strukturirane blokove):
  reference/curriculum/geometry/Trougao_cetverougao_mnogouglovi_formule_BiH.pdf
  reference/curriculum/geometry/Geometrijska_tijela_formule_BiH.pdf

NAJVAŽNIJA KONVENCIJA (namjerno odstupa od uobičajene školske prakse drugdje):
  R = prečnik kruga, i vrijedi R = 2r
  r = poluprečnik kruga
  d, d_1, d_2 = dijagonala/dijagonale (NIKAD prečnik)
  r_u = poluprečnik UPISANE kružnice, r_o = poluprečnik OPISANE kružnice
       (NIKAD R za poluprečnik opisane kružnice)

Arhitektura: ne postoji jedan ogroman geometrijski string koji ide u svaki
prompt. route_geometry_topic() deterministički prepozna da li je lekcija o
ravnim figurama ili tijelima i KOJE figure su relevantne, a build_geometry_rules()
sastavi SAMO: zajedničke oznake za taj opseg + blokove prepoznatih figura.
Lekcije bez geometrije ne dobiju ništa (prazan string).
"""
import re

# Koliko najviše blokova figura ide u jedan prompt — lekcija koja spomene više
# figura (npr. „Poređenje kvadrata i romba“) dobije oba, ali nikad cijeli
# dokument. Štiti veličinu prompta.
MAX_FIGURE_BLOCKS = 3


# ---------------------------------------------------------------------------
# ZAJEDNIČKE OZNAKE — dvije varijante, prema opsegu lekcije
# ---------------------------------------------------------------------------

_PLANE_SYMBOLS = (
    "GEOMETRIJSKE OZNAKE — RAVNE FIGURE (obavezno, ovo je jedina dozvoljena konvencija):\n"
    "- $O$ = obim figure; $P$ = površina figure. Ista oznaka ima isto značenje u cijelom zadatku.\n"
    "- Tjemena (vrhovi) su $A$, $B$, $C$, ...; stranice su $a$, $b$, $c$, ...; "
    "u trouglu stranica $a$ leži NASUPROT tjemenu $A$.\n"
    "- Unutrašnji uglovi su $\\alpha$, $\\beta$, $\\gamma$, ...\n"
    "- $s$ = poluobim trougla, $s=\\frac{O}{2}$.\n"
    "- $h_a$, $h_b$, $h_c$ = visine na stranice $a$, $b$, $c$.\n"
    "- $t_a$, $t_b$, $t_c$ = težišnice (medijane).\n"
    "- $d$, $d_1$, $d_2$ = dijagonala ili dijagonale — oznaka $d$ NIKAD ne znači prečnik.\n"
    "- $r_u$ = poluprečnik UPISANE kružnice; $r_o$ = poluprečnik OPISANE kružnice.\n"
    "- $r$ = poluprečnik kruga; $R$ = PREČNIK kruga, pri čemu je $R=2r$. "
    "Oznaka $R$ NIKAD ne znači poluprečnik opisane kružnice (za to postoji $r_o$).\n"
    "- $n$ = broj stranica mnogougla.\n"
    "- Jedinice: dužina i obim mm, cm, dm, m; površina mm², cm², dm², m². "
    "Sve dužine prije računanja pretvori u istu mjernu jedinicu; uz obim ide dužinska, "
    "a uz površinu kvadratna jedinica.\n"
)

_SOLID_SYMBOLS = (
    "GEOMETRIJSKE OZNAKE — GEOMETRIJSKA TIJELA (obavezno, ovo je jedina dozvoljena konvencija):\n"
    "- $B$ = površina jedne baze (osnove); $O_B$ = obim baze; $M$ = površina omotača; "
    "$P$ = ukupna površina tijela; $V$ = zapremina tijela.\n"
    "- $H$ = visina geometrijskog tijela; $h$ = visina mnogougla u osnovi.\n"
    "- $h_a$ = apotema piramide (visina bočne strane) — NE miješaj je s bočnom ivicom.\n"
    "- $s$ = bočna ivica piramide ili izvodnica kupe.\n"
    "- $a$, $b$, $c$ = dužine ivica ili stranica osnove.\n"
    "- $r$ = poluprečnik kruga; $R$ = PREČNIK kruga, pri čemu je $R=2r$. "
    "Oznaka $R$ NIKAD ne znači poluprečnik opisane kružnice (za to postoji $r_o$).\n"
    "- $r_u$ = poluprečnik upisane kružnice; $r_o$ = poluprečnik opisane kružnice.\n"
    "- $d$, $d_1$ = dijagonale osnove; $D$, $D_1$ = prostorne dijagonale — "
    "oznaka $d$ NIKAD ne znači prečnik.\n"
    "- $P_{DP}$ = površina dijagonalnog presjeka; $P_{OP}$ = površina osnog presjeka.\n"
    "- $\\pi$ = Ludolfov broj, približno $3,14$.\n"
    "- Jedinice: dužina mm, cm, dm, m; površina mm², cm², dm², m²; zapremina mm³, cm³, dm³, m³; "
    "$1\\,\\text{dm}^3=1$ litar, $1\\,\\text{cm}^3=1$ mililitar. Sve dužine pretvori u istu "
    "mjernu jedinicu, a rezultat napiši s kvadratnom (površina) ili kubnom (zapremina) jedinicom.\n"
)

_PLANE_TERMS = (
    "TERMINOLOGIJA (ravne figure): tjeme (vrh) pri prvom spominjanju, zatim samo tjeme; "
    "trougao, četverougao, mnogougao; jednakokraki, jednakostranični, pravougli, "
    "raznostranični, oštrougli, tupougli; obim, površina; težišnica, simetrala; "
    "poluprečnik, prečnik; kateta, hipotenuza; osnovica, krak.\n"
)

_SOLID_TERMS = (
    "TERMINOLOGIJA (tijela): tjeme (vrh) pri prvom spominjanju, zatim samo tjeme; "
    "baza (osnova), omotač, ivica, bočna strana, bočna ivica, apotema, izvodnica; "
    "obim, površina, zapremina; poluprečnik, prečnik; prizma, piramida, valjak, kupa, lopta.\n"
)


# ---------------------------------------------------------------------------
# BLOKOVI FORMULA PO FIGURI — doslovno prema referentnim dokumentima
# ---------------------------------------------------------------------------

_FIGURE_RULES = {
    # ---------------- ravne figure ----------------
    "trougao": (
        "FORMULE — TROUGAO:\n"
        "- $O = a+b+c$; $\\alpha+\\beta+\\gamma = 180^\\circ$.\n"
        "- Nejednakost trougla: $|b-c| < a < b+c$ (analogno za svaku stranicu).\n"
        "- $P = \\frac{a \\cdot h_a}{2} = \\frac{b \\cdot h_b}{2} = \\frac{c \\cdot h_c}{2}$.\n"
        "- Heronova formula: $s = \\frac{a+b+c}{2}$, pa $P = \\sqrt{s(s-a)(s-b)(s-c)}$.\n"
        "- Veze s kružnicama: $P = r_u \\cdot s$ i $P = \\frac{abc}{4r_o}$.\n"
        "- Visina je normala iz tjemena na nasuprotnu stranicu (sijeku se u ortocentru); "
        "težišnica spaja tjeme sa sredinom nasuprotne stranice i težišnice se dijele u odnosu $2:1$.\n"
    ),
    "pravougli_trougao": (
        "FORMULE — PRAVOUGLI TROUGAO:\n"
        "- Katete su $a$ i $b$, hipotenuza je $c$ (najduža stranica, nasuprot pravom uglu).\n"
        "- Pitagorina teorema: $c^2 = a^2+b^2$.\n"
        "- $P = \\frac{ab}{2}$; visina na hipotenuzu $h_c = \\frac{ab}{c}$.\n"
        "- Euklidove teoreme (uz projekcije $p$, $q$ kateta na hipotenuzu): "
        "$c = p+q$, $a^2 = cp$, $b^2 = cq$, $h_c^2 = pq$, $ab = ch_c$.\n"
        "- Kružnice: $r_o = \\frac{c}{2}$, $r_u = \\frac{a+b-c}{2}$.\n"
        "- Posebni trouglovi: $45^\\circ$-$45^\\circ$-$90^\\circ$ → druga kateta $a$, hipotenuza $a\\sqrt{2}$; "
        "$30^\\circ$-$60^\\circ$-$90^\\circ$ → duža kateta $a\\sqrt{3}$, hipotenuza $2a$.\n"
    ),
    "jednakokraki_trougao": (
        "FORMULE — JEDNAKOKRAKI TROUGAO:\n"
        "- Osnovica je $a$, kraci su $b$; visina na osnovicu je ujedno težišnica i simetrala ugla pri vrhu.\n"
        "- $O = a+2b$; $h_a = \\sqrt{b^2-\\left(\\frac{a}{2}\\right)^2}$; $P = \\frac{a \\cdot h_a}{2}$.\n"
    ),
    "jednakostranicni_trougao": (
        "FORMULE — JEDNAKOSTRANIČNI TROUGAO:\n"
        "- Sve stranice $a$, svi uglovi $60^\\circ$; visina, težišnica i simetrala se poklapaju.\n"
        "- $O = 3a$; $h = \\frac{a\\sqrt{3}}{2}$; $P = \\frac{a^2\\sqrt{3}}{4}$.\n"
        "- $r_u = \\frac{a\\sqrt{3}}{6}$; $r_o = \\frac{a\\sqrt{3}}{3}$.\n"
    ),
    "paralelogram": (
        "FORMULE — PARALELOGRAM:\n"
        "- Naspramne stranice paralelne i jednake; dijagonale se međusobno polove.\n"
        "- $O = 2(a+b)$; $P = a \\cdot h_a = b \\cdot h_b$.\n"
        "- $d_1^2 + d_2^2 = 2(a^2+b^2)$.\n"
    ),
    "pravougaonik": (
        "FORMULE — PRAVOUGAONIK:\n"
        "- $O = 2(a+b)$; $P = ab$.\n"
        "- Dijagonala: $d = \\sqrt{a^2+b^2}$; $r_o = \\frac{d}{2}$.\n"
    ),
    "kvadrat": (
        "FORMULE — KVADRAT:\n"
        "- $O = 4a$; $P = a^2 = \\frac{d^2}{2}$.\n"
        "- Dijagonala: $d = a\\sqrt{2}$.\n"
        "- $r_u = \\frac{a}{2}$; $r_o = \\frac{d}{2} = \\frac{a\\sqrt{2}}{2}$.\n"
    ),
    "romb": (
        "FORMULE — ROMB:\n"
        "- Sve stranice jednake; dijagonale su okomite i međusobno se polove.\n"
        "- $O = 4a$; $P = a \\cdot h_a = \\frac{d_1 d_2}{2}$.\n"
        "- $d_1^2 + d_2^2 = 4a^2$; $r_u = \\frac{h_a}{2}$.\n"
    ),
    "trapez": (
        "FORMULE — TRAPEZ:\n"
        "- Osnovice su $a$ i $c$, kraci $b$ i $d$, visina $h$, srednja linija $m$.\n"
        "- $O = a+b+c+d$; $m = \\frac{a+c}{2}$; $P = \\frac{(a+c)h}{2} = mh$.\n"
        "- Jednakokraki trapez (uz $a \\ge c$ i krak $b$): "
        "$h = \\sqrt{b^2-\\left(\\frac{a-c}{2}\\right)^2}$.\n"
        "- Pravougli trapez: jedan krak je okomit na osnovice i jednak visini $h$.\n"
    ),
    "deltoid": (
        "FORMULE — DELTOID:\n"
        "- Dva para jednakih susjednih stranica ($a$, $a$ i $b$, $b$); dijagonale su okomite.\n"
        "- $O = 2(a+b)$; $P = \\frac{d_1 d_2}{2}$.\n"
    ),
    "cetverougao": (
        "FORMULE — ČETVEROUGAO (opšte):\n"
        "- $O = a+b+c+d$; $\\alpha+\\beta+\\gamma+\\delta = 360^\\circ$.\n"
        "- Preko dijagonale $d$ i udaljenosti $h_1$, $h_2$ druga dva tjemena: "
        "$P = \\frac{d(h_1+h_2)}{2}$.\n"
        "- Tetivni četverougao: zbir naspramnih uglova je $180^\\circ$. "
        "Tangencijalni četverougao: $a+c = b+d$.\n"
    ),
    "mnogougao": (
        "FORMULE — MNOGOUGAO:\n"
        "- Broj tjemena = broj stranica = $n$; broj dijagonala iz jednog tjemena je $n-3$; "
        "ukupan broj dijagonala je $\\frac{n(n-3)}{2}$; broj trouglova iz jednog tjemena je $n-2$.\n"
        "- Zbir unutrašnjih uglova: $S_n = (n-2) \\cdot 180^\\circ$; zbir vanjskih uglova: $S_v = 360^\\circ$.\n"
        "- Pravilan $n$-tougao: $\\alpha = \\frac{(n-2) \\cdot 180^\\circ}{n}$, $\\beta = \\frac{360^\\circ}{n}$.\n"
        "- Broj stranica: $n = \\frac{S_n}{180^\\circ}+2$ ili $n = \\frac{360^\\circ}{\\beta}$.\n"
        "- Obim i površina pravilnog mnogougla: $O = na$; $P = \\frac{O \\cdot r_u}{2}$.\n"
        "- Pravilni šestougao: $O = 6a$, $P = \\frac{3a^2\\sqrt{3}}{2}$, "
        "$r_u = \\frac{a\\sqrt{3}}{2}$, $r_o = a$, $d_1 = a\\sqrt{3}$, $d_2 = 2a$.\n"
    ),
    "krug": (
        "FORMULE — KRUŽNICA I KRUG:\n"
        "- $r$ = poluprečnik (polumjer); $R$ = PREČNIK (promjer), pri čemu je $R=2r$. "
        "Oznaka $d$ NIKAD ne znači prečnik — $d$ je dijagonala. Ni $D$ nije prečnik "
        "(to je prostorna dijagonala kod tijela).\n"
        "- Obim kruga: $O = 2\\pi r = \\pi R$.\n"
        "- Površina kruga: $P = \\pi r^2$.\n"
        "- $\\pi$ je Ludolfov broj, približno $3,14$.\n"
        "- Tetiva je duž čije su obje krajnje tačke na kružnici; prečnik je najduža tetiva.\n"
        "- Prava i kružnica: tangenta dodiruje kružnicu u jednoj tački (normalna je na "
        "poluprečnik u dodirnoj tački), sječica (sekanta) je siječe u dvije tačke.\n"
        "- Dužina kružnog luka nad centralnim uglom $\\alpha$: $l = \\frac{\\pi r \\alpha}{180^\\circ}$.\n"
        "- Površina kružnog isječka nad centralnim uglom $\\alpha$: "
        "$P = \\frac{\\pi r^2 \\alpha}{360^\\circ}$.\n"
        "- Kružni prsten između poluprečnika $r_1$ i $r_2$ (uz $r_1 > r_2$): "
        "$P = \\pi(r_1^2 - r_2^2)$ — koristi $r_1$/$r_2$, NIKAD $R$ za poluprečnik.\n"
    ),
    "slicnost": (
        "FORMULE — PODUDARNOST I SLIČNOST:\n"
        "- Podudarnost: SSS, SUS, USU, SSU. Sličnost: UU, SSS, SUS.\n"
        "- Uz koeficijent sličnosti $k$: dužine i obimi se mijenjaju $k$ puta, površine $k^2$ puta — "
        "$\\frac{O_2}{O_1} = k$, $\\frac{P_2}{P_1} = k^2$.\n"
    ),

    # ---------------- geometrijska tijela ----------------
    "prizma": (
        "FORMULE — PRIZMA:\n"
        "- Za $n$-tostranu prizmu: $2n$ tjemena, $3n$ ivica, $n+2$ strane.\n"
        "- $M = O_B \\cdot H$; $P = 2B + M = 2B + O_B \\cdot H$; $V = B \\cdot H$.\n"
        "- Brzo pamćenje: $M = O_BH$ i $V = BH$.\n"
    ),
    "kocka": (
        "FORMULE — KOCKA:\n"
        "- $8$ tjemena, $12$ jednakih ivica, $6$ podudarnih kvadratnih strana.\n"
        "- $P = 6a^2$; $V = a^3$.\n"
        "- Dijagonala strane: $d = a\\sqrt{2}$; prostorna dijagonala: $D = a\\sqrt{3}$.\n"
        "- $P_{DP} = a^2\\sqrt{2}$.\n"
    ),
    "kvadar": (
        "FORMULE — KVADAR:\n"
        "- $8$ tjemena, $12$ ivica, tri para podudarnih naspramnih pravougaonih strana.\n"
        "- $P = 2(ab+ac+bc)$; $V = abc$.\n"
        "- $d = \\sqrt{a^2+b^2}$; $D = \\sqrt{a^2+b^2+c^2}$; $P_{DP} = c \\cdot d$.\n"
    ),
    "prizma_4": (
        "FORMULE — PRAVILNA ČETVOROSTRANA PRIZMA (osnova je kvadrat stranice $a$):\n"
        "- $B = a^2$; $O_B = 4a$; $M = 4aH$; $P = 2a^2+4aH$; $V = a^2H$.\n"
        "- $d = a\\sqrt{2}$; $D = \\sqrt{d^2+H^2} = \\sqrt{2a^2+H^2}$; $P_{DP} = dH = a\\sqrt{2} \\cdot H$.\n"
    ),
    "prizma_3": (
        "FORMULE — PRAVILNA TROSTRANA PRIZMA (osnova je jednakostranični trougao stranice $a$):\n"
        "- $h = \\frac{a\\sqrt{3}}{2}$; $B = \\frac{a^2\\sqrt{3}}{4}$; $O_B = 3a$.\n"
        "- $M = 3aH$; $P = \\frac{a^2\\sqrt{3}}{2}+3aH$; $V = \\frac{a^2\\sqrt{3} \\cdot H}{4}$.\n"
    ),
    "prizma_6": (
        "FORMULE — PRAVILNA ŠESTOSTRANA PRIZMA (osnova je pravilan šestougao stranice $a$):\n"
        "- $B = \\frac{3a^2\\sqrt{3}}{2}$; $O_B = 6a$; $M = 6aH$; "
        "$P = 3a^2\\sqrt{3}+6aH$; $V = \\frac{3a^2\\sqrt{3} \\cdot H}{2}$.\n"
        "- $r_u = \\frac{a\\sqrt{3}}{2}$; $r_o = a$; $d = 2a$; $d_1 = a\\sqrt{3}$.\n"
        "- $D = \\sqrt{d^2+H^2}$; $D_1 = \\sqrt{d_1^2+H^2}$.\n"
    ),
    "piramida": (
        "FORMULE — PIRAMIDA:\n"
        "- Za $n$-tostranu piramidu: $n+1$ tjeme, $2n$ ivica, $n+1$ strana.\n"
        "- $M = \\frac{O_B \\cdot h_a}{2}$; $P = B + M = B + \\frac{O_B \\cdot h_a}{2}$; "
        "$V = \\frac{B \\cdot H}{3}$.\n"
        "- Brzo pamćenje: $M = \\frac{O_Bh_a}{2}$ i $V = \\frac{BH}{3}$.\n"
        "- $H$ je visina piramide, $h_a$ apotema (visina bočne strane), $s$ bočna ivica — ne miješaj $h_a$ i $s$.\n"
    ),
    "piramida_4": (
        "FORMULE — PRAVILNA ČETVOROSTRANA PIRAMIDA (osnova je kvadrat stranice $a$):\n"
        "- $B = a^2$; $O_B = 4a$; $M = 2ah_a$; $P = a^2+2ah_a$; $V = \\frac{a^2H}{3}$.\n"
        "- $d = a\\sqrt{2}$; $P_{DP} = \\frac{dH}{2}$.\n"
        "- Pitagorine veze: $h_a^2 = H^2+\\left(\\frac{a}{2}\\right)^2$; "
        "$s^2 = h_a^2+\\left(\\frac{a}{2}\\right)^2$; $s^2 = H^2+\\left(\\frac{d}{2}\\right)^2$.\n"
    ),
    "piramida_3": (
        "FORMULE — PRAVILNA TROSTRANA PIRAMIDA (osnova je jednakostranični trougao stranice $a$):\n"
        "- $B = \\frac{a^2\\sqrt{3}}{4}$; $h = \\frac{a\\sqrt{3}}{2}$; "
        "$r_u = \\frac{a\\sqrt{3}}{6}$; $r_o = \\frac{a\\sqrt{3}}{3}$.\n"
        "- $M = \\frac{3ah_a}{2}$; $P = \\frac{a^2\\sqrt{3}}{4}+\\frac{3ah_a}{2}$; "
        "$V = \\frac{a^2\\sqrt{3} \\cdot H}{12}$.\n"
        "- $P_{OP} = \\frac{hH}{2}$; $h_a^2 = H^2+r_u^2$; "
        "$s^2 = h_a^2+\\left(\\frac{a}{2}\\right)^2$; $s^2 = H^2+r_o^2$.\n"
    ),
    "piramida_6": (
        "FORMULE — PRAVILNA ŠESTOSTRANA PIRAMIDA (osnova je pravilan šestougao stranice $a$):\n"
        "- $B = \\frac{3a^2\\sqrt{3}}{2}$; $r_u = \\frac{a\\sqrt{3}}{2}$; $r_o = a$.\n"
        "- $M = 3ah_a$; $P = \\frac{3a^2\\sqrt{3}}{2}+3ah_a$; $V = \\frac{a^2\\sqrt{3} \\cdot H}{2}$.\n"
        "- $d = 2a$; $d_1 = a\\sqrt{3}$; $P_{DP} = \\frac{dH}{2} = aH$.\n"
        "- $h_a^2 = H^2+r_u^2$; $s^2 = h_a^2+\\left(\\frac{a}{2}\\right)^2$; $s^2 = H^2+r_o^2$.\n"
    ),
    "valjak": (
        "FORMULE — VALJAK:\n"
        "- $R = 2r$; $B = \\pi r^2$; $O_B = 2\\pi r = \\pi R$.\n"
        "- $M = 2\\pi rH = \\pi RH$; $P = 2B+M = 2\\pi r(r+H)$; $V = \\pi r^2H$.\n"
        "- Razvijeni omotač je pravougaonik stranica $O_B$ i $H$.\n"
        "- Osni presjek je pravougaonik stranica $R$ i $H$: $P_{OP} = RH$; $D = \\sqrt{R^2+H^2}$.\n"
    ),
    "kupa": (
        "FORMULE — KUPA:\n"
        "- $R = 2r$; poluprečnik $r$, visina $H$ i izvodnica $s$ čine pravougli trougao.\n"
        "- $s^2 = r^2+H^2$, pa $s = \\sqrt{r^2+H^2}$, $H = \\sqrt{s^2-r^2}$, $r = \\sqrt{s^2-H^2}$.\n"
        "- $B = \\pi r^2$; $O_B = 2\\pi r = \\pi R$; $M = \\pi rs$; $P = B+M = \\pi r(r+s)$; "
        "$V = \\frac{\\pi r^2H}{3}$.\n"
        "- Osni presjek je jednakokraki trougao osnovice $R = 2r$ i visine $H$: "
        "$P_{OP} = \\frac{RH}{2} = rH$.\n"
    ),
    "lopta": (
        "FORMULE — LOPTA I SFERA:\n"
        "- $R = 2r$; $P = 4\\pi r^2 = \\pi R^2$; $V = \\frac{4}{3}\\pi r^3 = \\frac{\\pi R^3}{6}$.\n"
        "- Sfera je površ, lopta je tijelo ograničeno sferom: precizno je reći površina sfere i "
        "zapremina lopte, mada je u školskim zadacima uobičajen i izraz površina lopte.\n"
    ),
}


# ---------------------------------------------------------------------------
# ROUTING — deterministički, po CANONICAL oblast/lesson_title iz topics.json
# ---------------------------------------------------------------------------

# (regex, figure_id, scope). Redoslijed je bitan samo za stabilnost izlaza;
# svaka figura se dodaje najviše jednom. Uzorci su namjerno uski:
# npr. „kvadrat“ ne smije okinuti na „kvadratna jednačina“/„kvadriranje“,
# a „kocka“ ni „lopta“ nemaju takve homonime.
_FIGURE_PATTERNS = [
    # --- tijela (provjeravaju se prije ravnih figura: „osnova je kvadrat“ u
    #     lekciji o prizmi mora dati prizmu, ne samo kvadrat) ---
    (r"\bkocka|\bkocke|\bkocku|kockom", "kocka", "solid"),
    (r"\bkvadar|kvadra\b|kvadru|kvadrom", "kvadar", "solid"),
    (r"trostran\w*\s+prizm", "prizma_3", "solid"),
    (r"(četvorostran|cetvorostran|četverostran)\w*\s+prizm", "prizma_4", "solid"),
    (r"(šestostran|sestostran)\w*\s+prizm", "prizma_6", "solid"),
    (r"prizm", "prizma", "solid"),
    (r"trostran\w*\s+piramid", "piramida_3", "solid"),
    (r"(četvorostran|cetvorostran|četverostran)\w*\s+piramid", "piramida_4", "solid"),
    (r"(šestostran|sestostran)\w*\s+piramid", "piramida_6", "solid"),
    (r"piramid", "piramida", "solid"),
    (r"valjak|valjka|valjku|valjkom", "valjak", "solid"),
    (r"\bkupa\b|\bkupe\b|\bkupu\b|\bkupom\b", "kupa", "solid"),
    (r"lopt|sfer", "lopta", "solid"),

    # --- ravne figure ---
    (r"pravougl\w*\s+trougao|pravougl\w*\s+trougl|pitagorin", "pravougli_trougao", "plane"),
    (r"jednakokrak\w*\s+trougao|jednakokrak\w*\s+trougl", "jednakokraki_trougao", "plane"),
    (r"jednakostranič\w*\s+trougao|jednakostranič\w*\s+trougl", "jednakostranicni_trougao", "plane"),
    (r"trougao|trougl", "trougao", "plane"),
    (r"paralelogram", "paralelogram", "plane"),
    (r"pravougaonik|pravougaoni[kc]", "pravougaonik", "plane"),
    # „kvadrat“ SAMO kao FIGURA. Algebarski smisao (kvadrat broja, kvadrat
    # zbira, razlika kvadrata, kvadratna jednačina...) se odbija posebnom
    # provjerom _KVADRAT_ALGEBRAIC_RE — vidi route_geometry_topic.
    (r"\bkvadrat(?!n\w*\s|ur)\w*\b", "kvadrat", "plane"),
    (r"\bromb", "romb", "plane"),
    (r"trapez", "trapez", "plane"),
    (r"deltoid", "deltoid", "plane"),
    (r"mnogougao|mnogougl|mnogokut|šestougao|sestougao|petougao|osmougao|"
     r"n-tougao|n-tougl", "mnogougao", "plane"),
    (r"(četverougao|cetverougao|četvorougao|četverougl|četvorougl)", "cetverougao", "plane"),
    (r"sličn\w*\s+trougl|sličnost|podudarnost|koeficijent sličnosti", "slicnost", "plane"),
]

_COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), fid, scope) for p, fid, scope in _FIGURE_PATTERNS]

# „Kvadrat“ u ALGEBARSKOM smislu (stepenovanje), ne kao četverougao — živi
# nalaz iz kurikuluma: „Kvadrat racionalnog broja“, „Kvadrat zbira i razlike“,
# „Razlika kvadrata“, „Kvadrat binoma“, „Savršeni kvadrati“. Takve lekcije NE
# smiju dobiti formule za figuru kvadrat. Suprotno, „Dijagonala kvadrata“ i
# „Površina pravougaonika i kvadrata“ jesu figura i ostaju.
_KVADRAT_ALGEBRAIC_RE = re.compile(
    r"razlik\w*\s+kvadrat"
    r"|zbir\w*\s+kvadrat"
    r"|kvadrat\w*\s+(zbira|razlike|binoma|broja|brojeva|racionaln\w+|prirodn\w+|"
    r"cijel\w+|realn\w+|decimaln\w+|izraza)"
    r"|savrš\w*\s+kvadrat|savrs\w*\s+kvadrat"
    r"|kvadratni\s+korijen"
    r"|kvadriran",
    re.IGNORECASE,
)

# Lekcija koja spominje geometrijsku veličinu, ali nijednu konkretnu figuru
# (npr. „Mjerne jedinice za površinu“) i dalje treba dobiti oznake za ispravan
# opseg. Ovi uzorci određuju SAMO opseg (plane/solid), ne i blok figure.
# KRUŽNICA/KRUG — provjerava se ISKLJUČIVO nad NAZIVOM LEKCIJE, nikad nad
# oblašću. Razlog (živi nalaz + audit kurikuluma): dvije cijele oblasti se
# ZOVU „Skupovi tačaka, kružnica i krug“ i „Mnogougao, kružnica i krug“, ali
# većina lekcija u njima nije o krugu („Izlomljena linija“, „Vrste
# mnogouglova“, „Zbir unutrašnjih uglova mnogougla“...). Da se uzorak provjerava
# nad oblast+naslov, svih ~28 lekcija tih oblasti dobilo bi formule za krug.
#
# Namjerno NE sadrži goli „centar“: „ortocentar“, „centar rotacije“ i „centar
# simetrije figure“ su stvarne lekcije iz kurikuluma koje nisu o krugu. Lekcija
# „Centar, poluprečnik/polumjer i prečnik/promjer“ se ionako hvata preko
# „poluprečnik“, a „Tetiva i udaljenost od centra“ preko „tetiv“.
_CIRCLE_TITLE_RE = re.compile(
    r"kružnic|kruznic"          # kružnica/kružnice/kružnicu
    r"|kružn|kruzn"             # kružni luk, kružni isječak, kružnog luka
    r"|\bkrug\b|\bkruga\b|\bkrugu\b|\bkrugom\b|\bkrugova\b"
    r"|poluprečnik|poluprecnik|polumjer"
    r"|prečnik|precnik|promjer"
    r"|tetiv"
    r"|tangent"
    r"|sječic|sjecic|sekant",
    re.IGNORECASE,
)

# „Kružni dijagram“ / „kružni dijagrami“ je STATISTIKA (pita-grafikon), ne
# geometrija kruga — audit kurikuluma našao dvije takve lekcije („Tabele,
# stupčasti i kružni dijagrami“, „Kružni dijagram“) koje bi inače dobile
# formule za obim i površinu kruga.
_CIRCLE_FALSE_POSITIVE_RE = re.compile(r"dijagram", re.IGNORECASE)

_SOLID_SCOPE_RE = re.compile(
    r"geometrijsk\w*\s+tijel|zapremin|tijela u prostoru|omotač|prostorn\w*\s+dijagonal",
    re.IGNORECASE,
)
_PLANE_SCOPE_RE = re.compile(
    r"\bobim|površin|ravn\w*\s+figur|geometrijsk\w*\s+figur|dijagonal",
    re.IGNORECASE,
)


def route_geometry_topic(oblast, lesson_title):
    """Vrati (scope, figure_ids) za datu lekciju.

    scope je "plane", "solid" ili "" (lekcija nije geometrijska). figure_ids je
    lista prepoznatih figura (može biti prazna i kad scope postoji — npr.
    lekcija o mjernim jedinicama za površinu). Deterministički, bez AI poziva.
    """
    haystack = f"{oblast or ''} {lesson_title or ''}"

    kvadrat_is_algebraic = bool(_KVADRAT_ALGEBRAIC_RE.search(haystack))

    figures = []
    scopes = []

    # Krug se prepoznaje SAMO iz naziva lekcije (vidi _CIRCLE_TITLE_RE) i
    # provjerava se PRVI među ravnim figurama da bi lekcija tipa „Broj π i obim
    # kruga“ (čija oblast sadrži i „mnogougao“) dobila prvo formule za krug.
    # Tijela imaju prednost nad njim: valjak/kupa/lopta imaju kružnu osnovu, ali
    # tamo važi konvencija tijela, pa ih filter opsega ispod ionako odbaci.
    if (_CIRCLE_TITLE_RE.search(lesson_title or "")
            and not _CIRCLE_FALSE_POSITIVE_RE.search(lesson_title or "")):
        figures.append("krug")
        scopes.append("plane")

    for pattern, figure_id, scope in _COMPILED_PATTERNS:
        if figure_id == "kvadrat" and kvadrat_is_algebraic:
            continue  # „kvadrat broja/zbira/razlike“ nije četverougao
        if pattern.search(haystack) and figure_id not in figures:
            figures.append(figure_id)
            scopes.append(scope)

    # Kad je lekcija o TIJELU, opseg mora biti "solid" čak i ako naziv spominje
    # kružnicu („Osni presjek valjka“) — inače bi prvi element (krug/plane)
    # pogrešno odredio opseg.
    if len(scopes) > 1 and scopes[0] == "plane" and figures[0] == "krug" and "solid" in scopes:
        solid_index = scopes.index("solid")
        figures = figures[solid_index:] + figures[:solid_index]
        scopes = scopes[solid_index:] + scopes[:solid_index]

    if figures:
        # Opseg određuje PRVA prepoznata figura po redoslijedu uzoraka (tijela
        # se provjeravaju prva), pa lekcija o prizmi s kvadratnom osnovom
        # ostaje u "solid" konvenciji.
        scope = scopes[0]
        # Zadrži samo figure istog opsega — miješanje oznaka ravnih figura i
        # tijela u jednom promptu je upravo ono što pravi zabunu oko $d$/$R$.
        figures = [f for f, s in zip(figures, scopes) if s == scope]
        return scope, figures[:MAX_FIGURE_BLOCKS]

    if _SOLID_SCOPE_RE.search(haystack):
        return "solid", []
    if _PLANE_SCOPE_RE.search(haystack):
        return "plane", []
    return "", []


# Kratka završna provjera oznaka — DRUGA linija odbrane (prva je
# deterministički matbot/geometrycheck.py). Živi nalaz koji je ovo iznudio:
# model je vratio „Krug ima prečnik $D=10$“ uz tačan račun, jer prompt nigdje
# nije IZRIČITO zabranio uobičajene alternativne oznake ($S$ za površinu,
# $D$/$d$ za prečnik) koje dolaze iz drugih školskih tradicija.
_SYMBOL_SELF_CHECK = (
    "PROVJERA OZNAKA PRIJE SLANJA (obavezno):\n"
    "- Koristi ISKLJUČIVO oznake iz ovog prompta. Uobičajene alternative iz drugih "
    "udžbenika su ZABRANJENE: NIKAD $S$ za površinu (površina je $P$), "
    "NIKAD $D$ ni $d$ za prečnik kruga (prečnik je $R$, poluprečnik je $r$, $R=2r$).\n"
    "- Prije nego vratiš odgovor, pređi pogledom svaki simbol u tekstu zadatka, u "
    "tačnoj opciji, u expected_answer i u objašnjenju i provjeri da svaki znači "
    "tačno ono što piše u oznakama iznad.\n"
)


def build_geometry_rules(oblast, lesson_title, mode="practice"):
    """Sastavi SAMO geometrijske blokove relevantne za ovu lekciju.

    Vraća "" za negeometrijske lekcije — nijedan geometrijski simbol ni formula
    ne ulazi u prompt lekcije koja s njima nema veze.
    """
    del mode  # trenutno isti sadržaj za sve modove; parametar radi simetrije s rules.py
    scope, figures = route_geometry_topic(oblast, lesson_title)
    if not scope:
        return ""

    parts = [_SOLID_SYMBOLS if scope == "solid" else _PLANE_SYMBOLS]
    parts.append(_SOLID_TERMS if scope == "solid" else _PLANE_TERMS)
    for figure_id in figures:
        block = _FIGURE_RULES.get(figure_id)
        if block:
            parts.append(block)
    parts.append(_SYMBOL_SELF_CHECK)
    return "".join(parts)
