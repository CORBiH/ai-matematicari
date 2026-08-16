"""Determinističa odluka: da li izabrana lekcija SMIJE oblikovati odgovor na
trenutnu poruku učenika (mod „Objasni mi“).

ZAŠTO POSTOJI (živi nalaz D35-3, poziv 20 kampanje od 35): učenik je imao
izabranu lekciju iz oblasti Racionalni brojevi i pitao „Koliki je zbir
unutrašnjih uglova trougla i zašto?“. Odgovor je počeo cijelom nepovezanom
lekcijom o pretvaranju decimalnog broja u razlomak (s primjerom
$0,75=\\frac{75}{100}=\\frac{3}{4}$) i tek onda odgovorio na pitanje.

Uzrok nije bio model nego prompt: matbot/prompts.py je pravilo „PRVO
OBJAŠNJENJE teme … pokaži JEDAN mali riješen primjer“ palio ISKLJUČIVO na
osnovu prazne historije razgovora, bez ijedne provjere da li poruka uopšte ima
veze s lekcijom. Lekcija je u prompt ulazila bezuslovno, kao naredba šta da se
predaje.

ŠTA OVAJ MODUL JESTE: uzak deterministički klasifikator konteksta, bez ijednog
AI poziva. Odgovara na jedno pitanje — treba li poruci izabrana lekcija da bi
uopšte imala smisla?

  • JAK kontekst (koristi lekciju): poruka je uopštena/pokazna („Objasni mi
    ovo.“, „Ne razumijem.“, „Daj mi primjer.“) — bez lekcije se ne zna na šta
    se „ovo“ odnosi.
  • SLAB kontekst (ne guraj lekciju): poruka je samostalna — sadrži izraz/
    jednačinu ili imenuje matematički pojam koji se NE poklapa s izabranom
    lekcijom. Takva poruka nosi dovoljno podataka da se odgovori bez lekcije.

Kad se pojam iz poruke POKLAPA s naslovom/oblašću lekcije, kontekst ostaje jak
— pravo pitanje o razlomcima u lekciji o razlomcima i dalje koristi lekciju.

ŠTA OVAJ MODUL NIJE: klasifikator teme ni mjera „koliko je pitanje blizu
lekciji“. Kad ne može dokazati da je poruka samostalna, vraća JAK kontekst —
tj. ranije (nepromijenjeno) ponašanje.
"""
import re
import unicodedata

# Poruke koje bez izabrane lekcije ili historije NEMAJU značenje. Poredi se
# normalizovan oblik (mala slova, bez dijakritika i interpunkcije), kao prefiks
# ili cijela poruka — namjerno kratka, zatvorena lista, ne heuristika.
_DEICTIC_PHRASES = (
    "objasni", "objasni mi", "objasni mi ovo", "objasni ovo", "objasni jos jednom",
    "objasni drugacije", "objasni jednostavnije", "objasni mi jednostavnije",
    "ne razumijem", "ne razumem", "ne kontam", "ne shvatam", "nije mi jasno",
    "nista ne razumijem", "kako se ovo radi", "kako se to radi", "kako to ide",
    "daj mi primjer", "daj primjer", "daj mi jos jedan primjer", "jos jedan primjer",
    "jos primjera", "moze jednostavnije", "moze li jednostavnije", "moze jos jednom",
    "pojasni", "pojasni mi", "ponovi", "ponovi jos jednom",
    "pokazi cijeli postupak", "pokazi postupak", "sta je ovo", "sta to znaci",
    "nastavi", "dalje", "ne razumijem ovo", "zasto", "kako",
)

# Imenovani matematički pojmovi. Regexi traže CIJELE riječi s deklinovanim
# nastavcima — namjerno ne goli stem, da „uglavnom“ ne bi bilo prepoznato kao
# „ugao“ i time lažno proglasilo poruku drugom temom.
_TOPIC_PATTERNS = {
    "trougao": r"trougao|trougl(?:a|u|om|ovi|ova|ove|ovima)|trokut\w*",
    "cetverougao": r"c[ei]tv[eo]rougao|c[ei]tv[eo]rougl\w*",
    "kvadrat": r"kvadrat\w*",
    "pravougaonik": r"pravougaon\w*",
    "krug": r"krug\w*|kruzn\w*|polupre[cč]nik\w*|pre[cč]nik\w*",
    "ugao": r"ugao|ugl(?:a|u|om|ovi|ova|ove|ovima)",
    "razlomak": r"razlom\w*|brojnik\w*|nazivnik\w*",
    "decimala": r"decimal\w*",
    "procenat": r"procenat|procent\w*|posto",
    "jednacina": r"jedna[cč]in\w*|nejedna[cč]in\w*",
    "sistem": r"sistem\w*",
    "stepen": r"stepen\w*|kvadriranj\w*|kor[ij]en\w*",
    "proporcija": r"proporcij\w*|razmj?er\w*",
    "funkcija": r"funkcij\w*|grafik\w*",
    "mjere": r"povrsin\w*|obim\w*|zapremin\w*",
    "tijela": r"prizm\w*|piramid\w*|valjak\w*|kupa|lopt\w*|kock\w*|kvadar",
    "skup": r"skup\w*|unij\w*|pres[jе]?ek\w*",
    "djeljivost": r"prost broj\w*|dj?eljiv\w*|nzd|nzs",
    "statistika": r"srednja vr[ij]?ednost|aritmeti[cč]ka sredina|vjerovatn\w*",
    # Živi nalaz Explain baseline (b08): „Objasni mi Pitagorinu teoremu.“ u
    # lekciji o razlomcima NIJE prepoznato kao druga tema — pojam nije postojao
    # u leksikonu, pa je učenik prvo dobio cijeli neželjeni uvod u razlomke.
    "pitagora": r"pitagor\w*",
    "transformacije": r"rotacij\w*|translacij\w*|simetrij\w*",
}
_TOPIC_RES = {
    name: re.compile(r"\b(?:" + pattern + r")\b", re.UNICODE)
    for name, pattern in _TOPIC_PATTERNS.items()
}


def _normalize(value):
    """Mala slova, bez dijakritika i interpunkcije — isti postupak kao uski
    prozni klasifikatori u matbot/quick.py."""
    folded = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in folded if not unicodedata.combining(ch))
    words_only = re.sub(r"[^\w\s]", " ", without_marks.lower(), flags=re.UNICODE)
    return " ".join(words_only.split())


def _is_deictic(normalized):
    return any(
        normalized == phrase or normalized.startswith(phrase + " ")
        for phrase in _DEICTIC_PHRASES
    )


# ---------------------------------------------------------------------------
# VLASTITI PREDMET PORUKE (živi nalaz: deiktički prefiks je gasio provjeru)
# ---------------------------------------------------------------------------
# `_DEICTIC_PHRASES` sadrži i GOLE riječi „objasni“, „kako“, „zasto“, „pojasni“,
# a `_is_deictic` hvata svaku poruku koja tako POČINJE. Zato je jedna riječ na
# početku gasila cijelu provjeru relevantnosti: mjereno nad svih 536 lekcija,
# „Sabiranje razlomaka 3/4 + 2/5.“ je slab kontekst u 479 lekcija, a „Kako se
# računa 3/4 + 2/5?“ u 0; isto „Pitagorina teorema…“ 487 → „Objasni Pitagorinu
# teoremu.“ 0. Ista matematika, suprotan zaključak.
#
# ISPRAVKA NIJE BRISANJE TIH RIJEČI. One postoje zbog stvarnih nastavaka
# („Kako?“, „Zašto?“, „Objasni jednostavnije.“) koji BEZ lekcije i historije
# nemaju značenje i moraju je i dalje nasljeđivati.
#
# Razlika je u tome NOSI LI PORUKA VLASTITI PREDMET. Deiktički početak je samo
# uljudan uvod kad iza njega ne stoji nikakva matematika; kad stoji, poruka je
# samostalan zahtjev i sudi joj se po SADRŽAJU (što i dalje može ispasti
# „jak kontekst“ — samostalno pitanje O IZABRANOJ lekciji ostaje relevantno).
_NUMBER_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?")
# Operator ili relacija traži se u IZVORNOJ poruci: `_normalize` briše
# interpunkciju, pa bi „x=4“ poslije nje izgledalo kao obična riječ.
_OPERATOR_RE = re.compile(r"[+\-*/=<>^%]|\\frac|\\sqrt|\\cdot|\bputa\b|\bpodijelj")
_MATH_SEGMENT_RE = re.compile(r"\$[^$]+\$")


def carries_own_subject(text):
    """True = poruka nosi VLASTITI matematički predmet (samostalan zahtjev).

    Tri signala, svaki strukturan i jezički neutralan:
      • imenuje matematički pojam iz leksikona;
      • nosi matematički segment `$…$`;
      • nosi dvije brojčane vrijednosti, ili jednu uz operator/relaciju.

    Namjerno NE gleda dužinu poruke: „Zašto x=4?“ je kratko a samostalno, dok
    je „Zašto ovdje dijelimo sa 3?“ duže a i dalje nastavak prethodnog koraka
    (jedan broj, bez operatora)."""
    if not text:
        return False
    if named_topics(text):
        return True
    if _MATH_SEGMENT_RE.search(text):
        return True
    numbers = _NUMBER_TOKEN_RE.findall(text)
    if len(numbers) >= 2:
        return True
    return bool(numbers) and bool(_OPERATOR_RE.search(text))


def named_topics(text):
    """Skup imenovanih matematičkih pojmova koje tekst spominje."""
    normalized = _normalize(text)
    return {name for name, pattern in _TOPIC_RES.items() if pattern.search(normalized)}


def lesson_context_is_strong(student_message, lesson_title="", oblast=""):
    """True = izabrana lekcija smije oblikovati odgovor (ranije ponašanje).
    False = poruka je DOKAZANO iz druge teme; lekcija se ne smije nametati.

    Slab kontekst se tvrdi SAMO kad poruka imenuje bar jedan matematički pojam
    i nijedan od njih se ne poklapa s pojmovima izabrane lekcije. Sam izraz ili
    jednačina u poruci NIJE dokaz druge teme (u lekciji o jednačinama poruka
    „Riješi $2x+3=7$“ je upravo ta lekcija), pa se takva poruka namjerno ne
    proglašava samostalnom — kad guard ne može dokazati, ne pogađa nego vraća
    ranije ponašanje.

    Bez izabrane lekcije nema šta da procuri, pa je odgovor uvijek True."""
    if not (lesson_title or oblast):
        return True

    normalized = _normalize(student_message)
    if not normalized:
        return True
    # STVARAN NASTAVAK = deiktički početak BEZ vlastitog predmeta. Uljudan uvod
    # („Objasni…“, „Kako…“, „Zašto…“) ispred samostalnog pitanja više ne gasi
    # provjeru — vidi `carries_own_subject`.
    if _is_deictic(normalized) and not carries_own_subject(student_message):
        return True

    message_topics = named_topics(student_message)
    if not message_topics:
        return True
    return bool(message_topics & named_topics(lesson_title + " " + oblast))
