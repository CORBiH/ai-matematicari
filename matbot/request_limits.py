"""Tvrda granica HTTP tijela + STVARNO in-memory multipart parsiranje.

ZAŠTO OVAJ MODUL POSTOJI (nalaz audita, ne teorija):

Werkzeug podrazumijevano koristi `default_stream_factory`
(werkzeug/formparser.py), koji za SVAKI uploadovani fajl vraća
`SpooledTemporaryFile(max_size=512000)`. Čim upload pređe ~500 KB, taj spool
se „prelije“ u pravi OS temp fajl — dakle bajtovi slike bi dodirnuli disk
PRIJE nego što `matbot/imageinput.py` uopšte vidi `FileStorage`. Korištenje
`BytesIO` unutar imageinput.py to ne bi spriječilo, jer se dešava prekasno.

Zato ovdje mijenjamo IZVOR streama: `BoundedInMemoryRequest._get_file_stream`
uvijek vraća `io.BytesIO`. Memorija time nije neograničena — gornju granicu
postavlja `MAX_CONTENT_LENGTH` (config.MAX_REQUEST_BYTES), koji Werkzeug
provjerava i preko `Content-Length` headera i tokom samog čitanja toka, pa
prevelik zahtjev završi kao 413 prije nego što se napuni memorija.

Posljedica koju smijemo tvrditi u izvještaju: aplikacija ne kreira nijedan
privremeni fajl za upload — ni vlastiti, ni Werkzeug spool.
"""
import io

from flask import Request

from matbot import config


class BoundedInMemoryRequest(Request):
    """Flask Request koji upload drži isključivo u RAM-u, unutar 9 MiB limita."""

    # Werkzeug (3.1) čita ovo prije parsiranja tijela i baca RequestEntityTooLarge
    # čim `Content-Length` pređe granicu — dakle prije alokacije memorije.
    max_content_length = config.MAX_REQUEST_BYTES

    def _get_file_stream(
        self,
        total_content_length=None,
        content_type=None,
        filename=None,
        content_length=None,
    ):
        # BEZ SpooledTemporaryFile i BEZ TemporaryFile: nijedan bajt uploada ne
        # smije doći do diska. Ograničenje veličine dolazi iz max_content_length.
        return io.BytesIO()
