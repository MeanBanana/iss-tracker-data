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

## Editing checklist

When ARISS announces or changes an event:

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
