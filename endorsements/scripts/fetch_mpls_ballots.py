#!/usr/bin/env python3
"""Download the ballot for every Minneapolis precinct from the MN Secretary of State.

The state's ballot lookup (myballotmn.sos.mn.gov) has a JSON API:
  /api/Streets/GetStreets?ZipCode=55405       every address range in a ZIP code
  /api/PollingPlaceData/GetPollingPlaceData?prodAddressRangeId=N   precinct + ballot

This script asks for the ballot of each Minneapolis address range (about 4,400),
caches the raw answers in scripts/.cache/ (git-ignored), and writes a compact copy
to data/sources/mpls_ballots.json: each precinct's ward, number, and ballot.

It is resumable and deliberately slow. If the site starts timing out or returns a
bot check, stop and try again later; don't hammer it.

Usage:
    python3 scripts/fetch_mpls_ballots.py              # fetch what's missing, then compact
    python3 scripts/fetch_mpls_ballots.py --compact    # only rebuild the compact file from the cache
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import re
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
OUT = os.path.join(HERE, "..", "data", "sources", "mpls_ballots.json")
API = "https://myballotmn.sos.mn.gov/api"
UA = {"User-Agent": "mn-explained endorsements script"}

# ZIP codes that contain Minneapolis addresses.
ZIPS = [f"554{n:02d}" for n in range(1, 20)] + ["55421", "55430", "55454", "55455"]

# Official count, from the city's 2026 primary statistics (vote.minneapolismn.gov).
OFFICIAL_PRECINCT_COUNT = 137

KEEP = ["OfficeTitle", "UploadOfficeCode", "PartyName", "CandidateScreenName",
        "CampaignWebsite", "QuestionTextBrief", "QuestionTextFull"]


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def fetch(workers, delay):
    os.makedirs(f"{CACHE}/ballots", exist_ok=True)
    ranges = {}
    for z in ZIPS:
        for r in get(f"{API}/Streets/GetStreets?ZipCode={z}")["Streets"]:
            if r["CityName"].strip() == "MINNEAPOLIS":
                ranges[r["ProdAddressRangeId"]] = r
        time.sleep(delay)
    todo = [i for i in ranges if not os.path.exists(f"{CACHE}/ballots/{i}.json")]
    print(f"{len(ranges)} Minneapolis address ranges, {len(todo)} still to fetch", flush=True)

    failures = 0

    def one(rid):
        nonlocal failures
        for _ in range(3):
            try:
                data = get(f"{API}/PollingPlaceData/GetPollingPlaceData?prodAddressRangeId={rid}")
                json.dump(data, open(f"{CACHE}/ballots/{rid}.json", "w"))
                time.sleep(delay)
                return
            except Exception:
                time.sleep(5)
        failures += 1
        print("failed", rid, flush=True)

    with cf.ThreadPoolExecutor(workers) as ex:
        for i, _ in enumerate(ex.map(one, todo), 1):
            if i % 200 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
            if failures >= 20:
                raise SystemExit("Too many failures; the site may be down or blocking. Try again later.")


def compact():
    precincts, ballots, sig_to_id = {}, {}, {}
    for f in os.listdir(f"{CACHE}/ballots"):
        try:
            d = json.load(open(f"{CACHE}/ballots/{f}"))
        except Exception:
            continue
        p = d.get("PollingResult")
        if not p or not p["PrecinctName"].startswith("MINNEAPOLIS W-"):
            continue
        rows = sorted(({k: b.get(k, "") for k in KEEP} for b in d["Ballots"]),
                      key=lambda r: (r["UploadOfficeCode"], r["CandidateScreenName"]))
        sig = json.dumps(rows, sort_keys=True)
        if sig not in sig_to_id:
            sig_to_id[sig] = f"b{len(sig_to_id) + 1:02d}"
            ballots[sig_to_id[sig]] = rows
        m = re.match(r"MINNEAPOLIS W-(\d+) P-(\w+)", p["PrecinctName"])
        precincts[p["PrecinctName"]] = {
            "ward": int(m.group(1)),
            "precinct": m.group(2).lstrip("0") or "0",
            "house_district": p["LegDistrict"],
            "county_commissioner": p["CountyCommissioner"],
            "school_subdistrict": p["SchoolSubDistrict"],
            "ballot": sig_to_id[sig],
        }
    out = {
        "source": "https://myballotmn.sos.mn.gov/",
        "updated": datetime.date.today().isoformat(),
        "official_precinct_count": OFFICIAL_PRECINCT_COUNT,
        "precincts": dict(sorted(precincts.items())),
        "ballots": ballots,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1)
    print(f"Wrote {len(precincts)} precincts ({OFFICIAL_PRECINCT_COUNT} official), "
          f"{len(ballots)} distinct ballots to {os.path.relpath(OUT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--compact", action="store_true", help="skip fetching; rebuild the compact file from the cache")
    ap.add_argument("--cache", help="use a different cache directory")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.3)
    args = ap.parse_args()
    if args.cache:
        CACHE = args.cache
    if not args.compact:
        fetch(args.workers, args.delay)
    compact()
