import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    apple_id: str
    app_password: str
    public_url: str
    owner_password: str
    auth_token: str = ""
    data_dir: str = "/data"
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
            allowed_hosts=hosts,
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )
