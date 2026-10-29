from datetime import date

from newsdesk.calendar_events import upcoming


def test_prinsjesdag_rule():
    ev = upcoming([{"name": "Prinsjesdag", "rule": "third-tuesday-september"}], date(2026, 9, 1), 30)
    assert ev[0]["when"] == date(2026, 9, 15)


def test_wraps_into_next_year():
    ev = upcoming([{"name": "OECD", "date": "01-10"}], date(2026, 12, 20), 30)
    assert ev[0]["when"] == date(2027, 1, 10) and ev[0]["in_days"] == 21
