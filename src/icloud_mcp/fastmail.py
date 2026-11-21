"""Fastmail via JMAP (HTTPS + JSON, RFC 8620/8621) met een API-token.

Heeft dezelfde methoden als `MailClient` (IMAP), zodat de MCP-tools niet weten welk account erachter zit.
`uid` is hier het JMAP-e-mail-id. Het token staat alleen in de Authorization-header; nooit in logs of foutmeldingen.
"""
import re
import shlex
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid
from urllib.parse import quote

import httpx

from .attachments import _HTMLText
from .config import Config

MAX_BODY_CHARS = 20_000
CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"
SUBMISSION = "urn:ietf:params:jmap:submission"
USING = [CORE, MAIL, SUBMISSION]

# IMAP-vlaggen (zoals mail_mark ze doorgeeft) naar JMAP-keywords.
KEYWORDS = {"\\Seen": "$seen", "\\Flagged": "$flagged", "\\Answered": "$answered", "\\Draft": "$draft"}
LIST_PROPS = ["id", "from", "to", "subject", "receivedAt", "keywords"]


class JmapError(RuntimeError):
    pass


def _addresses(items) -> str:
    return ", ".join(
        f"{i['name']} <{i['email']}>" if i.get("name") else i.get("email", "") for i in (items or [])
    )


def _html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


class FastmailClient:
    def __init__(self, config: Config, http: httpx.Client | None = None):
        if not config.fastmail_api_token:
            raise RuntimeError("Fastmail is niet ingesteld (FASTMAIL_API_TOKEN)")
        self.config = config
        self._token = config.fastmail_api_token
        self._http = http or httpx.Client(timeout=30, follow_redirects=False)
        self._session: dict | None = None
        self._identities: list[dict] | None = None

    # ---- verbinding -------------------------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token}", **kwargs.pop("headers", {})}
        resp = self._http.request(method, url, headers=headers, **kwargs)
        if resp.status_code in (401, 403):
            raise JmapError("Fastmail weigerde het token (ongeldig, ingetrokken of te weinig rechten)")
        if resp.status_code >= 400:
            raise JmapError(f"Fastmail gaf HTTP {resp.status_code}")
        return resp

    @property
    def session(self) -> dict:
        if self._session is None:
            data = self._request("GET", self.config.fastmail_session_url).json()
            account = (data.get("primaryAccounts") or {}).get(MAIL)
            if not account:
                raise JmapError("Dit Fastmail-token geeft geen toegang tot e-mail")
            data["accountId"] = account
            self._session = data
        return self._session

    @property
    def account_id(self) -> str:
        return self.session["accountId"]

    def call(self, *calls: tuple) -> list:
        """Voer JMAP-methodes uit en geef per call de argumenten terug; een fout wordt een JmapError."""
        body = {"using": USING, "methodCalls": [[name, {"accountId": self.account_id, **args}, tag] for name, args, tag in calls]}
        data = self._request("POST", self.session["apiUrl"], json=body).json()
        out = []
        for name, args, _tag in data.get("methodResponses", []):
            if name == "error":
                raise JmapError(f"Fastmail-fout: {args.get('type', 'onbekend')}")
            out.append(args)
        return out

    @staticmethod
    def _check_changes(result: dict) -> None:
        for field in ("notUpdated", "notCreated", "notDestroyed"):
            failed = result.get(field)
            if failed:
                first = next(iter(failed.values()))
                raise JmapError(f"Wijzigen mislukt: {first.get('type', 'onbekend')}")

    # ---- mappen -----------------------------------------------------------------------------

    def _mailboxes(self) -> list[dict]:
        (res,) = self.call(("Mailbox/get", {"properties": ["id", "name", "parentId", "role"]}, "m"))
        boxes = {b["id"]: b for b in res["list"]}
        for box in boxes.values():
            parts, cur = [box["name"]], box
            while cur.get("parentId") and cur["parentId"] in boxes:
                cur = boxes[cur["parentId"]]
                parts.append(cur["name"])
            box["path"] = "/".join(reversed(parts))
        return list(boxes.values())

    def _mailbox_id(self, folder: str, boxes: list[dict] | None = None) -> str:
        """Map zoeken op naam of pad; INBOX, Trash, Junk, Sent, Drafts, Archive ook via hun rol."""
        boxes = boxes or self._mailboxes()
        wanted = folder.strip().lower()
        roles = {"inbox": "inbox", "trash": "trash", "junk": "junk", "spam": "junk", "sent": "sent", "drafts": "drafts", "archive": "archive"}
        by_role = [b for b in boxes if b.get("role") and b["role"] == roles.get(wanted)]
        if by_role:
            return by_role[0]["id"]
        for key in ("path", "name"):
            for box in boxes:
                if box[key].lower() == wanted:
                    return box["id"]
        raise JmapError(f"Map niet gevonden: {folder}")

    def _role_id(self, role: str, boxes: list[dict] | None = None) -> str:
        for box in boxes or self._mailboxes():
            if box.get("role") == role:
                return box["id"]
        raise JmapError(f"Fastmail heeft geen map met rol {role}")

    def list_folders(self) -> list[str]:
        return sorted(b["path"] for b in self._mailboxes())

    # ---- zoeken en lezen --------------------------------------------------------------------

    @staticmethod
    def _filter_from_query(query: str) -> dict:
        """Vertaal een IMAP-achtige zoekopdracht (UNSEEN, FROM "jan", SUBJECT "x", SINCE 01-Oct-2026 ...) naar een JMAP-filter.
        Alles wat niet herkend wordt, telt als vrije tekst. Condities worden met EN gecombineerd."""
        try:
            tokens = shlex.split(query)
        except ValueError:
            tokens = [query]
        flt: dict = {}
        free: list[str] = []
        i = 0
        simple = {"UNSEEN": ("notKeyword", "$seen"), "SEEN": ("hasKeyword", "$seen"),
                  "FLAGGED": ("hasKeyword", "$flagged"), "UNFLAGGED": ("notKeyword", "$flagged")}
        valued = {"FROM": "from", "TO": "to", "CC": "cc", "SUBJECT": "subject", "BODY": "body", "TEXT": "text"}
        dates = {"SINCE": "after", "BEFORE": "before"}
        while i < len(tokens):
            tok = tokens[i].upper()
            if tok == "ALL":
                pass
            elif tok in simple:
                flt[simple[tok][0]] = simple[tok][1]
            elif tok == "HASATTACHMENT":
                flt["hasAttachment"] = True
            elif tok in valued and i + 1 < len(tokens):
                i += 1
                flt[valued[tok]] = tokens[i]
            elif tok in dates and i + 1 < len(tokens):
                i += 1
                try:
                    day = datetime.strptime(tokens[i], "%d-%b-%Y")
                except ValueError:
                    try:
                        day = datetime.fromisoformat(tokens[i])
                    except ValueError:
                        raise JmapError(f"Ongeldige datum: {tokens[i]} (gebruik bv. 01-Oct-2026 of 2026-10-01)")
                flt[dates[tok]] = day.strftime("%Y-%m-%dT00:00:00Z")
            else:
                free.append(tokens[i])
            i += 1
        if free:
            flt["text"] = " ".join(free)
        return flt

    def search(self, folder: str = "INBOX", query: str = "ALL", limit: int = 20) -> list[dict]:
        """`folder` is een mapnaam of pad; `*` zoekt in alle mappen. `query` als IMAP-zoekopdracht (zie _filter_from_query)."""
        limit = max(1, min(limit, 100))
        flt = self._filter_from_query(query)
        if folder not in ("*", ""):
            flt["inMailbox"] = self._mailbox_id(folder)
        q, g = self.call(
            ("Email/query", {"filter": flt, "sort": [{"property": "receivedAt", "isAscending": False}], "limit": limit}, "q"),
            ("Email/get", {"#ids": {"resultOf": "q", "name": "Email/query", "path": "/ids"}, "properties": LIST_PROPS}, "g"),
        )
        by_id = {m["id"]: m for m in g["list"]}
        results = []
        for mail_id in q["ids"]:
            m = by_id.get(mail_id)
            if not m:
                continue
            results.append({
                "uid": m["id"],
                "from": _addresses(m.get("from")),
                "to": _addresses(m.get("to")),
                "subject": m.get("subject") or "",
                "date": m.get("receivedAt") or "",
                "unread": "$seen" not in (m.get("keywords") or {}),
            })
        return results

    def _get(self, uid: str, properties: list[str], **extra) -> dict:
        (res,) = self.call(("Email/get", {"ids": [uid], "properties": properties, **extra}, "g"))
        if not res["list"]:
            raise JmapError(f"Bericht {uid} niet gevonden")
        return res["list"][0]

    def read(self, uid: str, folder: str = "INBOX") -> dict:
        m = self._get(
            uid,
            ["id", "from", "to", "cc", "subject", "receivedAt", "messageId", "textBody", "htmlBody", "bodyValues", "attachments"],
            fetchTextBodyValues=True, fetchHTMLBodyValues=True, maxBodyValueBytes=MAX_BODY_CHARS * 4,
        )
        values = m.get("bodyValues") or {}
        text_parts = [p for p in m.get("textBody") or [] if p.get("partId") in values]
        if text_parts and all(p.get("type") == "text/plain" for p in text_parts):
            body = "\n".join(values[p["partId"]]["value"] for p in text_parts)
        else:
            html_parts = [p for p in (m.get("htmlBody") or text_parts) if p.get("partId") in values]
            body = "\n".join(
                _html_to_text(values[p["partId"]]["value"]) if p.get("type") == "text/html" else values[p["partId"]]["value"]
                for p in html_parts
            )
        message_ids = m.get("messageId") or []
        return {
            "uid": uid,
            "from": _addresses(m.get("from")),
            "to": _addresses(m.get("to")),
            "cc": _addresses(m.get("cc")),
            "subject": m.get("subject") or "",
            "date": m.get("receivedAt") or "",
            "message_id": f"<{message_ids[0]}>" if message_ids else "",
            "body": body[:MAX_BODY_CHARS],
            "truncated": len(body) > MAX_BODY_CHARS,
            "attachments": [
                {"index": i, "name": a.get("name") or "naamloos", "type": a.get("type") or "", "size": a.get("size") or 0}
                for i, a in enumerate(m.get("attachments") or [])
            ],
        }

    def get_attachment(self, uid: str, index: int, folder: str = "INBOX") -> tuple[str, str, bytes]:
        """Geef (naam, mime-type, bytes) van bijlage `index`; leest niets als gelezen af."""
        attachments = self._get(uid, ["attachments"]).get("attachments") or []
        if not 0 <= index < len(attachments):
            raise JmapError(f"Bijlage {index} bestaat niet (deze mail heeft {len(attachments)} bijlage(n))")
        part = attachments[index]
        name = part.get("name") or "naamloos"
        ctype = part.get("type") or "application/octet-stream"
        url = (self.session["downloadUrl"]
               .replace("{accountId}", quote(self.account_id, safe=""))
               .replace("{blobId}", quote(part["blobId"], safe=""))
               .replace("{type}", quote(ctype, safe=""))
               .replace("{name}", quote(name, safe="")))
        return name, ctype, self._request("GET", url).content

    # ---- wijzigen ---------------------------------------------------------------------------

    def _update(self, uid: str, patch: dict) -> None:
        (res,) = self.call(("Email/set", {"update": {uid: patch}}, "s"))
        self._check_changes(res)

    def set_flag(self, uid: str, folder: str, flag: str, value: bool) -> None:
        keyword = KEYWORDS.get(flag, flag.lstrip("\\").lower())
        self._update(uid, {f"keywords/{keyword}": True if value else None})

    def _move(self, uid: str, folder: str, target_id: str, extra: dict | None = None) -> None:
        """Zet de mail in `target_id` en haal hem uit `folder` (of uit alle mappen als die onbekend is)."""
        current = self._get(uid, ["mailboxIds"]).get("mailboxIds") or {}
        patch: dict = dict(extra or {})
        source = None
        if folder and folder != "*":
            try:
                source = self._mailbox_id(folder)
            except JmapError:
                source = None
        if source in current:
            patch[f"mailboxIds/{source}"] = None
        else:
            patch.update({f"mailboxIds/{mid}": None for mid in current})
        patch[f"mailboxIds/{target_id}"] = True
        self._update(uid, patch)

    def move(self, uid: str, folder: str, target: str) -> None:
        """Verplaats een mail naar een andere map (naam of pad). Bij Fastmail kan een mail in meer mappen staan; andere blijven."""
        self._move(uid, folder, self._mailbox_id(target))

    def move_to_trash(self, uid: str, folder: str = "INBOX", trash: str = "") -> None:
        target = self._mailbox_id(trash) if trash else self._role_id("trash")
        # Prullenbak vervangt alle mappen: een weggegooide mail hoort nergens anders meer te staan.
        current = self._get(uid, ["mailboxIds"]).get("mailboxIds") or {}
        patch = {f"mailboxIds/{mid}": None for mid in current if mid != target}
        patch[f"mailboxIds/{target}"] = True
        self._update(uid, patch)

    def mark_spam(self, uid: str, folder: str = "INBOX", spam: bool = True) -> None:
        """Spam: naar Junk en $junk (leert het filter van Fastmail). Geen spam: terug naar Inbox met $notjunk."""
        boxes = self._mailboxes()
        if spam:
            self._move(uid, folder, self._role_id("junk", boxes), {"keywords/$junk": True, "keywords/$notjunk": None})
        else:
            self._move(uid, folder, self._role_id("inbox", boxes), {"keywords/$notjunk": True, "keywords/$junk": None})

    # ---- versturen --------------------------------------------------------------------------

    def _identity(self) -> dict:
        if self._identities is None:
            (res,) = self.call(("Identity/get", {}, "i"))
            self._identities = res["list"]
            if not self._identities:
                raise JmapError("Fastmail heeft geen afzenderidentiteit voor dit token")
        wanted = self.config.fastmail_address.lower()
        for identity in self._identities:
            if wanted and identity["email"].lower() == wanted:
                return identity
        return self._identities[0]

    @property
    def address(self) -> str:
        return self._identity()["email"]

    def build_message(self, to: str, subject: str, body: str, cc: str = "", reply_to_uid: str = "", folder: str = "INBOX") -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.address.split("@")[-1])
        if reply_to_uid:
            original = self.read(reply_to_uid, folder)
            if original["message_id"]:
                msg["In-Reply-To"] = original["message_id"]
                msg["References"] = original["message_id"]
        msg.set_content(body)
        return msg

    def send(self, msg: EmailMessage) -> None:
        recipients = [addr for _, addr in getaddresses([msg["To"] or "", msg["Cc"] or ""]) if addr]
        if not recipients:
            raise JmapError("Geen geldige ontvanger")
        identity = self._identity()
        boxes = self._mailboxes()
        drafts, sent = self._role_id("drafts", boxes), self._role_id("sent", boxes)
        upload_url = self.session["uploadUrl"].replace("{accountId}", quote(self.account_id, safe=""))
        blob = self._request("POST", upload_url, content=msg.as_bytes(), headers={"Content-Type": "message/rfc822"}).json()["blobId"]
        imported, submitted = self.call(
            ("Email/import", {"emails": {"m": {"blobId": blob, "mailboxIds": {drafts: True}, "keywords": {"$draft": True, "$seen": True}}}}, "imp"),
            ("EmailSubmission/set", {
                "create": {"s": {"identityId": identity["id"], "emailId": "#m",
                                 "envelope": {"mailFrom": {"email": identity["email"]}, "rcptTo": [{"email": r} for r in recipients]}}},
                "onSuccessUpdateEmail": {"#s": {"keywords/$draft": None, f"mailboxIds/{drafts}": None, f"mailboxIds/{sent}": True}},
            }, "sub"),
        )
        for result in (imported, submitted):
            self._check_changes(result)
