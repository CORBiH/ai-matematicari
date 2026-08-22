"""AKTIVAN ZADATAK moda „Samo rezultat“ — najmanje moguće serversko stanje.

ZAŠTO POSTOJI (živi baseline, 2026-08-16, turnovi 6 i 7 od 10):

    učenik pošalje sliku stranice s pet zadataka
    Sol ispravno kaže: „Na slici vidim više zadataka. Napiši koji da riješim.“
    učenik: „Treći.“              → „Pošalji sliku ili napiši tekst trećeg zadatka.“
    učenik: „Objasni postupak.“   → „Nedostaje tekst ili slika trećeg zadatka.“

Slika je važila SAMO za svoj turn, pa je sve što je Sol pročitao nestajalo čim
bi odgovor otišao. Sirova historija razgovora to ne može nadoknaditi: u njoj
piše samo da je bilo „više zadataka“, nikad KOJI su to zadaci.

ŠTA OVO JESTE: jedan aktivan zadatak po Quick sesiji — kanonski tekst zadatka,
zatečeni podaci, tražena veličina, zadnji rezultat i (za stranicu s više
zadataka) inventar pročitanih zadataka. Ništa više.

ŠTA OVO NIJE: trajna memorija, baza slika, istorija učenika. Stanje je
in-memory, vezano za session_id, gubi se na restart (učenik tada ponovo pošalje
zadatak) i BRIŠE SE čim počne nov zadatak. Ne čuva se nijedan bajt slike,
nijedan skriveni reasoning modela ni transkript cijele stranice.

GRANICA POVJERENJA: zadatak iz inventara smije se riješiti BEZ nove slike samo
ako je Sol izričito potvrdio da ga je pročitao u cijelosti (`fully_readable`).
Za sve ostalo server pošteno kaže da ne vidi taj dio — nikad ne izmišlja.
"""
import copy
import re
import threading

from matbot import config
from matbot import textnorm

# Granice — stanje sesije ne smije rasti s brojem poruka.
MAX_TASK_TEXT_CHARS = 400
MAX_DETECTED_TASKS = 8
MAX_GIVENS = 12
MAX_RESULT_CHARS = 300


def _clip(value, limit):
    text = " ".join(str(value or "").split())
    return text[:limit]


def _fresh(session_id):
    return {
        "session_id": session_id,
        "source_type": "",        # "text" | "image"
        "task_text": "",          # kanonski tekst AKTIVNOG zadatka
        "givens": [],             # [{"symbol","value","unit"}] sa slike
        "requested": "",          # tražena veličina (requested_quantity)
        "unit": "",
        "last_result": "",        # zadnji objavljen odgovor
        "detected_tasks": [],     # [{"label","text","fully_readable"}]
        "selected_label": "",     # koji zadatak sa stranice je aktivan
        "confidence": "",
    }


class QuickContextStore:
    """Thread-safe in-memory kontekst po Quick sesiji (bez baze, kao Practice)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_session = {}

    def get(self, session_id):
        if not session_id:
            return None
        with self._lock:
            state = self._by_session.get(session_id)
            return copy.deepcopy(state) if state else None

    def save(self, context):
        session_id = context.get("session_id")
        if not session_id:
            return
        with self._lock:
            self._by_session.pop(session_id, None)
            self._by_session[session_id] = copy.deepcopy(context)
            while len(self._by_session) > config.MAX_SESSIONS_IN_MEMORY:
                oldest = next(iter(self._by_session))
                del self._by_session[oldest]

    def clear(self, session_id=None):
        with self._lock:
            if session_id is None:
                self._by_session.clear()
            else:
                self._by_session.pop(session_id, None)


# ---------------------------------------------------------------------------
# GRAĐENJE KONTEKSTA
# ---------------------------------------------------------------------------

def from_image_output(session_id, out):
    """Kontekst iz Sol strukturiranog izlaza. Zove se SAMO kad je turn prošao
    kapiju čitljivosti (jedan zadatak) ILI kad je stranica prepoznata kao
    „više zadataka“ (tada se pamti inventar, ne rješenje)."""
    context = _fresh(session_id)
    context["source_type"] = "image"
    context["confidence"] = getattr(out, "answer_confidence", "") or ""
    # Kanonski tekst: matematika koju je model STVARNO pročitao ima prednost
    # nad slobodnim opisom (isti redoslijed povjerenja kao imagecheck).
    context["task_text"] = _clip(
        getattr(out, "visible_math", "") or getattr(out, "visible_problem_text", ""),
        MAX_TASK_TEXT_CHARS)
    context["requested"] = getattr(out, "requested_quantity", "") or ""
    context["unit"] = _clip(getattr(out, "unit", ""), 24)
    context["givens"] = [
        {"symbol": _clip(v.symbol, 24), "value": _clip(v.value, 24),
         "unit": _clip(v.unit, 24)}
        for v in list(getattr(out, "visible_values", []) or [])[:MAX_GIVENS]
    ]
    context["detected_tasks"] = [
        {"label": _clip(t.label, 12), "text": _clip(t.text, MAX_TASK_TEXT_CHARS),
         "fully_readable": bool(t.fully_readable)}
        for t in list(getattr(out, "detected_tasks", []) or [])[:MAX_DETECTED_TASKS]
        if _clip(t.label, 12) and _clip(t.text, MAX_TASK_TEXT_CHARS)
    ]
    return context


def from_text_task(session_id, student_message):
    """Kontekst za tekstualni zadatak — pamti se sam tekst zadatka."""
    context = _fresh(session_id)
    context["source_type"] = "text"
    context["task_text"] = _clip(student_message, MAX_TASK_TEXT_CHARS)
    return context


def with_result(context, answer):
    """Zapamti zadnji objavljen rezultat aktivnog zadatka."""
    if not context:
        return context
    updated = dict(context)
    updated["last_result"] = _clip(answer, MAX_RESULT_CHARS)
    return updated


# ---------------------------------------------------------------------------
# IZBOR ZADATKA SA STRANICE („treći“, „pod b)“, „zadatak 3“)
# ---------------------------------------------------------------------------

_ORDINALS = {
    "prvi": "1", "prva": "1", "prvo": "1", "jedan": "1",
    "drugi": "2", "druga": "2", "drugo": "2", "dva": "2",
    "treci": "3", "treca": "3", "trece": "3", "tri": "3",
    "cetvrti": "4", "cetvrta": "4", "cetvrto": "4", "cetiri": "4",
    "peti": "5", "peta": "5", "peto": "5", "pet": "5",
    "sesti": "6", "sesta": "6", "sesto": "6", "sest": "6",
}
_LABEL_NUMBER_RE = re.compile(r"(?:^|\b)(?:zadatak\s*)?(\d{1,2})\s*[).]?(?:\b|$)")
_LABEL_LETTER_RE = re.compile(r"(?:^|\b)(?:pod\s*)?([a-fđčćšž])\s*\)")


def _fold(text):
    """Leksicki ugovor koji ZADRZAVA „)" — oznaka zadatka („pod b)") je nosi.

    „đ" se NAMJERNO ne preslikava: azbuka oznaka `_LABEL_LETTER_RE` ga sadrzi,
    pa bi preslikavanje promijenilo odgovor. Vidi matbot/textnorm.py."""
    return textnorm.normalize_lexical(text, keep=")")


def requested_task_label(message):
    """Oznaka zadatka koju učenik traži („treći“ → „3“, „pod b)“ → „b“), ili
    prazno. Namjerno uska: samo redni brojevi, broj i slovo s zagradom."""
    folded = _fold(message)
    if not folded:
        return ""
    for word, label in _ORDINALS.items():
        if re.search(r"\b" + word + r"\b", folded):
            return label
    letter = _LABEL_LETTER_RE.search(folded)
    if letter:
        return letter.group(1)
    number = _LABEL_NUMBER_RE.search(folded)
    if number:
        return number.group(1)
    return ""


def select_task(context, label):
    """Vrati (novi_kontekst, status). Status: „selected“, „unreadable“,
    „unknown“. NIKAD ne pogađa: nepoznata oznaka i nepročitan zadatak se
    pošteno prijavljuju pozivaocu."""
    if not context or not label:
        return context, "unknown"
    for task in context.get("detected_tasks") or ():
        if task["label"].strip().lower() != label.strip().lower():
            continue
        if not task.get("fully_readable"):
            return context, "unreadable"
        updated = dict(context)
        updated["selected_label"] = task["label"]
        updated["task_text"] = task["text"]
        # Podaci prethodnog zadatka ne smiju preći na novi.
        updated["givens"] = []
        updated["last_result"] = ""
        return updated, "selected"
    return context, "unknown"


# ---------------------------------------------------------------------------
# PROMPT BLOK — samo ono što je server-owned i bezbjedno
# ---------------------------------------------------------------------------

_REQUESTED_LABELS = {
    "area": "površina", "perimeter": "obim",
    "value_of_unknown": "vrijednost nepoznate", "numeric_result": "brojčani rezultat",
}


def prompt_block(context):
    """Kratak blok o AKTIVNOM zadatku za ulaz modela; prazno kad konteksta nema."""
    # Blok se ispisuje čim postoji IŠTA upotrebljivo. Ranije se tražio baš
    # `task_text`, pa je slika s pročitanim VRIJEDNOSTIMA ali bez zasebnog
    # `visible_math` gubila kontekst — a upravo ti podaci nose zadatak.
    if not context or not (context.get("task_text") or context.get("detected_tasks")
                           or context.get("givens") or context.get("last_result")):
        return ""
    lines = ["AKTIVAN ZADATAK (server ga pamti iz prethodnog turna ove sesije):"]
    if context.get("source_type") == "image":
        lines.append("- izvor: slika koju je učenik ranije poslao (slika NIJE ponovo priložena)")
    if context.get("selected_label"):
        lines.append(f"- izabran zadatak sa stranice: {context['selected_label']}")
    if context.get("task_text"):
        lines.append(f"- zadatak: {context['task_text']}")
    for given in context.get("givens") or ():
        unit = (" " + given["unit"]) if given.get("unit") else ""
        lines.append(f"- dato: {given['symbol']} = {given['value']}{unit}")
    requested = context.get("requested")
    if requested and requested != "other":
        lines.append(f"- traži se: {_REQUESTED_LABELS.get(requested, requested)}")
    if context.get("last_result"):
        lines.append(f"- već objavljen rezultat: {context['last_result']}")
    unread = [t["label"] for t in (context.get("detected_tasks") or ())
              if not t.get("fully_readable")]
    if context.get("detected_tasks") and not context.get("selected_label"):
        readable = ", ".join(t["label"] for t in context["detected_tasks"]
                             if t.get("fully_readable"))
        if readable:
            lines.append(f"- stranica sadrži zadatke: {readable}")
    if unread:
        lines.append("- NISU pouzdano pročitani zadaci: " + ", ".join(unread)
                     + " (za njih traži jasniju sliku, ne pogađaj)")
    return "\n".join(lines)
