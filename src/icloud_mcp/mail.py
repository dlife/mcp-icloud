"""iCloud Mail via IMAP/SMTP (standaardbibliotheek, geen extra afhankelijkheden)."""
import email
import imaplib
import smtplib
from contextlib import contextmanager
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .config import Config

MAX_BODY_CHARS = 20_000


class MailClient:
    def __init__(self, config: Config):
        self.config = config

    @contextmanager
    def _imap(self):
        conn = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        try:
            conn.login(self.config.mail_login, self.config.app_password)
            yield conn
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    @staticmethod
    def _quote(folder: str) -> str:
        return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'

    @staticmethod
    def _decode(value: str | None) -> str:
        if not value:
            return ""
        return str(make_header(decode_header(value)))

    def list_folders(self) -> list[str]:
        with self._imap() as conn:
            status, data = conn.list()
            if status != "OK":
                raise RuntimeError("Mappen ophalen mislukt")
            folders = []
            for raw in data:
                line = raw.decode("utf-8", "replace")
                folders.append(line.rsplit(' "/" ', 1)[-1].strip().strip('"'))
            return folders

    def search(self, folder: str = "INBOX", query: str = "ALL", limit: int = 20) -> list[dict]:
        """`query` is een IMAP-zoekopdracht, bv. 'UNSEEN', 'FROM "jan"' of 'SUBJECT "factuur"'."""
        limit = max(1, min(limit, 100))
        with self._imap() as conn:
            status, _ = conn.select(self._quote(folder), readonly=True)
            if status != "OK":
                raise RuntimeError(f"Map niet gevonden: {folder}")
            status, data = conn.uid("SEARCH", None, query)
            if status != "OK":
                raise RuntimeError("Ongeldige zoekopdracht")
            uids = data[0].split()[-limit:][::-1]
            results = []
            for uid in uids:
                status, msg_data = conn.uid(
                    "FETCH", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])"
                )
                if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                meta, header_bytes = msg_data[0]
                headers = email.message_from_bytes(header_bytes)
                results.append(
                    {
                        "uid": uid.decode(),
                        "from": self._decode(headers["From"]),
                        "to": self._decode(headers["To"]),
                        "subject": self._decode(headers["Subject"]),
                        "date": headers["Date"] or "",
                        "unread": b"\\Seen" not in meta,
                    }
                )
            return results

    def read(self, uid: str, folder: str = "INBOX") -> dict:
        with self._imap() as conn:
            status, _ = conn.select(self._quote(folder), readonly=True)
            if status != "OK":
                raise RuntimeError(f"Map niet gevonden: {folder}")
            status, msg_data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                raise RuntimeError(f"Bericht {uid} niet gevonden in {folder}")
            msg = email.message_from_bytes(msg_data[0][1])
            body, attachments = self._extract(msg)
            return {
                "uid": uid,
                "from": self._decode(msg["From"]),
                "to": self._decode(msg["To"]),
                "cc": self._decode(msg["Cc"]),
                "subject": self._decode(msg["Subject"]),
                "date": msg["Date"] or "",
                "message_id": msg["Message-ID"] or "",
                "body": body[:MAX_BODY_CHARS],
                "truncated": len(body) > MAX_BODY_CHARS,
                "attachments": attachments,
            }

    @staticmethod
    def _extract(msg: email.message.Message) -> tuple[str, list[str]]:
        plain, html, attachments = [], [], []
        for part in msg.walk():
            if part.is_multipart():
                continue
            filename = part.get_filename()
            if filename or part.get_content_disposition() == "attachment":
                attachments.append(MailClient._decode(filename) or "naamloos")
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
            if part.get_content_type() == "text/plain":
                plain.append(text)
            elif part.get_content_type() == "text/html":
                html.append(text)
        return "\n".join(plain) or "\n".join(html), attachments

    def set_flag(self, uid: str, folder: str, flag: str, value: bool) -> None:
        with self._imap() as conn:
            status, _ = conn.select(self._quote(folder))
            if status != "OK":
                raise RuntimeError(f"Map niet gevonden: {folder}")
            op = "+FLAGS" if value else "-FLAGS"
            status, _ = conn.uid("STORE", uid, op, f"({flag})")
            if status != "OK":
                raise RuntimeError("Vlag wijzigen mislukt")

    def move_to_trash(self, uid: str, folder: str = "INBOX", trash: str = "Deleted Messages") -> None:
        with self._imap() as conn:
            status, _ = conn.select(self._quote(folder))
            if status != "OK":
                raise RuntimeError(f"Map niet gevonden: {folder}")
            status, _ = conn.uid("COPY", uid, self._quote(trash))
            if status != "OK":
                raise RuntimeError(f"Verplaatsen naar {trash} mislukt")
            conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            conn.expunge()

    def build_message(self, to: str, subject: str, body: str, cc: str = "", reply_to_uid: str = "", folder: str = "INBOX") -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.config.from_address
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.config.from_address.split("@")[-1])
        if reply_to_uid:
            original = self.read(reply_to_uid, folder)
            if original["message_id"]:
                msg["In-Reply-To"] = original["message_id"]
                msg["References"] = original["message_id"]
        msg.set_content(body)
        return msg

    def send(self, msg: EmailMessage) -> None:
        recipients = [a for field in ("To", "Cc") if msg[field] for a in msg[field].split(",")]
        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(self.config.mail_login, self.config.app_password)
            smtp.send_message(msg, to_addrs=[a.strip() for a in recipients])
