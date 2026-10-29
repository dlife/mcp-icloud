# icloud-mcp

MCP server for iCloud Mail and Calendar (plus optional Gmail), meant to run 24/7 in Docker and be used from Claude.
The main documentation is in Dutch ([README.md](README.md)); this is the short version.

## Quick start
You need an always-on machine with Docker (NAS, Raspberry Pi, small server), a domain name, and an iCloud account with an [app-specific password](https://account.apple.com) (Sign-In and Security, App-Specific Passwords).

```
git clone https://github.com/dlife/mcp-icloud && cd mcp-icloud
./setup.sh --lang en
docker compose up -d --build
```

`setup.sh` only needs Docker. The wizard asks for your Apple ID, iCloud mail address, app-specific password (with instructions on where to create it), optionally Gmail, and the public URL. It **tests the logins**, generates a strong sign-in password for the Claude connection, and writes `.env` (readable only by you).

Prefer manual setup? `cp .env.example .env`, fill it in, then `docker compose up -d --build`.
Check locally: `curl http://127.0.0.1:8765/healthz` returns `ok`.

## Make it reachable
Put an HTTPS reverse proxy in front of `127.0.0.1:8765` (Synology DSM reverse proxy, Caddy, a tunnel...). Example for Caddy:

```
icloud.example.com {
    reverse_proxy icloud-mcp:8000
}
```

Don't add an extra token layer in the proxy: it would block Claude's OAuth flow. Authentication is built into the server.

## Connect to Claude
Claude, Settings, Connectors, Add custom connector, URL `https://icloud.example.com/mcp`. You get a login page from your own server; enter the `MCP_OWNER_PASSWORD` the wizard showed you.

## Update, back up, troubleshoot
`git pull && docker compose up -d --build --force-recreate`. Back-up script: `scripts/backup.sh`. Common problems (`AUTHENTICATIONFAILED`, `Invalid Host header`) are covered in the Dutch README under "Problemen oplossen".

## Tools and safety
Mail: list, search, read, read attachments (PDF, Office, text), send, mark, trash. Calendar: list, create, delete. Sending, trashing and creating/deleting events do nothing without `confirm=true`. Logs never contain mail or calendar content. Set `account=gmail` on mail tools for the optional Gmail account.

## Development
`pip install -e '.[dev]' && pytest`
