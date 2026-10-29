import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from icloud_mcp.config import Config
from icloud_mcp.server import build_app

TOKEN = "t" * 40
PUBLIC = "https://icloud.example.com"
PASSWORD = "een-lang-wachtwoord"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


class FakeMail:
    def __init__(self):
        self.sent = []
        self.trashed = []

    def list_folders(self):
        return ["INBOX", "Sent Messages"]

    def build_message(self, *args):
        return args

    def send(self, msg):
        self.sent.append(msg)

    def move_to_trash(self, uid, folder):
        self.trashed.append((uid, folder))

    def get_attachment(self, uid, index, folder):
        return "n.txt", "text/plain", b"x" * 100


@pytest.fixture
def fake():
    return FakeMail()


@pytest.fixture
def client(fake, tmp_path):
    config = Config("a@icloud.com", "pw", PUBLIC, PASSWORD, TOKEN, data_dir=str(tmp_path))
    with TestClient(build_app(config, fake)) as c:
        yield c


def rpc(client, method, params=None, id=1):
    resp = client.post("/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})
    assert resp.status_code == 200, resp.text
    text = resp.text
    if text.startswith("event:"):
        text = next(l[5:] for l in text.splitlines() if l.startswith("data:"))
    return json.loads(text)


def test_healthz_is_open(client):
    assert client.get("/healthz").text == "ok"


def test_mcp_requires_token(client):
    assert client.post("/mcp", json={}).status_code == 401
    bad = {**HEADERS, "Authorization": "Bearer nope"}
    assert client.post("/mcp", headers=bad, json={}).status_code == 401


def test_lists_mail_tools(client):
    rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    names = {t["name"] for t in rpc(client, "tools/list", id=2)["result"]["tools"]}
    assert {"mail_list_folders", "mail_search", "mail_read", "mail_send", "mail_mark", "mail_trash", "mail_attachment"} <= names


def test_send_needs_confirmation(client, fake):
    args = {"to": "x@y.be", "subject": "Hoi", "body": "Tekst"}
    rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    out = rpc(client, "tools/call", {"name": "mail_send", "arguments": args}, id=2)
    assert "VOORBEELD" in out["result"]["content"][0]["text"]
    assert fake.sent == []
    rpc(client, "tools/call", {"name": "mail_send", "arguments": {**args, "confirm": True}}, id=3)
    assert len(fake.sent) == 1


def test_trash_needs_confirmation(client, fake):
    rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    rpc(client, "tools/call", {"name": "mail_trash", "arguments": {"uid": "5"}}, id=2)
    assert fake.trashed == []
    rpc(client, "tools/call", {"name": "mail_trash", "arguments": {"uid": "5", "confirm": True}}, id=3)
    assert fake.trashed == [("5", "INBOX")]


def test_attachment_tool_reads_text_and_respects_limits(fake, tmp_path):
    init = ("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    call = {"name": "mail_attachment", "arguments": {"uid": "1", "index": 0}}
    for limits, expected in [({}, "xxxx"), ({"max_attachment_chars": 10}, '"truncated": true'), ({"max_attachment_bytes": 50}, "te groot"), ({"max_attachment_bytes": 0}, "staat uit")]:
        config = Config("a@icloud.com", "pw", PUBLIC, PASSWORD, TOKEN, data_dir=str(tmp_path), **limits)
        with TestClient(build_app(config, fake)) as c:
            rpc(c, *init)
            text = rpc(c, "tools/call", {**call}, id=2)["result"]["content"][0]["text"]
            assert expected in text, (limits, text)


def test_allowed_hosts_still_accept_localhost_and_reject_others(fake, tmp_path):
    config = Config("a@icloud.com", "pw", PUBLIC, PASSWORD, TOKEN, data_dir=str(tmp_path), allowed_hosts=("icloud.example.com",))
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    with TestClient(build_app(config, fake)) as c:
        for host, status in [("127.0.0.1:8765", 200), ("icloud.example.com", 200), ("evil.example", 421)]:
            resp = c.post("/mcp", headers={**HEADERS, "Host": host}, json=init)
            assert resp.status_code == status, (host, resp.status_code, resp.text)


def test_full_oauth_flow(client):
    redirect = "https://claude.ai/api/mcp/auth_callback"
    reg = client.post("/register", json={"redirect_uris": [redirect], "client_name": "Claude", "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]

    verifier = "v" * 60
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    auth = client.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz", "scope": "icloud"}, follow_redirects=False)
    assert auth.status_code == 302, auth.text
    login_url = auth.headers["location"]
    assert login_url.startswith(PUBLIC + "/login?req=")
    req_id = parse_qs(urlparse(login_url).query)["req"][0]

    assert "Claude" in client.get("/login", params={"req": req_id}).text
    bad = client.post("/login", data={"req": req_id, "password": "fout"}, follow_redirects=False)
    assert bad.status_code == 401
    ok = client.post("/login", data={"req": req_id, "password": PASSWORD}, follow_redirects=False)
    assert ok.status_code == 303
    query = parse_qs(urlparse(ok.headers["location"]).query)
    assert query["state"] == ["xyz"]

    tok = client.post("/token", data={"grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": redirect, "client_id": client_id, "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    access, refresh = tok.json()["access_token"], tok.json()["refresh_token"]

    headers = {**HEADERS, "Authorization": f"Bearer {access}"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    assert client.post("/mcp", headers=headers, json=init).status_code == 200

    new = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id})
    assert new.status_code == 200 and new.json()["access_token"] != access
    assert client.post("/token", data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id}).status_code == 400


def test_login_locks_out_after_repeated_failures(client):
    reg = client.post("/register", json={"redirect_uris": ["https://claude.ai/cb"], "token_endpoint_auth_method": "none"}).json()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(b"x" * 60).digest()).rstrip(b"=").decode()
    for _ in range(5):
        auth = client.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"], "redirect_uri": "https://claude.ai/cb", "code_challenge": challenge, "code_challenge_method": "S256"}, follow_redirects=False)
        req_id = parse_qs(urlparse(auth.headers["location"]).query)["req"][0]
        client.post("/login", data={"req": req_id, "password": "fout"})
    resp = client.post("/login", data={"req": req_id, "password": PASSWORD})
    assert resp.status_code == 429


def test_tokens_survive_restart(fake, tmp_path):
    config = Config("a@icloud.com", "pw", PUBLIC, PASSWORD, TOKEN, data_dir=str(tmp_path))
    from icloud_mcp.oauth import OwnerOAuthProvider
    import asyncio

    first = OwnerOAuthProvider(str(tmp_path), PUBLIC, PASSWORD)
    token = first._issue_tokens("c1", ["icloud"], None)
    second = OwnerOAuthProvider(str(tmp_path), PUBLIC, PASSWORD)
    assert asyncio.run(second.load_access_token(token.access_token)) is not None
    assert "access_token" not in (tmp_path / "oauth.json").read_text()
    assert token.access_token not in (tmp_path / "oauth.json").read_text()


class FakeAgenda:
    def __init__(self):
        self.created, self.deleted = [], []

    def build_event(self, *args):
        return "uid-1", "BEGIN:VCALENDAR\r\nEND:VCALENDAR"

    def create_event(self, calendar, **fields):
        self.created.append((calendar, fields))

    def delete_event(self, uid, calendar):
        self.deleted.append((uid, calendar))

    def list_calendars(self):
        return [{"name": "Werk", "url": "x"}]


def test_agenda_writes_need_confirmation(tmp_path):
    fake_agenda = FakeAgenda()
    config = Config("a@icloud.com", "pw", PUBLIC, PASSWORD, TOKEN, data_dir=str(tmp_path))
    with TestClient(build_app(config, FakeMail(), fake_agenda)) as c:
        rpc(c, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        args = {"calendar": "Werk", "summary": "Lunch", "start": "2026-11-02T12:00"}
        out = rpc(c, "tools/call", {"name": "agenda_create_event", "arguments": args}, id=2)
        assert "VOORBEELD" in out["result"]["content"][0]["text"] and fake_agenda.created == []
        rpc(c, "tools/call", {"name": "agenda_create_event", "arguments": {**args, "confirm": True}}, id=3)
        assert len(fake_agenda.created) == 1
        rpc(c, "tools/call", {"name": "agenda_delete_event", "arguments": {"uid": "u", "calendar": "Werk"}}, id=4)
        assert fake_agenda.deleted == []
        rpc(c, "tools/call", {"name": "agenda_delete_event", "arguments": {"uid": "u", "calendar": "Werk", "confirm": True}}, id=5)
        assert fake_agenda.deleted == [("u", "Werk")]


def test_audit_log_never_contains_arguments(client, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="icloud_mcp")
    rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    rpc(client, "tools/call", {"name": "mail_send", "arguments": {"to": "geheim@voorbeeld.be", "subject": "GEHEIMONDERWERP", "body": "x"}}, id=2)
    assert "tool mail_send ok" in caplog.text
    assert "geheim@voorbeeld.be" not in caplog.text and "GEHEIMONDERWERP" not in caplog.text


def test_account_selects_mailbox_and_gmail_must_be_configured(fake, tmp_path):
    gmail_fake = FakeMail()
    gmail_fake.list_folders = lambda: ["[Gmail]/All Mail"]
    init = ("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    config = Config("a@icloud.com", "pw", PUBLIC, PASSWORD, TOKEN, data_dir=str(tmp_path))
    call = {"name": "mail_list_folders", "arguments": {"account": "gmail"}}
    with TestClient(build_app(config, fake, None, gmail_fake)) as c:
        rpc(c, *init)
        assert "All Mail" in rpc(c, "tools/call", call, id=2)["result"]["content"][0]["text"]
        assert "INBOX" in rpc(c, "tools/call", {"name": "mail_list_folders", "arguments": {}}, id=3)["result"]["content"][0]["text"]
        trash = {"name": "mail_trash", "arguments": {"uid": "5", "confirm": True, "account": "gmail"}}
        rpc(c, "tools/call", trash, id=4)
        assert gmail_fake.trashed == [("5", "INBOX")] and fake.trashed == []
    with TestClient(build_app(config, fake)) as c:
        rpc(c, *init)
        out = rpc(c, "tools/call", call, id=2)["result"]
        assert out["isError"] and "Gmail is niet ingesteld" in out["content"][0]["text"]
