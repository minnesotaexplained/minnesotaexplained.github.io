#!/usr/bin/env python3
"""Build a starter endorsements JSON from the MN Secretary of State ballot lookup.

The SOS "What's on my ballot" page (myballotmn.sos.mn.gov/results?id=<ID>...)
loads its data from a JSON API keyed by the same id. This script pulls that
ballot and writes every office and candidate (minus write-in slots) to
data/<district>.json. Edit the file afterwards: delete the candidates you are
not endorsing and fill in descriptions.

Usage:
    python3 scripts/fetch_ballot.py 232369    # writes data/61A.json
    python3 scripts/fetch_ballot.py 232369 --out data/custom.json

Running it again overwrites the output file, including your edits.
"""
import argparse
import json
import os
import sys
import urllib.request

API = "https://myballotmn.sos.mn.gov/api/PollingPlaceData/GetPollingPlaceData?prodAddressRangeId={}"
LOOKUP = "https://myballotmn.sos.mn.gov/"

PARTY_NAMES = {
    "DEMOCRATIC-FARMER-LABOR": "DFL",
    "NONPARTISAN": "",
}


def party(name):
    if name in PARTY_NAMES:
        return PARTY_NAMES[name]
    return name.title()


def build(data):
    pr = data["PollingResult"]
    offices = []
    by_title = {}

    for row in data["Ballots"]:
        if row["PartyName"] == "WRITE-IN":
            continue
        title = row["OfficeTitle"]

        if row["QuestionId"]:
            # Ballot question: endorse "Yes" or "No" like a candidate.
            name = f"{title.title()}: {row['QuestionTextBrief']}"
            offices.append({
                "office": name,
                "note": row["QuestionTextFull"],
                "candidates": [
                    {"name": "Yes", "party": "", "description": "", "links": []},
                    {"name": "No", "party": "", "description": "", "links": []},
                ],
            })
            continue

        if title not in by_title:
            by_title[title] = {"office": title, "note": "", "candidates": []}
            offices.append(by_title[title])

        links = []
        if row["CampaignWebsite"]:
            links.append({"label": "Campaign website", "url": row["CampaignWebsite"]})
        by_title[title]["candidates"].append({
            "name": row["CandidateScreenName"],
            "party": party(row["PartyName"]),
            "description": "",
            "links": links,
        })

    return {
        "district": pr["LegDistrict"],
        "title": f"House District {pr['LegDistrict']} Endorsements",
        "election": pr["FullElectionDescription"].split(" ", 1)[1].title(),
        "election_date": pr["ElectionDate"].split(" ")[0],
        "intro": "",
        "ballot_lookup_url": LOOKUP,
        "offices": offices,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("address_range_id", help="the id= value from a myballotmn results URL")
    ap.add_argument("--out", help="output path (default: data/<district>.json)")
    args = ap.parse_args()

    with urllib.request.urlopen(API.format(args.address_range_id)) as resp:
        data = json.load(resp)

    result = build(data)
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", f"{result['district']}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    n = sum(len(o["candidates"]) for o in result["offices"])
    print(f"Wrote {len(result['offices'])} offices, {n} candidates to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
