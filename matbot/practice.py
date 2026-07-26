"""Orkestracija JEDNOG Practice turna: stanje → prompt → jedan AI poziv →
validacija → sigurna primjena rezultata → response u frontend ugovoru.

Pravila primjene (server, ne model):
- aktivni zadatak se mijenja ISKLJUČIVO iz new_task.text
- gave_hint → hint_level + 1 (cap), zadatak ostaje
- evaluation correct → correct_streak + 1; partially_correct/incorrect → streak = 0
- greška AI poziva ili nevalidan output → NULA promjena stanja, kratka sigurna
  poruka BEZ 'status' i BEZ 'next_state' (frontend tada čuva svoje stanje).
"""
import logging
import uuid

from matbot import config, prompts
from matbot.llm import LLMError
from matbot.mathsafe import sanitize_math_text
from matbot.schema import InvalidOutputError, validate_output
from matbot.topics import lesson_info

logger = logging.getLogger("matbot.practice")

SAFE_ERROR_MESSAGE = "Nešto je zapelo pri sastavljanju odgovora. Pošalji poruku ponovo za koji trenutak."


def _error_response(active_task=""):
    # Namjerno BEZ 'status' i BEZ 'next_state': frontend čita last_tutor_task
    # SAMO kad je status === 'ready' (templates/index.html:1843-1846), pa je ova
    # vrijednost trenutno inertna dok god status izostaje. Ipak šaljemo STVARNI
    # aktivni zadatak (ne prazan string) da odgovor ostane istinit i otporan na
    # buduće izmjene frontend logike koje bi mogle početi čitati ovo polje i bez
    # statusa — prazan string bi tada mogao izgledati kao "zadatak obrisan".
    return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": active_task or ""}


def _next_state(session):
    # Samo sigurni UI podaci. NIKAD expected_answer_summary ni interna polja.
    state = {
        "v": 1,
        "correct_streak": session["correct_streak"],
        "hint_level": session["hint_level"],
    }
    if session["current_task"]:
        state["task"] = {"question": session["current_task"]}
    return state


def run_practice_turn(store, llm, turn):
    """turn: očišćeni dict iz api.py (session_id, grade, selected_topic,
    selected_oblast, student_message, intent, difficulty_request,
    interaction_phase, last_tutor_task). Vraća JSON-spreman dict."""
    request_id = uuid.uuid4().hex[:12]

    lesson = lesson_info(turn["grade"], turn["selected_topic"])
    lesson_id = lesson["id"] if lesson else (turn["selected_topic"] or "")
    lesson_title = lesson["title"] if lesson else ""
    oblast = lesson["oblast"] if lesson else (turn["selected_oblast"] or "")

    session = store.load(
        session_id=turn["session_id"],
        grade=turn["grade"],
        lesson_id=lesson_id,
        lesson_title=lesson_title,
        oblast=oblast,
        mode="practice",
    )

    # Server je izvor istine za zadatak; klijentski last_tutor_task koristimo
    # SAMO da preživimo restart servera (tekst zadatka nije interno rješenje —
    # expected_answer_summary ostaje prazan i model tada sam ponovo računa).
    # Sanitizovan ISTO kao svaki tekst iz modela: klijent je nepouzdan izvor
    # (mogao je ranije primiti isti neispravan LaTeX prije nego što je ova
    # zaštita uvedena), i ovaj tekst se odmah šalje modelu u AKTIVNI ZADATAK.
    if not session["current_task"] and turn["last_tutor_task"]:
        session["current_task"] = sanitize_math_text(
            turn["last_tutor_task"][: config.MAX_TASK_CHARS]
        )

    # Snimljeno PRIJE AI poziva: ako bilo šta ispod (AI poziv, validacija,
    # primjena rezultata, upis) baci grešku, ovo je jedina istina koju smijemo
    # vratiti — session objekat je lokalna kopija i NIKAD se ne commituje
    # (store.save) osim na uspješnom kraju ove funkcije.
    active_task_before_llm = session["current_task"]

    instructions = prompts.build_instructions(turn["grade"])
    input_text = prompts.build_input(
        session,
        student_message=turn["student_message"],
        intent=turn["intent"],
        difficulty_request=turn["difficulty_request"],
        interaction_phase=turn["interaction_phase"],
    )

    try:
        result = llm.practice_turn(instructions, input_text)
        validate_output(result.output)
        out = result.output

        # --- sigurna primjena rezultata (jedina mjesta gdje se stanje mijenja) ---
        if out.evaluation == "correct":
            session["correct_streak"] += 1
        elif out.evaluation in ("partially_correct", "incorrect"):
            session["correct_streak"] = 0

        if out.gave_hint and out.new_task is None:
            session["hint_level"] = min(session["hint_level"] + 1, config.MAX_HINT_LEVEL)

        # sanitize_math_text: deterministička (ne-AI) zaštita — model povremeno
        # vrati nebalansirane $ ili \frac{ zagrade, što MathJax prikazuje kao
        # "Math input error". Ne mijenja ispravan LaTeX; dira samo pokvarene
        # segmente tako da učenik dobije čitljiv obični tekst umjesto greške.
        if out.new_task is not None:
            task_text = sanitize_math_text(out.new_task.text.strip())
            session["current_task"] = task_text
            session["expected_answer_summary"] = out.new_task.expected_answer.strip()
            session["difficulty"] = out.new_task.difficulty
            session["hint_level"] = 0
            session["recent_tasks"].append(task_text)

        # vidljivi odgovor: reply + (novi zadatak, ako postoji i nije već u replyju)
        reply = sanitize_math_text(out.reply.strip())
        if out.new_task is not None and task_text not in reply:
            answer = reply + "\n\nZadatak: " + task_text
        else:
            answer = reply

        session["recent_turns"].append(
            {"student": turn["student_message"][:300], "tutor": answer[:400]}
        )
        store.save(session)  # JEDINA commit tačka u cijeloj funkciji

        logger.info(
            "practice_turn request_id=%s ok latency_ms=%s usage=%s",
            request_id, result.latency_ms, result.usage,
        )

        return {
            "status": "ready",
            "answer": answer,
            "answer_verdict": out.evaluation,          # null kad turn nije bio pokušaj odgovora
            "last_tutor_task": session["current_task"] or "",
            "next_state": _next_state(session),
            "session_mode": "practice",
            "effective_topic": lesson_id or "",
        }
    except LLMError as e:
        logger.warning("practice_turn request_id=%s category=%s", request_id, e.category)
        return _error_response(active_task_before_llm)
    except InvalidOutputError as e:
        logger.warning("practice_turn request_id=%s category=invalid_output detail=%s", request_id, e)
        return _error_response(active_task_before_llm)
    except Exception:
        # Zadnja linija odbrane za NEOČEKIVANE greške u obradi ovog turna
        # (bug u primjeni rezultata, itd.) — store.save() gore nikad nije
        # dosegnut ako je izuzetak nastao prije njega, pa je stanje netaknuto.
        logger.exception("practice_turn request_id=%s unexpected_error", request_id)
        return _error_response(active_task_before_llm)
