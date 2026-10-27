# iCloud Drive-spiegel

Houdt een kopie van iCloud Drive bij in een NAS-map, met [rclone](https://rclone.org/iclouddrive/).
Claude leest die map daarna via je bestaande Synology-koppeling; de iCloud-MCP-server zelf raakt je Apple-wachtwoord niet aan.

Let op:
- iCloud Drive via rclone is **experimenteel**, en Apple accepteert hier **geen app-specifiek wachtwoord**: het is je echte Apple-wachtwoord plus een 2FA-code.
- De login (trust-token) verloopt na ongeveer 30 dagen. Dan wordt de container "unhealthy" en moet je opnieuw inloggen (zie onder).
- De kopie is eenrichtingsverkeer: wat je in `data/` wijzigt wordt bij de volgende synchronisatie overschreven. Verwijdert iemand in iCloud heel veel bestanden tegelijk, dan stopt rclone door `MIRROR_MAX_DELETE`.
- `config/rclone.conf` bevat je (versleutelde, niet beveiligde) wachtwoord en token: `chmod 600` en nooit committen.

## Eerste keer instellen
In deze map, via SSH op de NAS:

1. `mkdir -p config data`
2. Maak de remote interactief aan (je krijgt een 2FA-melding op je iPhone):
   `sudo docker run --rm -it -v "$PWD/config:/config/rclone" rclone/rclone:1.69.3 config`
   Kies `n` (new remote), naam `icloud`, type `iclouddrive`, vul je Apple-ID (het adres van je Apple-account) en wachtwoord in, daarna de 2FA-code.
3. Controleer: `sudo docker run --rm -v "$PWD/config:/config/rclone" rclone/rclone:1.69.3 lsd icloud:`
4. Pas in `docker-compose.yml` het pad `./data` aan naar de share waar de kopie moet komen, en start: `sudo docker compose up -d`
5. Volg het logboek: `sudo docker compose logs -f`. De eerste synchronisatie kan lang duren.

## Opnieuw inloggen (ongeveer elke 30 dagen, of als de container unhealthy is)
`sudo docker run --rm -it -v "$PWD/config:/config/rclone" rclone/rclone:1.69.3 reconnect icloud:`
Daarna `sudo docker compose restart`.

## Claude erbij laten
Voeg de map met de kopie als share toe aan de config van je Synology-koppeling, net zoals je `music`, `photo` en `video` hebt toegevoegd. Houd die koppeling alleen-lezen.
