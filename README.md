# iss-tracker-data

Public, read-only data for the ISS Tracker app. Phones download this file and
cache it. Nothing is ever sent back.

- `sstv-events.json`: upcoming ISS SSTV events, maintained by hand from the
  [ARISS upcoming SSTV events page](https://www.ariss.org/upcoming-sstv-events.html).
  Served at https://meanbanana.github.io/iss-tracker-data/sstv-events.json

Event information © ARISS; see their site for the official schedule.

## Format (schema 1)

```json
{
  "schema": 1,
  "updated": "2026-10-15T09:00:00Z",
  "events": [
    {
      "id": "ariss-sstv-34",
      "title": "ARISS Hardware",
      "startDate": "2026-10-22",
      "startTime": null,
      "endDate": "2026-10-25",
      "endTime": null,
      "frequencyHz": 437550000,
      "mode": "Robot 36",
      "pattern": "36 s on, 2 min off",
      "status": "scheduled",
      "url": "https://www.ariss.org/upcoming-sstv-events.html"
    }
  ]
}
```

| Field | Rules |
|---|---|
| `id` | Never changes once published. `ariss-sstv-<series>`, or `ariss-sstv-<startDate>` without a series number. |
| `startDate`, `endDate` | `YYYY-MM-DD`, UTC. |
| `startTime`, `endTime` | `HH:MM` UTC, or `null` while ARISS says TBD (the app then treats the whole day as possible). |
| `frequencyHz` | 144–146 MHz or 435–438 MHz, or `null` (the app uses 437.550 MHz). This value overrides the app's built-in frequency. |
| `mode`, `pattern` | Text, at most 40 / 60 characters, or `null`. |
| `status` | `scheduled` or `cancelled`. |
| window | End after start, at most 14 days. |

An empty `events` list is fine.

## Automatic updates

The *Update events* workflow (`.github/workflows/update.yml`) runs every 2
hours (and by hand via *Run workflow*). `scripts/update_events.py` reads the
[ARISS upcoming SSTV events page](https://www.ariss.org/upcoming-sstv-events.html)
and the ARISS press-release RSS, rebuilds `sstv-events.json`, and checks it
with the unit tests and `scripts/validate.py`.

- **Changes come as a pull request**, from the branch `auto/sstv-events`
  ("Update SSTV events from ARISS"), with the diff in the description.
  Check the dates, times and frequency against ARISS and merge it; nothing
  reaches phones until you do. A later run updates the same pull request,
  and closes it if ARISS goes back to what main already has.
- **What it reads:** each bold, dated heading on the page is a post. It
  takes the series number, the quoted title, begin/end date and time ("time
  TBD" gives `null`), the frequency, the mode (Robot 36, PD 120) and the
  on/off pattern; "cancelled" or "postponed" sets `status: cancelled`. A
  date without a year takes the post's year, or the next year if that would
  put it more than 2 months before the post (a December post about January).
  Several posts about one series are combined, newest first. A press release
  about an event that isn't on the page adds it; the page wins otherwise.
- **What it publishes:** events that end today or later, plus those that
  ended in the last 2 days. An empty list is normal between events.
- **ids never change:** `ariss-sstv-<series>`, or `ariss-sstv-<startDate>`
  when there is no series yet; an event published with a date id keeps it
  after ARISS gives the series.
- **Issues it may open:**
  - *Parser needs attention*: the page couldn't be fetched, the parser
    failed, it found no dated headings (ARISS changed the page layout), or
    the result didn't pass the validator. No pull request is opened.
    Repeated failures comment on the same open issue.
  - *ARISS SSTV on … MHz is not a built-in app frequency*: an event uses a
    frequency missing from `app-frequencies.json`. Phones already use the
    event's frequency; this is a reminder to update the app's fallback.
- **Keepalive:** on the 1st of each month the workflow commits a timestamp
  to `.github/keepalive`, because GitHub disables scheduled workflows in a
  repository with no activity for 60 days.

### Fixing a value by hand: `overrides.json`

When the parser gets a field wrong, or ARISS posts a time only elsewhere
(e.g. on X), put the fix in `overrides.json` on main. It is merged field by
field over the parsed event with that `id` on every run, so it survives:

```json
{
  "ariss-sstv-34": { "startTime": "09:30", "endTime": "18:00" }
}
```

Remove the entry once ARISS's page says the same.

### Fixing "Parser needs attention"

1. Open the run linked in the issue and read the problem lines.
2. If ARISS was just down, the next run fixes itself; close the issue.
3. If the page layout changed, save the new page as a fixture
   (`tests/fixtures/ariss_upcoming_sstv.html`), adjust
   `scripts/update_events.py` until `python -m unittest discover tests`
   passes, and merge that. Meanwhile, edit `sstv-events.json` by hand
   (checklist below).

## Editing checklist

For hand edits (normally the automatic pull request does this). When ARISS
announces or changes an event:

1. Add or edit the event in `sstv-events.json`. Keep its `id` unchanged.
2. Fill in `startTime` / `endTime` once ARISS publishes them.
3. If an event is cancelled before it ends, set `"status": "cancelled"`.
   Don't delete it: phones that announced it need it to send the
   cancellation notice. Old events can be deleted after they end.
4. Set `updated` to the current UTC time.
5. Check it: `python scripts/validate.py sstv-events.json`
6. Commit and push. The *Validate events* workflow checks it again. Pages
   updates within a few minutes. Phones pick it up on their next refresh
   (app open after 6 h, or the daily background job).

## radios.json

The radios the app's channel-plan screen can offer: every radio that can
receive 2 m or 70 cm, with its tuning steps and memory count. Served at
https://meanbanana.github.io/iss-tracker-data/radios.json (schema 1). Nobody
edits it by hand; the *Build radios.json* workflow regenerates it.

Sources:

- [CHIRP](https://chirpmyradio.com/) (GPL-3.0): memory-style radios, tuning
  steps and memory counts. Installed from a pinned commit
  (`requirements-radios.txt`) because CHIRP is not on PyPI.
- [Hamlib](https://hamlib.github.io/) (`rigctl`, LGPL-2.1+): receivers that tune
  freely, for example the Icom IC-9700.
- `sdr-extra.json`: a short hand-kept list of SDR receivers.

Only facts about the radios (names, numbers) end up in `radios.json`; no CHIRP
or Hamlib code is copied.

Schedule: every Monday 04:23 UTC, plus a monthly keepalive commit so GitHub
doesn't disable the schedule. The run commits `radios.json` to `main` only when
the radios or the source versions changed. To run it now: Actions, *Build
radios.json*, *Run workflow* on `main`, or `gh workflow run radios.yml`.
Pull requests that touch the builder run the same build without committing, and
attach `radios.json` and the raw `rigctl` output as artifacts. Locally:
`python -m unittest tests.test_build_radios`; the full build needs CHIRP and
Hamlib installed (`python scripts/build_radios.py --out radios.json`).
