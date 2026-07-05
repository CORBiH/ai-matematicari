# Arhiva: Cloud Run deploy fajlovi (legacy)

Aplikacija je jula 2026. preseljena sa Google Cloud Runa na Hetzner VPS
(docker compose + nginx; vidi `docker-compose.yml` u root-u i
`.github/workflows/deploy-vps.yml`). Ovi fajlovi su zadržani samo kao
referenca stare postavke:

- `cloudbuild.yaml` — Cloud Build → Cloud Run deploy pipeline
- `deploy.sh` — ručni gcloud deploy
- `Procfile` — gunicorn komanda za buildpack okruženja (VPS koristi Dockerfile CMD)
- `.gcloudignore` — gcloud upload ignore lista

Ako se Cloud Run više nikad ne koristi, cijeli folder se može obrisati.
