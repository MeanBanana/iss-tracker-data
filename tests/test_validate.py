import copy
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from validate import validate  # noqa: E402

# A frozen copy of sstv-events.json: the live file changes with every
# schedule update, and the tests must not depend on what ARISS has published.
SEED = json.loads(
    (ROOT / "tests" / "fixtures" / "sstv_events_seed.json").read_text(encoding="utf-8")
)


def with_event(**changes):
    data = copy.deepcopy(SEED)
    data["events"][0].update(changes)
    return data


class ValidateTest(unittest.TestCase):
    def test_seed_file_is_valid(self):
        self.assertEqual(validate(SEED), [])

    def test_empty_events_is_valid(self):
        data = {"schema": 1, "updated": "2026-10-15T09:00:00Z", "events": []}
        self.assertEqual(validate(data), [])

    def test_confirmed_times_are_valid(self):
        self.assertEqual(validate(with_event(startTime="09:00", endTime="15:55")), [])

    def test_bad_date(self):
        self.assertTrue(validate(with_event(startDate="22 Oct")))

    def test_bad_time(self):
        self.assertTrue(validate(with_event(startTime="25:00")))

    def test_window_over_14_days(self):
        self.assertTrue(validate(with_event(endDate="2026-11-06")))

    def test_end_before_start(self):
        self.assertTrue(validate(with_event(endDate="2026-10-21")))

    def test_out_of_band_frequency(self):
        self.assertTrue(validate(with_event(frequencyHz=100000000)))

    def test_null_frequency_is_allowed(self):
        self.assertEqual(validate(with_event(frequencyHz=None)), [])

    def test_duplicate_id(self):
        data = copy.deepcopy(SEED)
        data["events"].append(copy.deepcopy(data["events"][0]))
        self.assertTrue(validate(data))

    def test_unknown_status(self):
        self.assertTrue(validate(with_event(status="maybe")))

    def test_unknown_schema(self):
        data = copy.deepcopy(SEED)
        data["schema"] = 2
        self.assertTrue(validate(data))

    def test_bad_updated(self):
        data = copy.deepcopy(SEED)
        data["updated"] = "yesterday"
        self.assertTrue(validate(data))

    def test_long_mode(self):
        self.assertTrue(validate(with_event(mode="x" * 41)))


if __name__ == "__main__":
    unittest.main()
