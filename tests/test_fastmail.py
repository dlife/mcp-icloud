import json
from email import message_from_bytes

import httpx
import pytest

from icloud_mcp.config import Config
from icloud_mcp.fastmail import FastmailClient, JmapError

TOKEN = "fm-geheim-token"
BOXES = [
    {"id": "b-inbox", "name": "Inbox", "parentId": None, "role": "inbox"},
    {"id": "b-trash", "name": "Trash", "parentId": None, "role": "trash"},
    {"id": "b-junk", "name": "Spam", "parentId": None, "role": "junk"},
    {"id": "b-sent", "name": "Sent", "parentId": None, "role": "sent"},
    {"id": "b-drafts", "name": "Drafts", "parentId": None, "role": "drafts"},
    {"id": "b-fact", "name": "Facturen", "parentId": None, "role": None},
    {"id": "b-2026", "name": "2026", "parentId": "b-fact", "role": None},
]
MAILS = {
    "e1": {
        "id": "e1", "from": [{"name": "Jérôme", "email": "j@x.be"}], "to": [{"email": "me@fastmail.com"}],
        "cc": [], "subject": "Factuur", "receivedAt": "2026-11-20T10:00:00Z", "keywords": {"$seen": True},
        "messageId": ["abc@x.be"], "mailboxIds": {"b-inbox": True},
        "textBody": [{"partId": "1", "type": "text/plain"}], "htmlBody": [{"partId": "1", "type": "text/plain"}],
        "bodyValues": {"1": {"value": "Hallo wereld"}},
        "attachments": [{"blobId": "blob1", "name": "f 1.pdf", "type": "application/pdf", "size": 4}],
    },
    "e2": {
        "id": "e2", "from": [{"email": "n@x.be"}], "to": [], "cc": [], "subject": "Nieuwsbrief", "receivedAt": "2026-11-21T09:00:00Z",
        "keywords": {}, "messageId": [], "mailboxIds": {"b-inbox": True, "b-fact": True},
        "textBody": [{"partId": "1", "type": "text/html"}], "htmlBody": [{"partId": "1", "type": "text/html"}],
        "bodyValues": {"1": {"value": "<p>Hoi <b>daar</b></p><script>x()</script>"}}, "attachments": [],
    },
}


class FakeFastmail:
    """Minimale JMAP-server in het geheugen; onthoudt aanroepen om te controleren."""

    def __init__(self):
        self.mails = json.loads(json.dumps(MAILS))
        self.requests = []  # (methode, url, headers)
        self.calls = []  # JMAP-methodecalls
        self.uploads = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url), dict(request.headers)))
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        path = request.url.path
        if path == "/jmap/session":
            return httpx.Response(200, json={
                "primaryAccounts": {"urn:ietf:params:jmap:mail": "acc1"},
                "apiUrl": "https://api.test/jmap/api/",
                "downloadUrl": "https://api.test/jmap/download/{accountId}/{blobId}/{name}?type={type}",
                "uploadUrl": "https://api.test/jmap/upload/{accountId}/",
            })
        if path.startswith("/jmap/download/"):
            return httpx.Response(200, content=b"%PDF")
        if path.startswith("/jmap/upload/"):
            self.uploads.append(request.content)
            return httpx.Response(200, json={"blobId": "up1"})
        body = json.loads(request.content)
        out = []
        for name, args, tag in body["methodCalls"]:
            self.calls.append((name, args))
            out.append([name, self.run(name, args), tag])
        return httpx.Response(200, json={"methodResponses": out})

    def run(self, name, args):
        if name == "Mailbox/get":
            return {"list": BOXES}
        if name == "Identity/get":
            return {"list": [{"id": "id1", "email": "me@fastmail.com"}]}
        if name == "Email/query":
            ids = sorted(self.mails, key=lambda i: self.mails[i]["receivedAt"], reverse=True)
            return {"ids": ids[: args["limit"]]}
        if name == "Email/get":
            ids = args.get("ids") or sorted(self.mails)
            return {"list": [{k: v for k, v in self.mails[i].items() if k in args["properties"] or k == "id"} for i in ids if i in self.mails]}
        if name == "Email/set":
            for uid, patch in args["update"].items():
                for key, value in patch.items():
                    prop, sub = key.split("/")
                    target = self.mails[uid][prop]
                    if value is None:
                        target.pop(sub, None)
                    else:
                        target[sub] = value
            return {"updated": {u: None for u in args["update"]}}
        if name in ("Email/import", "EmailSubmission/set"):
            return {"created": {"m": {"id": "new"}}}
        raise AssertionError(name)


@pytest.fixture
def fake():
    return FakeFastmail()


@pytest.fixture
def client(fake):
    config = Config("a@icloud.com", "pw", "https://x.example", "een-lang-wachtwoord", fastmail_api_token=TOKEN, fastmail_session_url="https://api.test/jmap/session")
    return FastmailClient(config, httpx.Client(transport=httpx.MockTransport(fake.handler)))


def test_requires_token():
    with pytest.raises(RuntimeError, match="FASTMAIL_API_TOKEN"):
        FastmailClient(Config("a@icloud.com", "pw", "https://x.example", "een-lang-wachtwoord"))


def test_token_only_in_authorization_header(client, fake):
    client.list_folders()
    assert all(TOKEN not in url for _, url, _ in fake.requests)
    assert all(h["authorization"] == f"Bearer {TOKEN}" for _, _, h in fake.requests)


def test_bad_token_error_does_not_leak_it(fake):
    config = Config("a@icloud.com", "pw", "https://x.example", "een-lang-wachtwoord", fastmail_api_token="fout", fastmail_session_url="https://api.test/jmap/session")
    c = FastmailClient(config, httpx.Client(transport=httpx.MockTransport(fake.handler)))
    with pytest.raises(JmapError) as exc:
        c.list_folders()
    assert "weigerde" in str(exc.value) and "fout" not in str(exc.value).replace("fouten", "")


def test_list_folders_builds_paths(client):
    assert client.list_folders() == ["Drafts", "Facturen", "Facturen/2026", "Inbox", "Sent", "Spam", "Trash"]


def test_search_newest_first_and_unread(client, fake):
    results = client.search("INBOX", "ALL", 10)
    assert [r["uid"] for r in results] == ["e2", "e1"]
    assert results[0]["unread"] is True and results[1]["unread"] is False
    assert results[1]["from"] == "Jérôme <j@x.be>"
    query = next(a for n, a in fake.calls if n == "Email/query")
    assert query["filter"] == {"inMailbox": "b-inbox"}


def test_search_translates_imap_style_query(client, fake):
    client.search("*", 'UNSEEN FROM "jan" SUBJECT "factuur oktober" SINCE 01-Oct-2026 HASATTACHMENT losse woorden')
    flt = next(a for n, a in fake.calls if n == "Email/query")["filter"]
    assert flt == {"notKeyword": "$seen", "from": "jan", "subject": "factuur oktober", "after": "2026-10-01T00:00:00Z", "hasAttachment": True, "text": "losse woorden"}


def test_search_unknown_folder(client):
    with pytest.raises(JmapError, match="Map niet gevonden"):
        client.search("Bestaatniet")


def test_read_plain_and_attachments(client):
    msg = client.read("e1")
    assert msg["body"] == "Hallo wereld"
    assert msg["message_id"] == "<abc@x.be>"
    assert msg["attachments"] == [{"index": 0, "name": "f 1.pdf", "type": "application/pdf", "size": 4}]


def test_read_html_only_is_converted(client):
    body = client.read("e2")["body"]
    assert "Hoi" in body and "daar" in body and "<p>" not in body and "x()" not in body


def test_read_does_not_change_flags(client, fake):
    client.read("e2")
    assert not any(n == "Email/set" for n, _ in fake.calls)
    assert fake.mails["e2"]["keywords"] == {}


def test_get_attachment_downloads_blob_and_checks_index(client, fake):
    assert client.get_attachment("e1", 0) == ("f 1.pdf", "application/pdf", b"%PDF")
    url = next(u for m, u, _ in fake.requests if "/download/" in u)
    assert url == "https://api.test/jmap/download/acc1/blob1/f%201.pdf?type=application%2Fpdf"
    with pytest.raises(JmapError, match="1 bijlage"):
        client.get_attachment("e1", 3)


def test_mark_read_unread(client, fake):
    client.set_flag("e2", "INBOX", "\\Seen", True)
    assert fake.mails["e2"]["keywords"] == {"$seen": True}
    client.set_flag("e2", "INBOX", "\\Seen", False)
    assert fake.mails["e2"]["keywords"] == {}


def test_move_keeps_other_labels(client, fake):
    client.move("e2", "INBOX", "Facturen/2026")
    assert fake.mails["e2"]["mailboxIds"] == {"b-fact": True, "b-2026": True}


def test_trash_replaces_all_mailboxes(client, fake):
    client.move_to_trash("e2")
    assert fake.mails["e2"]["mailboxIds"] == {"b-trash": True}


def test_spam_and_not_spam(client, fake):
    client.mark_spam("e1", "INBOX")
    assert fake.mails["e1"]["mailboxIds"] == {"b-junk": True}
    assert fake.mails["e1"]["keywords"].get("$junk") is True
    client.mark_spam("e1", "Spam", spam=False)
    assert fake.mails["e1"]["mailboxIds"] == {"b-inbox": True}
    assert "$junk" not in fake.mails["e1"]["keywords"] and fake.mails["e1"]["keywords"]["$notjunk"] is True


def test_move_failure_is_reported(client, fake):
    fake.run_orig = fake.run

    def failing(name, args):
        if name == "Email/set":
            return {"notUpdated": {"e1": {"type": "forbidden"}}}
        return fake.run_orig(name, args)

    fake.run = failing
    with pytest.raises(JmapError, match="forbidden"):
        client.mark_spam("e1")


def test_build_message_reply_and_send(client, fake):
    msg = client.build_message("x@y.be", "Hoi", "Tekst", cc="c@y.be", reply_to_uid="e1")
    assert msg["From"] == "me@fastmail.com" and msg["In-Reply-To"] == "<abc@x.be>"
    client.send(msg)
    sent = message_from_bytes(fake.uploads[0])
    assert sent["Subject"] == "Hoi"
    imp = next(a for n, a in fake.calls if n == "Email/import")
    assert imp["emails"]["m"]["mailboxIds"] == {"b-drafts": True}
    sub = next(a for n, a in fake.calls if n == "EmailSubmission/set")
    assert [r["email"] for r in sub["create"]["s"]["envelope"]["rcptTo"]] == ["x@y.be", "c@y.be"]
    assert sub["onSuccessUpdateEmail"]["#s"]["mailboxIds/b-sent"] is True


def test_send_without_recipient_fails(client):
    msg = client.build_message("", "Hoi", "Tekst")
    with pytest.raises(JmapError, match="ontvanger"):
        client.send(msg)
