"""Interactieve installatiewizard / interactive setup wizard: maakt .env aan en test de logins.

Gebruik / usage: icloud-mcp-setup [--out .env] [--lang nl|en] [--skip-checks]
Alleen standaardbibliotheek voor de vragen; de tests gebruiken de bestaande mail- en agendacode.
"""
import argparse
import getpass
import os
import secrets
import sys
from urllib.parse import urlparse

TEXT = {
    "nl": {
        "intro": "iCloud MCP-installatie. Antwoord met Enter om de voorgestelde waarde (tussen haakjes) te nemen.",
        "exists": "{path} bestaat al. Overschrijven? (de oude wordt bewaard als {path}.bak)",
        "abort": "Gestopt, niets gewijzigd.",
        "apple_id": "Apple-ID (e-mailadres waarmee je bij Apple inlogt)",
        "mail_addr": "iCloud-mailadres (@icloud.com, @me.com of eigen domein). Enter als dit gelijk is aan je Apple-ID",
        "pw_help": ("Je hebt een APP-SPECIFIEK wachtwoord nodig (niet je gewone Apple-wachtwoord):\n"
                    "  1. Ga naar https://account.apple.com, Aanmelden en beveiliging, App-specifieke wachtwoorden\n"
                    "  2. Maak er een met de naam 'MCP' (vorm: xxxx-xxxx-xxxx-xxxx)"),
        "app_pw": "App-specifiek wachtwoord (je ziet het niet terwijl je typt)",
        "gmail_q": "Gmail ook koppelen?",
        "gmail_help": ("Voor Gmail heb je tweestapsverificatie nodig en een app-wachtwoord:\n"
                       "  https://myaccount.google.com/apppasswords"),
        "gmail_addr": "Gmail-adres",
        "gmail_pw": "Gmail app-wachtwoord (onzichtbaar)",
        "url": "Publieke HTTPS-adres van deze server, bv. https://icloud.voorbeeld.nl",
        "url_bad": "Dat moet met https:// beginnen en een hostnaam bevatten.",
        "tz": "Tijdzone",
        "owner": "Aanmeldwachtwoord voor de Claude-koppeling gegenereerd (minstens 12 tekens).",
        "owner_note": "Je vult dit straks in op de inlogpagina als Claude de connector koppelt. Bewaar het in je wachtwoordmanager:",
        "check": "Logins testen...",
        "ok": "OK",
        "fail": "MISLUKT",
        "retry": "Opnieuw invoeren? (anders sla ik het toch op)",
        "written": "Klaar: {path} is aangemaakt (alleen leesbaar voor jou).",
        "next": ("Volgende stappen:\n"
                 "  1. Start de server:      docker compose up -d --build\n"
                 "  2. Zet een reverse proxy met HTTPS voor 127.0.0.1:8765 (zie README)\n"
                 "  3. Voeg in Claude (Instellingen, Connectors, Aangepaste connector) toe: {url}/mcp"),
        "yes": "j", "yn": "[j/N]",
    },
    "en": {
        "intro": "iCloud MCP setup. Press Enter to accept the suggested value (in brackets).",
        "exists": "{path} already exists. Overwrite? (the old one is kept as {path}.bak)",
        "abort": "Stopped, nothing changed.",
        "apple_id": "Apple ID (the e-mail address you sign in to Apple with)",
        "mail_addr": "iCloud mail address (@icloud.com, @me.com or custom domain). Enter if same as your Apple ID",
        "pw_help": ("You need an APP-SPECIFIC password (not your normal Apple password):\n"
                    "  1. Go to https://account.apple.com, Sign-In and Security, App-Specific Passwords\n"
                    "  2. Create one named 'MCP' (format: xxxx-xxxx-xxxx-xxxx)"),
        "app_pw": "App-specific password (hidden while typing)",
        "gmail_q": "Also connect Gmail?",
        "gmail_help": ("Gmail needs 2-step verification and an app password:\n"
                       "  https://myaccount.google.com/apppasswords"),
        "gmail_addr": "Gmail address",
        "gmail_pw": "Gmail app password (hidden)",
        "url": "Public HTTPS address of this server, e.g. https://icloud.example.com",
        "url_bad": "It must start with https:// and contain a hostname.",
        "tz": "Time zone",
        "owner": "Generated the sign-in password for the Claude connection (at least 12 characters).",
        "owner_note": "You enter it on the login page when Claude connects. Store it in your password manager:",
        "check": "Testing logins...",
        "ok": "OK",
        "fail": "FAILED",
        "retry": "Enter again? (otherwise I save it anyway)",
        "written": "Done: {path} created (readable by you only).",
        "next": ("Next steps:\n"
                 "  1. Start the server:     docker compose up -d --build\n"
                 "  2. Put a reverse proxy with HTTPS in front of 127.0.0.1:8765 (see README)\n"
                 "  3. In Claude (Settings, Connectors, Add custom connector) add: {url}/mcp"),
        "yes": "y", "yn": "[y/N]",
    },
}


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    reader = getpass.getpass if secret else input
    return reader(f"{prompt}{suffix}: ").strip() or default


def confirm(t: dict, prompt: str) -> bool:
    return input(f"{prompt} {t['yn']} ").strip().lower().startswith(t["yes"])


def clean_password(value: str) -> str:
    return value.replace(" ", "").strip()


def valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname)


def render_env(v: dict[str, str]) -> str:
    """Zelfde volgorde en uitleg als .env.example, met de ingevulde waarden."""
    host = urlparse(v["MCP_PUBLIC_URL"]).hostname or ""
    lines = [
        "# Aangemaakt door icloud-mcp-setup. Nooit committen.",
        f"ICLOUD_APPLE_ID={v['ICLOUD_APPLE_ID']}",
        f"ICLOUD_MAIL_ADDRESS={v['ICLOUD_MAIL_ADDRESS']}",
        "ICLOUD_MAIL_USER=",
        f"ICLOUD_APP_PASSWORD={v['ICLOUD_APP_PASSWORD']}",
        "",
        f"GMAIL_ADDRESS={v.get('GMAIL_ADDRESS', '')}",
        f"GMAIL_APP_PASSWORD={v.get('GMAIL_APP_PASSWORD', '')}",
        "",
        f"ICLOUD_TIMEZONE={v['ICLOUD_TIMEZONE']}",
        f"MCP_PUBLIC_URL={v['MCP_PUBLIC_URL']}",
        f"MCP_ALLOWED_HOSTS={host}",
        f"MCP_OWNER_PASSWORD={v['MCP_OWNER_PASSWORD']}",
        "MCP_AUTH_TOKEN=",
        "MCP_MAX_ATTACHMENT_MB=15",
        "MCP_MAX_ATTACHMENT_CHARS=50000",
        "",
    ]
    return "\n".join(lines)


def check_logins(values: dict[str, str]) -> dict[str, str | None]:
    """Test mail, agenda en Gmail. Geeft per onderdeel None (gelukt) of de foutmelding."""
    from .agenda import AgendaClient
    from .config import Config
    from .mail import MailClient

    config = Config(
        apple_id=values["ICLOUD_APPLE_ID"], app_password=values["ICLOUD_APP_PASSWORD"],
        public_url=values["MCP_PUBLIC_URL"], owner_password=values["MCP_OWNER_PASSWORD"],
        mail_address=values["ICLOUD_MAIL_ADDRESS"], timezone=values["ICLOUD_TIMEZONE"],
        gmail_address=values.get("GMAIL_ADDRESS", ""), gmail_app_password=values.get("GMAIL_APP_PASSWORD", ""),
    )
    probes = {
        "iCloud Mail": lambda: MailClient(config, "icloud").list_folders(),
        "iCloud Agenda": lambda: AgendaClient(config).list_calendars(),
    }
    if config.gmail_address:
        probes["Gmail"] = lambda: MailClient(config, "gmail").list_folders()
    results: dict[str, str | None] = {}
    for name, probe in probes.items():
        try:
            probe()
            results[name] = None
        except Exception as exc:  # inlogfouten komen als allerlei exceptiontypes binnen
            results[name] = str(exc) or exc.__class__.__name__
    return results


def collect(t: dict) -> dict[str, str]:
    v: dict[str, str] = {}
    print("\n" + t["pw_help"] + "\n")
    v["ICLOUD_APPLE_ID"] = ask(t["apple_id"])
    mail = ask(t["mail_addr"])
    v["ICLOUD_MAIL_ADDRESS"] = "" if mail == v["ICLOUD_APPLE_ID"] else mail
    v["ICLOUD_APP_PASSWORD"] = ask(t["app_pw"], secret=True)
    if confirm(t, t["gmail_q"]):
        print("\n" + t["gmail_help"] + "\n")
        v["GMAIL_ADDRESS"] = ask(t["gmail_addr"])
        v["GMAIL_APP_PASSWORD"] = clean_password(ask(t["gmail_pw"], secret=True))
    while True:
        url = ask(t["url"]).rstrip("/")
        if valid_url(url):
            v["MCP_PUBLIC_URL"] = url
            break
        print(t["url_bad"])
    v["ICLOUD_TIMEZONE"] = ask(t["tz"], os.environ.get("TZ") or "Europe/Brussels")
    v["MCP_OWNER_PASSWORD"] = secrets.token_urlsafe(18)
    return v


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maakt .env aan voor icloud-mcp.")
    parser.add_argument("--out", default=".env")
    parser.add_argument("--lang", choices=sorted(TEXT))
    parser.add_argument("--skip-checks", action="store_true", help="geen login-tests (offline)")
    args = parser.parse_args(argv)

    lang = args.lang or ("nl" if input("Taal / Language [nl/en] (nl): ").strip().lower() != "en" else "en")
    t = TEXT[lang]
    print(t["intro"])
    if os.path.exists(args.out) and not confirm(t, t["exists"].format(path=args.out)):
        print(t["abort"])
        return 1

    while True:
        values = collect(t)
        if args.skip_checks:
            break
        print("\n" + t["check"])
        results = check_logins(values)
        for name, error in results.items():
            print(f"  {name}: {t['ok'] if error is None else t['fail'] + ' - ' + error}")
        if all(e is None for e in results.values()) or not confirm(t, t["retry"]):
            break

    if os.path.exists(args.out):
        os.replace(args.out, args.out + ".bak")
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(render_env(values))
    print("\n" + t["written"].format(path=args.out))
    print(t["owner"] + "\n" + t["owner_note"] + f"\n  {values['MCP_OWNER_PASSWORD']}\n")
    print(t["next"].format(url=values["MCP_PUBLIC_URL"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
