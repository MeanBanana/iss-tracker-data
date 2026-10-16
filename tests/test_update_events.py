import json
import pathlib
import sys
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import update_events as ue  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
PAGE = (FIXTURES / "ariss_upcoming_sstv.html").read_text(encoding="utf-8", errors="replace")
FEED = (FIXTURES / "ariss_press_feed.xml").read_text(encoding="utf-8", errors="replace")
SEED = json.loads((ROOT / "sstv-events.json").read_text(encoding="utf-8"))
APP_FREQS = [145800000, 145825000, 437550000, 437800000]
NOW = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)


def page(*entries):
    """A minimal page shaped like the real one: bold headings, <br /> lines."""
    body = "<br /><br />".join(
        f"<strong>{heading}</strong><br />" + "<br />".join(lines)
        for heading, lines in entries
    )
    return (
        '<h2 class="wsite-content-title">Upcoming SSTV events</h2>'
        f'<div class="paragraph">{body}</div>'
    )


def feed(*items):
    xml = "".join(
        f"<item><title>{t}</title><link>https://www.ariss.org/r{i}</link>"
        f"<pubDate>{d}</pubDate><description><![CDATA[{text}]]></description></item>"
        for i, (t, d, text) in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{xml}</channel></rss>'


def run(page_html=PAGE, feed_xml=None, current=None, overrides=None, now=NOW):
    def fetch(url):
        if url == ue.PAGE_URL:
            if isinstance(page_html, Exception):
                raise page_html
            return page_html
        if feed_xml is None:
            raise OSError("no feed in this test")
        return feed_xml

    return ue.run(
        fetch=fetch,
        now=now,
        current=SEED if current is None else current,
        overrides=overrides or {},
        app_frequencies=APP_FREQS,
    )


def event(result, eid):
    return next(e for e in result.output["events"] if e["id"] == eid)


class RealPageTest(unittest.TestCase):
    def test_series_34_from_the_real_page(self):
        r = run()
        self.assertEqual(r.status, "unchanged", r.problems)
        self.assertGreaterEqual(r.raw_headings, 5)
        e = event(r, "ariss-sstv-34")
        self.assertEqual(e["title"], "ARISS Hardware")
        self.assertEqual((e["startDate"], e["startTime"]), ("2026-10-22", None))
        self.assertEqual((e["endDate"], e["endTime"]), ("2026-10-25", None))
        self.assertEqual(e["frequencyHz"], 437550000)
        self.assertEqual(e["mode"], "Robot 36")
        self.assertEqual(e["pattern"], "36 s on, 2 min off")
        self.assertEqual(e["status"], "scheduled")

    def test_past_series_parse_with_times(self):
        # Seen from 3 Oct, Series 33 (Oct 2-6, times given) is still published.
        r = run(now=datetime(2026, 10, 3, tzinfo=timezone.utc))
        e = event(r, "ariss-sstv-33")
        self.assertEqual((e["startDate"], e["startTime"]), ("2026-10-02", "09:00"))
        self.assertEqual((e["endDate"], e["endTime"]), ("2026-10-06", "15:55"))
        self.assertEqual(e["title"], "Student Education")

    def test_newest_post_wins_older_fills_gaps(self):
        # Series 31 has two posts: the newer one gives times, the older TBD.
        r = run(now=datetime(2026, 4, 11, tzinfo=timezone.utc))
        e = event(r, "ariss-sstv-31")
        self.assertEqual((e["startDate"], e["startTime"]), ("2026-04-10", "11:15"))
        self.assertEqual((e["endDate"], e["endTime"]), ("2026-04-14", "13:55"))
        self.assertEqual(e["frequencyHz"], 437550000)

    def test_real_feed_adds_nothing_new_now(self):
        r = run(feed_xml=FEED)
        self.assertEqual(r.status, "unchanged", r.problems)


class ParsingTest(unittest.TestCase):
    def test_tbd_times_are_null(self):
        r = run(page((
            '2026-10-14 Series 40 "Test"',
            ["Begins Thursday October 22 time TBD", "Concludes Sunday October 25 (end time TBD)",
             "Images at 437.550 MHz, Robot 36"],
        )))
        e = event(r, "ariss-sstv-40")
        self.assertIsNone(e["startTime"])
        self.assertIsNone(e["endTime"])

    def test_missing_year_from_posting_date(self):
        r = run(page((
            '2026-10-14 Series 40 "Test"',
            ["Begins Friday October 30 10:00 UTC", "Concludes Monday November 2 12:30 UTC",
             "Frequency 437.55 MHz"],
        )))
        e = event(r, "ariss-sstv-40")
        self.assertEqual(e["startDate"], "2026-10-30")
        self.assertEqual(e["endDate"], "2026-11-02")
        self.assertEqual((e["startTime"], e["endTime"]), ("10:00", "12:30"))

    def test_december_post_about_january(self):
        r = run(
            page((
                '2026-12-20 Series 41 "New Year"',
                ["Begins Saturday January 9 10:00 UTC", "Concludes Tuesday January 12 16:00 UTC",
                 "Images at 437.550 MHz, Robot 36"],
            )),
            now=datetime(2026, 12, 21, tzinfo=timezone.utc),
        )
        e = event(r, "ariss-sstv-41")
        self.assertEqual(e["startDate"], "2027-01-09")
        self.assertEqual(e["endDate"], "2027-01-12")

    def test_us_style_posting_date(self):
        r = run(
            page((
                "12-04-2026 SSTV Series 42 Update:",
                ["Scheduled to start at 00:01 UTC December 5. The campaign will conclude "
                 "at 23:59 UTC December 13.", "Images at 437.55 MHz"],
            )),
            now=datetime(2026, 12, 4, tzinfo=timezone.utc),
        )
        e = event(r, "ariss-sstv-42")
        self.assertEqual((e["startDate"], e["startTime"]), ("2026-12-05", "00:01"))
        self.assertEqual((e["endDate"], e["endTime"]), ("2026-12-13", "23:59"))
        self.assertEqual(e["title"], "ARISS SSTV Series 42")

    def test_pd120_on_145_800(self):
        r = run(page((
            '2026-10-14 Series 43 "Scouting"',
            ["Begins October 20, 2026 12:00 UTC", "Ends October 23, 2026 18:00 UTC",
             "12 images transmitted at 145.800 MHz using mode PD-120. "
             "The transmissions will be 2 minutes on and then 2 minutes off."],
        )))
        e = event(r, "ariss-sstv-43")
        self.assertEqual(e["frequencyHz"], 145800000)
        self.assertEqual(e["mode"], "PD 120")
        self.assertEqual(e["pattern"], "2 min on, 2 min off")
        r2 = run(page((
            '2026-10-14 Series 43 "Scouting"',
            ["Begins October 20 12:00 UTC", "Ends October 23 18:00 UTC",
             "145.8 MHz mode PD120"],
        )))
        self.assertEqual(event(r2, "ariss-sstv-43")["mode"], "PD 120")

    def test_cancelled_and_postponed(self):
        for word in ["CANCELLED", "canceled", "postponed"]:
            r = run(page((
                f'2026-10-15 Series 34 "ARISS Hardware" {word}',
                ["Begins Thursday October 22 time TBD", "Concludes Sunday October 25 time TBD",
                 "437.55 MHz Robot 36"],
            )))
            self.assertEqual(event(r, "ariss-sstv-34")["status"], "cancelled", word)

    def test_rss_only_event_is_added_page_wins_on_conflict(self):
        rss = feed(
            ("ARISS News Release 26-90 ARISS SSTV Event Scheduled", "Mon, 26 Oct 2026 12:00:00 +0000",
             "The SSTV event is scheduled to begin Friday, October 30 at 10:30 UTC and end on "
             "Tuesday, November 3 at 16:30 UTC. Images at 145.800 MHz using PD120."),
            ("ARISS News Release 26-89 ARISS SSTV Event", "Wed, 14 Oct 2026 12:00:00 +0000",
             "The SSTV event will begin Thursday, October 22 at 11:00 UTC and end on Sunday, "
             "October 25 at 17:00 UTC. Images at 145.800 MHz."),
            ("ARISS News Release 26-88 New director", "Tue, 13 Oct 2026 12:00:00 +0000",
             "Nothing about slow scan here. Begins October 20."),
        )
        r = run(feed_xml=rss)
        ids = [e["id"] for e in r.output["events"]]
        self.assertEqual(ids, ["ariss-sstv-34", "ariss-sstv-2026-10-30"])
        added = event(r, "ariss-sstv-2026-10-30")
        self.assertEqual((added["startTime"], added["endTime"]), ("10:30", "16:30"))
        self.assertEqual(added["frequencyHz"], 145800000)
        self.assertEqual(added["url"], "https://www.ariss.org/r0")
        # The page's Series 34 wins over the release about the same dates.
        s34 = event(r, "ariss-sstv-34")
        self.assertEqual((s34["frequencyHz"], s34["startTime"]), (437550000, None))

    def test_feed_failure_is_not_fatal(self):
        r = run(feed_xml=None)
        self.assertEqual(r.status, "unchanged")


class MergeTest(unittest.TestCase):
    def test_overrides_merge_field_by_field_and_survive_a_rerun(self):
        overrides = {"ariss-sstv-34": {"startTime": "09:30", "endTime": "18:00"}}
        r = run(overrides=overrides)
        self.assertEqual(r.status, "changed")
        e = event(r, "ariss-sstv-34")
        self.assertEqual((e["startTime"], e["endTime"]), ("09:30", "18:00"))
        self.assertEqual(e["mode"], "Robot 36")  # other fields still parsed
        again = run(current=r.output, overrides=overrides)
        self.assertEqual(again.status, "unchanged")

    def test_existing_id_is_kept(self):
        current = json.loads(json.dumps(SEED))
        current["events"][0]["id"] = "ariss-sstv-2026-10-22"
        r = run(current=current)
        self.assertEqual([e["id"] for e in r.output["events"]], ["ariss-sstv-2026-10-22"])
        self.assertEqual(r.status, "unchanged")

    def test_unended_event_missing_from_ariss_is_kept_and_listed(self):
        current = json.loads(json.dumps(SEED))
        hand = {
            "id": "ariss-sstv-hand", "title": "Added by hand",
            "startDate": "2026-11-05", "startTime": None,
            "endDate": "2026-11-06", "endTime": None,
            "frequencyHz": 145800000, "mode": "PD 120", "pattern": None,
            "status": "cancelled", "url": None,
        }
        ended = dict(hand, id="ariss-sstv-old", startDate="2026-10-01", endDate="2026-10-02")
        current["events"] = current["events"] + [hand, ended]
        r = run(current=current)
        self.assertIn(hand, r.output["events"])  # unchanged, still cancelled
        self.assertNotIn("ariss-sstv-old", [e["id"] for e in r.output["events"]])
        self.assertEqual(
            r.missing, ["ariss-sstv-hand: Added by hand, 2026-11-05 to 2026-11-06"]
        )
        # Nothing else changed: only the ended event drops out.
        self.assertEqual(r.status, "changed")
        self.assertEqual(run(current=r.output).status, "unchanged")
        self.assertEqual(run(current=r.output).missing, r.missing)

    def test_same_dates_under_another_id_is_listed_not_duplicated(self):
        current = json.loads(json.dumps(SEED))
        current["events"][0]["id"] = "ariss-sstv-custom"
        r = run(current=current)
        ids = [e["id"] for e in r.output["events"]]
        self.assertEqual(ids, ["ariss-sstv-34"])
        self.assertEqual(len(r.missing), 1)
        self.assertIn("ariss-sstv-custom", r.missing[0])
        self.assertIn("ariss-sstv-34", r.missing[0])

    def test_only_updated_differs_means_unchanged(self):
        current = json.loads(json.dumps(SEED))
        current["updated"] = "2020-01-01T00:00:00Z"
        r = run(current=current)
        self.assertEqual(r.status, "unchanged")
        self.assertEqual(r.output["updated"], "2026-10-16T08:00:00Z")


class SafetyTest(unittest.TestCase):
    def test_zero_headings_is_a_broken_parser(self):
        r = run("<html><body><h2>Upcoming SSTV events</h2><p>Nothing here</p></body></html>")
        self.assertEqual(r.status, "broken")
        self.assertIn("no event headings", r.problems[0])

    def test_fetch_failure_is_a_broken_parser(self):
        r = run(OSError("timed out"))
        self.assertEqual(r.status, "broken")
        self.assertIn("timed out", r.problems[0])

    def test_only_ended_events_gives_an_empty_list_not_an_issue(self):
        r = run(now=datetime(2026, 11, 20, tzinfo=timezone.utc))
        self.assertEqual(r.status, "changed")
        self.assertEqual(r.output["events"], [])
        self.assertEqual(r.problems, [])

    def test_recently_ended_events_stay_two_days(self):
        self.assertEqual(
            [e["id"] for e in run(now=datetime(2026, 10, 27, tzinfo=timezone.utc)).output["events"]],
            ["ariss-sstv-34"],
        )
        self.assertEqual(run(now=datetime(2026, 10, 28, tzinfo=timezone.utc)).output["events"], [])

    def test_invalid_output_is_broken_with_validator_errors(self):
        r = run(page((
            '2026-10-14 Series 44 "Too long"',
            ["Begins October 20 12:00 UTC", "Ends November 20 12:00 UTC", "437.55 MHz"],
        )))
        self.assertEqual(r.status, "broken")
        self.assertTrue(any("14 days" in p for p in r.problems), r.problems)

    def test_unknown_frequency_raises_a_mismatch(self):
        r = run(page((
            '2026-10-14 Series 45 "Other radio"',
            ["Begins October 20 12:00 UTC", "Ends October 22 12:00 UTC",
             "Images at 437.825 MHz, Robot 36"],
        )))
        self.assertEqual(
            r.mismatches, ["ARISS SSTV on 437.825 MHz is not a built-in app frequency"]
        )
        self.assertEqual(run().mismatches, [])


class OutputTest(unittest.TestCase):
    def test_stable_format(self):
        r = run(page(
            ('2026-10-14 Series 47 "B"', ["Begins October 24 10:00 UTC", "Ends October 25 10:00 UTC", "437.55 MHz"]),
            ('2026-10-14 Series 46 "A"', ["Begins October 20 10:00 UTC", "Ends October 21 10:00 UTC", "437.55 MHz"]),
        ), current={"schema": 1, "updated": "2026-10-15T00:00:00Z", "events": []})
        text = ue.dumps(r.output)
        self.assertTrue(text.endswith("}\n"))
        self.assertIn('\n  "schema": 1,', text)
        self.assertEqual([e["id"] for e in json.loads(text)["events"]], ["ariss-sstv-46", "ariss-sstv-47"])
        self.assertEqual(json.loads(text)["updated"], "2026-10-16T08:00:00Z")


if __name__ == "__main__":
    unittest.main()
