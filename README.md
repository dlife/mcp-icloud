# icloud-mcp

MCP-server voor iCloud, bedoeld om 24/7 op de Synology (Docker) te draaien. Mail en Agenda.

Wat er bewust **niet** in zit: Herinneringen (Structured doet dat), Foto's (Synology Photos) en iCloud Drive
(geen officiële API en geen app-specifiek wachtwoord; de pc kopieert de map met `robocopy` naar een NAS-share).

## Tools
`mail_list_folders`, `mail_search`, `mail_read`, `mail_attachment`, `mail_send`, `mail_mark`, `mail_trash`.

`mail_read` toont per bijlage een `index`; `mail_attachment` haalt daarmee de tekst uit PDF, Word, Excel, PowerPoint en tekstbestanden (alleen lezen, in het geheugen, niets op schijf). Scans en afbeeldingen geven een uitleg in `note`; OCR volgt later. Grenzen via `MCP_MAX_ATTACHMENT_MB` (standaard 15) en `MCP_MAX_ATTACHMENT_CHARS` (standaard 50000).
Agenda: `agenda_list_calendars`, `agenda_list_events`, `agenda_create_event`, `agenda_delete_event` (CalDAV met je Apple-ID en app-specifiek wachtwoord).
`mail_send`, `mail_trash`, `agenda_create_event` en `agenda_delete_event` doen niets zonder `confirm=true`; zonder krijg je een voorbeeld terug.

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

## Bijwerken
In de projectmap: `git pull`, dan `sudo docker compose up -d --build --force-recreate`.
Na een wijziging in `.env` is `sudo docker compose up -d --force-recreate` genoeg (een gewone herstart leest `.env` niet opnieuw).
Nieuwe tools verschijnen in Claude pas na een nieuw gesprek, of na de connector uit en aan te zetten.

## Instellingen in `.env`
Zie `.env.example`. Het Apple-ID (voor de Agenda) en het mailadres van iCloud Mail kunnen verschillen: zet dan het iCloud-adres in `ICLOUD_MAIL_ADDRESS`.
`LOG_LEVEL` (standaard `INFO`) stelt het logboek in.

## Logboek
`sudo docker compose logs --tail 50`. Het logboek bevat per aanroep alleen toolnaam, duur en uitkomst, nooit onderwerpen, adressen of inhoud.

## Back-up
`sh scripts/backup.sh /pad/naar/doelmap` bewaart `.env` en het OAuth-volume (de laatste 14 keer).
Plan dat in DSM, Taakplanner, als gebruikersscript als root. Het bestand bevat je app-wachtwoord en tokens: bewaar het in een map die alleen jij kunt lezen.
Terugzetten: pak het archief uit, zet `env` terug als `.env` en pak `oauth-data.tgz` uit in een nieuw volume `icloud-mcp-data`.

## Problemen oplossen
- `AUTHENTICATIONFAILED` bij Mail: gebruik een app-specifiek wachtwoord en het volledige iCloud-mailadres in `ICLOUD_MAIL_ADDRESS` (iCloud Mail bestaat alleen met een `@icloud.com`-adres of eigen domein bij iCloud).
- `Invalid Host header`: `MCP_ALLOWED_HOSTS` moet de hostnaam bevatten waarop de server bereikbaar is.
- Nieuwe tool niet zichtbaar in Claude: nieuw gesprek starten of de connector opnieuw aanzetten.
- Opnieuw koppelen met Claude: verwijder de connector en voeg hem opnieuw toe; je logt dan weer in met `MCP_OWNER_PASSWORD`.

## Ontwikkelen
`pip install -e '.[dev]' && pytest`
