# Potpisani token za /api/ai-tutor/* (AI_TUTOR_EMBED_SECRET)

## Šta štiti

`POST /api/ai-tutor/chat` i `POST /api/ai-tutor/chat/stream` su skupi pozivi
(OpenAI tokeni). Klijentska "Thinkific gate" provjera (`?from=thinkific&t=...`)
je samo kozmetička — svako ko zna URL može skriptovati API direktno. Token
podiže tu ljestvicu: bez važećeg tokena API odbija zahtjev (403).

`GET /api/ai-tutor/topics` NIJE iza tokena (jeftin je, a home ekran mora raditi
i prije nego JS pročita token).

## Kako radi

1. Postavi env `AI_TUTOR_EMBED_SECRET` (npr. `openssl rand -hex 32`).
   Bez postavljenog secreta enforcement je ISKLJUČEN (svi prolaze) i app
   loguje `ENV SANITY` upozorenje — sigurno uvođenje bez loma.
2. Server pri `GET /` ugrađuje token u stranicu:
   `<meta name="matbot-embed-token" content="<exp>.<hmac_sha256(secret, exp)>">`.
   Rok važenja: `AI_TUTOR_TOKEN_TTL_S` (default 7200 s = 2 h).
3. Frontend šalje token u headeru `X-Tutor-Token` na svaki tutor poziv
   (prihvata se i query parametar `emb`).
4. Backend verifikuje potpis (HMAC-SHA256, constant-time poređenje) + rok.
   `LOCAL_MODE=1` uvijek prolazi (lokalni razvoj bez tokena).

## Šta Thinkific treba da uradi

Ništa posebno: Thinkific ugrađuje app kroz iframe na `GET /`, a server sam
ugrađuje svjež token u tu stranicu. Učenik koji drži tab otvoren duže od TTL-a
dobiće poruku "Sesija je istekla... Osvježi stranicu."

## Poštena granica ove zaštite

Token se ugrađuje u javno dostupnu stranicu, pa napadač koji može da GET-uje
`/` može uzeti svjež token. Zaštita dakle sprječava *direktno skriptovanje
API-ja* i ograničava replay na TTL — ne autentifikuje učenika. Za punu zaštitu
kombinovati sa: `CORS_ORIGINS` (allow-lista), `FRAME_ANCESTORS` (CSP),
rate limitom (postoji) i po želji IP/Referer pravilima u nginxu.

## Testiranje

`tests/test_embed_token.py` pokriva: mint/verify, istekao/pogrešan/falsifikovan
token, 403 bez tokena u produkciji, 200 sa važećim tokenom, LOCAL_MODE bez
tokena, rollout bez secreta.
