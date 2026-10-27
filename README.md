# icloud-mcp

MCP-server voor iCloud, bedoeld om 24/7 op de Synology (Docker) te draaien. Stap 1: Mail.

## Tools
`mail_list_folders`, `mail_search`, `mail_read`, `mail_send`, `mail_mark`, `mail_trash`.
`mail_send` en `mail_trash` doen niets zonder `confirm=true`; zonder krijg je een voorbeeld terug.

## Draaien op de NAS
1. Zet deze map op de NAS, bv. in `docker/mcp-icloud`.
2. `cp .env.example .env && chmod 600 .env`, vul in.
3. Container Manager: Project, Aanmaken, kies de map en start. Of via SSH: `sudo docker compose up -d --build`.
4. Test op de NAS: `curl http://127.0.0.1:8765/healthz` geeft `ok`; `/mcp` zonder aanmelding geeft 401.

## Publiek bereikbaar maken
Zet een reverse proxy met HTTPS voor `127.0.0.1:8765` (of poort 8000 van de container).

**Synology DSM** (aanbevolen): Configuratiescherm, Aanmeldingsportaal, Geavanceerd, Reverse Proxy, Aanmaken.
Bron: HTTPS, hostnaam `icloud.example.com`, poort 443. Bestemming: HTTP, `localhost`, poort 8765.
Koppel daarna in Beveiliging, Certificaat, Instellingen een certificaat aan die regel (dat moet `icloud.example.com` bevatten).
De aanmelding gebeurt in de server zelf, een extra token-laag in de proxy is niet nodig (en zou de OAuth-stroom van Claude blokkeren).

**Caddy**:

```
icloud.example.com {
    reverse_proxy icloud-mcp:8000
}
```

Draait Caddy in een eigen Docker-netwerk, zet de container dan in dat netwerk (zie commentaar in `docker-compose.yml`).
Vergeet het DNS-record en de port-forward naar 443 niet.

## Koppelen met Claude
In Claude: Instellingen, Connectors, Aangepaste connector toevoegen, URL `https://icloud.example.com/mcp`. Claude registreert zichzelf, je krijgt een inlogpagina van je eigen server en geeft met `MCP_OWNER_PASSWORD` toegang. Tokens staan (als hash) in het volume `icloud-mcp-data`; toegang intrekken doe je door dat volume te verwijderen.

## Ontwikkelen
`pip install -e '.[dev]' && pytest`
