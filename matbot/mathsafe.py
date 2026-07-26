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
# Mapiranje ide OBRNUTO od JSON dekodera: kontrolni znak → literalna dva znaka.
_CONTROL_TO_LATEX_ESCAPE = {
    "\x0c": "\\f",  # form feed   (JSON \f)  — npr. \frac
    "\x08": "\\b",  # backspace   (JSON \b)  — npr. \begin
    "\t":   "\\t",  # tab         (JSON \t)  — npr. \times
    "\r":   "\\r",  # carriage return (JSON \r) — npr. \right
    "\n":   "\\n",  # newline     (JSON \n)  — npr. \neq, \newcommand
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
