"""Validate sstv-events.json against the rules the ISS Tracker app applies.

The app drops or ignores bad values; here they are errors, so a typo never
reaches GitHub Pages. Usage: python scripts/validate.py sstv-events.json
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MAX_WINDOW = timedelta(days=14)
STATUSES = {"scheduled", "cancelled"}


def _in_band(hz):
    return 144e6 <= hz <= 146e6 or 435e6 <= hz <= 438e6


def _date(value, field, errors, where):
    if not isinstance(value, str) or not DATE_RE.match(value):
        errors.append(f"{where}: {field} must be YYYY-MM-DD, got {value!r}")
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        errors.append(f"{where}: {field} is not a real date: {value!r}")
        return None


def _time(value, field, errors, where):
    if value is None:
        return None
    m = TIME_RE.match(value) if isinstance(value, str) else None
    if not m:
        errors.append(f"{where}: {field} must be HH:MM (UTC) or null, got {value!r}")
        return None
    return timedelta(hours=int(m.group(1)), minutes=int(m.group(2)))


def _check_event(e, errors, where):
    if not isinstance(e, dict):
        errors.append(f"{where}: must be an object")
        return None
    eid = e.get("id")
    if not isinstance(eid, str) or not eid:
        errors.append(f"{where}: id must be a non-empty string")
    start = _date(e.get("startDate"), "startDate", errors, where)
    end = _date(e.get("endDate"), "endDate", errors, where)
    start_t = _time(e.get("startTime"), "startTime", errors, where)
    end_t = _time(e.get("endTime"), "endTime", errors, where)
    if start and end:
        ws = start + (start_t or timedelta())
        we = end + (end_t if end_t is not None else timedelta(hours=23, minutes=59, seconds=59))
        if we <= ws:
            errors.append(f"{where}: event ends before it starts")
        elif we - ws > MAX_WINDOW:
            errors.append(f"{where}: event window is longer than 14 days")
    hz = e.get("frequencyHz")
    if hz is not None and (not isinstance(hz, (int, float)) or not _in_band(hz)):
        errors.append(f"{where}: frequencyHz {hz!r} is outside 144-146 / 435-438 MHz")
    for field, limit in (("mode", 40), ("pattern", 60)):
        v = e.get(field)
        if v is not None and (not isinstance(v, str) or len(v) > limit):
            errors.append(f"{where}: {field} must be text of at most {limit} characters")
    if e.get("status", "scheduled") not in STATUSES:
        errors.append(f"{where}: status must be one of {sorted(STATUSES)}")
    return eid


def validate(data):
    """Return a list of problems (empty when the file is valid)."""
    errors = []
    if not isinstance(data, dict):
        return ["file must be a JSON object"]
    if data.get("schema") != 1:
        errors.append(f"schema must be 1, got {data.get('schema')!r}")
    updated = data.get("updated")
    try:
        datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"updated must be an ISO timestamp, got {updated!r}")
    events = data.get("events")
    if not isinstance(events, list):
        return errors + ["events must be a list"]
    seen = set()
    for i, e in enumerate(events):
        eid = _check_event(e, errors, f"events[{i}]")
        if isinstance(eid, str):
            if eid in seen:
                errors.append(f"events[{i}]: duplicate id {eid!r}")
            seen.add(eid)
    return errors


def main(argv):
    path = argv[1] if len(argv) > 1 else "sstv-events.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"{path}: {e}")
        return 1
    problems = validate(data)
    for p in problems:
        print(f"{path}: {p}")
    if not problems:
        print(f"{path}: OK ({len(data['events'])} events)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
