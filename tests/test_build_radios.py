import copy
import json
import pathlib
import sys
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_radios as br  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
CHIRP = json.loads((FIX / "chirp_records.json").read_text(encoding="utf-8"))
RIG_LIST = (FIX / "rigctl_list.txt").read_text(encoding="utf-8")
RIG_CAPS = (FIX / "rigctl_caps_ic9700.txt").read_text(encoding="utf-8")
SDR = json.loads((ROOT / "sdr-extra.json").read_text(encoding="utf-8"))["radios"]
NOW = datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc)


def by_model(model):
    return next(r for r in CHIRP if r["model"] == model)


def hamlib_ic9700():
    caps = br.parse_rigctl_caps(RIG_CAPS)
    rig = next(r for r in br.parse_rigctl_list(RIG_LIST) if r["model"] == "IC-9700")
    return dict(rig, steps_hz=caps["steps_hz"], bands=caps["bands"])


def merged():
    chirp = [e for e in map(br.chirp_entry, CHIRP) if e]
    return br.merge(chirp, [hamlib_ic9700()], SDR)


class BuildRadiosTest(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(br.normalise("UV-5R"), "uv5r")
        self.assertEqual(br.normalise("uv 5r"), "uv5r")
        self.assertEqual(br.normalise("A.b_c/d"), "abcd")

    def test_bands_of(self):
        self.assertEqual(br.bands_of([(136_000_000, 174_000_000)]), ["2m"])
        self.assertEqual(br.bands_of([(1_800_000, 30_000_000)]), [])
        self.assertEqual(
            br.bands_of([(430_000_000, 440_000_000), (144_000_000, 146_000_000)]),
            ["2m", "70cm"],
        )

    def test_chirp_entry_uv5r(self):
        e = br.chirp_entry(by_model("UV-5R"))
        self.assertEqual(e["id"], "baofeng-uv5r")
        self.assertEqual(e["styles"], ["memory"])
        self.assertIn(5000, e["stepsHz"])
        self.assertIn(6250, e["stepsHz"])
        self.assertIs(e["stepsConfirmed"], True)
        self.assertEqual(e["memories"], 128)
        self.assertEqual(e["bands"], ["2m", "70cm"])
        self.assertIs(e["chirp"], True)
        self.assertIs(e["anyFrequency"], False)
        self.assertEqual(e["aliases"], ["UV5R"])

    def test_chirp_entry_variant_in_id(self):
        rec = dict(by_model("UV-5R"), variant="Plus")
        self.assertEqual(br.chirp_entry(rec)["id"], "baofeng-uv5r-plus")

    def test_chirp_entry_hf_only_dropped(self):
        self.assertIsNone(br.chirp_entry(by_model("IC-718")))

    def test_default_steps_unconfirmed(self):
        self.assertIs(br.chirp_entry(by_model("FT-60"))["stepsConfirmed"], False)

    def test_rigctl_parsing(self):
        rigs = br.parse_rigctl_list(RIG_LIST)
        self.assertIn(
            {"id": 3081, "brand": "Icom", "model": "IC-9700"}, rigs
        )
        self.assertEqual(len(rigs), 9)
        caps = br.parse_rigctl_caps(RIG_CAPS)
        self.assertIn("2m", caps["bands"])
        self.assertIn("70cm", caps["bands"])
        self.assertEqual(caps["steps_hz"], [10, 100, 1000, 5000])

    def test_merge_both_sources(self):
        radios = merged()
        hits = [r for r in radios if r["id"] == "icom-ic9700"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["styles"], ["memory", "free"])
        self.assertIs(hits[0]["anyFrequency"], True)
        self.assertIs(hits[0]["chirp"], True)
        self.assertEqual(hits[0]["memories"], 99)

    def test_merge_sorted_and_hamlib_only(self):
        radios = merged()
        keys = [(r["brand"].lower(), r["model"].lower()) for r in radios]
        self.assertEqual(keys, sorted(keys))
        ts2000 = [r for r in radios if r["id"] == "kenwood-ts2000"]
        self.assertEqual(ts2000, [])  # no bands supplied -> not 2m/70cm

    def test_sdr_extra_included(self):
        e = next(r for r in merged() if r["id"] == "rtlsdrblog-v3")
        self.assertEqual(e["styles"], ["free"])
        self.assertEqual(e["stepsHz"], [100])

    def test_document_valid(self):
        doc = br.document(merged(), "abc123", "4.5.5", NOW)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["generated"], "2026-10-21T03:00:00Z")
        self.assertEqual(doc["sources"], {"chirp": "abc123", "hamlib": "4.5.5"})
        self.assertEqual(br.validate(doc), [])

    def test_validate_rejects_missing_fields(self):
        doc = br.document(merged(), "abc123", "4.5.5", NOW)
        bad = copy.deepcopy(doc)
        del bad["radios"][0]["stepsHz"]
        problems = br.validate(bad)
        self.assertEqual(len(problems), 1)
        self.assertIn(bad["radios"][0]["id"], problems[0])


if __name__ == "__main__":
    unittest.main()
