"""Detekcija NPP teme za "Vježbajmo" (slobodan unos → najbliži npp_topic_id).

Učenik piše slobodno (npr. "daj mi zadatke sa razlomcima", "ne razumijem NZS").
Sistem mapira poruku na najbliži ``npp_topic_id`` TEKUĆEG razreda. Tema se NIKAD
ne izmišlja — svaki rezultat se validira protiv mastera
(``validate_detected_topic``); nesiguran ulaz → ``"unknown"``.

Dizajn (dvije razine, različit prag):
1. ``detect_topic_heuristic`` — DATA-DRIVEN i KONZERVATIVNO: poklapa TAČNE
   distinktivne riječi/skraćenice iz ``tema_ui``/``oblast_ui`` (iz mastera). Vraća
   temu samo kad je poklapanje jednoznačno; inače ``unknown``. Bolje "unknown"
   (pa LLM) nego pogrešna tema. Ništa nije hardkodirano — automatski prati sadržaj
   bilo kog razreda 6–9.
2. ``detect_topic_llm`` — jeftin klasifikator kroz injektovani ``openai_chat``;
   smije vratiti SAMO postojeći npp id ili unknown. U testovima se mockira.

``is_vague_message`` (koja odlučuje smije li se poruka poslati LLM-u) koristi
PERMISIVNIJE poklapanje (stemovi + skraćenice): lažni "nije vague" samo pokrene
jedan (siguran) LLM poziv, dok bi lažni "vague" nepravedno odbio pravu temu.

Robusnost na dijakritike: ulaz i nazivi tema se prije poklapanja normalizuju kroz
``fold_diacritics`` (č/ć→c, đ→d, š→s, ž→z + lowercase).
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import Any, Callable

from matbot.content_loader import get_master, get_thinkific_map, normalize_value
from matbot.topic_lookup import validate_detected_topic

log = logging.getLogger("matbot.topic_detector")

UNKNOWN = "unknown"

# BiH dijakritici → ASCII; obuhvata i velika slova (poslije ide .lower()).
_DIACRITIC_MAP = str.maketrans({
    "č": "c", "ć": "c", "đ": "d", "š": "s", "ž": "z",
    "Č": "c", "Ć": "c", "Đ": "d", "Š": "s", "Ž": "z",
})


def fold_diacritics(text: Any) -> str:
    """Normalizuj tekst: dijakritici → ASCII + lowercase."""
    return normalize_value(text).translate(_DIACRITIC_MAP).lower()


# Signal da je poruka konkretan matematički zadatak/pitanje.
_MATH_SIGNAL_RE = re.compile(
    r"\d|[+\-*/=<>%^√·×÷]|\b(izracunaj|rijesi|zadatak|koliko|jednacin\w*|nejednacin\w*)\b"
)

# Riječi koje ne nose temu (previše česte u nazivima/porukama) — ne ulaze u indeks.
_STOPWORDS = frozenset({
    "pojam", "vrste", "vrsta", "osnovni", "osnovne", "osnovno", "zadatak", "zadaci",
    "zadatke", "zadatku", "zadataka", "zadacima", "zadatkom", "primjer", "primjeri",
    "broj", "brojevi", "brojeva", "brojem", "operacije", "operacija", "racunske",
    "racunanje", "znacenje", "oznacavanje", "prikaz", "prikazivanje", "nacin",
    "nacini", "koristenje", "svojstva", "svojstvo", "pravila", "pravilo", "uvod",
    "teorija", "definicija", "izraz", "izrazi", "veze", "odnos", "odnosi", "kroz",
    "izmedu", "prema", "daj", "mi", "ne", "razumijem", "znam", "kako", "sta",
    "koji", "koja", "koje", "molim", "pomozi", "pomoc", "pomoci", "pomocu",
    "hocu", "zelim", "treba", "trebam", "trebas", "trebamo", "nam", "nas", "sam",
    "smo", "objasni", "vjezba", "vjezbamo", "vjezbanje", "sada", "ovo", "ova",
    "ovaj", "jedan", "jednu", "jedno", "vise", "malo", "neki", "neke", "dajte",
    "racunala", "racunalo", "dzepnog",
})

# C1 (2026-07-14): matematički pojmovi koje djeca koriste, a KOJIH NEMA u
# nazivima NPP tema (naziv kaže "Srednje vrijednosti", učenik piše "medijana").
# Služi SAMO kao propusnica do LLM klasifikatora (is_vague_message) — ne mapira
# temu i ne može izmisliti pogrešnu. Namjerno bez generičkih riječi ("nepoznato")
# da meta-poruke ("nepoznato pitanje", "sutra imam kontrolni") ostanu vague.
_MATH_TERM_STEMS = frozenset({
    "medi", "modu", "pros", "sred",                      # statistika
    "hipo", "kate", "prec", "tang", "dija",              # geometrija
    "povr", "zapr", "obim",                              # mjere
    "koef", "nagi", "bino", "mono", "kori",              # algebra
    "apso", "pred",                                      # predznak, apsolutna vrij.
    "porc", "proc", "post",                              # procenti/postotak
})

_WORD_RE = re.compile(r"[a-z]{4,}")
# Skraćenice: velika slova (2–5) BEZ samoglasnika (NZD, NZS) — tako ne hvatamo
# velika-slovima pisane obične riječi (PITAGORINA, MNOGOUGAO imaju samoglasnike).
_UPPER_RUN_RE = re.compile(r"\b[A-ZČĆĐŠŽ]{2,5}\b")
_VOWELS = set("AEIOU")
_STEM_LEN = 4

_INDEX_CACHE: dict[int, dict[str, Any]] = {}


def _abbrevs(text: Any) -> set[str]:
    """Skraćenice (folded) iz teksta: velika slova bez samoglasnika (npr. NZD/NZS)."""
    out: set[str] = set()
    for tok in _UPPER_RUN_RE.findall(normalize_value(text)):
        if not (set(tok) & _VOWELS):
            out.add(tok.translate(_DIACRITIC_MAP).lower())
    return out


def _content_words(text: Any) -> set[str]:
    """Tačne (folded) sadržajne riječi, bez stopwords."""
    return {w for w in _WORD_RE.findall(fold_diacritics(text)) if w not in _STOPWORDS}


def _stem(word: str) -> str:
    return word[:_STEM_LEN] if len(word) > _STEM_LEN else word


def _build_index(master: dict) -> dict[str, Any]:
    """Sagradi (i keširaj) leksički indeks za detekciju iz mastera.

    ``topic_words`` = TAČNA distinktivna riječ/skraćenica iz ``tema_ui`` →
    npp_topic_id (samo ako se pojavljuje u TAČNO jednoj temi). ``oblast_words`` =
    tačna riječ iz ``oblast_ui`` → prvi npp_topic_id te oblasti (samo ako riječ
    pokazuje na jednu oblast). ``stem_keywords`` = svi STEMOVI + skraćenice (za
    permisivnu vague-provjeru)."""
    grade = master.get("grade")
    cached = _INDEX_CACHE.get(grade)
    if cached is not None and cached.get("_src") == master.get("source_path"):
        return cached

    tema_word_topics: dict[str, set[str]] = defaultdict(set)
    oblast_word_topics: dict[str, set[str]] = defaultdict(set)
    first_topic_of_oblast: dict[str, str] = {}
    stem_keywords: set[str] = set()
    # STEM riječi → SVE oblasti u čijim se nazivima pojavljuje (i dvosmislene).
    # Stem (ne tačna riječ) jer učenik piše drugi padež: "trouglovi" vs naziv
    # "TROUGLOVA". Koristi se za veto nad lažnim jednorječnim pogotkom.
    word_oblasti: dict[str, set[str]] = defaultdict(set)

    for t in master.get("topics", []):
        tid = t.get("topic", "")
        if not tid:
            continue
        oblast = t.get("oblast", "")
        display = t.get("display_name", "")
        first_topic_of_oblast.setdefault(oblast, tid)

        display_words = _content_words(display) | _abbrevs(display)
        for w in display_words:
            tema_word_topics[w].add(tid)
            word_oblasti[_stem(w)].add(oblast)
        for w in _content_words(oblast) | _abbrevs(oblast):
            oblast_word_topics[w].add(tid)
            word_oblasti[_stem(w)].add(oblast)

        stem_keywords |= {_stem(w) for w in _content_words(display)}
        stem_keywords |= {_stem(w) for w in _content_words(oblast)}
        stem_keywords |= _abbrevs(display) | _abbrevs(oblast)

    topic_words = {w: next(iter(ts)) for w, ts in tema_word_topics.items() if len(ts) == 1}
    oblast_words: dict[str, str] = {}
    for w, tids in oblast_word_topics.items():
        oblasti = {master["topics_by_id"][x]["oblast"] for x in tids if x in master["topics_by_id"]}
        if len(oblasti) == 1:
            oblast_words[w] = first_topic_of_oblast[next(iter(oblasti))]

    index = {
        "_src": master.get("source_path"),
        "topic_words": topic_words,
        "oblast_words": oblast_words,
        "stem_keywords": stem_keywords,
        "word_oblasti": dict(word_oblasti),
    }
    _INDEX_CACHE[grade] = index
    return index


def _message_words(text: Any) -> set[str]:
    """Tačne riječi + skraćenice iz poruke (za konzervativnu heuristiku)."""
    return _content_words(text) | _abbrevs(text)


def is_vague_message(text: Any, master: dict | None = None) -> bool:
    """True ako je poruka preopćenita za detekciju (npr. "Ne razumijem", "Pomozi").

    Konkretna = ima matematički signal ILI (kad je ``master`` dat) pogađa neki
    STEM tematske/oblast ključne riječi tog razreda (permisivno — cilj je pustiti
    pravu temu do LLM klasifikatora)."""
    t = fold_diacritics(text)
    if not t:
        return True
    if _MATH_SIGNAL_RE.search(t):
        return False
    if len(t) < 4:
        return True
    if master is not None:
        index = _build_index(master)
        msg_stems = {_stem(w) for w in _content_words(text)} | _abbrevs(text)
        if msg_stems & index["stem_keywords"]:
            return False
        # C1 (2026-07-14): matematički POJAM kojeg NEMA u nazivima tema
        # ("medijana", "modus", "hipotenuza") — ranije je takva poruka bila
        # "vague" pa LLM klasifikator NIKAD nije ni pozvan (AUD-03).
        if msg_stems & _MATH_TERM_STEMS:
            return False
    return True


# Generički glagoli/upitne riječi: nose RADNJU, ne temu. Iz VETA se izuzimaju —
# inače "izračunaj" (stem "izra") slučajno pogodi "Izrazi…" i poništi valjan
# pogodak ("izračunaj NZD" → unknown). Indeks se NE dira, samo veto.
_VETO_IGNORE_STEMS = frozenset({
    _stem(w) for w in (
        "izracunaj", "izracunati", "izracunavanje", "racunaj", "racunati",
        "rijesi", "rjesava", "rjesavanje", "odredi", "napisi", "nacrtaj",
        "crtati", "pokazi", "provjeri", "pretvori", "pretvorim", "skrati",
        "skratim", "mjeri", "mjerim", "konstruise", "konstruisi",
    )
})


def _contradicted_by_other_words(
    words: set[str], chosen_oblast: str, index: dict
) -> bool:
    """C1 (2026-07-14): veto nad LAŽNIM jednorječnim pogotkom.

    Dugi nazivi tema (naročito 8. razred, VERZALOM) nose usputne riječi koje su
    slučajno jedinstvene: "…KAO OSNOVOM" je hvatao "mnoze stepeni sa istom
    OSNOVOM" → Geometrijska tijela, a "SLIČNI monomi" je hvatao "SLIČNI
    trouglovi" → polinomi. Prava tematska riječi ("stepeni", "trouglovi") je
    pritom odbačena jer je dvosmislena.

    Pravilo: ako neka DRUGA sadržajna riječ poruke postoji u nazivima tema, ali
    NIJEDNA njena oblast nije izabrana → pogodak je vjerovatno slučajan.
    Vrati True (→ unknown → prepusti LLM-u, koji je pouzdaniji za značenje).

    Poređenje ide po STEMU (učenik piše "trouglovi", naziv ima "TROUGLOVA")."""
    for w in words:
        st = _stem(w)
        if st in _VETO_IGNORE_STEMS:
            continue                # glagol radnje ne osporava temu
        oblasti = index["word_oblasti"].get(st)
        if oblasti and chosen_oblast not in oblasti:
            return True
    return False


def detect_topic_heuristic(message: Any, master: dict | None = None) -> str:
    """Konzervativna data-driven detekcija. Vraća npp_topic_id ili unknown.

    Vraća temu SAMO kad TAČNE distinktivne riječi jednoznačno pokazuju na jednu
    temu (ili, kao fallback, na jednu oblast). Sve dvosmisleno → unknown."""
    master = master if master is not None else get_master()
    words = _message_words(message)
    if not words:
        return UNKNOWN
    index = _build_index(master)

    topic_hits = {index["topic_words"][w] for w in words if w in index["topic_words"]}
    if len(topic_hits) == 1:
        tid = next(iter(topic_hits))
        matched = {w for w in words if index["topic_words"].get(w) == tid}
        chosen_oblast = normalize_value(
            master.get("topics_by_id", {}).get(tid, {}).get("oblast")
        )
        if chosen_oblast and _contradicted_by_other_words(
            words - matched, chosen_oblast, index
        ):
            return UNKNOWN          # slučajan pogodak → LLM odlučuje
        return tid
    if len(topic_hits) > 1:
        return UNKNOWN  # dvosmisleno → prepusti LLM-u

    oblast_hits = {index["oblast_words"][w] for w in words if w in index["oblast_words"]}
    if len(oblast_hits) == 1:
        return next(iter(oblast_hits))
    return UNKNOWN


_JSON_RE = re.compile(r"\{[^{}]*\}")


def detect_topic_llm(
    message: Any,
    master: dict,
    tmap: dict,
    openai_chat: Callable,
    model: str,
    timeout: float | None = None,
) -> str:
    """Jeftin LLM klasifikator: smije vratiti SAMO postojeći npp_topic_id ili
    unknown. Svaki izlaz se validira; garbage → unknown. Nikad ne baca izuzetak."""
    try:
        text = normalize_value(message)[:1000]
        # Oblast uz naziv: učenik često koristi riječ koje NEMA u nazivu teme
        # ("hipotenuza" → oblast "Pitagorina teorema"; "porcenti" → "Postotak…").
        topics_list = "\n".join(
            f"- {t['topic']} | {t.get('oblast', '')} | {t.get('display_name', '')}"
            for t in master.get("topics", [])
        )
        messages = [
            {
                "role": "system",
                "content": (
                    f"Ti si klasifikator NPP tema za matematiku {master.get('grade', 6)}. "
                    "razreda (BiH). Odgovori ISKLJUČIVO JSON-om oblika "
                    '{"detected_topic": "<npp_topic_id>"} ili {"detected_topic": "unknown"}. '
                    "Dozvoljeni su SAMO npp_topic_id-evi sa liste.\n"
                    "Lista je u formatu: npp_id | OBLAST | naziv teme.\n"
                    "PRAVILA:\n"
                    "1. Učenik piše neformalno, s greškama i sinonimima. Poveži "
                    "ZNAČENJE poruke s temom, ne doslovne riječi (npr. "
                    "\"hipotenuza\" → Pitagorina teorema; \"porcenti\" → Postotak; "
                    "\"minus puta minus\" → množenje cijelih brojeva; "
                    "\"prosjek ocjena\" → aritmetička sredina; \"nagib prave\" → "
                    "linearna funkcija).\n"
                    "2. Ako poruka jasno pripada nekoj OBLASTI, izaberi "
                    "najprikladniju temu iz te oblasti — nemoj vraćati unknown "
                    "samo zato što ne znaš tačnu pod-temu.\n"
                    "3. Vrati \"unknown\" SAMO ako poruka nije o matematici ili je "
                    "presiromašna da se odredi oblast (npr. \"zdravo\", \"ne znam\", "
                    "\"pomozi mi\").\n"
                    "Ne dodaji nikakav drugi tekst."
                ),
            },
            {
                "role": "user",
                "content": f"Poruka učenika:\n{text}\n\nDozvoljene teme:\n{topics_list}",
            },
        ]
        # AUD-03 root cause (2026-07-14): gpt-5-mini je REASONING model —
        # reasoning tokeni troše isti budžet kao odgovor. Sa max_tokens=60 svih
        # 60 je odlazilo na reasoning (finish_reason="length", content=""), pa je
        # klasifikator UVIJEK vraćao unknown. Mjereno: reasoning ~64 tok. + ~20
        # za JSON → 400 daje udoban rezervoar bez bitnog troška.
        resp = openai_chat(model, messages, timeout=timeout, max_tokens=400)
        raw = resp.choices[0].message.content or ""
        m = _JSON_RE.search(raw)
        if not m:
            return UNKNOWN
        tid = normalize_value(json.loads(m.group(0)).get("detected_topic"))
        verdict = validate_detected_topic(tid, master, tmap)
        return verdict["topic"] if verdict["status"] == "found" else UNKNOWN
    except Exception:
        log.warning("topic_detector: LLM klasifikacija nije uspjela → unknown", exc_info=True)
        return UNKNOWN


def detect_topic(
    message: Any,
    master: dict | None = None,
    tmap: dict | None = None,
    openai_chat: Callable | None = None,
    model: str = "gpt-5-mini",
    timeout: float | None = None,
) -> dict:
    """Heuristike pa (opciono) LLM. Vraća ``{"detected_topic": ..., "method":
    "heuristic"|"llm"|"none"}``; tema je uvijek validan npp id ili unknown."""
    master = master if master is not None else get_master()
    tmap = tmap if tmap is not None else get_thinkific_map()

    tid = detect_topic_heuristic(message, master)
    if tid != UNKNOWN:
        return {"detected_topic": tid, "method": "heuristic"}

    if openai_chat is not None and not is_vague_message(message, master):
        tid = detect_topic_llm(message, master, tmap, openai_chat, model, timeout)
        if tid != UNKNOWN:
            return {"detected_topic": tid, "method": "llm"}

    return {"detected_topic": UNKNOWN, "method": "none"}
