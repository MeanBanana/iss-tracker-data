"""Build radios.json: radios that can receive 2 m / 70 cm, from CHIRP, Hamlib
and a hand-kept SDR list. This file holds the pure functions (parsing, merging,
validation); extraction from the CHIRP and Hamlib installs and main() follow.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

BAND_RANGES_HZ = {"2m": (144_000_000, 146_000_000), "70cm": (430_000_000, 440_000_000)}
STYLES = ("memory", "free")
REQUIRED = {
    "id": str,
    "brand": str,
    "model": str,
    "aliases": list,
    "styles": list,
    "stepsHz": list,
    "stepsConfirmed": bool,
    "anyFrequency": bool,
    "bands": list,
    "memories": int,
    "chirp": bool,
}
_UNIT_HZ = {"hz": 1, "khz": 1_000, "mhz": 1_000_000, "ghz": 1_000_000_000}


def normalise(text):
    t = text.lower()
    for ch in " -._/":
        t = t.replace(ch, "")
    return t


def bands_of(ranges):
    return [
        name
        for name, (lo, hi) in BAND_RANGES_HZ.items()
        if any(r_lo <= hi and r_hi >= lo for r_lo, r_hi in ranges)
    ]


def _entry_id(brand, model, variant=""):
    out = normalise(brand) + "-" + normalise(model)
    return out + "-" + normalise(variant) if variant else out


def _aliases(model):
    flat = model.replace("-", "").replace(" ", "")
    return [flat] if flat != model else []


def chirp_entry(rec):
    bands = bands_of([(int(lo), int(hi)) for lo, hi in rec["bands"]])
    if not bands:
        return None
    lo, hi = rec["memory_bounds"]
    steps = sorted({int(round(k * 1000)) for k in rec["steps_khz"]})
    confirmed = not rec["defaults_steps"]
    if not steps:  # some drivers list no steps at all: assume the usual 5 kHz, unconfirmed
        steps, confirmed = [5000], False
    return {
        "id": _entry_id(rec["vendor"], rec["model"], rec.get("variant") or ""),
        "brand": rec["vendor"],
        "model": rec["model"],
        "aliases": _aliases(rec["model"]),
        "styles": ["memory"],
        "stepsHz": steps,
        "stepsConfirmed": confirmed,
        "anyFrequency": bool(rec["nostep"]),
        "bands": bands,
        "memories": int(hi) - int(lo) + 1,
        "chirp": True,
    }


_LIST_ROW = re.compile(r"^\s*(\d+)\s+(\S.*?)\s{2,}(\S.*?)(?:\s{2,}.*)?$")


def parse_rigctl_list(text):
    """Rows of `rigctl -l`; the header and anything else is ignored."""
    rigs = []
    for line in text.splitlines():
        m = _LIST_ROW.match(line)
        if m:
            rigs.append(
                {"id": int(m.group(1)), "brand": m.group(2).strip(), "model": m.group(3).strip()}
            )
    return rigs


_HZ_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(hz|khz|mhz|ghz)\s*(?:-|\.\.\.|to)\s*(\d+(?:\.\d+)?)\s*(hz|khz|mhz|ghz)?",
    re.I,
)
_STEP = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(hz|khz|mhz)", re.I)  # "5.0000 kHz:   AM FM ..."


def _hz(value, unit):
    return int(round(float(value) * _UNIT_HZ[unit.lower()]))


def parse_rigctl_caps(text):
    """RX ranges and tuning steps from `rigctl --dump-caps` text."""
    section = None
    rx, steps = [], set()
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            low = line.lower()
            if re.match(r"\s*(rx|receive)\b.*range", low):
                section = "rx"
            elif low.startswith("tuning steps") and not low.startswith("tuning steps list"):
                section = "steps"
            else:
                section = None
            continue
        if section == "rx":
            m = _HZ_RANGE.search(line)
            if m:
                rx.append((_hz(m.group(1), m.group(2)), _hz(m.group(3), m.group(4) or m.group(2))))
        elif section == "steps":
            m = _STEP.search(line)
            if m:
                steps.add(_hz(m.group(1), m.group(2)))
    return {"steps_hz": sorted(steps), "bands": bands_of(rx)}


def _hamlib_entry(h):
    """Hamlib item: rigctl list keys plus `steps_hz` and `bands` from its caps."""
    bands = list(h.get("bands") or [])
    if not bands:
        return None
    steps = sorted(s for s in (h.get("steps_hz") or []) if s >= 100) or [100]
    return {
        "id": _entry_id(h["brand"], h["model"]),
        "brand": h["brand"],
        "model": h["model"],
        "aliases": _aliases(h["model"]),
        "styles": ["free"],
        "stepsHz": steps,
        "stepsConfirmed": True,
        "anyFrequency": True,
        "bands": bands,
        "memories": 0,
        "chirp": False,
    }


def merge(chirp, hamlib, sdr):
    """Free-tuning sources join every CHIRP entry with the same brand+model
    (variants stay separate entries); anything unmatched is its own entry."""
    out = {entry["id"]: dict(entry) for entry in chirp}
    by_key = {}
    for entry in out.values():
        by_key.setdefault(normalise(entry["brand"]) + normalise(entry["model"]), []).append(entry)
    for entry in [e for e in map(_hamlib_entry, hamlib) if e] + [dict(s) for s in sdr]:
        matches = by_key.get(normalise(entry["brand"]) + normalise(entry["model"]))
        if not matches:
            out.setdefault(entry["id"], entry)
            continue
        for have in matches:
            have["styles"] = [s for s in STYLES if s in have["styles"] or s in entry["styles"]]
            have["bands"] = [b for b in BAND_RANGES_HZ if b in have["bands"] or b in entry["bands"]]
    return sorted(out.values(), key=lambda r: (r["brand"].lower(), r["model"].lower(), r["id"]))


def document(radios, chirp_ref, hamlib_ref, now):
    return {
        "schema": 1,
        "generated": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": {"chirp": chirp_ref, "hamlib": hamlib_ref},
        "radios": radios,
    }


def validate(doc):
    problems = []
    if doc.get("schema") != 1:
        problems.append("schema must be 1")
    if not isinstance(doc.get("generated"), str):
        problems.append("generated must be a string")
    if not isinstance(doc.get("sources"), dict):
        problems.append("sources must be an object")
    radios = doc.get("radios")
    if not isinstance(radios, list):
        return problems + ["radios must be a list"]
    seen = set()
    for i, r in enumerate(radios):
        if not isinstance(r, dict):
            problems.append(f"radio #{i}: not an object")
            continue
        name = r["id"] if isinstance(r.get("id"), str) else f"#{i}"
        if name in seen:
            problems.append(f"radio {name}: duplicate id")
        seen.add(name)
        for key, typ in REQUIRED.items():
            if key not in r:
                problems.append(f"radio {name}: missing {key}")
            elif not isinstance(r[key], typ) or (typ is int and isinstance(r[key], bool)):
                problems.append(f"radio {name}: {key} has wrong type")
        styles = r.get("styles")
        if isinstance(styles, list) and (not styles or any(s not in STYLES for s in styles)):
            problems.append(f"radio {name}: styles must be a non-empty subset of {list(STYLES)}")
        bands = r.get("bands")
        if isinstance(bands, list) and (not bands or any(b not in BAND_RANGES_HZ for b in bands)):
            problems.append(f"radio {name}: bands must be a non-empty subset of 2m/70cm")
        steps = r.get("stepsHz")
        if isinstance(steps, list) and (
            not steps or any(not isinstance(s, int) or s <= 0 for s in steps)
        ):
            problems.append(f"radio {name}: stepsHz must be positive integers")
    return problems


# --- live extraction (CI only: needs CHIRP and Hamlib installed) -------------


def _chirp_version():
    """CHIRP's own version plus the pinned commit when pip installed it from git."""
    import chirp

    version = str(getattr(chirp, "CHIRP_VERSION", "unknown"))
    try:
        from importlib import metadata

        direct = json.loads(metadata.distribution("chirp").read_text("direct_url.json") or "{}")
        commit = direct.get("vcs_info", {}).get("commit_id", "")
        if commit:
            version += "+" + commit[:10]
    except Exception:
        pass
    return version


def extract_chirp():
    """(records for chirp_entry, version). Classes that fail are logged and counted."""
    from chirp import chirp_common, directory

    directory.import_drivers()
    records, skipped = [], 0
    for cls in directory.DRV_TO_RADIO.values():
        try:
            f = cls(None).get_features()
            steps = list(f.valid_tuning_steps)
            records.append(
                {
                    "vendor": cls.VENDOR,
                    "model": cls.MODEL,
                    "variant": getattr(cls, "VARIANT", "") or "",
                    "steps_khz": steps,
                    "defaults_steps": steps == list(chirp_common.COMMON_TUNING_STEPS),
                    "nostep": bool(f.has_nostep_tuning),
                    "bands": [(int(lo), int(hi)) for lo, hi in f.valid_bands],
                    "memory_bounds": tuple(f.memory_bounds),
                }
            )
        except Exception as e:
            skipped += 1
            print(f"skip CHIRP class {getattr(cls, '__name__', cls)}: {e!r}", file=sys.stderr)
    extract_chirp.skipped = skipped
    return records, _chirp_version()


def _rigctl(args, timeout=5):
    return subprocess.run(
        ["rigctl", *args], capture_output=True, text=True, timeout=timeout, check=True
    ).stdout


def extract_hamlib():
    """(items for merge, version). Rigs whose caps fail or show no 2 m / 70 cm RX are dropped."""
    version = _rigctl(["--version"]).strip()
    listing = parse_rigctl_list(_rigctl(["-l"], timeout=30))
    items, skipped = [], 0
    for rig in listing:
        try:
            caps = parse_rigctl_caps(_rigctl(["-m", str(rig["id"]), "--dump-caps"]))
        except Exception as e:
            skipped += 1
            print(f"skip Hamlib rig {rig['id']} {rig['model']}: {e!r}", file=sys.stderr)
            continue
        if caps["bands"]:
            items.append({**rig, **caps})
    extract_hamlib.skipped = skipped
    return items, version


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="radios.json")
    ap.add_argument("--min-radios", type=int, default=517)
    args = ap.parse_args(argv)

    chirp_records, chirp_ref = extract_chirp()
    hamlib_items, hamlib_ref = extract_hamlib()
    with open("sdr-extra.json", encoding="utf-8") as fh:
        sdr = json.load(fh)["radios"]
    chirp_entries = [e for e in map(chirp_entry, chirp_records) if e]
    radios = merge(chirp_entries, hamlib_items, sdr)
    doc = document(radios, chirp_ref, hamlib_ref, datetime.now(timezone.utc))

    problems = validate(doc)
    if len(radios) < args.min_radios:
        problems.append(f"only {len(radios)} radios, expected at least {args.min_radios}")
    print(
        f"radios: {len(radios)} (CHIRP entries {len(chirp_entries)}, Hamlib rigs with 2m/70cm "
        f"{len(hamlib_items)}, SDR {len(sdr)}); skipped CHIRP classes: "
        f"{getattr(extract_chirp, 'skipped', 0)}, skipped Hamlib rigs: "
        f"{getattr(extract_hamlib, 'skipped', 0)}; CHIRP {chirp_ref}; Hamlib {hamlib_ref}"
    )
    if problems:
        print(*problems[:50], sep="\n", file=sys.stderr)
        return 1

    try:
        with open(args.out, encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        old = None
    if old and old.get("radios") == radios and old.get("sources") == doc["sources"]:
        print("unchanged; file left as is")
        return 0
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
