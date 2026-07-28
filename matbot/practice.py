"""Orkestracija JEDNOG Practice turna: stanje → prompt → jedan AI poziv →
validacija → sigurna primjena rezultata → response u frontend ugovoru.

Dvije vrste interakcije (turn["interaction_type"]):
- "choice_answer": učenik je kliknuo jednu od 4 ponuđene opcije aktivnog
  zadatka. Server DETERMINISTIČKI utvrđuje je li izbor tačan (poređenje sa
  session["correct_option_id"]) — model se u istom pozivu koristi SAMO da
  napiše feedback/hint/objašnjenje dosljedan tom već utvrđenom verdiktu.
- bilo šta drugo ("student_question" ili prazno): tekstualna poruka. NIKAD se
  ne tretira kao pokušaj odgovora — 'evaluation' koji model eventualno vrati
  se ignoriše, answer_verdict u odgovoru je uvijek None, correct_streak se ne
  dira. Grading ide isključivo kroz choice_answer granu.

Pravila primjene (server, ne model):
- aktivni zadatak (tekst + 4 opcije) mijenja se ISKLJUČIVO iz new_task
  (bootstrap / "novi zadatak" / lakši / teži) — server tada i miješa
  redoslijed opcija (shuffle) tačno jednom i pamti stvarni correct_option_id.
- gave_hint → hint_level + 1 (cap), zadatak ostaje
- choice_answer: tačan klik → correct_streak + 1, zadatak završen; prvi
  pogrešan klik → zadatak ostaje aktivan, tačna opcija se NE otkriva; drugi
  pogrešan klik → zadatak završen i tačna opcija se otkriva
  (revealed_correct_option_id u responseu)
- greška AI poziva ili nevalidan output → NULA promjena stanja, kratka sigurna
  poruka BEZ 'status' i BEZ 'next_state' (frontend tada čuva svoje stanje).
"""
import copy
import logging
import random
import uuid

from matbot import config, prompts
from matbot.llm import LLMError
from matbot.mathsafe import sanitize_and_validate_math_text, sanitize_math_text
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
    # Samo sigurni UI podaci. NIKAD expected_answer_summary, correct_option_id
    # ni interna polja (wrong_option_ids, task_completed su server-only).
    state = {
        "v": 1,
        "correct_streak": session["correct_streak"],
        "hint_level": session["hint_level"],
    }
    if session["current_task"]:
        task = {"question": session["current_task"]}
        if session["current_options"]:
            task["options"] = session["current_options"]  # [{"id","text"}] — bez correct
        state["task"] = task
    return state


def _shuffle_options(texts, correct_index):
    """texts: 4 već sanitizovana teksta opcija. Vraća (current_options
    browser-safe [{"id","text"}] u NOVOM redoslijedu, correct_option_id).
    Shuffle se izvodi TAČNO JEDNOM po novom zadatku — rezultat se sprema u
    sesiju i nikad se ne ponavlja za isti zadatak (drugi pokušaj/retry ne
    smije ponovo promiješati opcije)."""
    ids = ["a", "b", "c", "d"]
    pairs = list(enumerate(texts))
    random.shuffle(pairs)
    current_options = []
    correct_option_id = ""
    for slot, (orig_index, text) in enumerate(pairs):
        option_id = ids[slot]
        current_options.append({"id": option_id, "text": text})
        if orig_index == correct_index:
            correct_option_id = option_id
    return current_options, correct_option_id


def _apply_new_task(session, new_task):
    """Sanitizuje tekst zadatka i sve 4 opcije, promiješa opcije i primjenjuje
    svježe stanje na sesiju (server je jedini koji dodjeljuje ID-jeve opcijama
    i pamti koji je tačan). Vraća sanitizovan tekst zadatka.

    Svaki dio (pitanje, svaka opcija) prolazi kroz
    sanitize_and_validate_math_text — ako BILO KOJI dio ostane nebezbjedan
    (sirov \\frac/\\sqrt/\\text/\\cdot/\\begin/\\end izvan $...$, vidljiv "\\n",
    zabranjen kontrolni znak ili prepoznat oštećen LaTeX oblik i nakon
    pokušaja sigurne reparacije), CIO zadatak se odbija — baca se
    InvalidOutputError koju pozivalac (run_practice_turn) već hvata i vraća
    postojeći sigurni fallback, BEZ mutacije sesije i BEZ drugog AI poziva.
    """
    task_text, task_safe = sanitize_and_validate_math_text(new_task.text.strip())
    if not task_safe:
        raise InvalidOutputError("nebezbjedan matematički zapis u tekstu zadatka")

    sanitized_texts = []
    for opt in new_task.options:
        opt_text, opt_safe = sanitize_and_validate_math_text(
            opt.text.strip(), allow_whole_expression_wrap=True
        )
        if not opt_safe:
            raise InvalidOutputError("nebezbjedan matematički zapis u opciji zadatka")
        sanitized_texts.append(opt_text)

    current_options, correct_option_id = _shuffle_options(sanitized_texts, new_task.correct_option_index)

    session["current_task"] = task_text
    session["expected_answer_summary"] = new_task.expected_answer.strip()
    session["difficulty"] = new_task.difficulty
    session["hint_level"] = 0
    session["recent_tasks"].append(task_text)
    session["current_options"] = current_options
    session["correct_option_id"] = correct_option_id
    session["wrong_option_ids"] = []
    session["task_completed"] = False
    session["last_choice_turn_id"] = ""
    session["last_choice_response"] = None
    return task_text


def run_practice_turn(store, llm, turn):
    """turn: očišćeni dict iz api.py (session_id, grade, selected_topic,
    selected_oblast, student_message, intent, difficulty_request,
    interaction_phase, last_tutor_task, interaction_type, selected_option_id,
    client_turn_id). Vraća JSON-spreman dict."""
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

    if turn.get("interaction_type") == "choice_answer":
        return _handle_choice_answer(store, llm, session, turn, lesson_id, request_id)
    return _handle_text_turn(store, llm, session, turn, lesson_id, request_id)


def _handle_text_turn(store, llm, session, turn, lesson_id, request_id):
    # Server je izvor istine za zadatak; klijentski last_tutor_task koristimo
    # SAMO da preživimo restart servera (tekst zadatka nije interno rješenje —
    # expected_answer_summary/opcije ostaju prazni i model tada sam pravi novi
    # zadatak s opcijama). Sanitizovan ISTO kao svaki tekst iz modela.
    if not session["current_task"] and turn["last_tutor_task"]:
        session["current_task"] = sanitize_math_text(
            turn["last_tutor_task"][: config.MAX_TASK_CHARS]
        )

    # Snimljeno PRIJE AI poziva: ako bilo šta ispod baci grešku, ovo je jedina
    # istina koju smijemo vratiti — session je lokalna kopija i NIKAD se ne
    # commituje (store.save) osim na uspješnom kraju ove funkcije.
    active_task_before_llm = session["current_task"]

    instructions = prompts.build_instructions(
        turn["grade"], lesson_title=session["lesson_title"], oblast=session["oblast"]
    )
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

        # NAPOMENA: out.evaluation se OVDJE NIKAD ne koristi. Tekstualna poruka
        # (pitanje, "ne znam", "uradi ga ti", ...) nije pokušaj odgovora —
        # ocjenjivanje ide ISKLJUČIVO kroz _handle_choice_answer. correct_streak
        # se ovdje ne dira.
        if out.gave_hint and out.new_task is None:
            session["hint_level"] = min(session["hint_level"] + 1, config.MAX_HINT_LEVEL)

        task_text = active_task_before_llm
        if out.new_task is not None:
            task_text = _apply_new_task(session, out.new_task)

        # vidljivi odgovor: reply + (novi zadatak, ako postoji i nije već u replyju)
        reply, reply_safe = sanitize_and_validate_math_text(out.reply.strip())
        if not reply_safe:
            raise InvalidOutputError("nebezbjedan matematički zapis u odgovoru")
        if out.new_task is not None and task_text not in reply:
            answer = reply + "\n\nZadatak: " + task_text
        else:
            answer = reply

        session["recent_turns"].append(
            {"student": turn["student_message"][:300], "tutor": answer[:400]}
        )

        response = {
            "status": "ready",
            "answer": answer,
            "answer_verdict": None,          # tekst se nikad ne ocjenjuje
            "last_tutor_task": session["current_task"] or "",
            "next_state": _next_state(session),
            "session_mode": "practice",
            "effective_topic": lesson_id or "",
        }

        # "Uradi ga ti" (intent="solution_request", isti mehanizam kao postojeći
        # hint_request chip): model je upravo dao puni postupak u 'reply' —
        # server sad deterministički završava zadatak i otkriva tačnu opciju.
        # Nije pogrešan klik: wrong_option_ids/correct_streak se ne diraju.
        if turn["intent"] == "solution_request" and session["correct_option_id"]:
            session["task_completed"] = True
            response["revealed_correct_option_id"] = session["correct_option_id"]

        store.save(session)  # JEDINA commit tačka u cijeloj funkciji

        logger.info(
            "practice_turn request_id=%s ok latency_ms=%s usage=%s",
            request_id, result.latency_ms, result.usage,
        )
        return response
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


def _handle_choice_answer(store, llm, session, turn, lesson_id, request_id):
    active_task_before = session["current_task"]

    # Nema aktivnog MC zadatka (npr. sesija istekla/restart bez last_tutor_task
    # obnove) → deterministički odbij, bez AI poziva, bez promjene stanja.
    if not session["current_options"] or not session["correct_option_id"]:
        return _error_response(active_task_before)

    client_turn_id = turn.get("client_turn_id") or ""

    # Idempotentan retry: ISTI client_turn_id kao zadnji OBRAĐENI choice_answer
    # → vrati identičan (keširan) odgovor, BEZ novog AI poziva i BEZ mutacije
    # stanja (mreža/dupli tab retry ne smije duplo brojati pokušaj).
    if client_turn_id and client_turn_id == session["last_choice_turn_id"] \
            and session["last_choice_response"] is not None:
        return copy.deepcopy(session["last_choice_response"])

    # Zadatak je već završen (tačan klik / drugi pogrešan / "uradi ga ti") —
    # svaki NOVI (ne-idempotentan) klik na završen zadatak je nevažeći: bez AI
    # poziva, bez promjene stanja, bez otkrivanja bilo čega dodatnog.
    if session["task_completed"]:
        return _error_response(active_task_before)

    selected_option_id = turn.get("selected_option_id") or ""
    options_by_id = {opt["id"]: opt for opt in session["current_options"]}
    if selected_option_id not in options_by_id:
        # Nevažeći ID za aktivni zadatak: odbij deterministički, NIKAD ne
        # pozivaj model, NIKAD ne mijenjaj stanje, NIKAD ne otkrivaj tačan odgovor.
        return _error_response(active_task_before)

    selected_text = options_by_id[selected_option_id]["text"]
    is_correct = (selected_option_id == session["correct_option_id"])
    wrong_attempts_before = len(session["wrong_option_ids"])

    if is_correct:
        session["correct_streak"] += 1
        session["task_completed"] = True
    else:
        session["correct_streak"] = 0
        session["wrong_option_ids"].append(selected_option_id)
        if wrong_attempts_before >= 1:
            session["task_completed"] = True  # drugi pogrešan klik → kraj zadatka

    instructions = prompts.build_instructions(turn["grade"])
    input_text = prompts.build_input(
        session,
        student_message=turn["student_message"],
        intent=turn["intent"],
        difficulty_request=turn["difficulty_request"],
        interaction_phase=turn["interaction_phase"],
        trusted_choice_verdict={
            "selected_text": selected_text,
            "is_correct": is_correct,
            "wrong_attempts": wrong_attempts_before,
        },
    )

    try:
        result = llm.practice_turn(instructions, input_text)
        validate_output(result.output)
        out = result.output

        # Server verdikt UVIJEK ima prednost. Model smije samo objašnjavati —
        # ako suprotstavi ('evaluation' kontradiktoran is_correct, ili vrati
        # new_task na klik), to se ignoriše i bilježi (bez sirovog teksta), bez
        # drugog/repair poziva.
        if out.new_task is not None:
            logger.warning("practice_choice request_id=%s unexpected_new_task_ignored", request_id)
        expected_word = "correct" if is_correct else "incorrect"
        if out.evaluation is not None and out.evaluation != expected_word:
            logger.warning("practice_choice request_id=%s verdict_mismatch", request_id)

        reply, reply_safe = sanitize_and_validate_math_text(out.reply.strip())
        if not reply_safe:
            raise InvalidOutputError("nebezbjedan matematički zapis u odgovoru")

        session["recent_turns"].append({
            "student": f"[izabrao opciju: {selected_text}]"[:300],
            "tutor": reply[:400],
        })

        response = {
            "status": "ready",
            "answer": reply,
            "answer_verdict": "correct" if is_correct else "incorrect",  # server-truth
            "last_tutor_task": session["current_task"] or "",
            "next_state": _next_state(session),
            "session_mode": "practice",
            "effective_topic": lesson_id or "",
        }
        # Otkrivanje tačne opcije SAMO na drugi pogrešan klik — nikad na prvi,
        # nikad na tačan klik (tamo je kliknuta opcija već poznato tačna).
        if not is_correct and wrong_attempts_before >= 1:
            response["revealed_correct_option_id"] = session["correct_option_id"]

        if client_turn_id:
            session["last_choice_turn_id"] = client_turn_id
            session["last_choice_response"] = copy.deepcopy(response)

        store.save(session)  # JEDINA commit tačka u cijeloj funkciji

        logger.info(
            "practice_choice request_id=%s ok is_correct=%s wrong_attempts=%s latency_ms=%s usage=%s",
            request_id, is_correct, wrong_attempts_before, result.latency_ms, result.usage,
        )
        return response
    except LLMError as e:
        logger.warning("practice_choice request_id=%s category=%s", request_id, e.category)
        return _error_response(active_task_before)
    except InvalidOutputError as e:
        logger.warning("practice_choice request_id=%s category=invalid_output detail=%s", request_id, e)
        return _error_response(active_task_before)
    except Exception:
        logger.exception("practice_choice request_id=%s unexpected_error", request_id)
        return _error_response(active_task_before)
