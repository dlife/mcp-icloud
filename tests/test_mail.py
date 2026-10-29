from email.message import EmailMessage

import pytest

from icloud_mcp import mail as mail_module
from icloud_mcp.config import Config
from icloud_mcp.mail import MailClient

RAW = EmailMessage()
RAW["From"] = "=?utf-8?q?J=C3=A9r=C3=B4me?= <j@x.be>"
RAW["To"] = "a@icloud.com"
RAW["Subject"] = "Factuur"
RAW["Date"] = "Mon, 26 Oct 2026 10:00:00 +0100"
RAW["Message-ID"] = "<abc@x.be>"
RAW.set_content("Hallo wereld")
RAW.add_attachment(b"data", maintype="application", subtype="pdf", filename="f.pdf")


class FakeIMAP:
    def __init__(self, *a):
        self.calls = []

    def login(self, *a):
        pass

    def logout(self):
        pass

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Sent Messages"']

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder))
        return ("OK", [b"1"]) if "Nope" not in folder else ("NO", [])

    def uid(self, cmd, *args):
        self.calls.append((cmd, args))
        if cmd == "SEARCH":
            return "OK", [b"1 2 3"]
        if cmd == "FETCH" and "HEADER" in args[1]:
            header = (b"Subject: Onderwerp\r\nFrom: j@x.be\r\nTo: a@icloud.com\r\nDate: today\r\n\r\n")
            return "OK", [(b"1 (FLAGS (\\Seen))", header), b")"]
        if cmd == "FETCH":
            return "OK", [(b"1 (BODY[])", RAW.as_bytes()), b")"]
        return "OK", [b""]

    def expunge(self):
        self.calls.append(("expunge",))


@pytest.fixture
def client(monkeypatch):
    fake = FakeIMAP()
    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", lambda *a: fake)
    c = MailClient(Config("a@icloud.com", "pw", "https://icloud.example.com", "een-lang-wachtwoord"))
    c.fake = fake
    return c


def test_list_folders(client):
    assert client.list_folders() == ["INBOX", "Sent Messages"]


def test_search_newest_first_with_limit(client):
    results = client.search(limit=2)
    assert [r["uid"] for r in results] == ["3", "2"]
    assert results[0]["unread"] is False


def test_read_decodes_and_lists_attachments(client):
    msg = client.read("1")
    assert msg["body"].strip() == "Hallo wereld"
    assert msg["attachments"] == [{"index": 0, "name": "f.pdf", "type": "application/pdf", "size": 4}]
    assert "Jérôme" in msg["from"]


def test_get_attachment_returns_bytes_and_checks_index(client):
    assert client.get_attachment("1", 0) == ("f.pdf", "application/pdf", b"data")
    with pytest.raises(RuntimeError, match="1 bijlage"):
        client.get_attachment("1", 5)


def test_unknown_folder_raises(client):
    with pytest.raises(RuntimeError):
        client.read("1", "Nope")


def test_reply_sets_threading_headers(client):
    msg = client.build_message("j@x.be", "Re: Factuur", "ok", reply_to_uid="1")
    assert msg["In-Reply-To"] == "<abc@x.be>"


def test_trash_copies_then_expunges(client):
    client.move_to_trash("1")
    kinds = [c[0] for c in client.fake.calls]
    assert kinds.index("COPY") < kinds.index("STORE") < kinds.index("expunge")


def test_login_uses_mail_address_not_apple_id(monkeypatch):
    logins = []

    class Recorder(FakeIMAP):
        def login(self, user, password):
            logins.append(user)

    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", lambda *a: Recorder())
    config = Config("dieter@gmail.com", "pw", "https://x.example", "een-lang-wachtwoord", mail_address="dieter@icloud.com")
    client = MailClient(config)
    client.list_folders()
    assert logins == ["dieter@icloud.com"]
    assert client.build_message("a@b.be", "s", "b")["From"] == "dieter@icloud.com"


@pytest.fixture
def gmail(monkeypatch):
    fake = FakeIMAP()
    hosts = []
    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", lambda *a: hosts.append(a) or fake)
    config = Config("a@icloud.com", "pw", "https://x.example", "een-lang-wachtwoord", gmail_address="me@gmail.com", gmail_app_password="abcd")
    c = MailClient(config, "gmail")
    c.fake, c.hosts = fake, hosts
    return c


def test_gmail_account_uses_gmail_host_and_address(gmail):
    gmail.list_folders()
    assert gmail.hosts == [("imap.gmail.com", 993)]
    assert gmail.build_message("a@b.be", "s", "b")["From"] == "me@gmail.com"


def test_gmail_search_syntax_uses_x_gm_raw(gmail):
    gmail.search("[Gmail]/All Mail", "gmail:has:attachment filename:pdf")
    search = next(c for c in gmail.fake.calls if c[0] == "SEARCH")
    assert search[1] == (None, "X-GM-RAW", '"has:attachment filename:pdf"')


def test_gmail_trash_in_all_mail_does_not_expunge(gmail):
    gmail.move_to_trash("1", "[Gmail]/All Mail")
    kinds = [c[0] for c in gmail.fake.calls]
    assert "COPY" in kinds and "expunge" not in kinds
    assert next(c for c in gmail.fake.calls if c[0] == "COPY")[1][1] == '"[Gmail]/Trash"'


def test_gmail_not_configured_raises():
    with pytest.raises(RuntimeError, match="Gmail is niet ingesteld"):
        MailClient(Config("a@icloud.com", "pw", "https://x.example", "een-lang-wachtwoord"), "gmail")


def test_gmail_app_password_spaces_are_stripped(monkeypatch):
    for k, v in {"MCP_OWNER_PASSWORD": "een-lang-wachtwoord", "ICLOUD_APPLE_ID": "a@b.be", "ICLOUD_APP_PASSWORD": "x", "MCP_PUBLIC_URL": "https://x.example", "GMAIL_ADDRESS": "me@gmail.com", "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop"}.items():
        monkeypatch.setenv(k, v)
    assert Config.from_env().gmail_app_password == "abcdefghijklmnop"
