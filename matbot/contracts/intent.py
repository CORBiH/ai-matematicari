"""Deterministička prepoznaja UČENIKOVE izričite molbe za oblik zadatka.

ZAŠTO POSTOJI (Live96, pozivi 507/523/524/532/539/548): izbor oblika je do sada
potpuno ignorisao učenikovu poruku. Učenik napiše „daj zadatak u kojem
nedostaje jedna vrijednost“, server dodijeli direct_computation, model poruku
ipak posluša — i zadatak padne jer dodijeljeni oblik ne može predstaviti ono
što je model napisao. Sukob server-plana i učenikove molbe bio je najveći
pojedinačni uzrok odbijanja.

Ovo NIJE slobodno pogađanje po ključnim riječima i NIJE model poziv: zatvorena,
server-owned tabela kanonskih fraza (normalizovanih: mala slova, bez
dijakritika, bez interpunkcije). Odluka važi SAMO kad se poklopi TAČNO JEDAN
arhetip; bez poklapanja, s višestrukim poklapanjem, ili s arhetipom koji ugovor
ne dozvoljava / još nije implementiran — plan pada na normalnu rotaciju, nikad
na grešku i nikad na dodatni poziv.

Tabela je po ARHETIPU (univerzalna interakcija), nikad po lekciji.
"""
import re

# Normalizacija dijakritike: dovoljna je za bosanski (č/ć→c, š→s, ž→z, đ→dj).
_DIACRITICS = str.maketrans({
    "č": "c", "ć": "c", "š": "s", "ž": "z", "đ": "dj",
    "Č": "c", "Ć": "c", "Š": "s", "Ž": "z", "Đ": "dj",
})

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Kanonske fraze po arhetipu, NAD NORMALIZOVANIM tekstom. Svaka je usko
# sidrena na formulacije kojima učenik stvarno traži OBLIK zadatka — opšte
# riječi („zadatak“, „daj“) namjerno ne postoje ovdje.
_PATTERNS = {
    "find_missing_value": (
        r"\bnedostaje\b",
        r"\bdopuni (?:prazninu|jednakost)\b",
        r"\bpopuni prazninu\b",
        r"\bnedostajuc\w*\b",
    ),
    "identify_error": (
        r"\bgresk\w*\b",
        r"\bpogrijesio\b",
        r"\bpogresn\w*\b",
    ),
    "identify_equivalent": (
        r"\bist[au] vrijednost\b",
        r"\bekvivalent\w*\b",
        r"\bjednak[au] vrijednost\b",
    ),
    "direct_computation": (
        r"\bizracuna\w*\b",
        r"\bsracuna\w*\b",
    ),
}
_COMPILED = {
    archetype_id: tuple(re.compile(p) for p in patterns)
    for archetype_id, patterns in _PATTERNS.items()
}


def normalize_message(message):
    text = (message or "").translate(_DIACRITICS).lower()
    return _NON_ALNUM_RE.sub(" ", text).strip()


def requested_archetype(message):
    """Arhetip koji učenikova poruka IZRIČITO traži, ili "".

    Prazan rezultat znači „nema jednoznačne molbe“ — pozivalac tada koristi
    normalnu serversku rotaciju. Višestruko poklapanje se namjerno tretira kao
    dvosmisleno (npr. „izračunaj gdje je greška“) i vraća ""."""
    normalized = normalize_message(message)
    if not normalized:
        return ""
    matched = [
        archetype_id
        for archetype_id, patterns in _COMPILED.items()
        if any(p.search(normalized) for p in patterns)
    ]
    if len(matched) == 1:
        return matched[0]
    return ""
