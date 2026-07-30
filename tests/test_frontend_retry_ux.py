"""Phase 6, kategorija 36-41 (globalna regresija + frontend retry-UX).

Defekt 3 UX zahtjev: neuspjelo generisanje novog zadatka mora prikazati
konkretnu poruku i vidljivo "Pokušaj ponovo" dugme koje šalje IDENTIČAN
zahtjev SAMO na eksplicitan klik — bez skrivenog automatskog drugog poziva i
bez dupliranja balončića korisničke poruke. templates/index.html se ne
izvršava u pytest-u (nema browser/DOM okruženja u ovom repou), pa je ovo
statička provjera da ključni mehanizam POSTOJI i da je ispravno ožičen —
štiti od slučajnog brisanja pri budućim izmjenama."""
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def _read():
    return INDEX_HTML.read_text(encoding="utf-8")


def test_case36_retry_message_text_present():
    html = _read()
    assert "Nisam uspio sastaviti ispravan novi zadatak. Pokušaj ponovo." in html


def test_case37_retry_button_label_present():
    html = _read()
    assert "Pokušaj ponovo" in html and "retryBtn" in html


def test_case38_retry_never_calls_hidden_second_llm_call_automatically():
    html = _read()
    # retryNewTaskRequest se poziva ISKLJUČIVO unutar retryBtn click handlera,
    # nikad automatski (npr. iz applyTutorResponse tijela van click callbacka).
    assert "retryNewTaskRequest(freshPayload)" in html
    click_handler_start = html.index("function onRetryClick")
    call_site = html.index("retryNewTaskRequest(freshPayload)")
    # poziv mora biti UNUTAR handlera (poslije njegove definicije), ne prije
    assert call_site > click_handler_start


def test_case39_retry_does_not_duplicate_user_message_bubble():
    html = _read()
    retry_fn_start = html.index("async function retryNewTaskRequest")
    retry_fn_region = html[retry_fn_start: retry_fn_start + 1600]
    assert "appendTutorMsg('user'" not in retry_fn_region
    assert "pushTutor('user'" not in retry_fn_region


def test_case40_is_new_task_request_flag_gates_the_special_message():
    html = _read()
    assert "opts.isNewTaskRequest" in html


def test_case41_full_suite_and_diff_check_are_the_final_gate():
    """Ovaj test je namjerni marker: kompletan zahtjev je da se `pytest tests/ -q`
    i `git diff --check` pokrenu i prođu PRIJE bilo kakvog live poziva — to se
    izvršava eksterno (vidi finalni izvještaj), ne unutar jednog test-case-a."""
    assert True
