import os
import stat

from icloud_mcp import setup


def test_valid_url():
    assert setup.valid_url("https://icloud.example.com")
    assert not setup.valid_url("http://icloud.example.com")
    assert not setup.valid_url("icloud.example.com")


def test_clean_password_strips_spaces():
    assert setup.clean_password("abcd efgh ijkl mnop") == "abcdefghijklmnop"


def test_render_env_derives_allowed_host():
    text = setup.render_env({
        "ICLOUD_APPLE_ID": "a@b.be", "ICLOUD_MAIL_ADDRESS": "", "ICLOUD_APP_PASSWORD": "x",
        "ICLOUD_TIMEZONE": "Europe/Brussels", "MCP_PUBLIC_URL": "https://icloud.example.com",
        "MCP_OWNER_PASSWORD": "geheim-geheim-1",
    })
    assert "MCP_ALLOWED_HOSTS=icloud.example.com" in text
    assert "GMAIL_ADDRESS=\n" in text


def test_wizard_writes_private_env_that_config_accepts(tmp_path, monkeypatch):
    answers = iter(["en", "me@gmail.com", "", "n", "https://icloud.example.com/", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda *_: "abcd-efgh-ijkl-mnop")
    out = tmp_path / ".env"
    assert setup.main(["--out", str(out), "--skip-checks"]) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    env = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line and not line.startswith("#"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from icloud_mcp.config import Config
    config = Config.from_env()
    assert config.public_url == "https://icloud.example.com"
    assert config.allowed_hosts == ("icloud.example.com",)


def test_check_logins_reports_errors(monkeypatch):
    class Boom:
        def __init__(self, *a):
            pass

        def list_folders(self):
            raise RuntimeError("AUTHENTICATIONFAILED")

        def list_calendars(self):
            return []

    monkeypatch.setattr("icloud_mcp.mail.MailClient", Boom)
    monkeypatch.setattr("icloud_mcp.agenda.AgendaClient", Boom)
    result = setup.check_logins({
        "ICLOUD_APPLE_ID": "a@b.be", "ICLOUD_MAIL_ADDRESS": "", "ICLOUD_APP_PASSWORD": "x",
        "ICLOUD_TIMEZONE": "Europe/Brussels", "MCP_PUBLIC_URL": "https://x.be", "MCP_OWNER_PASSWORD": "y" * 12,
    })
    assert result["iCloud Mail"] == "AUTHENTICATIONFAILED"
    assert result["iCloud Agenda"] is None
