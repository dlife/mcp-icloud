import logging

import uvicorn
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse

from .config import Config
from .mail import MailClient
from .oauth import SCOPE, OwnerOAuthProvider, login_routes

log = logging.getLogger("icloud_mcp")


def build_server(config: Config, mail: MailClient | None = None) -> FastMCP:
    mail = mail or MailClient(config)
    provider = OwnerOAuthProvider(config.data_dir, config.public_url, config.owner_password, config.auth_token)
    # Lokale adressen blijven toegestaan zodat je op de NAS zelf kunt testen.
    local_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(config.allowed_hosts),
        allowed_hosts=[*config.allowed_hosts, *local_hosts],
        allowed_origins=[*(f"https://{h}" for h in config.allowed_hosts), "http://127.0.0.1:*", "http://localhost:*"],
    )
    mcp = FastMCP(
        "iCloud",
        instructions="Toegang tot de iCloud-account van de gebruiker. Mail is beschikbaar; agenda, herinneringen, drive en foto's volgen.",
        host=config.host,
        port=config.port,
        stateless_http=True,
        transport_security=security,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=config.public_url,
            resource_server_url=f"{config.public_url}/mcp",
            required_scopes=[SCOPE],
            validate_token_resource=False,  # één resource; tokens worden niet per resource uitgegeven
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    get_login, post_login = login_routes(provider)
    mcp.custom_route("/login", methods=["GET"])(get_login)
    mcp.custom_route("/login", methods=["POST"])(post_login)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request):
        return PlainTextResponse("ok")

    @mcp.tool()
    def mail_list_folders() -> list[str]:
        """Geef alle mailmappen van het iCloud-account."""
        return mail.list_folders()

    @mcp.tool()
    def mail_search(folder: str = "INBOX", query: str = "ALL", limit: int = 20) -> list[dict]:
        """Zoek mails in een map, nieuwste eerst. `query` is een IMAP-zoekopdracht, bv. UNSEEN, FROM "jan", SUBJECT "factuur", SINCE 01-Oct-2026. Max 100 resultaten."""
        return mail.search(folder, query, limit)

    @mcp.tool()
    def mail_read(uid: str, folder: str = "INBOX") -> dict:
        """Lees één mail (afzender, onderwerp, tekst, namen van bijlagen). Markeert de mail niet als gelezen."""
        return mail.read(uid, folder)

    @mcp.tool()
    def mail_send(to: str, subject: str, body: str, cc: str = "", reply_to_uid: str = "", folder: str = "INBOX", confirm: bool = False) -> str:
        """Verstuur een mail. Zonder confirm=true gebeurt er niets en krijg je alleen een voorbeeld; toon dat aan de gebruiker en vraag akkoord voordat je opnieuw aanroept met confirm=true. Met reply_to_uid wordt het een antwoord op dat bericht."""
        msg = mail.build_message(to, subject, body, cc, reply_to_uid, folder)
        if not confirm:
            return f"VOORBEELD (niet verstuurd)\nAan: {to}\nCc: {cc}\nOnderwerp: {subject}\n\n{body}\n\nRoep opnieuw aan met confirm=true na akkoord van de gebruiker."
        mail.send(msg)
        log.info("mail verstuurd naar %s", to)
        return f"Verstuurd naar {to}."

    @mcp.tool()
    def mail_mark(uid: str, read: bool = True, folder: str = "INBOX") -> str:
        """Markeer een mail als gelezen (read=true) of ongelezen (read=false)."""
        mail.set_flag(uid, folder, "\\Seen", read)
        return "Bijgewerkt."

    @mcp.tool()
    def mail_trash(uid: str, folder: str = "INBOX", confirm: bool = False) -> str:
        """Verplaats een mail naar de prullenmand. Vereist confirm=true na akkoord van de gebruiker."""
        if not confirm:
            return "Niet uitgevoerd: vraag de gebruiker om akkoord en roep opnieuw aan met confirm=true."
        mail.move_to_trash(uid, folder)
        return "Naar de prullenmand verplaatst."

    return mcp


def build_app(config: Config, mail: MailClient | None = None):
    """ASGI-app: MCP op /mcp (OAuth), OAuth-endpoints, /login en een open /healthz."""
    return build_server(config, mail).streamable_http_app()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    uvicorn.run(build_app(config), host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
