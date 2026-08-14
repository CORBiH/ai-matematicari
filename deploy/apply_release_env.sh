#!/bin/sh
# Upisi auditiranu produkcijsku konfiguraciju u `.env`. IDEMPOTENTNO.
#
# ZASTO JE OVO SKRIPTA, A NE REDOVI U WORKFLOWU: logika koja odlucuje sta ce
# produkcija izvrsavati mora biti PROVJERLJIVA offline. Dok je stajala kao
# heredoc unutar `ssh ... << EOF`, nijedan test je nije mogao pokrenuti, pa je
# i mogla zaostati za deklaracijom — a upravo je zaostajanje spiska (deploy je
# upisivao dvije vrijednosti, a obavezno ih je bilo pet) uzrok tihog odstupanja
# produkcije od onoga sto kapije mjere.
#
# Ulaz:  $1 = ciljni .env (podrazumijevano ./.env)
#        $2 = deklaracija (podrazumijevano deploy/production_release.env)
#
# GARANCIJE:
#   • postojeci kljuc se ZAMJENJUJE na svom mjestu, novi se DOPISUJE;
#   • ponovljeno pokretanje daje bajt za bajt isti fajl (nema duplikata);
#   • nijedan drugi red `.env`-a se ne dira — tajne (OPENAI_API_KEY,
#     FLASK_SECRET_KEY, ...) ostaju netaknute i NIKAD se ne ispisuju;
#   • ispisuje se samo IME upisanog kljuca, nikad vrijednost.
set -eu

TARGET="${1:-.env}"
SOURCE="${2:-deploy/production_release.env}"

if [ ! -f "$SOURCE" ]; then
  echo "release env: declaration $SOURCE is missing" >&2
  exit 1
fi

[ -f "$TARGET" ] || : > "$TARGET"

# Bez zavrsnog novog reda bi prvi dopisani kljuc zavrsio zalijepljen na
# posljednju postojecu vrijednost (npr. `...secret` + `MATBOT_...`).
if [ -s "$TARGET" ] && [ "$(tail -c 1 "$TARGET" | wc -l)" -eq 0 ]; then
  echo "" >> "$TARGET"
fi

# `|| [ -n "$line" ]` cita i posljednji red bez zavrsnog novog reda.
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|'#'*) continue ;; esac
  key="${line%%=*}"
  if grep -q "^${key}=" "$TARGET"; then
    # Razdvojnik `|` i vrijednosti ogranicene na [A-Za-z0-9_.:+-] (dokazano
    # testom nad deklaracijom) znace da u zamjeni nema `&`, `|` ni `\`, dakle
    # nema sed metaznaka.
    sed -i "s|^${key}=.*|${line}|" "$TARGET"
  else
    printf '%s\n' "$line" >> "$TARGET"
  fi
  echo "release env: ${key} persisted"
done < "$SOURCE"
