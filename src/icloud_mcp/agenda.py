"""iCloud Agenda via CalDAV (Apple-ID + app-specifiek wachtwoord)."""
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import caldav
from icalendar import Calendar as ICal
from icalendar import Event as IEvent

from .config import Config

MAX_EVENTS = 200


class AgendaClient:
    def __init__(self, config: Config):
        self.config = config
        self.tz = ZoneInfo(config.timezone)

    def _principal(self):
        client = caldav.DAVClient(url=self.config.caldav_url, username=self.config.apple_id, password=self.config.app_password)
        return client.principal()

    def _calendars(self, only_events: bool = True):
        calendars = self._principal().calendars()
        if only_events:
            calendars = [c for c in calendars if "VEVENT" in (c.get_supported_components() or ["VEVENT"])]
        return calendars

    def _find(self, name: str):
        for cal in self._calendars():
            if (cal.get_display_name() or "").lower() == name.lower():
                return cal
        raise RuntimeError(f"Agenda niet gevonden: {name}")

    def parse(self, value: str, end_of_day: bool = False) -> datetime | date:
        """ISO-tekst naar datetime (lokale tijd als er geen zone staat) of date (alleen datum)."""
        value = value.strip()
        if len(value) == 10:
            day = date.fromisoformat(value)
            return datetime.combine(day, datetime.min.time(), self.tz) + (timedelta(days=1) if end_of_day else timedelta())
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=self.tz)

    def list_calendars(self) -> list[dict]:
        return [{"name": c.get_display_name(), "url": str(c.url)} for c in self._calendars()]

    def list_events(self, start: str, end: str, calendar: str = "") -> list[dict]:
        begin, finish = self.parse(start), self.parse(end, end_of_day=True)
        calendars = [self._find(calendar)] if calendar else self._calendars()
        events = []
        for cal in calendars:
            for obj in cal.search(start=begin, end=finish, event=True, expand=True):
                comp = obj.icalendar_component
                events.append(self._to_dict(comp, cal.get_display_name()))
        events.sort(key=lambda e: e["start"])
        return events[:MAX_EVENTS]

    def _to_dict(self, comp, calendar_name: str | None) -> dict:
        start = comp.get("dtstart").dt
        end = comp.get("dtend").dt if comp.get("dtend") else start
        all_day = not isinstance(start, datetime)
        return {
            "uid": str(comp.get("uid", "")),
            "calendar": calendar_name,
            "summary": str(comp.get("summary", "")),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "all_day": all_day,
            "location": str(comp.get("location", "")),
            "description": str(comp.get("description", ""))[:2000],
        }

    def build_event(self, summary: str, start: str, end: str = "", location: str = "", description: str = "", all_day: bool = False) -> tuple[str, str]:
        """Geeft (uid, ical-tekst). Zonder einde: 1 uur, of de hele dag bij all_day."""
        event = IEvent()
        uid = str(uuid.uuid4())
        event.add("uid", uid)
        event.add("summary", summary)
        event.add("dtstamp", datetime.now(self.tz))
        if all_day:
            first = self.parse(start).date()
            last = self.parse(end).date() if end else first
            event.add("dtstart", first)
            event.add("dtend", last + timedelta(days=1))
        else:
            begin = self.parse(start)
            event.add("dtstart", begin)
            event.add("dtend", self.parse(end) if end else begin + timedelta(hours=1))
        if location:
            event.add("location", location)
        if description:
            event.add("description", description)
        cal = ICal()
        cal.add("prodid", "-//icloud-mcp//NL")
        cal.add("version", "2.0")
        cal.add_component(event)
        return uid, cal.to_ical().decode()

    def create_event(self, calendar: str, **fields) -> str:
        uid, ical = self.build_event(**fields)
        self._find(calendar).save_event(ical)
        return uid

    def delete_event(self, uid: str, calendar: str) -> None:
        self._find(calendar).event_by_uid(uid).delete()
