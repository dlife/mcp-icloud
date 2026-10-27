from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar as ICal
from icalendar import Event as IEvent

from icloud_mcp import agenda as agenda_module
from icloud_mcp.agenda import AgendaClient
from icloud_mcp.config import Config

BRU = ZoneInfo("Europe/Brussels")


def make_component(uid, summary, start, end=None, location=""):
    ev = IEvent()
    ev.add("uid", uid)
    ev.add("summary", summary)
    ev.add("dtstart", start)
    if end:
        ev.add("dtend", end)
    if location:
        ev.add("location", location)
    return ev


class FakeObj:
    def __init__(self, comp):
        self.icalendar_component = comp
        self.deleted = False

    def delete(self):
        self.deleted = True


class FakeCalendar:
    def __init__(self, name, events=(), components=("VEVENT",)):
        self.name, self.objs, self.components, self.saved = name, [FakeObj(c) for c in events], list(components), []
        self.url = f"https://caldav.example/{name}/"

    def get_display_name(self):
        return self.name

    def get_supported_components(self):
        return self.components

    def search(self, **kw):
        self.last_search = kw
        return self.objs

    def save_event(self, ical):
        self.saved.append(ical)

    def event_by_uid(self, uid):
        return next(o for o in self.objs if str(o.icalendar_component["uid"]) == uid)


@pytest.fixture
def setup(monkeypatch):
    work = FakeCalendar("Werk", [
        make_component("b", "Tandarts", datetime(2026, 10, 29, 14, 0, tzinfo=BRU), datetime(2026, 10, 29, 15, 0, tzinfo=BRU), "Gent"),
        make_component("a", "Verjaardag", date(2026, 10, 28), date(2026, 10, 29)),
    ])
    reminders = FakeCalendar("Herinneringen", components=("VTODO",))
    calendars = [work, reminders]

    class FakePrincipal:
        def calendars(self):
            return calendars

    class FakeClient:
        def __init__(self, **kw):
            self.kw = kw

        def principal(self):
            return FakePrincipal()

    monkeypatch.setattr(agenda_module.caldav, "DAVClient", FakeClient)
    return AgendaClient(Config("a@gmail.com", "pw", "https://x.example", "een-lang-wachtwoord")), work


def test_only_event_calendars_are_listed(setup):
    client, _ = setup
    assert [c["name"] for c in client.list_calendars()] == ["Werk"]


def test_events_sorted_with_all_day_flag(setup):
    client, work = setup
    events = client.list_events("2026-10-28", "2026-10-29")
    assert [e["summary"] for e in events] == ["Verjaardag", "Tandarts"]
    assert events[0]["all_day"] is True and events[1]["all_day"] is False
    assert events[1]["location"] == "Gent"
    # alleen een datum als einde telt die hele dag mee
    assert work.last_search["end"] == datetime(2026, 10, 30, tzinfo=BRU)
    assert work.last_search["expand"] is True


def test_unknown_calendar_raises(setup):
    client, _ = setup
    with pytest.raises(RuntimeError):
        client.list_events("2026-10-28", "2026-10-29", "Bestaat niet")


def test_build_event_defaults_to_one_hour_in_local_time(setup):
    client, _ = setup
    uid, ical = client.build_event("Lunch", "2026-11-02T12:00")
    comp = next(c for c in ICal.from_ical(ical).walk("VEVENT"))
    assert str(comp["uid"]) == uid
    assert comp["dtstart"].dt == datetime(2026, 11, 2, 12, 0, tzinfo=BRU)
    assert comp["dtend"].dt == datetime(2026, 11, 2, 13, 0, tzinfo=BRU)


def test_all_day_event_ends_next_day(setup):
    client, _ = setup
    _, ical = client.build_event("Vrij", "2026-11-02", "2026-11-03", all_day=True)
    comp = next(c for c in ICal.from_ical(ical).walk("VEVENT"))
    assert comp["dtstart"].dt == date(2026, 11, 2)
    assert comp["dtend"].dt == date(2026, 11, 4)


def test_create_and_delete(setup):
    client, work = setup
    client.create_event("werk", summary="Lunch", start="2026-11-02T12:00")
    assert len(work.saved) == 1 and "Lunch" in work.saved[0]
    client.delete_event("b", "Werk")
    assert work.objs[0].deleted is True
