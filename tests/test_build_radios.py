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

    def test_chirp_entry_empty_steps_fall_back(self):
        rec = dict(by_model("UV-5R"), steps_khz=[], nostep=False, defaults_steps=False)
        e = br.chirp_entry(rec)
        self.assertEqual(e["stepsHz"], [5000])
        self.assertFalse(e["stepsConfirmed"])

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
        self.assertEqual(len(rigs), 10)
        caps = br.parse_rigctl_caps(RIG_CAPS)
        self.assertIn("2m", caps["bands"])
        self.assertIn("70cm", caps["bands"])
        self.assertEqual(caps["steps_hz"][:7], [1, 10, 100, 500, 1000, 5000, 6250])

    def test_merge_both_sources(self):
        radios = merged()
        hits = [r for r in radios if r["id"] == "icom-ic9700"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["styles"], ["memory", "free"])
        self.assertIs(hits[0]["anyFrequency"], True)
        self.assertIs(hits[0]["chirp"], True)
        self.assertEqual(hits[0]["memories"], 99)

    def test_merge_sorted(self):
        radios = merged()
        keys = [(r["brand"].lower(), r["model"].lower()) for r in radios]
        self.assertEqual(keys, sorted(keys))

    def test_merge_stepped_chirp_keeps_any_frequency_false(self):
        chirp = [br.chirp_entry(by_model("UV-5R"))]
        ham = [{"id": 1, "brand": "Baofeng", "model": "UV-5R", "steps_hz": [100], "bands": ["2m"]}]
        out = br.merge(chirp, ham, [])
        self.assertEqual(len(out), 1)
        self.assertIs(out[0]["anyFrequency"], False)
        self.assertEqual(out[0]["styles"], ["memory", "free"])
        self.assertEqual(out[0]["stepsHz"], chirp[0]["stepsHz"])
        self.assertEqual(out[0]["bands"], ["2m", "70cm"])

    def test_merge_band_union(self):
        chirp = [br.chirp_entry(dict(by_model("UV-5R"), bands=[[144000000, 146000000]]))]
        ham = [{"id": 1, "brand": "Baofeng", "model": "UV-5R", "steps_hz": [], "bands": ["70cm"]}]
        self.assertEqual(br.merge(chirp, ham, [])[0]["bands"], ["2m", "70cm"])

    def test_merge_variant_matches_hamlib_without_duplicate(self):
        chirp = [br.chirp_entry(dict(by_model("UV-5R"), variant="Plus"))]
        ham = [{"id": 1, "brand": "Baofeng", "model": "UV-5R", "steps_hz": [100], "bands": ["2m"]}]
        out = br.merge(chirp, ham, [])
        self.assertEqual([r["id"] for r in out], ["baofeng-uv5r-plus"])
        self.assertEqual(out[0]["styles"], ["memory", "free"])

    def test_merge_variants_all_gain_free(self):
        chirp = [
            br.chirp_entry(by_model("UV-5R")),
            br.chirp_entry(dict(by_model("UV-5R"), variant="Plus")),
        ]
        ham = [{"id": 1, "brand": "Baofeng", "model": "UV-5R", "steps_hz": [], "bands": ["2m"]}]
        out = br.merge(chirp, ham, [])
        self.assertEqual(len(out), 2)
        self.assertTrue(all(r["styles"] == ["memory", "free"] for r in out))

    def test_merge_hamlib_only_entry(self):
        ham = [{"id": 5, "brand": "Kenwood", "model": "TS-2000",
                "steps_hz": [10, 50, 100, 1000, 5000], "bands": ["2m", "70cm"]}]
        e = br.merge([], ham, [])[0]
        self.assertEqual(e["id"], "kenwood-ts2000")
        self.assertEqual(e["styles"], ["free"])
        self.assertEqual(e["memories"], 0)
        self.assertIs(e["chirp"], False)
        self.assertIs(e["anyFrequency"], True)
        self.assertEqual(e["stepsHz"], [100, 1000, 5000])
        self.assertEqual(e["aliases"], ["TS2000"])

    def test_merge_hamlib_only_step_fallback(self):
        ham = [{"id": 5, "brand": "X", "model": "Y", "steps_hz": [10, 50], "bands": ["2m"]}]
        self.assertEqual(br.merge([], ham, [])[0]["stepsHz"], [100])

    def test_merge_hamlib_without_bands_dropped(self):
        ham = [{"id": 5, "brand": "X", "model": "Y", "steps_hz": [], "bands": []}]
        self.assertEqual(br.merge([], ham, []), [])

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
