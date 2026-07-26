"""Deterministička (ne-AI) zaštita od LaTeX-a koji bi izazvao "Math input error"
u MathJax-u na frontendu. Nema ambiciju da bude potpun LaTeX parser — provjerava
samo dvije stvari koje su dovoljne da spriječe vidljivu grešku kod učenika:

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
            out.append(part)
            continue
        if unterminated_tail and i == last_index:
            out.append(part)  # nikad zatvoren $ — prikaži kao obični tekst
            continue
        if part.count("{") != part.count("}"):
            out.append(part)  # nebalansirane vitičaste zagrade — ukloni delimitere
        else:
            out.append("$" + part + "$")  # ispravan segment — zadrži kako jeste
    return "".join(out)
