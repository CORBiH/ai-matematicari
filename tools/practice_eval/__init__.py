"""Live dijagnostika moda „Vježbaj sa mnom“ (FAZA 1).

Ovaj paket NIJE dio aplikacije i aplikacija ga nikad ne uvozi. On pokreće
STVARNI produkcijski put zahtjeva (Flask ruta → guard chain → run_practice_turn
→ pravi OpenAIPracticeLLM) nad unaprijed napisanim scenarijima i ocjenjuje
rezultat determinističkim provjerama koje već postoje u `matbot/`.

Nijedan modul ovdje ne smije mijenjati ponašanje MAT-BOT-a, oslabiti live
release gate (`tools/run_live_release_gate.py`) niti čitati `.env`.
"""
