from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _resolve_rule(rule: str, year: int) -> date:
    # e.g. "third-tuesday-september"
    ordinal, weekday, month = rule.split("-")
    d = date(year, MONTHS.index(month) + 1, 1)
    offset = (WEEKDAYS.index(weekday) - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (ORDINALS[ordinal] - 1))


def _occurrence(event: dict, year: int) -> date:
    if "rule" in event:
        return _resolve_rule(event["rule"], year)
    month, day = map(int, event["date"].split("-"))
    return date(year, month, day)


def upcoming(events: list[dict], today: date, days: int = 60) -> list[dict]:
    out = []
    for ev in events:
        for year in (today.year, today.year + 1):
            when = _occurrence(ev, year)
            if today <= when <= today + timedelta(days=days):
                out.append({**ev, "when": when, "in_days": (when - today).days})
    return sorted(out, key=lambda e: e["when"])
