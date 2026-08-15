"""Ulaz u Vježbajmo — TANKA granica prema JEDINOM Practice motoru.

Šta je ovdje nekad živjelo, i zašto više ne živi:

Do povlačenja (2026-08-14) ovaj fajl je nosio CIJELI stari jednopozivni
Practice motor (orkestracija turna, K1/K3 ugovorni generator, legacy porodice,
ljestvica nagovještaja 1→2→3, vlastita tabela uvoda) i `run_practice_turn` je
biralo između njega i univerzalnog puta prema `MATBOT_PRACTICE_PIPELINE`.

Taj izbor je uklonjen ZAJEDNO s motorom. Vježbajmo od sada ima TAČNO JEDAN
podržan izvršni put — `matbot/tutor/` — koji sam bira između:

  • determinističke strategije (`matbot/deterministic/`) — NULA modelskih
    poziva za lekcije čija porodica ima potpun serverski generator;
  • modelske Tutor+Recenzent rute — najviše DVA poziva, nikad tri.

ZAŠTO NEMA VIŠE ZASTAVICE: dok je izbor postojao, zaostala vrijednost u
produkcijskom `.env`-u je mogla tiho vratiti nepodržan motor. Sada ne postoji
ni grana ni zastavica koju bi takva vrijednost mogla oživjeti — stari motor
nije isključen, nego ga nema.

ROLLBACK je od sada Git historija (poznat dobar commit), ne konfiguracija.
Vidi docs/ARCHITECTURE.md.
"""
from matbot.tutor import pipeline as tutor_pipeline
# Jedan tekst, jedno mjesto: granica i motor ne smiju imati dvije verzije iste
# poruke. `explain.py`, `quick.py` i evaluacijski alat je uvoze odavde.
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE  # noqa: F401  (javni re-export)

# ISKRENA PORUKA ZA LEKCIJU BEZ PROVJERLJIVOG ZADATKA — jasna i drukčija od
# privremene greške, i vraća se BEZ ijednog AI poziva.
#
# AKTIVNI put je trenutno NE emituje (padanje zatvoreno ide kroz
# SAFE_ERROR_MESSAGE). Ostaje jer je `tools/practice_eval/checks.py` drži u
# spisku serverskih „canned" tekstova: kad bi se ikad ponovo pojavila na turnu
# koji je trebao dati zadatak, evaluacija to mora prijaviti kao tehnički
# fallback predstavljen kao uspjeh — a ne prešutjeti.
PRACTICE_UNAVAILABLE_MESSAGE = (
    "Za ovu lekciju vježba trenutno nije dostupna. Izaberi drugu lekciju iz iste "
    "oblasti ili pređi na „Objasni mi“."
)


def run_practice_turn(store, llm, turn):
    """turn: očišćeni dict iz api.py (session_id, grade, selected_topic,
    selected_oblast, student_message, intent, difficulty_request,
    interaction_phase, last_tutor_task, interaction_type, selected_option_id,
    client_turn_id). Vraća JSON-spreman dict.

    Bez grananja: postoji tačno jedan podržan motor. Izbor determinističke
    naspram modelske strategije se donosi UNUTAR njega, iz serverskih
    činjenica (porodica lekcije, UI polja, zatvoren skup poruka) — nikad iz
    modelove proze i nikad po ID-ju lekcije."""
    return tutor_pipeline.run_turn(store, llm, turn)
