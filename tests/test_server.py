import json

import pytest
from starlette.testclient import TestClient

from icloud_mcp.config import Config
from icloud_mcp.server import build_app

TOKEN = "t" * 40
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


@pytest.fixture
def fake():
    return FakeMail()


@pytest.fixture
def client(fake):
    config = Config("a@icloud.com", "pw", TOKEN)
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
    assert {"mail_list_folders", "mail_search", "mail_read", "mail_send", "mail_mark", "mail_trash"} <= names


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
