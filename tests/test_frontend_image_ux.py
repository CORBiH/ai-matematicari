"""Frontend ugovor za prilog slike (Rezultat mod) — statička provjera.

templates/index.html se ne izvršava u pytest-u (nema browsera/DOM-a u ovom
repou), pa je ovo, kao i tests/test_frontend_retry_ux.py, provjera da ključni
mehanizam POSTOJI i da je ispravno ožičen — štiti od slučajnog brisanja pri
budućim izmjenama.
"""
import re
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "templates" / "index.html"


def _read():
    return INDEX_HTML.read_text(encoding="utf-8")


def _function_body(html, header, length=3000):
    start = html.index(header)
    return html[start:start + length]


def _send_tutor_msg(html):
    """Cijelo tijelo sendTutorMsg (do sljedeće funkcije), bez pogađanja dužine."""
    start = html.index("async function sendTutorMsg")
    end = html.index("async function retryNewTaskRequest")
    return html[start:end]


# --- 1-3: transport ---------------------------------------------------------


def test_case1_text_only_result_keeps_current_streaming_transport():
    html = _read()
    body = _send_tutor_msg(html)
    assert "if (!imgFile){" in body
    assert "streamTutorRequest(payload, ac)" in body


def test_case2_image_turn_uses_one_multipart_request():
    html = _read()
    body = _function_body(html, "async function jsonTutorRequest")
    assert "new FormData()" in body
    assert body.count("fetch('/api/ai-tutor/chat'") == 2      # multipart + JSON grana
    assert "fd.append('image', imgFile, imgFile.name)" in body
    assert body.count("fd.append('image'") == 1               # tačno jedno file polje


def test_case3_no_sse_to_json_fallback_for_image_turns():
    html = _read()
    body = _send_tutor_msg(html)
    stream_call = body.index("streamTutorRequest(payload, ac)")
    guard = body.index("if (!imgFile){")
    assert guard < stream_call            # SSE se ni ne pokušava kad ima slike
    assert "TRANSPORT ZA SLIKU" in body   # namjera dokumentovana uz kod


def test_multipart_preserves_all_validated_fields():
    html = _read()
    body = _send_tutor_msg(html)
    for field in ("session_id:", "client_turn_id:", "grade:", "mode:",
                  "selected_topic:", "selected_oblast:", "student_message:",
                  "conversation_history:"):
        assert field in body
    assert "fd.append('payload', JSON.stringify(payload))" in _read()


# --- 4-5: šta se smije poslati ---------------------------------------------


def test_case4_image_only_submission_allowed():
    html = _read()
    body = _send_tutor_msg(html)
    # grana "nema teksta, ima slike" postoji i NE izmišlja poruku u ime učenika
    assert "} else if (imgFile){" in body
    assert "text = '';" in body


def test_case5_text_plus_image_submission_allowed():
    html = _read()
    body = _send_tutor_msg(html)
    assert "const imgFile = tutorImageFile();" in body
    assert "jsonTutorRequest(payload, ac, imgFile)" in body


# --- 6-9: životni ciklus priloga -------------------------------------------


def test_case6_attachment_cleared_only_after_successful_response():
    html = _read()
    # Prozor prati rast funkcije: Faza 4F je u applyTutorResponse dodala
    # provjeru generacije zahtjeva i kapiju identiteta zadatka. Provjeravana
    # invarijanta je nepromijenjena — čišćenje priloga je i dalje UNUTAR grane
    # uspjeha; mijenja se samo koliko teksta test mora obuhvatiti.
    body = _function_body(html, "function applyTutorResponse", 8000)
    clear_at = body.index("clearTutorImage();")
    ready_at = body.index("if (j.status === 'ready'){")
    assert ready_at < clear_at            # čišćenje je UNUTAR grane uspjeha


def test_case7_attachment_survives_client_and_server_validation_errors():
    html = _read()
    # jedini pozivi clearTutorImage su: uspjeh, ručno uklanjanje, reset
    # razgovora i odbijen izbor fajla — nikad na grešku odgovora servera
    body = _send_tutor_msg(html)
    error_branch = body[body.index("}catch(err){"):]
    assert "clearTutorImage" not in error_branch
    assert "413" in html                  # 413 je poznata blokada, bez retryja


def test_case8_remove_button_revokes_preview_url():
    html = _read()
    assert "URL.revokeObjectURL(tutorImageObjectUrl)" in html
    assert "tutorImgRemove.addEventListener('click', clearTutorImage)" in html
    clear_fn = _function_body(html, "function clearTutorImage(){", 600)
    assert "revokeTutorImagePreview();" in clear_fn
    # novi izbor slike takođe pušta prethodni URL
    show_fn = _function_body(html, "function showTutorImageChip(f){", 700)
    assert "revokeTutorImagePreview();" in show_fn
    assert "window.addEventListener('pagehide', revokeTutorImagePreview)" in html


def test_case9_duplicate_send_is_blocked():
    html = _read()
    body = _function_body(html, "async function sendTutorMsg", 600)
    assert "if (tutorBusy) return;" in body
    busy = _function_body(html, "function setTutorBusy(on){", 700)
    assert "sendTutor.disabled = on;" in busy
    assert "tutorImg.disabled = on" in busy
    assert "tutorCameraImg.disabled = on" in busy


# --- format, pristupačnost, privatnost --------------------------------------


def test_only_jpeg_png_webp_offered_and_camera_capture_kept():
    html = _read()
    assert html.count('accept="image/jpeg,image/png,image/webp"') == 2
    assert 'capture="environment"' in html
    assert 'accept="image/*"' not in html


def test_client_side_checks_are_ux_only_and_backend_stays_authority():
    html = _read()
    fn = _function_body(html, "function handleTutorImageChange(input){", 1600)
    # prazan/nepoznat File.type ne blokira slanje sam po sebi
    assert "const typeKnown = !!(f.type && f.type.indexOf('image/') === 0);" in fn
    assert "TUTOR_IMAGE_MAX_BYTES" in fn
    assert "Jedini autoritet je backend" in html


def test_chip_shows_thumbnail_name_and_is_accessible():
    html = _read()
    chip = html[html.index('id="tutorImageChip"'):html.index('id="tutorImageChip"') + 500]
    assert 'role="status"' in chip and 'aria-live="polite"' in chip
    assert 'id="tutorImageThumb"' in chip
    assert 'aria-label="Ukloni priloženu sliku"' in chip
    assert "'1 slika priložena: '" in html      # jasno je da je priložena JEDNA slika


def test_no_image_data_in_history_or_localstorage():
    html = _read()
    assert "IMAGE_HISTORY_MARKER = '[U ovom turnu je bila priložena slika.]'" in html
    assert "pushTutor('user', imgFile ? ((text ? text + ' ' : '') + IMAGE_HISTORY_MARKER) : text)" in html
    # nema base64/data URL/FileReader/object URL-a u pohrani
    assert "readAsDataURL" not in html
    assert "setLastImageContext" not in html
    assert "last_image_context" not in html
    for match in re.finditer(r"localStorage\.setItem\(([^,]+),", html):
        assert "IMG" not in match.group(1).upper() or "IMAGE" not in match.group(1).upper()
    assert "localStorage.setItem(LASTIMAGECTX_KEY" not in html


def test_friendly_localized_errors_for_unsupported_and_oversized():
    html = _read()
    assert "Podržane su JPG, PNG i WebP slike." in html
    assert "Slika je prevelika (najviše 8 MB)" in html
