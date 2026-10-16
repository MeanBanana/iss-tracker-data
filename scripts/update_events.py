"""Rebuild sstv-events.json from the ARISS upcoming-SSTV page and press RSS.

Standard library only. Run by .github/workflows/update.yml every 2 hours:

    python scripts/update_events.py --file sstv-events.json --report report.json

It never publishes by itself: when the parsed result differs from the file
(ignoring `updated`) it rewrites the file, and the workflow proposes it as a
pull request for a human to merge. A failed fetch, a parser error, a page with
no event headings (the format changed) or output that fails validate.py is
reported as "broken" so the workflow opens an issue instead.
"""

import argparse
import email.utils
import html
import json
import pathlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from validate import validate  # noqa: E402

PAGE_URL = "https://www.ariss.org/upcoming-sstv-events.html"
FEED_URL = "https://www.ariss.org/6/feed"
USER_AGENT = "iss-tracker-data-bot (+https://github.com/MeanBanana/iss-tracker-data)"
TIMEOUT_S = 30
KEEP_AFTER_END = timedelta(days=2)
FIELDS = ("id", "title", "startDate", "startTime", "endDate", "endTime",
          "frequencyHz", "mode", "pattern", "status", "url")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTH_RE = (r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
             r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_DATE_RE = re.compile(_MONTH_RE + r"\.?\s*(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s*(\d{4}))?")
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b(\s*UTC)?", re.I)
_POSTED_RE = re.compile(r"^\s*(?:(\d{4})-(\d{2})-(\d{2})|(\d{2})-(\d{2})-(\d{4}))")
_START_RE = re.compile(r"\b(begin|begins|beginning|start|starts|starting)\b", re.I)
_END_RE = re.compile(r"\b(conclude|concludes|end|ends|ending)\b", re.I)
_FREQ_RE = re.compile(r"\b(\d{3}(?:\.\d{1,4})?)\s*MHz", re.I)
_SERIES_RE = re.compile(r"\bSeries\s+(\d+)", re.I)
_QUOTED_RE = re.compile(r"[\"“”]([^\"“”]{2,60})[\"“”]")
_CANCEL_RE = re.compile(r"\b(cancel+ed|postponed)\b", re.I)
_NUM_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
              "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
_NUM = r"(\d+(?:-\d+)?|" + "|".join(_NUM_WORDS) + r")"
_PATTERN_RE = re.compile(
    _NUM + r"\s*(seconds?|secs?|minutes?|mins?)\s*on\b[,;]?\s*(?:and\s+)?(?:then\s+)?"
    + _NUM + r"(?:\s*-\s*\d+)?\s*(?:minutes?|mins?)\s*off", re.I)


# --- page text ---------------------------------------------------------------

class _Lines(HTMLParser):
    """Splits a page into text lines (at <br> and block ends), remembering
    which characters were bold. Scripts and styles are skipped."""

    _BREAKS = {"br", "p", "div", "li", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = [[]]  # each line: list of (text, bold)
        self._bold = 0
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("strong", "b"):
            self._bold += 1
        elif tag in self._BREAKS:
            self.lines.append([])

    def handle_startendtag(self, tag, attrs):
        if tag in self._BREAKS:
            self.lines.append([])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("strong", "b"):
            self._bold = max(0, self._bold - 1)
        elif tag in self._BREAKS:
            self.lines.append([])

    def handle_data(self, data):
        if not self._skip:
            self.lines[-1].append((data, self._bold > 0))


def _clean(text):
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


@dataclass
class _Entry:
    posted: date
    heading: str
    body: list = field(default_factory=list)
    url: str = PAGE_URL


def _posted(text):
    m = _POSTED_RE.match(text)
    if not m:
        return None
    try:
        if m.group(1):
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return date(int(m.group(6)), int(m.group(4)), int(m.group(5)))  # MM-DD-YYYY
    except ValueError:
        return None


def page_entries(page_html):
    """Entries in page order (newest first): a bold line starting with a
    posting date, then the lines below it."""
    parser = _Lines()
    parser.feed(page_html)
    lines = parser.lines
    # Start under the "Upcoming SSTV events" heading when there is one.
    for i, line in enumerate(lines):
        if _clean("".join(t for t, _ in line)).lower() == "upcoming sstv events":
            lines = lines[i + 1:]
            break
    entries = []
    for line in lines:
        text = _clean("".join(t for t, _ in line))
        if not text:
            continue
        bold = _clean("".join(t for t, b in line if b))
        posted = _posted(text)
        if posted and bold and _posted(bold):
            entries.append(_Entry(posted=posted, heading=text))
        elif entries:
            entries[-1].body.append(text)
    return entries


# --- field extraction ----------------------------------------------------------

def _clauses(text):
    """Lines split into sentences (keeps "Nov.12" and "437.55" together)."""
    out = []
    for line in text.split("\n"):
        out.extend(p.strip() for p in re.split(r"(?<=[A-Za-z)])\.\s+(?=[A-Z])", line) if p.strip())
    return out


def _date_in(fragment, posted):
    m = _DATE_RE.search(fragment)
    if not m:
        return None
    month = _MONTHS[m.group(1)[:3].lower()]
    day = int(m.group(2))
    try:
        if m.group(3):
            return date(int(m.group(3)), month, day)
        d = date(posted.year, month, day)
        # A December post about a January event: the year rolls over.
        if d < posted - timedelta(days=61):
            d = date(posted.year + 1, month, day)
        return d
    except ValueError:
        return None


def _time_in(fragment):
    if re.search(r"\bTBD\b", fragment, re.I):
        return None
    times = list(_TIME_RE.finditer(fragment))
    utc = [m for m in times if m.group(3)]
    if utc:
        m = utc[0]
    elif times and re.search(r"\bUTC\b", fragment, re.I):
        m = times[0]
    else:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _start_end(text, posted):
    """((startDate, startTime), (endDate, endTime)) from the first clauses
    that say when the event begins and ends."""
    start = end = None
    for clause in _clauses(text):
        s, e = _START_RE.search(clause), _END_RE.search(clause)
        if s and start is None:
            part = clause[s.start():e.start()] if e and e.start() > s.start() else clause[s.start():]
            # "…at 00:01 UTC December 5" can put the date after the keyword;
            # "Oct. 3 Start time: 12:20" before it.
            d = _date_in(part, posted) or _date_in(clause[:s.start()], posted)
            if d:
                start = (d, _time_in(part))
        if e and end is None:
            part = clause[e.start():]
            d = _date_in(part, posted) or (
                _date_in(clause[s.end():e.start()], posted) if s and s.start() < e.start() else None)
            if d:
                end = (d, _time_in(part))
    return start, end


def _pattern(text):
    m = _PATTERN_RE.search(text)
    if not m:
        return None
    on = _NUM_WORDS.get(m.group(1).lower(), m.group(1))
    off = _NUM_WORDS.get(m.group(3).lower(), m.group(3))
    unit = "s" if m.group(2).lower().startswith("s") else "min"
    return f"{on} {unit} on, {off} min off"


def _mode(text):
    if re.search(r"\bRobot\s*-?\s*36\b", text, re.I):
        return "Robot 36"
    m = re.search(r"\bPD\s*-?\s*(\d{2,3})\b", text)
    return f"PD {m.group(1)}" if m else None


def _frequency(text):
    m = _FREQ_RE.search(text)
    return round(float(m.group(1)) * 1e6) if m else None


def _title(heading, series):
    m = _QUOTED_RE.search(heading)
    if m:
        return m.group(1).strip()
    return f"ARISS SSTV Series {series}" if series else "ARISS SSTV event"


def _raw_event(entry):
    """Everything one post says, None where it doesn't say."""
    text = "\n".join([entry.heading] + entry.body)
    series = _SERIES_RE.search(entry.heading)
    series = int(series.group(1)) if series else None
    start, end = _start_end("\n".join(entry.body) or entry.heading, entry.posted)
    return {
        "series": series,
        "title": _title(entry.heading, series),
        "startDate": start[0].isoformat() if start else None,
        "startTime": start[1] if start else None,
        "endDate": end[0].isoformat() if end else None,
        "endTime": end[1] if end else None,
        "frequencyHz": _frequency(text),
        "mode": _mode(text),
        "pattern": _pattern(text),
        "cancelled": bool(_CANCEL_RE.search(text)),
        "url": entry.url,
    }


def _merge_posts(raws):
    """Posts about the same series (newest first): the newest post says
    whether it is cancelled; any other field comes from the newest post that
    gives it. Times stay with the date they were given with."""
    merged = dict(raws[0])
    for key in ("frequencyHz", "mode", "pattern"):
        merged[key] = next((r[key] for r in raws if r[key] is not None), None)
    for which in ("start", "end"):
        src = next((r for r in raws if r[f"{which}Date"]), None)
        merged[f"{which}Date"] = src[f"{which}Date"] if src else None
        same_day = [r for r in raws if r[f"{which}Date"] == merged[f"{which}Date"]]
        merged[f"{which}Time"] = next(
            (r[f"{which}Time"] for r in same_day if r[f"{which}Time"]), None)
    titled = next((r for r in raws if not r["title"].startswith("ARISS SSTV ")), raws[0])
    merged["title"] = titled["title"]
    return merged


def parse_page(page_html):
    """(events, raw heading count). Events have no `id` or `status` yet."""
    entries = page_entries(page_html)
    raws = [_raw_event(e) for e in entries]
    by_series, events = {}, []
    for r in raws:
        if r["series"] is None:
            events.append(r)
        else:
            by_series.setdefault(r["series"], []).append(r)
    events.extend(_merge_posts(group) for group in by_series.values())
    return events, len(entries)


def parse_feed(feed_xml):
    """Events announced in press releases that mention SSTV."""
    root = ET.fromstring(feed_xml)
    content = "{http://purl.org/rss/1.0/modules/content/}encoded"
    events = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        raw_html = (item.findtext(content) or "") + " " + (item.findtext("description") or "")
        text = _clean(html.unescape(re.sub(r"<[^>]+>", " ", raw_html)))
        if "SSTV" not in title + text:
            continue
        try:
            posted = email.utils.parsedate_to_datetime(item.findtext("pubDate")).date()
        except (TypeError, ValueError):
            continue
        entry = _Entry(posted=posted, heading=title, body=[text],
                       url=(item.findtext("link") or FEED_URL).strip())
        r = _raw_event(entry)
        r["title"] = _title(text, r["series"]) if r["series"] else "ARISS SSTV event"
        events.append(r)
    return events


# --- the run ---------------------------------------------------------------------

@dataclass
class Result:
    status: str  # "changed", "unchanged" or "broken"
    output: dict = None
    problems: list = field(default_factory=list)
    mismatches: list = field(default_factory=list)
    raw_headings: int = 0
    # Unended events in the current file that ARISS no longer lists.
    missing: list = field(default_factory=list)


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _assign_ids(events, current):
    """`ariss-sstv-<series>` or `ariss-sstv-<startDate>`. An event first
    published without a series number (a date id) keeps that id once ARISS
    gives the series: ids never change."""
    existing = {
        e.get("startDate"): e.get("id")
        for e in (current or {}).get("events", [])
        if re.fullmatch(r"ariss-sstv-\d{4}-\d{2}-\d{2}", str(e.get("id")))
    }
    used = set()
    for e in sorted(events, key=lambda e: (e["series"] is None, e["startDate"])):
        new = f"ariss-sstv-{e['series']}" if e["series"] else f"ariss-sstv-{e['startDate']}"
        old = existing.get(e["startDate"])
        e["id"] = old if old and old not in used else new
        used.add(e["id"])


def _strip_updated(doc):
    return {k: v for k, v in (doc or {}).items() if k != "updated"}


def run(fetch=_fetch, now=None, current=None, overrides=None, app_frequencies=()):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        page_events, headings = parse_page(fetch(PAGE_URL))
    except Exception as e:  # noqa: BLE001 - any failure means "look at the parser"
        return Result("broken", problems=[f"Could not read the ARISS page: {e}"])
    if headings == 0:
        return Result("broken", problems=[
            "The ARISS page has no event headings: its format has probably changed."])

    try:
        feed_events = parse_feed(fetch(FEED_URL))
    except Exception as e:  # noqa: BLE001 - the page is the main source
        print(f"RSS ignored: {e}", file=sys.stderr)
        feed_events = []

    events = [e for e in page_events if e["startDate"] and e["endDate"]]
    page_starts = {e["startDate"] for e in events}
    page_series = {e["series"] for e in events if e["series"]}
    for e in feed_events:  # the page wins when both have the event
        if e["startDate"] and e["endDate"] and e["startDate"] not in page_starts \
                and e["series"] not in page_series:
            events.append(e)
            page_starts.add(e["startDate"])

    cutoff = (now - KEEP_AFTER_END).date().isoformat()
    events = [e for e in events if e["endDate"] >= cutoff]
    _assign_ids(events, current)

    published = []
    for e in events:
        out = {k: e.get(k) for k in FIELDS if k not in ("id", "status")}
        out = {"id": e["id"], **out, "status": "cancelled" if e["cancelled"] else "scheduled"}
        out.update((overrides or {}).get(e["id"], {}))
        published.append({k: out.get(k) for k in FIELDS})

    # Never drop an event silently: one that hasn't ended but is gone from
    # ARISS (deleted instead of marked cancelled, or added by hand) stays as
    # it is, so phones keep it and can still get a cancellation notice. A
    # human decides. Same dates under another id: listed, not duplicated.
    today = now.date().isoformat()
    ids = {e["id"] for e in published}
    starts = {e["startDate"]: e["id"] for e in published}
    missing = []
    for old in (current or {}).get("events", []):
        if old.get("id") in ids or str(old.get("endDate") or "") < today:
            continue
        label = (f"{old.get('id')}: {old.get('title')}, "
                 f"{old.get('startDate')} to {old.get('endDate')}")
        renamed = starts.get(old.get("startDate"))
        if renamed:
            missing.append(f"{label} (ARISS now lists these dates as {renamed})")
        else:
            published.append({k: old.get(k) for k in FIELDS})
            missing.append(label)
    published.sort(key=lambda e: (e["startDate"], e["id"]))

    output = {
        "schema": 1,
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "events": published,
    }
    problems = validate(output)
    if problems:
        return Result("broken", output=output, problems=problems,
                      raw_headings=headings, missing=missing)
    mismatches = sorted({
        f"ARISS SSTV on {e['frequencyHz'] / 1e6:.3f} MHz is not a built-in app frequency"
        for e in published
        if e["frequencyHz"] is not None and e["frequencyHz"] not in app_frequencies
    })
    status = "unchanged" if _strip_updated(output) == _strip_updated(current) else "changed"
    return Result(status, output=output, mismatches=mismatches,
                  raw_headings=headings, missing=missing)


def dumps(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _load(path, default):
    p = pathlib.Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default="sstv-events.json")
    ap.add_argument("--overrides", default="overrides.json")
    ap.add_argument("--app-frequencies", default="app-frequencies.json")
    ap.add_argument("--report", help="write a JSON report here (for the workflow)")
    args = ap.parse_args(argv)

    current = _load(args.file, None)
    result = run(
        current=current,
        overrides=_load(args.overrides, {}),
        app_frequencies=_load(args.app_frequencies, []),
    )
    if result.status == "changed":
        pathlib.Path(args.file).write_text(dumps(result.output), encoding="utf-8")
    report = {
        "status": result.status,
        "problems": result.problems,
        "mismatches": result.mismatches,
        "missing": result.missing,
        "rawHeadings": result.raw_headings,
        "events": len(result.output["events"]) if result.output else 0,
    }
    if args.report:
        pathlib.Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
