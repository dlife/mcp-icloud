import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    apple_id: str
    app_password: str
    auth_token: str
    imap_host: str = "imap.mail.me.com"
    imap_port: int = 993
    smtp_host: str = "smtp.mail.me.com"
    smtp_port: int = 587
    allowed_hosts: tuple[str, ...] = ()
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Config":
        def need(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise RuntimeError(f"Omgevingsvariabele {name} ontbreekt")
            return value

        token = need("MCP_AUTH_TOKEN")
        if len(token) < 32:
            raise RuntimeError("MCP_AUTH_TOKEN moet minstens 32 tekens lang zijn")
        hosts = tuple(h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip())
        return cls(
            apple_id=need("ICLOUD_APPLE_ID"),
            app_password=need("ICLOUD_APP_PASSWORD"),
            auth_token=token,
            allowed_hosts=hosts,
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )
