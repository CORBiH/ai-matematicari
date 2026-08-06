"""Serverski izveden KANONSKI identitet objavljenog zadatka.

ZAŠTO POSTOJI (produkcijski nalaz, lekcija o pravilima djeljivosti):

    objavljeno : „Koji od sljedećih brojeva je djeljiv sa 25?“ · 322 390 349 375
    učenik     : klikne 390 → crveno, ocijenjeno netačno (sve ispravno)
    učenik     : „Daj mi novi zadatak.“
    objavljeno : ISTI zadatak, ISTE četiri opcije — a jednom i uz uvod
                 „Evo lakšeg zadatka.“

Zaštita od ponavljanja je do tada poredila `task.task_signature` — strukturu
koju MODEL sam deklariše o sebi. Kad model za vizuelno identičan zadatak
deklariše makar malo drugačije `normalized_parameters`, digest se razlikuje i
duplikat prođe. Server nikad nije poredio ono što učenik STVARNO VIDI.

Ovdje se identitet računa ISKLJUČIVO iz vidljivog paketa — teksta zadatka i
vrijednosti opcija — pa nijedna modelova deklaracija ne može ga promijeniti.
Isti potpis ide i u browser, da UI stanje može biti vezano za identitet
zadatka, a ne za njegov tekst.

ŠTA HVATA:
  • doslovno isti tekst i iste opcije,
  • promijenjen REDOSLIJED opcija (opcije se sortiraju),
  • promijenjene ID-jeve opcija uz iste vrijednosti (ID-jevi nisu dio potpisa),
  • kozmetiku: veličinu slova, višestruke razmake, `$…$`, LaTeX razmake,
    navodnike i završnu interpunkciju,
  • promijenjen uvodni tekst odgovora (uvod nije dio teksta zadatka).

ŠTA NAMJERNO NE TVRDI: puni parafrazni identitet. „Koji broj je djeljiv sa 25?“
i „Odaberi broj djeljiv sa 25.“ su za čovjeka isti zadatak, a ovdje nisu isti
potpis. Dokazivanje toga traži semantiku koju server nema — a guard koji ne
može dokazati mora skipovati, ne nagađati (CLAUDE.md).
"""
import hashlib
import json
import re
import unicodedata

# Kozmetika koja NIKAD ne mijenja matematiku zadatka.
_MATH_DELIMITERS_RE = re.compile(r"\$+")
_LATEX_SPACING_RE = re.compile(r"\\[,;:!]|\\quad|\\qquad|\\ ")
_WHITESPACE_RE = re.compile(r"\s+")
# Interpunkcija i navodnici koji ne nose vrijednost. Znakovi koji MOGU nositi
# matematiku (cifre, slova, + - * / = < > ^ _ { } ( ) , .) se NE diraju.
_COSMETIC_RE = re.compile(r"[„“”\"'`]")


def normalize_fragment(text):
    """Kanonski oblik jednog vidljivog fragmenta (pitanje ili opcija)."""
    body = unicodedata.normalize("NFKC", text or "")
    body = _MATH_DELIMITERS_RE.sub(" ", body)
    body = _LATEX_SPACING_RE.sub(" ", body)
    body = _COSMETIC_RE.sub("", body)
    body = _WHITESPACE_RE.sub(" ", body).strip()
    # Završna interpunkcija ne razlikuje zadatke („…sa 25?“ vs „…sa 25 ?“).
    body = re.sub(r"\s*([?!.:;])\s*$", r"\1", body)
    return body.casefold()


def canonical_parts(task_text, option_texts):
    """Kanonski dijelovi potpisa — čitljivo, za dijagnostiku i testove."""
    options = sorted(normalize_fragment(option) for option in (option_texts or ()))
    return {"question": normalize_fragment(task_text), "options": options}


def canonical_signature(task_text, option_texts):
    """Stabilan sha256 identitet vidljivog paketa. Prazan kad nema teksta."""
    parts = canonical_parts(task_text, option_texts)
    if not parts["question"]:
        return ""
    encoded = json.dumps(parts, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_same_task(first_signature, second_signature):
    """True samo kad su OBA potpisa poznata i jednaka.

    Nepoznat potpis nikad nije dokaz jednakosti — isti princip kao svaki drugi
    verifikator u projektu."""
    return bool(first_signature) and first_signature == second_signature
