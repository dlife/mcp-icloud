import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MailAccount:
    """Verbindingsgegevens van één mailaccount (IMAP + SMTP)."""
    name: str
    login: str
    password: str
    address: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    trash: str
    # Gmail kent eigen zoeksyntax (X-GM-RAW) en bewaart verzonden mail zelf.
    gmail: bool = False


@dataclass(frozen=True)
class Config:
    apple_id: str
    app_password: str
    public_url: str
    owner_password: str
    auth_token: str = ""
    data_dir: str = "/data"
    # Het iCloud-mailadres kan afwijken van het Apple-ID (bv. Apple-ID is een Gmail-adres).
    mail_address: str = ""
    mail_user: str = ""
    caldav_url: str = "https://caldav.icloud.com"
    timezone: str = "Europe/Brussels"
    imap_host: str = "imap.mail.me.com"
    imap_port: int = 993
    smtp_host: str = "smtp.mail.me.com"
    smtp_port: int = 587
    allowed_hosts: tuple[str, ...] = ()
    host: str = "0.0.0.0"
    port: int = 8000
    max_attachment_bytes: int = 15 * 1024 * 1024
    max_attachment_chars: int = 50_000
    gmail_address: str = ""
    gmail_app_password: str = ""

    @property
    def from_address(self) -> str:
        return self.mail_address or self.apple_id

    @property
    def mail_login(self) -> str:
        """Gebruikersnaam voor IMAP/SMTP: standaard het volledige mailadres."""
        return self.mail_user or self.from_address

    def mail_account(self, name: str = "icloud") -> MailAccount:
        if name == "gmail":
            if not (self.gmail_address and self.gmail_app_password):
                raise RuntimeError("Gmail is niet ingesteld (GMAIL_ADDRESS en GMAIL_APP_PASSWORD)")
            return MailAccount("gmail", self.gmail_address, self.gmail_app_password, self.gmail_address,
                               "imap.gmail.com", 993, "smtp.gmail.com", 587, "[Gmail]/Trash", gmail=True)
        if name != "icloud":
            raise RuntimeError(f"Onbekend account: {name} (kies icloud of gmail)")
        return MailAccount("icloud", self.mail_login, self.app_password, self.from_address,
                           self.imap_host, self.imap_port, self.smtp_host, self.smtp_port, "Deleted Messages")

    @classmethod
    def from_env(cls) -> "Config":
        def need(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise RuntimeError(f"Omgevingsvariabele {name} ontbreekt")
            return value

        password = need("MCP_OWNER_PASSWORD")
        if len(password) < 12:
            raise RuntimeError("MCP_OWNER_PASSWORD moet minstens 12 tekens lang zijn")
        # Optioneel: vaste token voor curl-tests; leeg laten in productie.
        token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
        if token and len(token) < 32:
            raise RuntimeError("MCP_AUTH_TOKEN moet minstens 32 tekens lang zijn")
        hosts = tuple(h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip())
        return cls(
            apple_id=need("ICLOUD_APPLE_ID"),
            app_password=need("ICLOUD_APP_PASSWORD"),
            public_url=need("MCP_PUBLIC_URL").rstrip("/"),
            owner_password=password,
            auth_token=token,
            data_dir=os.environ.get("MCP_DATA_DIR", "/data"),
            mail_address=os.environ.get("ICLOUD_MAIL_ADDRESS", "").strip(),
            mail_user=os.environ.get("ICLOUD_MAIL_USER", "").strip(),
            timezone=os.environ.get("ICLOUD_TIMEZONE", "Europe/Brussels").strip(),
            allowed_hosts=hosts,
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8000")),
            max_attachment_bytes=int(os.environ.get("MCP_MAX_ATTACHMENT_MB", "15")) * 1024 * 1024,
            max_attachment_chars=int(os.environ.get("MCP_MAX_ATTACHMENT_CHARS", "50000")),
            gmail_address=os.environ.get("GMAIL_ADDRESS", "").strip(),
            # Google toont app-wachtwoorden met spaties; die horen er niet bij.
            gmail_app_password=os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip(),
        )
