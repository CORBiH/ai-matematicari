"""MAT-BOT — parsiranje broja zadatka iz korisničkog teksta.

extract_requested_tasks(): "zadatak 3", "3.", "prvi", "zadnji"(-1); izbjegava
lažne pogotke ("5. razred", decimale "3.5"). Izdvojeno iz app.py (refactor).
"""
import re


ORDINAL_WORDS = {
    "prvi": 1, "drugi": 2, "treći": 3, "treci": 3, "četvrti": 4, "cetvrti": 4,
    "peti": 5, "šesti": 6, "sesti": 6, "sedmi": 7, "osmi": 8, "deveti": 9, "deseti": 10,
    "zadnji": -1, "posljednji": -1
}
# Bare "N." oblik: ne hvataj decimale ("3.5") ni "5. razred" (najčešći lažni pogodak).
_task_num_re = re.compile(
    r"(?:zadatak\s*(?:broj\s*)?(\d{1,4}))|(?:\b(\d{1,4})\s*\.(?!\d)(?!\s*razred))|(?:\b(" + "|".join(ORDINAL_WORDS.keys()) + r")\b)",
    flags=re.IGNORECASE
)
FOLLOWUP_TASK_RE = re.compile(r"^\s*\d{2,5}\s*[a-z]\)?\s*$", re.IGNORECASE)

def extract_requested_tasks(text: str):
    if not text: return []
    tasks = []
    for m in _task_num_re.finditer(text):
        if m.group(1): tasks.append(int(m.group(1)))
        elif m.group(2): tasks.append(int(m.group(2)))
        elif m.group(3): tasks.append(ORDINAL_WORDS.get(m.group(3).lower()))
    out, seen = [], set()
    for n in tasks:
        if n not in seen: out.append(n); seen.add(n)
    return out

def requested_clause(requested) -> str:
    if not requested:
        return ""
    labels = ["posljednji" if n == -1 else str(n) for n in requested]
    return " Riješi ISKLJUČIVO sljedeće zadatke: " + ", ".join(labels) + ". Ostale ignoriraj."
