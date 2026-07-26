# MAT-BOT

Repo je resetovan na čist frontend (2026-07-26): sav prethodni backend
(matbot/, app.py logika, data/, tests/, scripts/, docs/) je uklonjen jer je
gomilanje modova i pivotova postalo neodrživo. Puna historija je i dalje
dostupna kroz `git log`.

Trenutno stanje:

- `templates/index.html` + `static/` — frontend UI, netaknut
- `app.py` — minimalan Flask stub koji samo servira `index.html`, bez ijedne
  AI-tutor/API rute

Sljedeći korak: napraviti detaljan plan za novi backend prije pisanja koda.
