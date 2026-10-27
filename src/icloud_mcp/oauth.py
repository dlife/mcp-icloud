"""Eigen OAuth 2.1-server voor één gebruiker: Claude registreert zichzelf, jij keurt goed met een wachtwoord.

Tokens worden alleen als hash bewaard, in een JSON-bestand dat een herstart overleeft.
"""
import hashlib
import hmac
import html
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

SCOPE = "icloud"
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
CODE_TTL = 300
PENDING_TTL = 600
MAX_CLIENTS = 50
MAX_FAILS = 5
LOCKOUT_SECONDS = 300


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class OwnerOAuthProvider:
    def __init__(self, data_dir: str, public_url: str, owner_password: str, static_token: str = ""):
        self.path = Path(data_dir) / "oauth.json"
        self.public_url = public_url.rstrip("/")
        self.owner_password = owner_password
        self.static_token = static_token
        self.pending: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams, float]] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.fails: list[float] = []
        self.state = {"clients": {}, "access": {}, "refresh": {}}
        self._load()

    # --- opslag ---
    def _load(self) -> None:
        try:
            self.state.update(json.loads(self.path.read_text()))
        except FileNotFoundError:
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.path)

    def _prune(self) -> None:
        now = time.time()
        for kind in ("access", "refresh"):
            self.state[kind] = {k: v for k, v in self.state[kind].items() if v["expires_at"] > now}
        self.codes = {k: v for k, v in self.codes.items() if v.expires_at > now}
        self.pending = {k: v for k, v in self.pending.items() if v[2] > now}

    # --- clients ---
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self.state["clients"].get(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if len(self.state["clients"]) >= MAX_CLIENTS:
            raise ValueError("Te veel geregistreerde clients")
        self.state["clients"][client_info.client_id] = client_info.model_dump(mode="json")
        self._save()

    # --- autorisatie ---
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self._prune()
        req_id = secrets.token_urlsafe(24)
        self.pending[req_id] = (client, params, time.time() + PENDING_TTL)
        return f"{self.public_url}/login?req={req_id}"

    def locked_out(self) -> bool:
        now = time.time()
        self.fails = [t for t in self.fails if now - t < LOCKOUT_SECONDS]
        return len(self.fails) >= MAX_FAILS

    def check_password(self, password: str) -> bool:
        ok = hmac.compare_digest(password.encode(), self.owner_password.encode())
        if not ok:
            self.fails.append(time.time())
        return ok

    def issue_code(self, req_id: str) -> str | None:
        entry = self.pending.pop(req_id, None)
        if not entry or entry[2] < time.time():
            return None
        client, params, _ = entry
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [SCOPE],
            expires_at=time.time() + CODE_TTL,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        code = self.codes.get(authorization_code)
        if code and code.client_id == client.client_id and code.expires_at > time.time():
            return code
        return None

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        self.codes.pop(authorization_code.code, None)
        return self._issue_tokens(client.client_id, authorization_code.scopes, authorization_code.resource)

    def _issue_tokens(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        self._prune()
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.state["access"][_hash(access)] = {"client_id": client_id, "scopes": scopes, "expires_at": now + ACCESS_TTL, "resource": resource}
        self.state["refresh"][_hash(refresh)] = {"client_id": client_id, "scopes": scopes, "expires_at": now + REFRESH_TTL, "resource": resource}
        self._save()
        return OAuthToken(access_token=access, expires_in=ACCESS_TTL, scope=" ".join(scopes), refresh_token=refresh)

    # --- tokens ---
    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        data = self.state["refresh"].get(_hash(refresh_token))
        if data and data["client_id"] == client.client_id and data["expires_at"] > time.time():
            return RefreshToken(token=refresh_token, **data)
        return None

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        if scopes and not set(scopes) <= set(refresh_token.scopes):
            raise TokenError("invalid_scope")
        self.state["refresh"].pop(_hash(refresh_token.token), None)
        return self._issue_tokens(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        if self.static_token and hmac.compare_digest(token.encode(), self.static_token.encode()):
            return AccessToken(token=token, client_id="static", scopes=[SCOPE])
        data = self.state["access"].get(_hash(token))
        if data and data["expires_at"] > time.time():
            return AccessToken(token=token, **data)
        return None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.state["access"].pop(_hash(token.token), None)
        self.state["refresh"].pop(_hash(token.token), None)
        self._save()


LOGIN_PAGE = """<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>iCloud koppelen</title>
<style>body{{font:16px system-ui;max-width:24rem;margin:15vh auto;padding:0 1rem}}input,button{{font:inherit;padding:.6rem;width:100%;box-sizing:border-box;margin-top:.6rem}}.e{{color:#b00}}</style>
<h1>iCloud koppelen</h1><p>Geef <b>{client}</b> toegang tot je iCloud-mail en bestanden?</p>{error}
<form method="post"><input type="hidden" name="req" value="{req}"><input type="password" name="password" placeholder="Wachtwoord" autofocus required>
<button>Toegang geven</button></form></html>"""


def login_routes(provider: OwnerOAuthProvider):
    def page(req_id: str, error: str = "", status: int = 200) -> Response:
        entry = provider.pending.get(req_id)
        if not entry or entry[2] < time.time():
            return HTMLResponse("Deze aanvraag is verlopen. Start de koppeling opnieuw vanuit Claude.", status_code=400)
        name = html.escape(entry[0].client_name or "Een app")
        msg = f'<p class="e">{html.escape(error)}</p>' if error else ""
        return HTMLResponse(LOGIN_PAGE.format(client=name, error=msg, req=html.escape(req_id)), status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    async def get_login(request: Request) -> Response:
        return page(request.query_params.get("req", ""))

    async def post_login(request: Request) -> Response:
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        req_id, password = form.get("req", ""), form.get("password", "")
        if provider.locked_out():
            return page(req_id, "Te veel pogingen. Probeer het over een paar minuten opnieuw.", 429)
        if not provider.check_password(password):
            return page(req_id, "Verkeerd wachtwoord.", 401)
        target = provider.issue_code(req_id)
        if not target:
            return HTMLResponse("Deze aanvraag is verlopen. Start de koppeling opnieuw vanuit Claude.", status_code=400)
        return RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})

    return get_login, post_login
