"""Phase 2 — modularni prompt builder za 6. razred (MVP AI tutor).

Spaja izlaz Phase 1 (``content_loader`` + ``topic_lookup``) sa pedagoškim
sadržajem iz AI_MATH_CONTENT_MASTER u strukturiran prompt rezultat. **NE zove
OpenAI** i **ne radi mrežne/IO operacije** — čist je i deterministički.

Ništa iz sadržaja (teme, zadaci, greške, hintovi) NIJE hardkodirano: dolazi iz
``master_content`` (Excel je izvor istine). Hardkodiran je samo *tekst pravila
ponašanja* (globalne modularne smjernice i mode-instrukcije), isto kao što
``prompts.py`` drži pedagoška pravila.

Rezultat (``build_tutor_prompt`` / ``build_fallback_prompt``)::

    {
      "system_prompt": str,
      "user_prompt": str,
      "mode": "explain|practice|exam|quick",
      "final_topic": str,               # = effective_topic (ili "unknown")
      "opened_lesson_topic": str,       # tema otvorene Thinkific lekcije (iz lookup-a)
      "effective_topic": str,           # tema stvarno korištena za kontekst prompta
      "status": "ready|fallback|ambiguous|invalid",
      "topic_context_used": bool,
      "video_flow_used": bool,
      "topic_conflict": bool,
    }

Phase 2 (audit): system prompt za tutor putanju dolazi ISKLJUČIVO iz
``matbot.tutor_prompts`` (novi, razred-uslovni stack bez legacy baze iz
``prompts.py``). Legacy ``/submit`` i dalje koristi ``prompts.py`` — netaknuto.
"""
from __future__ import annotations

import re
from typing import Any

from matbot.answer_checker import format_check_block
from matbot.content_loader import normalize_value
from matbot.tutor_prompts import (
    CHAT_FORMATTING_GUIDELINES,
    GLOBAL_MODULAR_GUIDELINES,
    LANGUAGE_TONE_GUIDELINES,
    build_result_mode_system_prompt,
    build_tutor_system_prompt,
)
from matbot.tutor_prompts import global_modular_guidelines as _global_modular_guidelines

# --- Modovi ---------------------------------------------------------------------
VALID_MODES = ("explain", "practice", "exam", "quick")
DEFAULT_MODE = "explain"

# Kanonske vrijednosti + bosanski UI aliasi (mapirani nakon lower+underscore).
_MODE_ALIASES = {
    "explain": "explain",
    "objasni": "explain",
    "objasni_mi": "explain",
    "practice": "practice",
    "vjezba": "practice",
    "vjezbaj": "practice",
    "vjezbaj_sa_mnom": "practice",
    "exam": "exam",
    "kontrolni": "exam",
    "test": "exam",
    "sutra_imam_kontrolni": "exam",
    "quick": "quick",
    "brzo": "quick",
    "rezultat": "quick",
    "samo_rezultat": "quick",
}

# --- Polja topic konteksta (tačno prema handoff §4/§6) --------------------------
TOPIC_CONTEXT_FIELDS = (
    "lesson_scope",
    "common_mistake_1", "common_mistake_2", "common_mistake_3",
    "ai_if_mistake_1", "ai_if_mistake_2", "ai_if_mistake_3",
    "hint_method",
    "solved_example_problem",
    "solved_example_step_1", "solved_example_step_2",
    "solved_example_step_3", "solved_example_step_4",
    "solved_example_answer",
    "typical_task_1", "typical_task_2", "typical_task_3",
    "controlni_task_1", "controlni_task_2", "controlni_task_3",
    "controlni_trick", "controlni_warning",
    "forbidden_ai_behavior",
    "when_to_recommend_video",
    "exit_criteria",
)
_TOPIC_META_FIELDS = (
    "grade", "oblast", "display_name", "npp_scope", "topic_type", "difficulty_level"
)

# NAPOMENA (Phase 2): GLOBAL_MODULAR_GUIDELINES, LANGUAGE_TONE_GUIDELINES i
# CHAT_FORMATTING_GUIDELINES sada žive u matbot.tutor_prompts (jedan izvor
# system-prompt teksta); ovdje su re-importovani radi kompatibilnosti.

# statusi
_STATUS_READY = "ready"
_FALLBACK_STATUS = {"unknown": "fallback", "ambiguous": "ambiguous", "invalid": "invalid"}


# --- Male pomoćne funkcije ------------------------------------------------------

def _collect(ctx: dict, keys: tuple[str, ...]) -> list[str]:
    return [ctx.get(k, "") for k in keys if ctx.get(k)]


def _compose_system_prompt(
    grade: Any,
    extra: list[str] | None = None,
    topic_context: dict | None = None,
) -> str:
    """Jedinstvena tačka sastavljanja system prompta za SVE modularne putanje.

    Phase 2 (audit): delegira na ``tutor_prompts.build_tutor_system_prompt``
    (razred-uslovni stack, bez legacy baze; konstrukcijska pravila ulaze samo
    kada ih tema traži — ``topic_context``)."""
    return build_tutor_system_prompt(grade, topic_context=topic_context, extra=extra)


# --- Javne pomoćne funkcije -----------------------------------------------------

def normalize_mode(mode: Any) -> str:
    """Vrati jedan od ``explain|practice|exam|quick``. Nepoznato/prazno → explain."""
    key = re.sub(r"[\s\-]+", "_", normalize_value(mode).lower())
    return _MODE_ALIASES.get(key, DEFAULT_MODE)


def trim_conversation_history(history: Any, limit: int = 5) -> list:
    """Zadnjih ``limit`` poruka. None/ne-lista → []. Redoslijed očuvan."""
    if not isinstance(history, list) or limit <= 0:
        return []
    return history[-limit:]


def get_topic_context(final_topic: Any, master_content: dict) -> dict:
    """Izvuci polja iz TOPICS reda za dati topic. ``{}`` ako je tema
    ``unknown``/nepostojeća (nikad ne izmišlja sadržaj)."""
    tid = normalize_value(final_topic)
    if not tid or tid.lower() == "unknown":
        return {}
    row = (master_content or {}).get("topics_by_id", {}).get(tid)
    if not row:
        return {}
    ctx: dict[str, str] = {"topic": tid}
    for f in _TOPIC_META_FIELDS:
        if row.get(f):
            ctx[f] = row[f]
    for f in TOPIC_CONTEXT_FIELDS:
        ctx[f] = row.get(f, "")
    return ctx


def get_oblast_topics(oblast: Any, master_content: dict) -> list[dict]:
    """READY topic redovi za datu oblast (case-insensitive poređenje naziva),
    u redoslijedu TOPICS sheeta. ``[]`` ako oblast ne postoji — nikad se ne
    izmišlja."""
    key = normalize_value(oblast).lower()
    if not key:
        return []
    return [
        r
        for r in (master_content or {}).get("topics", [])
        if r.get("topic")
        and normalize_value(r.get("oblast")).lower() == key
        and (not r.get("status") or normalize_value(r.get("status")).upper() == "READY")
    ]


def get_video_flow_context(
    payload: dict, final_topic: Any, master_content: dict
) -> dict | None:
    """VIDEO_FLOW red za temu — SAMO ako je ``entry_source == 'thinkific_lesson'``
    i postoji red sa istim ``topic``. Bira najprecizniji red: lesson_title, pa
    lesson_order, pa prvi red za tu temu. Inače ``None``."""
    payload = payload or {}
    if normalize_value(payload.get("entry_source")) != "thinkific_lesson":
        return None
    tid = normalize_value(final_topic)
    if not tid or tid.lower() == "unknown":
        return None
    rows = (master_content or {}).get("video_flow") or []
    topic_rows = [r for r in rows if r.get("topic") == tid]
    if not topic_rows:
        return None

    title = normalize_value(payload.get("lesson_title"))
    if title:
        exact = [r for r in topic_rows if r.get("lesson_title") == title]
        if exact:
            return exact[0]
    order = normalize_value(payload.get("lesson_order"))
    if order:
        by_order = [r for r in topic_rows if r.get("lesson_order") == order]
        if by_order:
            return by_order[0]
    return topic_rows[0]


def build_mode_instructions(
    mode: Any, final_topic: Any, topic_context: dict, payload: dict | None = None
) -> str:
    """Mode-specifične instrukcije (bosanski). ``exam`` bez teme traži oblast."""
    mode = normalize_mode(mode)
    tid = normalize_value(final_topic)
    known = bool(topic_context) and bool(tid) and tid.lower() != "unknown"
    payload = payload or {}

    if mode == "practice":
        block = (
            "MOD: VJEŽBAJ (practice)\n"
            "- Daj TAČNO JEDAN zadatak i onda ČEKAJ odgovor učenika.\n"
            "- NE daji 10 zadataka odjednom.\n"
            "- Sam tekst zadatka OBAVEZNO počni novim redom \"Zadatak: ...\" — "
            "to je jedini format koji sistem prepoznaje kao aktivni zadatak.\n"
            "- Ako je u kontekstu naveden spisak NEDAVNO DATIH ZADATAKA, tvoj "
            "novi zadatak mora biti RAZLIČIT od svih njih: drugi brojevi i "
            "drugi kontekst (ne mijenjaj samo jednu riječ).\n"
            "- Ne završavaj svaku poruku istom rečenicom (npr. \"Čekam tvoj "
            "odgovor!\") — mijenjaj završnicu ili je izostavi.\n"
        )
        block += _difficulty_line(payload)
        tasks = _collect(topic_context, ("typical_task_1", "typical_task_2", "typical_task_3"))
        if tasks:
            block += (
                "- Ponuđeni tipični zadaci su UZOR za tip i težinu; ako su svi "
                "već iskorišteni, napravi novi zadatak istog tipa sa drugim "
                "brojevima:\n"
            )
            block += "".join(f"   • {t}\n" for t in tasks)
        return block

    if mode == "exam":
        if not known:
            return (
                "MOD: KONTROLNI (exam)\n"
                "- Tema kontrolnog NIJE poznata. PRVO pitaj učenika iz koje je "
                "OBLASTI/TEME kontrolni (npr. skupovi, djeljivost, razlomci, "
                "decimalni brojevi, kružnica/uglovi). NE pretpostavljaj temu.\n"
            )
        block = (
            "MOD: KONTROLNI (exam)\n"
            "- Daj TAČNO 3 kontrolna zadatka, zatim 1 trik i 1 upozorenje.\n"
            "- Format: zadaci kao numerisana lista 1., 2., 3., zatim red "
            "\"Trik:\" i red \"Upozorenje:\".\n"
        )
        c_tasks = _collect(
            topic_context, ("controlni_task_1", "controlni_task_2", "controlni_task_3")
        )
        if c_tasks:
            block += "- Kontrolni zadaci:\n"
            block += "".join(f"   • {t}\n" for t in c_tasks)
        trick = topic_context.get("controlni_trick", "")
        warning = topic_context.get("controlni_warning", "")
        if trick:
            block += f"- Trik: {trick}\n"
        if warning:
            block += f"- Upozorenje: {warning}\n"
        return block

    if mode == "quick":
        return (
            "MOD: SAMO REZULTAT (quick)\n"
            "- Odgovor mora biti KOMPAKTAN: SAMO rezultat + najviše JEDNA kratka "
            "rečenica provjere, sve u jednom kratkom pasusu.\n"
            "- Bez dugog objašnjenja, bez naslova i bez nizanja koraka.\n"
        )

    # explain (default)
    return (
        "MOD: OBJASNI (explain)\n"
        "- Ovaj mod ISKLJUČIVO objašnjava, što jednostavnije moguće. "
        "NIKAD ne zadaji zadatak za vježbu, ne piši \"Zadatak:\" i ne traži "
        "od učenika da nešto riješi i javi rezultat — za vježbu postoji mod "
        "Vježba.\n"
        "- Budi razgovoran, ne repetitivan: prvo ideja/pristup u 2–3 kratke "
        "rečenice, zatim JEDAN kratak RIJEŠENI primjer (ti ga riješiš do "
        "kraja) ILI ponudi primjer pitanjem (npr. \"Hoćeš primjer?\").\n"
        "- U riješenom primjeru rezultat napiši prirodno u rečenici "
        "(npr. \"pa je \\(\\frac{3}{8}+\\frac{2}{8}=\\frac{5}{8}\\)\"). NE "
        "koristi podebljanu oznaku \"**Rezultat:**\" — ona pripada modu Vježba/"
        "Rezultat, ne objašnjenju.\n"
        "- NE prepričavaj cijelu lekciju odjednom i NE ispisuj sav sadržaj teme "
        "svaki put — podatke o temi koristi kao pomoć, ne kao skriptu.\n"
        "- Ako historija razgovora VEĆ sadrži objašnjenje ove teme, NE ponavljaj "
        "ga: nastavi razgovor, odgovori na follow-up ili daj sljedeći "
        "primjer/korak.\n"
        "- Ako učenik kratko potvrdi (\"može\", \"hoću\", \"nastavi\"), nastavi "
        "primjerom ili sljedećim korakom — bez ponavljanja objašnjenja.\n"
        "- Budi kratak; detaljno objašnjavaj SAMO ako učenik to izričito zatraži.\n"
        "- Ako nedostaje kontekst za rješavanje, postavi JEDNO kratko pitanje "
        "prije rješavanja.\n"
    )


def _difficulty_line(payload: dict) -> str:
    """Instrukcija težine novog zadatka: eksplicitni zahtjev ("daj teži") ili
    ljestvica po nizu tačnih odgovora (correct_streak)."""
    hint = normalize_value((payload or {}).get("_difficulty_hint")).lower()
    if hint == "harder":
        return (
            "- TEŽINA: novi zadatak mora biti malo TEŽI od prethodnog — veći "
            "brojevi, korak više ili kombinacija dvije operacije.\n"
        )
    if hint == "easier":
        return (
            "- TEŽINA: novi zadatak mora biti malo LAKŠI od prethodnog — manji "
            "brojevi i jedan korak manje.\n"
        )
    try:
        streak = int((payload or {}).get("_correct_streak", 0) or 0)
    except (TypeError, ValueError):
        streak = 0
    if streak >= 3:
        return (
            "- TEŽINA: učenik je riješio više zadataka zaredom — novi zadatak "
            "neka bude OSJETNO teži (kombinacija operacija ili tekstualni "
            "zadatak).\n"
        )
    if streak >= 1:
        return (
            "- TEŽINA: prethodni odgovor je bio tačan — novi zadatak neka bude "
            "malo teži od prethodnog.\n"
        )
    return ""


def _task_items_note(payload: dict) -> str:
    """BUG 12: blok stanja višestavkovnog zadatka — KOD odlučuje koja je stavka
    na redu, model samo slijedi (kao image_test)."""
    state = (payload or {}).get("_task_items_prev")
    if not state:
        return ""
    labels = state.get("labels") or []
    graded = state.get("graded") or []
    pending = [n for n in labels if n not in graded]
    lines = ["STANJE STAVKI ZADATKA (vodi ga sistem — obavezujuće):"]
    if graded:
        lines.append(
            f"- Stavke {', '.join(map(str, graded))} su VEĆ ocijenjene u ranijim "
            "porukama. NE ocjenjuj ih ponovo, NE prepričavaj ih i ne mijenjaj "
            "njihovu ocjenu."
        )
    current = (payload or {}).get("_current_task_item")
    if current:
        lines.append(
            f"- Učenikova poruka je odgovor na stavku {current}. Ocijeni "
            f"ISKLJUČIVO stavku {current}."
        )
    elif pending:
        lines.append(
            f"- Odgovor još čekaju stavke: {', '.join(map(str, pending))} — "
            "učenikova poruka se odnosi na njih (prvo najniži broj)."
        )
    return "\n".join(lines) + "\n"


# Živi nalaz 2026-07-11 (bug #2): u Vježbi (help/followup) mode-blok je task-
# fokusiran pa "priguši" empatija-pravilo iz sistemskog prompta — model na
# "glup sam"/"preteško" odmah ide na proceduru. Zato distres detektujemo
# deterministički i ubacujemo ISTAKNUTU direktivu na VRH bloka.
_DISTRESS_RE = re.compile(
    r"\b(glup\w*|blesav\w*|tup(?:a|av\w*)?|ne\s+mogu|ne\s+umijem|ne\s+umem|"
    r"pretesk\w*|pretesko|mrzim|ne\s+volim|nikad(?:a)?\s+ne(?:\s+cu|cu)?\s*(?:\w+\s+)?nauc\w*|"
    r"odustaj\w*|glupa\s+matematika|bezveze|ne\s+ide\s+mi|beznadezn\w*|"
    r"nisam\s+sposoban\w*|nesposoban\w*|frustr\w*)\b"
)


def _student_in_distress(payload: dict) -> bool:
    # _original_student_message: servis prepiše student_message sintetičkim
    # hint-tekstom pri reroute-u na practice_help; original nosi emociju.
    payload = payload or {}
    msg = normalize_value(payload.get("_original_student_message")
                          or payload.get("student_message"))
    if not msg:
        return False
    folded = (msg.lower().replace("č", "c").replace("ć", "c").replace("đ", "d")
              .replace("š", "s").replace("ž", "z"))
    return bool(_DISTRESS_RE.search(folded))


_EMPATHY_DIRECTIVE = (
    "‼️ PRIORITET — EMOCIJA UČENIKA: učenik je izrazio frustraciju ili "
    "samokritiku. PRVA rečenica tvog odgovora MORA biti kratko, iskreno "
    "ohrabrenje koje normalizuje grešku (npr. \"Nisi glup — ovo mnogi prvo "
    "pobrkaju, idemo polako zajedno.\"). Tek POSLIJE toga daj hint/korak. "
    "NIKAD ne počinji procedurom ni ocjenskom labelom prije ohrabrenja.\n"
)


def build_practice_followup_instructions(payload: dict, topic_context: dict) -> str:
    """Phase 4.3 — instrukcije kada je učenikova poruka ODGOVOR na prethodni
    practice zadatak (``payload.interaction_phase == 'answering_practice_task'``).

    Zamjenjuje standardne practice instrukcije: AI provjerava odgovor. Audit:
    ako postoji ``payload["answer_check"]`` (deterministička provjera iz koda),
    njena presuda je OBAVEZUJUĆA za model.

    Produkt-odluka (2026-07-10, BUG 2): poslije TAČNOG odgovora tutor ODMAH
    daje novi, malo teži zadatak — cilj Vježbe je što više riješenih zadataka,
    ne razmjena ljubaznosti."""
    payload = payload or {}
    last_task = normalize_value(payload.get("last_tutor_task"))[:600]
    block = (
        "MOD: VJEŽBA — PROVJERA ODGOVORA (practice follow-up)\n"
        "- The student is responding to this exact previous task: "
        f"{last_task or '[last_tutor_task nije poslan]'}\n"
        "- Učenikova poruka je ODGOVOR na prethodno postavljeni zadatak — NE "
        "tretiraj je kao novo pitanje.\n"
        "- Provjeri tačnost odgovora koristeći TAČNO taj prethodni vidljivi zadatak "
        "i historiju razgovora.\n"
        "- OBAVEZAN POSTUPAK PROVJERE: PRVO sam riješi zadatak i izračunaj tačan "
        "rezultat, TEK ONDA uporedi sa učenikovim odgovorom. NIKAD ne piši "
        "\"Nije tačno\" prije nego što si sam izračunao rezultat.\n"
        "- PRIHVATI EKVIVALENTNE OBLIKE kao tačne: neskraćeni razlomak "
        "(3/5 = 6/10), mješoviti i nepravi zapis istog broja (2 1/4 = 9/4), "
        "decimalni zapis iste vrijednosti — osim kada zadatak izričito traži "
        "određeni oblik (tada je vrijednost tačna, ali objasni traženi oblik).\n"
        "- KOD ODGOVORA NA JEDNU STAVKU/ZADATAK: PRVA REČENICA mora sadržavati "
        "TAČNO JEDAN konačan sud: ako je sistemska presuda correct, počni sa "
        "\"Tačno.\"; ako je correct_value_wrong_form, počni sa \"Djelimično "
        "tačno.\"; ako je incorrect, počni sa \"Netačno.\". OSTATAK odgovora NE "
        "SMIJE protivrječiti tom sudu, ne smije koristiti drugu labelu i te "
        "labele NE ponavljaj nigdje dalje u istoj poruci. (Kod više stavki "
        "odjednom vidi pravilo ispod — labela ide UZ SVAKU stavku, ne na vrh.)\n"
        "- Ako zadatak ima VIŠE numerisanih stavki: ocijeni SVAKU stavku posebno "
        "i jasno navedi koja je tačna, a koja nije, numerišući ih ORIGINALNIM "
        "brojevima (1., 2., 3. — nikad dvije stavke pod \"1.\"). Stavku na koju "
        "učenik NIJE odgovorio NE ocjenjuj kao netačnu — na kraju zamoli odgovor "
        "SAMO za nju. NIKAD ne proglašavaj cijeli odgovor netačnim ako je samo "
        "jedna stavka pogrešna. KOD VIŠE STAVKI NE stavljaj jednu zajedničku "
        "labelu (\"Tačno.\"/\"Netačno.\") na vrh poruke — svaka stavka ima SVOJU "
        "labelu uz svoj broj (npr. \"1. Netačno. ...\", \"2. Tačno. ...\").\n"
        "- Ako učenik spomene SAMO jednu stavku (npr. \"treće pitanje\", \"3.\", "
        "\"zadnji zadatak\"), ocijeni ISKLJUČIVO nju. Ostale stavke NE ocjenjuj, "
        "NE rješavaj i NE izmišljaj učenikove odgovore na njih.\n"
        "- AKO JE TAČNO (ili djelimično tačno): potvrdi labelom i u 1–2 rečenice "
        "objasni zašto, PA U ISTOJ PORUCI ODMAH daj JEDAN novi zadatak iz iste "
        "teme — bez pitanja \"želiš li još\". Novi zadatak počni novim redom "
        "\"Zadatak: ...\" i neka bude malo teži od prethodnog (osim ako TEŽINA "
        "ispod kaže drugačije). Izuzetak: ako sve stavke višestavkovnog zadatka "
        "još nisu odgovorene, prvo zatraži preostale stavke, bez novog zadatka.\n"
        "- AKO NIJE TAČNO: blago reci da nije tačno, pa PRVO kratko IMENUJ "
        "vjerovatnu grešku (npr. \"čini se da si sabrao i nazivnike\") da učenik "
        "shvati ZAŠTO je pogriješio, TEK ONDA pokaži tačan račun korak po korak "
        "(nikad samo \"nije tačno\" bez računa). NE daji odmah novi zadatak — "
        "završi jednim kratkim pitanjem razumijevanja ili ponudi LAKŠI zadatak.\n"
        "- Ako učenik napiše \"ne znam\", \"objasni\" ili \"pomozi\": daj JEDAN "
        "vođeni hint ili JEDAN sljedeći korak — NE novi zadatak i NE cijelo "
        "rješenje odmah.\n"
        "- EMPATIJA: ako učenik uz odgovor izrazi frustraciju ili samokritiku "
        "(\"glup sam\", \"ne mogu\", \"preteško\"), poslije ocjenske labele "
        "dodaj kratko ohrabrenje koje normalizuje grešku prije nego nastaviš.\n"
        "- NE ponavljaj isti zadatak osim ako je odgovor nejasan.\n"
        "- NE ponavljaj cijelo objašnjenje teme i NE počinji isti zadatak ispočetka.\n"
        "- Odgovor mora biti KRATAK i prirodan za chat: bez naslova poput "
        "\"### Tema\" i bez dugih lekcija.\n"
        "- Ne završavaj svaku poruku istom frazom; mijenjaj formulacije.\n"
        "PRIMJER DOBROG ODGOVORA (tačno):\n"
        "  Tačno. Lijepo si sabrao brojnike, nazivnik ostaje isti.\n"
        "  Zadatak: Izračunaj \\(\\frac{7}{12} - \\frac{5}{12}\\).\n"
        "PRIMJER DOBROG ODGOVORA (netačno):\n"
        "  Netačno, ali blizu si. Čini se da si sabrao i nazivnike — to je "
        "najčešća greška. Kod istih nazivnika brojnici se saberu, a nazivnik "
        "ostaje isti, pa je \\(\\frac{2}{9} + \\frac{5}{9} = \\frac{7}{9}\\). "
        "Gdje misliš da je zapelo?\n"
    )
    block += _difficulty_line(payload)
    items_note = _task_items_note(payload)
    if items_note:
        block += items_note
    if last_task:
        block += f"ZADNJI ZADATAK (tačan vidljivi tekst):\n{last_task}\n"
    check = payload.get("answer_check")
    if check is not None:
        check_block = format_check_block(check)
        if check_block:
            block += check_block + "\n"
    return block


def build_practice_help_instructions(payload: dict, topic_context: dict) -> str:
    """Učenik ne šalje odgovor, nego traži pomoć/hint/rješenje za aktivni zadatak."""
    payload = payload or {}
    intent = normalize_value(payload.get("_practice_help_intent")).lower()
    task = normalize_value(payload.get("_practice_help_task"))[:600]
    item = normalize_value(payload.get("_practice_help_item"))
    item_text = f" stavku {item}" if item else ""
    block = (
        "MOD: VJEŽBA — POMOĆ ZA AKTIVNI ZADATAK\n"
        f"- Učenik NE šalje odgovor za ocjenjivanje, nego traži pomoć za{item_text} "
        "iz prethodnog practice zadatka.\n"
        "- NE koristi ocjenjivačke labele \"Tačno.\", \"Djelimično tačno.\" ili "
        "\"Netačno.\" jer ovo nije provjera učenikovog odgovora.\n"
        "- EMPATIJA PRIJE HINTA: ako učenikova poruka izražava frustraciju ili "
        "samokritiku (\"glup sam\", \"ne mogu\", \"preteško mi je\", \"mrzim\"), "
        "PRVA rečenica mora biti kratko ohrabrenje koje normalizuje grešku "
        "(npr. \"Nisi glup, ovo mnogi prvo pobrkaju — idemo polako.\"), pa TEK "
        "ONDA hint. Ne prelazi odmah na proceduru.\n"
        "- Radi samo zadatak naveden u bloku ispod; ne uvodi novu temu i ne "
        "ocjenjuj ostale stavke.\n"
    )
    if intent == "hint":
        block += (
            "- Učenik je zapeo ili traži HINT: daj JEDAN kratak sljedeći korak "
            "ILI konkretno pitanje koje vodi ka rješenju. NE otkrivaj konačan "
            "rezultat i NE ocjenjuj (bez \"Tačno.\"/\"Netačno.\").\n"
            "- NE ponavljaj cijelo rješenje ni prethodno objašnjenje od početka. "
            "Ako je rješenje već pokazano, umjesto ponavljanja pitaj KONKRETNO "
            "koji korak učeniku nije jasan (npr. \"Je li ti jasno kako smo "
            "\\(\\frac{1}{4}\\) sveli na \\(\\frac{2}{8}\\)?\").\n"
            "- Na kraju kratko pozovi učenika da pokuša nastaviti.\n"
        )
    else:
        block += (
            "- Učenik traži da pokažeš/objasniš zadatak: možeš prikazati kompletno "
            "rješenje ako je to prirodno za poruku.\n"
            "- Ako prikažeš kompletno rješenje, NE traži od učenika da odgovori na "
            "isti zadatak ponovo. Umjesto toga pitaj želi li sličan zadatak ili "
            "sljedeću stavku.\n"
        )
    if task:
        block += f"AKTIVNI ZADATAK ZA POMOĆ:\n{task}\n"
    # bug #2: kad je distres stvarno detektovan, istaknuta direktiva na VRHU
    # (bullet u listi model zna preskočiti).
    if _student_in_distress(payload):
        block = _EMPATHY_DIRECTIVE + block
    return block


def build_continuation_instructions(payload: dict) -> str:
    """Phase 7.2 — učenikova poruka je KRATKA POTVRDA/nastavak ("može", "hoću",
    "nastavi", "daj primjer") poslije prethodnog odgovora tutora
    (``payload.interaction_phase == 'continuing_explanation'``).

    Zamjenjuje standardni mode blok: AI nastavlja od svoje zadnje poruke
    (``last_tutor_message``) umjesto da ponavlja objašnjenje teme ispočetka."""
    last_msg = normalize_value((payload or {}).get("last_tutor_message"))[:600]
    block = (
        "MOD: NASTAVAK RAZGOVORA (follow-up)\n"
        "- Učenikova poruka je kratka potvrda/nastavak (npr. \"može\", \"hoću\", "
        "\"nastavi\", \"daj primjer\") — NE tretiraj je kao novi zahtjev za "
        "objašnjenjem teme.\n"
        "- NASTAVI tačno tamo gdje je tvoja ZADNJA PORUKA stala; NE ponavljaj "
        "objašnjenje ni \"Ideja:\" blok ispočetka.\n"
        "- Ako je zadnja poruka ponudila primjer (npr. \"Hoćeš primjer?\"), daj "
        "JEDAN konkretan primjer korak po korak.\n"
        "- Ako je zadnja poruka ponudila zajedničko rješavanje, počni JEDAN "
        "vođeni primjer i uključi učenika.\n"
        "- Budi kratak i prirodan; po potrebi završi JEDNIM kratkim pitanjem za "
        "sljedeći korak.\n"
        "- NE ispisuj ponovo naslov teme (\"Tema:\") osim ako je zaista potrebno.\n"
        "- Ovo je objašnjenje, ne rješavanje zadatka: rezultat primjera napiši "
        "prirodno u rečenici, NE koristi podebljanu oznaku \"**Rezultat:**\".\n"
    )
    if last_msg:
        block += f"ZADNJA PORUKA TUTORA (nastavi od nje):\n{last_msg}\n"
    return block


# --- Blokovi user prompta -------------------------------------------------------

_ENTRY_LABELS = (
    ("grade", "Razred"),
    ("entry_source", "Entry source"),
    ("course_name", "Kurs"),
    ("section_name", "Sekcija"),
    ("lesson_order", "Lesson order"),
    ("lesson_title", "Thinkific lekcija"),
    ("selected_topic", "Selected topic"),
    ("selected_oblast", "Selected oblast"),
    ("detected_topic", "Detected topic"),
)

_TOPIC_LABELS = (
    ("display_name", "Naziv teme"),
    ("oblast", "Oblast"),
    ("npp_scope", "NPP opseg (nivo teme)"),
    ("lesson_scope", "Opseg lekcije (lesson_scope)"),
    ("common_mistake_1", "Česta greška 1"),
    ("common_mistake_2", "Česta greška 2"),
    ("common_mistake_3", "Česta greška 3"),
    ("ai_if_mistake_1", "AI ako greška 1"),
    ("ai_if_mistake_2", "AI ako greška 2"),
    ("ai_if_mistake_3", "AI ako greška 3"),
    ("hint_method", "Metoda hinta"),
    ("solved_example_problem", "Riješeni primjer — zadatak"),
    ("solved_example_step_1", "Riješeni primjer — korak 1"),
    ("solved_example_step_2", "Riješeni primjer — korak 2"),
    ("solved_example_step_3", "Riješeni primjer — korak 3"),
    ("solved_example_step_4", "Riješeni primjer — korak 4"),
    ("solved_example_answer", "Riješeni primjer — odgovor"),
    ("typical_task_1", "Tipičan zadatak 1"),
    ("typical_task_2", "Tipičan zadatak 2"),
    ("typical_task_3", "Tipičan zadatak 3"),
    ("controlni_task_1", "Kontrolni zadatak 1"),
    ("controlni_task_2", "Kontrolni zadatak 2"),
    ("controlni_task_3", "Kontrolni zadatak 3"),
    ("controlni_trick", "Kontrolni trik"),
    ("controlni_warning", "Kontrolni upozorenje"),
    ("when_to_recommend_video", "Kada preporučiti video"),
    ("exit_criteria", "Kriterij izlaska"),
)

_VIDEO_LABELS = (
    ("sta_ucenik_upravo_naucio", "Šta je učenik upravo naučio"),
    ("ai_after_video", "AI poslije videa"),
    ("recommended_mode", "Preporučeni mod"),
)


def _build_entry_context(payload: dict, final_topic: str, mode: str) -> str:
    lines = ["KONTEKST:"]
    for key, label in _ENTRY_LABELS:
        val = normalize_value(payload.get(key))
        if val:
            lines.append(f"- {label}: {val}")
    lines.append(f"- Final topic: {final_topic}")
    lines.append(f"- Mod: {mode}")
    return "\n".join(lines)


# Phase 1 (audit): topic blok filtriran po modu — explain ne treba kontrolne
# zadatke, exam ne treba riješeni primjer itd. Manji prompt = jasnije instrukcije.
_META_FIELDS = ("display_name", "oblast", "npp_scope", "lesson_scope")
_MISTAKE_FIELDS = (
    "common_mistake_1", "common_mistake_2", "common_mistake_3",
    "ai_if_mistake_1", "ai_if_mistake_2", "ai_if_mistake_3",
)
_SOLVED_FIELDS = (
    "solved_example_problem",
    "solved_example_step_1", "solved_example_step_2",
    "solved_example_step_3", "solved_example_step_4",
    "solved_example_answer",
)
_TYPICAL_FIELDS = ("typical_task_1", "typical_task_2", "typical_task_3")
_CONTROLNI_FIELDS = (
    "controlni_task_1", "controlni_task_2", "controlni_task_3",
    "controlni_trick", "controlni_warning",
)
_EXTRA_FIELDS = ("when_to_recommend_video", "exit_criteria")

_MODE_TOPIC_FIELDS = {
    "explain": frozenset(
        _META_FIELDS + _MISTAKE_FIELDS + ("hint_method",) + _SOLVED_FIELDS + _EXTRA_FIELDS
    ),
    "practice": frozenset(
        _META_FIELDS + _MISTAKE_FIELDS + ("hint_method",)
        + _TYPICAL_FIELDS + _SOLVED_FIELDS + _EXTRA_FIELDS
    ),
    "practice_followup": frozenset(
        _META_FIELDS + _MISTAKE_FIELDS + ("hint_method",) + _EXTRA_FIELDS
    ),
    "exam": frozenset(
        _META_FIELDS + _MISTAKE_FIELDS + ("hint_method",) + _CONTROLNI_FIELDS
    ),
    "quick": frozenset(_META_FIELDS),
}


def _build_topic_block(topic_context: dict, mode: str | None = None) -> str:
    """Topic blok za user prompt. ``mode`` filtrira polja po namjeni (Phase 1);
    ``None``/nepoznat mod zadržava staro ponašanje (sva polja)."""
    if not topic_context:
        return ""
    allowed = _MODE_TOPIC_FIELDS.get(mode)
    lines = ["PODACI O TEMI (iz AI_MATH_CONTENT_MASTER):"]
    for key, label in _TOPIC_LABELS:
        if allowed is not None and key not in allowed:
            continue
        val = topic_context.get(key, "")
        if val:
            lines.append(f"- {label}: {val}")
    return "\n".join(lines)


def _build_video_flow_block(vf: dict | None) -> str:
    if not vf:
        return ""
    lines = ["VIDEO_FLOW KONTEKST (učenik dolazi iz Thinkific lekcije):"]
    for key, label in _VIDEO_LABELS:
        val = normalize_value(vf.get(key))
        if val:
            lines.append(f"- {label}: {val}")
    return "\n".join(lines)


def get_video_recommendation(topic: Any, master_content: dict) -> list[dict]:
    """VIDEO_LINKS redovi (lesson_type == 'video') za temu, ili ``[]``.

    Izvor je ``master["videos_by_topic"]`` (NPP). URL trenutno nije dostupan pa se
    lekcija preporučuje po nazivu (section_name/lesson_title)."""
    tid = normalize_value(topic)
    if not tid or tid.lower() == "unknown":
        return []
    return list((master_content or {}).get("videos_by_topic", {}).get(tid, []))


def _video_labels(videos: list[dict], limit: int = 2) -> list[str]:
    names: list[str] = []
    for v in videos[:limit]:
        section = normalize_value(v.get("section_name"))
        title = normalize_value(v.get("lesson_title"))
        if title and section and title != section:
            names.append(f"{title} (sekcija: {section})")
        elif title or section:
            names.append(title or section)
    return names


def build_video_reco_block(videos: list[dict], stuck: bool = False) -> str:
    """Blok koji modelu daje NAZIVE povezanih video lekcija (bez URL-a).

    ``stuck=True`` (Vježbajmo, učenik zapeo) → aktivno preporuči video;
    inače (Objasni mi) → ponudi ga opciono na kraju. Model ne smije izmišljati
    URL ni druge lekcije."""
    names = _video_labels(videos)
    if not names:
        return ""
    listed = "; ".join(names)
    if stuck:
        lead = (
            "UČENIK JE ZAPEO — PREPORUČI VIDEO:\n"
            "- Učenik više puta griješi ili ne zna. Ljubazno mu predloži da "
            "pogleda povezanu video lekciju prije nego nastavite."
        )
    else:
        lead = (
            "VIDEO LEKCIJA (opciono ponudi na kraju):\n"
            "- Ako je korisno, na KRAJU kratko ponudi povezanu video lekciju."
        )
    return (
        f"{lead}\n"
        f"- Poveži isključivo s ovom lekcijom, po nazivu (link nije dostupan): {listed}\n"
        "- NE izmišljaj URL, broj lekcije ni druge lekcije — koristi samo ovaj naziv."
    )


def _build_recent_tasks_block(payload: dict, mode: str, interaction_phase: str = "") -> str:
    """Audit: anti-ponavljanje — spisak nedavno datih zadataka (šalje ga browser).

    Ulazi u prompt SAMO kada model treba generisati novi zadatak: practice/exam
    bez follow-up faze, ILI practice follow-up (poslije tačnog odgovora tutor
    odmah daje novi zadatak — BUG 2); model se ne oslanja na vlastito pamćenje."""
    if mode not in ("practice", "exam"):
        return ""
    if interaction_phase and interaction_phase != "answering_practice_task":
        return ""
    tasks = [normalize_value(t) for t in (payload or {}).get("recent_tasks") or []]
    tasks = [t for t in tasks if t]
    if not tasks:
        return ""
    lines = [
        "NEDAVNO DATI ZADACI (učenik ih je VEĆ dobio — novi zadatak mora biti "
        "drugačiji: drugi brojevi I drugi kontekst):"
    ]
    lines.extend(f"- {t}" for t in tasks[-6:])
    return "\n".join(lines)


def _build_student_block(payload: dict) -> str:
    parts = []
    msg = normalize_value(payload.get("student_message") or payload.get("message"))
    if msg:
        parts.append(f"PORUKA UČENIKA:\n{msg}")
    ocr = normalize_value(
        payload.get("image_ocr_text")
        or payload.get("ocr_text")
        or payload.get("image_text")
    )
    if ocr:
        parts.append(f"TEKST ZADATKA SA SLIKE (OCR):\n{ocr}")
    return "\n\n".join(parts)


def build_image_test_instructions(payload: dict) -> str:
    """Mode blok kada je aktivan image_test tok (``payload["_image_test"]``).

    Stanje (koja stavka je na redu) određuje KOD, ne model — blok samo prenosi
    odluku: riješi isključivo tekuću stavku, zadrži numeraciju, ne izmišljaj
    nepovezane zadatke."""
    img = (payload or {}).get("_image_test") or {}
    current = normalize_value(img.get("current"))
    if not current:
        return ""
    labels = [normalize_value(l) for l in img.get("labels") or []]
    solved = [normalize_value(s) for s in img.get("solved") or []]
    task = normalize_value(img.get("current_task"))[:500]
    block = (
        "MOD: ZADACI SA SLIKE (image_test)\n"
        f"- Učenik rješava zadatke sa poslane slike (ukupno {len(labels)}: "
        f"{', '.join(labels)}). Već riješeno: {', '.join(solved) or 'ništa'}.\n"
        f"- SADA riješi ili objasni ISKLJUČIVO zadatak {current} — prema onome "
        "što učenik traži.\n"
        "- NE rješavaj ostale zadatke unaprijed i NIKAD ne izmišljaj novi "
        "nepovezani zadatak za vježbu dok traje rad na slici.\n"
        f"- Zadrži originalnu numeraciju sa slike (piši \"{current}.\").\n"
    )
    if task:
        block += f"- Tekst zadatka {current}: {task}\n"
    if img.get("style") == "result_only":
        block += "- STIL: samo rezultat + najviše jedna kratka rečenica provjere.\n"
    else:
        block += "- STIL: korak po korak, kratko i jasno, primjereno razredu.\n"
    remaining = [l for l in labels if l not in solved and l != current]
    if remaining:
        block += (
            f"- Na kraju kratko pitaj želi li učenik nastaviti na zadatak "
            f"{remaining[0]}.\n"
        )
    else:
        block += "- Ovo je posljednji zadatak sa slike — na kraju kratko pohvali i ponudi vježbu.\n"
    return block


def _build_image_context_block(payload: dict) -> str:
    ctx = normalize_value((payload or {}).get("last_image_context"))[:2000]
    if not ctx:
        return ""
    return (
        "KONTEKST ZADNJE SLIKE (sačuvano iz prethodnog zadatka sa slike):\n"
        f"{ctx}\n"
        "Ako učenik pita za prvi/drugi/treći zadatak, rezultat ili postupak "
        "sa slike, koristi OVAJ kontekst i sačuvaj originalnu numeraciju. "
        "Prije objašnjenja ponovo provjeri sačuvani odgovor prema tekstu zadatka. "
        "Ako blok PROVJERA SAČUVANOG KONTEKSTA kaže da je raniji odgovor bio "
        "pogrešan, počni jasnom ispravkom (npr. \"Ranije sam pogrešno napisao ...\") "
        "i zatim objasni tačan račun. NE smiješ tiho promijeniti rezultat. "
        "NE odgovaraj da treba poslati novo pitanje ako je ovaj kontekst dovoljan."
    )


_ASSISTANT_ROLES = {"assistant", "bot", "tutor", "ai"}


def build_history_messages(history: Any) -> list[dict]:
    """Phase 2 (audit) — historija kao PRAVE chat poruke umjesto teksta u user
    promptu. Modeli znatno bolje prate tok dijaloga kroz role poruke.

    Vraća listu ``{"role": "user"|"assistant", "content": str}`` (zadnjih 5,
    prazno/nevalidno se preskače). Nepoznate role se tretiraju kao user."""
    messages: list[dict] = []
    for msg in trim_conversation_history(history):
        if isinstance(msg, dict):
            role = normalize_value(msg.get("role")).lower() or "user"
            content = normalize_value(
                msg.get("content") or msg.get("text") or msg.get("message")
            )
        else:
            role, content = "user", normalize_value(msg)
        if not content:
            continue
        messages.append({
            "role": "assistant" if role in _ASSISTANT_ROLES else "user",
            "content": content,
        })
    return messages


# --- Glavni builderi ------------------------------------------------------------

def build_tutor_prompt(
    payload: dict,
    lookup_result: dict,
    master_content: dict,
    thinkific_map: dict | None = None,  # rezervisano; nije potrebno u Phase 2
) -> dict:
    """Sagradi strukturiran prompt iz Phase 1 lookup rezultata + master sadržaja.

    ``lookup_result.status``: ``found`` → puni prompt (status ``ready``); ostalo
    (``unknown``/``ambiguous``/``invalid``) → ``build_fallback_prompt``.
    """
    payload = payload or {}
    lookup_result = lookup_result or {}
    master_content = master_content or {}

    status = normalize_value(lookup_result.get("status")).lower() or "unknown"
    if status != "found":
        reason = status if status in ("ambiguous", "invalid") else "unknown"
        return build_fallback_prompt(payload, reason)

    mode = normalize_mode(payload.get("mode"))
    # Phase 4.3: follow-up odgovor na practice zadatak uvijek ide kao practice.
    # Phase 7.2: kratka potvrda ("može", "nastavi") → nastavak, ne novo objašnjenje.
    interaction_phase = normalize_value(payload.get("interaction_phase")).lower()
    is_practice_followup = interaction_phase == "answering_practice_task"
    is_practice_help = interaction_phase == "practice_help"
    is_continuation = interaction_phase == "continuing_explanation"
    if is_practice_followup:
        mode = "practice"
    elif is_practice_help:
        mode = "explain"
    lesson_topic = normalize_value(lookup_result.get("final_topic"))
    topic_ids = master_content.get("topic_ids", set())

    # Konflikt tema (guidelines §9): otvorena lekcija vs. validan detected_topic.
    detected = normalize_value(payload.get("detected_topic"))
    is_lesson_ctx = normalize_value(payload.get("entry_source")) == "thinkific_lesson"
    topic_conflict = (
        is_lesson_ctx
        and bool(detected)
        and detected.lower() != "unknown"
        and detected in topic_ids
        and detected != lesson_topic
    )
    effective_topic = detected if topic_conflict else lesson_topic

    topic_context = get_topic_context(effective_topic, master_content)
    video_flow = get_video_flow_context(payload, effective_topic, master_content)

    # --- NPP video preporuka (VIDEO_LINKS) ---
    # Objasni mi (explain): ponudi video opciono; Vježbajmo (practice): samo kada
    # je učenik zapeo (payload["_student_stuck"], postavlja ga ai_tutor_service).
    # P10 (2026-07-11): video prati temu PITANJA (detektovanu) kad je poznata i
    # različita od otvorene lekcije — ne otvorenu/izabranu lekciju. Decoupled od
    # is_lesson_ctx gejta koji vrijedi za effective_topic (framing lekcije).
    video_topic = effective_topic
    if detected and detected.lower() != "unknown" and detected in topic_ids and detected != effective_topic:
        video_topic = detected
    videos = get_video_recommendation(video_topic, master_content)
    stuck = bool(payload.get("_student_stuck"))
    if is_practice_help and payload.get("_stuck_help"):
        # "ne znam" → help: video se nudi SAMO na pragu (F5 ramp), ne odmah,
        # iako je prompt-mod postao explain.
        want_video = bool(videos) and stuck
    else:
        want_video = bool(videos) and (mode == "explain" or (mode == "practice" and stuck))
    video_reco_block = build_video_reco_block(videos, stuck=stuck) if want_video else ""

    # --- system prompt ---
    forbidden = topic_context.get("forbidden_ai_behavior", "")
    extra = (
        ["ZABRANJENO PONAŠANJE ZA OVU TEMU (forbidden_ai_behavior):\n- " + forbidden]
        if forbidden
        else None
    )
    system_prompt = _compose_system_prompt(
        payload.get("grade"), extra, topic_context=topic_context
    )

    # --- user prompt ---
    user_parts = [_build_entry_context(payload, effective_topic, mode)]
    if topic_conflict:
        user_parts.append(
            "NAPOMENA O NESLAGANJU TEME:\n"
            f"- Otvorena lekcija je tema '{lesson_topic}', ali zadatak izgleda kao "
            f"tema '{effective_topic}'.\n"
            "- Riješi zadatak prema STVARNOJ temi zadatka i KRATKO napomeni ovo "
            "neslaganje učeniku."
        )
    image_test_block = build_image_test_instructions(payload)
    if image_test_block:
        # image_test tok nadjačava standardne modove: stanje bira stavku,
        # model je samo rješava (nikad ne generiše nepovezani zadatak)
        mode_block = image_test_block
    elif is_practice_followup:
        mode_block = build_practice_followup_instructions(payload, topic_context)
    elif is_practice_help:
        mode_block = build_practice_help_instructions(payload, topic_context)
    elif is_continuation:
        mode_block = build_continuation_instructions(payload)
    else:
        mode_block = build_mode_instructions(mode, effective_topic, topic_context, payload)
    topic_block_mode = "practice_followup" if is_practice_followup else mode
    for block in (
        _build_topic_block(topic_context, mode=topic_block_mode),
        _build_video_flow_block(video_flow),
        video_reco_block,
        _build_image_context_block(payload),
        _build_recent_tasks_block(payload, mode, interaction_phase),
        mode_block,
        _build_student_block(payload),
    ):
        if block:
            user_parts.append(block)
    user_prompt = "\n\n".join(user_parts).strip()

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        # Phase 2: historija ide kao PRAVE role poruke (system → history → user)
        "history_messages": build_history_messages(payload.get("conversation_history")),
        "mode": mode,
        "final_topic": effective_topic,
        "opened_lesson_topic": lesson_topic,
        "effective_topic": effective_topic,
        "status": _STATUS_READY,
        "topic_context_used": bool(topic_context),
        "video_flow_used": bool(video_flow),
        "video_recommended": want_video,
        "topic_conflict": topic_conflict,
    }


def build_general_tutor_prompt(payload: dict) -> dict:
    """Phase 6 — free_chat sa KONKRETNIM pitanjem, ali tema nije prepoznata.

    Gradi prompt BEZ topic konteksta (ništa se ne izmišlja): base tutor + modularna
    pravila + mode instrukcije + poruka učenika. Status je ``ready`` jer se OpenAI
    poziva; ``final_topic`` ostaje ``"unknown"``."""
    payload = payload or {}
    mode = normalize_mode(payload.get("mode"))
    # Phase 7.2: i bez prepoznate teme poštuj fazu interakcije (practice odgovor
    # ili nastavak razgovora) umjesto standardnog mode bloka.
    phase = normalize_value(payload.get("interaction_phase")).lower()
    image_test_block = build_image_test_instructions(payload)
    if image_test_block:
        mode_block = image_test_block          # stanje bira stavku sa slike
    elif phase == "answering_practice_task":
        mode = "practice"
        mode_block = build_practice_followup_instructions(payload, {})
    elif phase == "practice_help":
        mode = "explain"
        mode_block = build_practice_help_instructions(payload, {})
    elif phase == "continuing_explanation":
        mode_block = build_continuation_instructions(payload)
    else:
        mode_block = build_mode_instructions(mode, "unknown", {}, payload)

    system_prompt = _compose_system_prompt(payload.get("grade"))

    user_parts = [
        _build_entry_context(payload, "unknown", mode),
        "NAPOMENA (tema nije prepoznata):\n"
        "- Pitanje učenika je konkretno, ali tema nije pronađena u biblioteci tema.\n"
        f"- Odgovori na KONKRETNO pitanje koristeći gradivo {normalize_value(payload.get('grade')) or '6'}. razreda, kratko i "
        "korak po korak.\n"
        "- NE izmišljaj temu i ne spominji internu listu tema.",
        _build_image_context_block(payload),
        _build_recent_tasks_block(payload, mode, phase),
        mode_block,
        _build_student_block(payload),
    ]
    user_prompt = "\n\n".join(p for p in user_parts if p).strip()

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "history_messages": build_history_messages(payload.get("conversation_history")),
        "mode": mode,
        "final_topic": "unknown",
        "opened_lesson_topic": "unknown",
        "effective_topic": "unknown",
        "status": _STATUS_READY,
        "topic_context_used": False,
        "video_flow_used": False,
        "topic_conflict": False,
    }


def build_result_mode_prompt(payload: dict) -> dict:
    """Result/Quick mod — potpuno odvojen od razreda/teme/lekcije.

    Izvor istine je tekst/slika učenika. NE ubacuje se topic kontekst, modularna
    pravila ni didaktika razreda; tema ostaje ``None`` (kontekst je isključen).
    """
    payload = payload or {}
    system_prompt = build_result_mode_system_prompt()

    solve_item = normalize_value(payload.get("_result_solve_item"))
    user_parts = []
    if solve_item:
        user_parts.append(
            "ZADATAK SA SLIKE:\n"
            f"- Odgovori SAMO na zadatak broj {solve_item}. Ostale zadatke sa slike "
            "ignoriši. Daj kratak, tačan rezultat tog zadatka."
        )
    else:
        user_parts.append(
            "MOD: SAMO REZULTAT (bez teme i razreda)\n"
            "- Riješi zadatak koji je učenik poslao (tekst ili slika) i daj kratak, "
            "tačan rezultat. Ne traži temu ni razred i ne odbijaj valjan matematički "
            "zadatak zbog razreda/oblasti."
        )
    for block in (
        _build_image_context_block(payload),
        build_mode_instructions("quick", "unknown", {}),
        _build_student_block(payload),
    ):
        if block:
            user_parts.append(block)
    user_prompt = "\n\n".join(p for p in user_parts if p).strip()

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "history_messages": build_history_messages(payload.get("conversation_history")),
        "mode": "quick",
        "final_topic": None,
        "opened_lesson_topic": None,
        "effective_topic": None,
        "status": _STATUS_READY,
        "topic_context_used": False,
        "video_flow_used": False,
        "topic_conflict": False,
        "context_policy": "disabled_for_result_mode",
    }


def build_exam_oblast_prompt(payload: dict, master_content: dict) -> dict | None:
    """Phase 7 — priprema kontrolnog za CIJELU OBLAST (bez pojedinačne teme).

    Vraća ready prompt SAMO kada su ispunjeni svi uslovi: ``mode == exam``,
    ``selected_topic`` prazan, a ``selected_oblast`` postoji u masteru
    (case-insensitive). U svakom drugom slučaju vraća ``None`` i pozivalac
    nastavlja postojeći tok (topic-based exam ostaje netaknut).

    Kontekst su ISKLJUČIVO READY teme te oblasti iz mastera (display_name +
    ``controlni_*`` materijal) — teme se nikad ne izmišljaju. ``final_topic``
    ostaje ``"unknown"`` (pravilo 10: non-unknown final_topic mora postojati u
    TOPICS), a oblast se vraća kroz dodatni ključ ``exam_oblast``."""
    payload = payload or {}
    if normalize_mode(payload.get("mode")) != "exam":
        return None
    if normalize_value(payload.get("selected_topic")):
        return None
    oblast = normalize_value(payload.get("selected_oblast"))
    if not oblast:
        return None
    rows = get_oblast_topics(oblast, master_content or {})
    if not rows:
        return None
    canonical = normalize_value(rows[0].get("oblast")) or oblast

    # oblast (npr. "Osnovne geometrijske konstrukcije...") može tražiti
    # konstrukcijski blok i bez pojedinačne teme
    system_prompt = _compose_system_prompt(
        payload.get("grade"), topic_context={"oblast": canonical}
    )

    lines = [
        f"OBLAST KONTROLNOG: {canonical}",
        "TEME I KONTROLNI MATERIJAL OVE OBLASTI (iz AI_MATH_CONTENT_MASTER):",
    ]
    for r in rows:
        lines.append(f"- Tema: {r.get('display_name') or r['topic']}")
        for key, label in (
            ("controlni_task_1", "Kontrolni zadatak"),
            ("controlni_task_2", "Kontrolni zadatak"),
            ("controlni_task_3", "Kontrolni zadatak"),
            ("controlni_trick", "Trik"),
            ("controlni_warning", "Upozorenje"),
        ):
            val = normalize_value(r.get(key))
            if val:
                lines.append(f"   • {label}: {val}")
    oblast_block = "\n".join(lines)

    mode_block = (
        "MOD: KONTROLNI IZ OBLASTI (exam)\n"
        "- Učenik sutra ima kontrolni iz CIJELE OBLASTI, ne iz jedne lekcije.\n"
        "- Daj TAČNO 3 zadatka u stilu kontrolnog, IZBALANSIRANO iz RAZLIČITIH "
        "tema ove oblasti (koristi ponuđene kontrolne zadatke kao uzor).\n"
        "- Zatim navedi TAČNO 1 čest trik i TAČNO 1 upozorenje iz ponuđenog "
        "materijala.\n"
        "- Format: zadaci kao numerisana lista 1., 2., 3., zatim red "
        "\"Trik:\" i red \"Upozorenje:\".\n"
        "- Koristi ISKLJUČIVO teme i materijal iz bloka iznad — NE izmišljaj "
        "teme ni sadržaj.\n"
        "- Ako je naveden spisak NEDAVNO DATIH ZADATAKA, novi zadaci moraju "
        "biti različiti od njih (drugi brojevi i kontekst).\n"
    )
    exam_phase = normalize_value(payload.get("interaction_phase")).lower()
    # Phase 7.2: "može"/"nastavi" usred exam sesije → nastavak od zadnje poruke,
    # ne ponovni ispis 3 zadatka (materijal oblasti ostaje kao kontekst).
    if exam_phase == "continuing_explanation":
        mode_block = build_continuation_instructions(payload)

    user_parts = [
        _build_entry_context(payload, "unknown", "exam"),
        oblast_block,
        _build_image_context_block(payload),
        _build_recent_tasks_block(payload, "exam", exam_phase),
        mode_block,
        _build_student_block(payload),
    ]
    user_prompt = "\n\n".join(p for p in user_parts if p).strip()

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "history_messages": build_history_messages(payload.get("conversation_history")),
        "mode": "exam",
        "final_topic": "unknown",
        "opened_lesson_topic": "unknown",
        "effective_topic": "unknown",
        "status": _STATUS_READY,
        "topic_context_used": False,
        "video_flow_used": False,
        "topic_conflict": False,
        "oblast_context_used": True,
        "exam_oblast": canonical,
    }


def build_fallback_prompt(payload: dict, reason: Any) -> dict:
    """Prompt kada tema NIJE upotrebljiva. ``reason``:
    ``unknown`` → status ``fallback``; ``ambiguous`` → ``ambiguous``;
    ``invalid`` → ``invalid``. Nikad ne izmišlja temu."""
    payload = payload or {}
    reason = normalize_value(reason).lower() or "unknown"
    status = _FALLBACK_STATUS.get(reason, "fallback")
    mode = normalize_mode(payload.get("mode"))

    system_prompt = _compose_system_prompt(payload.get("grade"))

    if reason == "ambiguous":
        ask = (
            "Pronašao sam više sličnih lekcija i ne mogu sa sigurnošću odabrati temu.\n"
            "Zamoli učenika da izabere OBLAST/TEMU koju TRENUTNO radi (ručni izbor)."
        )
    elif reason == "invalid":
        ask = (
            "Tražena tema nije prepoznata kao validna tema iz mastera.\n"
            "NE izmišljaj temu. Zamoli učenika da izabere postojeću oblast/temu ili "
            "pošalje zadatak."
        )
    elif mode == "exam":
        ask = (
            "Tema kontrolnog nije poznata. Pitaj učenika iz koje je OBLASTI/TEME "
            "kontrolni (npr. skupovi, djeljivost, razlomci, decimalni brojevi, "
            "kružnica/uglovi). NE pretpostavljaj temu."
        )
    else:  # unknown
        ask = (
            "Ne mogu automatski prepoznati lekciju/temu.\n"
            "Zamoli učenika da izabere oblast iz liste ili pošalje zadatak, pa "
            "pomozi korak po korak."
        )

    user_prompt = "\n\n".join(
        [_build_entry_context(payload, "unknown", mode), "FALLBACK:\n" + ask]
    ).strip()

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "history_messages": [],   # ne-ready ne zove model; ključ postoji radi oblika
        "mode": mode,
        "final_topic": "unknown",
        "opened_lesson_topic": "unknown",
        "effective_topic": "unknown",
        "status": status,
        "topic_context_used": False,
        "video_flow_used": False,
        "topic_conflict": False,
    }
