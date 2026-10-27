# icloud-mcp

MCP-server voor iCloud, bedoeld om 24/7 op de Synology (Docker) te draaien. Stap 1: Mail.

## Tools
`mail_list_folders`, `mail_search`, `mail_read`, `mail_send`, `mail_mark`, `mail_trash`.
`mail_send` en `mail_trash` doen niets zonder `confirm=true`; zonder krijg je een voorbeeld terug.

## Draaien op de NAS
1. Kopieer deze map naar een map naast je andere containers, bv. `/volume1/docker/mcp-icloud`.
2. `cp .env.example .env && chmod 600 .env`, vul in (token maak je met `openssl rand -hex 32`).
3. `docker compose up -d --build`
4. Test: `curl http://127.0.0.1:8765/healthz` geeft `ok`; `/mcp` zonder token geeft 401.

De poort staat alleen op localhost; HTTPS en het subdomein komen in stap 2.

## Ontwikkelen
`pip install -e '.[dev]' && pytest`
