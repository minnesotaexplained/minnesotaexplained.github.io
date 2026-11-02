#!/usr/bin/env python3
"""Build data/MPLS.json: every race on any Minneapolis ballot, with the wards and
precincts that vote in each district race.

Inputs (all in this repo):
  data/61A.json                       races shared by every Minneapolis ballot, plus 61A's
                                      district races. Your edits there carry over.
  data/sources/mpls_ballots.json      every precinct's ballot (scripts/fetch_mpls_ballots.py)
  data/sources/mpls_candidates.json   profiles for district-race candidates not on 61A

For district races not on the 61A ballot, the DFL candidate (or, in nonpartisan races,
the candidate with a profile in mpls_candidates.json) is listed. Edit the settings
below to change who is endorsed.

Usage:
    python3 scripts/build_mpls.py
"""
import copy
import json
import os
import re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")

# ---------------- Editorial settings ----------------

# Not endorsing these. If they're unopposed they stay on the page, marked
# "Not endorsed"; otherwise they're removed (and the race drops off if empty).
NOT_ENDORSED = {
    "Michael Howard", "Omar Fateh", "Fue Lee", "Abdul Abdi", "Becca Dryden",
    "Simon Trautmann Cordova", "Bobby Joe Champion", "Shannon Gibney", "Angela Conley",
}

NOT_ENDORSED_NOTE = "We are not officially endorsing these candidates, but we are providing information on them."

INTRO = ("This page covers every Minneapolis ballot. Races marked \"District race\" appear only on some "
         "ballots; open \"Who votes in this race\" to see which wards and precincts. To find your ward "
         "and precinct, or to see your exact ballot, use the Secretary of State's ballot lookup.")

# Shown under the intro when the precinct data is incomplete. {missing} is filled in.
COVERAGE_NOTE = ("The precinct lists cover {have} of Minneapolis's {official} precincts. {missing} "
                 "{verb} not included yet; if you vote there, check your exact ballot with the "
                 "Secretary of State's ballot lookup.")
# Precincts we know exist but couldn't download (from the city's official precinct list).
KNOWN_MISSING = ["Ward 1 Precinct 2", "Ward 11 Precinct 3"]

# Extra notes on specific district races.
RACE_NOTES = {
    "State Senator District 51": "Senate District 51 is mostly Richfield and Bloomington; only a small part of Minneapolis votes in it.",
    "State Representative District 51A": "House District 51A is mostly Richfield; only a small part of Minneapolis votes in it.",
}

# ----------------------------------------------------

PARTY = {"DEMOCRATIC-FARMER-LABOR": "DFL", "NONPARTISAN": ""}
DISTRICT_RACE = r"^(State Senator District|State Representative District|County Commissioner District|School Board Member District)"


def load(path):
    return json.load(open(os.path.join(DATA, path)))


def pct_key(p):
    m = re.match(r"(\d+)(.*)", p)
    return (int(m.group(1)), m.group(2)) if m else (999, p)


def main():
    src = load("sources/mpls_ballots.json")
    profiles = load("sources/mpls_candidates.json")["candidates"]
    base = load("61A.json")

    precincts = src["precincts"]
    all_names = set(precincts)
    wards = defaultdict(set)
    office_pcts = defaultdict(set)
    office_code, office_rows = {}, {}
    for name, p in precincts.items():
        wards[p["ward"]].add(name)
        for row in src["ballots"][p["ballot"]]:
            t = row["OfficeTitle"]
            office_pcts[t].add(name)
            office_code[t] = row["UploadOfficeCode"]
            office_rows.setdefault(t, {})[row["CandidateScreenName"]] = row

    def coverage(title):
        names = office_pcts[title]
        if names == all_names:
            return None
        by_ward = defaultdict(list)
        for n in names:
            by_ward[precincts[n]["ward"]].append(precincts[n]["precinct"])
        ward_list = [{"ward": w, "all": names >= wards[w], "precincts": sorted(by_ward[w], key=pct_key)}
                     for w in sorted(by_ward)]
        ws = [str(w["ward"]) for w in ward_list]
        cov = {"summary": f"{len(names)} precinct{'s' if len(names) != 1 else ''} in "
                          f"Ward{'s' if len(ws) > 1 else ''} {', '.join(ws)}",
               "wards": ward_list}
        if title in RACE_NOTES:
            cov["note"] = RACE_NOTES[title]
        return cov

    def candidates_in(title):
        return [n for n, r in office_rows[title].items() if r["PartyName"] != "WRITE-IN"]

    # Races from the 61A file (shared races + 61A's district races).
    offices = []
    for o in base["offices"]:
        o = copy.deepcopy(o)
        sos = o["office"] if o["office"] in office_code else next(
            (t for t in office_code if o["office"].upper().startswith(t.upper() + ":")), None)
        if sos is None:
            raise SystemExit(f"Can't match a 61A race to the state data: {o['office']}")
        o["_code"], o["_sos"] = office_code[sos], sos
        if coverage(sos):
            o["coverage"] = coverage(sos)
        offices.append(o)
    have = {o["_sos"] for o in offices}

    # District races not on the 61A ballot.
    for title in sorted(office_code, key=lambda t: office_code[t]):
        if not re.match(DISTRICT_RACE, title) or title in have:
            continue
        cands = []
        for name in candidates_in(title):
            row = office_rows[title][name]
            if name not in profiles and row["PartyName"] != "DEMOCRATIC-FARMER-LABOR":
                continue
            c = {"name": name, "party": PARTY.get(row["PartyName"], row["PartyName"].title()), "description": ""}
            c.update(copy.deepcopy(profiles.get(name, {"links": []})))
            if not profiles.get(name):
                print("No profile yet for", name, "-", title)
            cands.append(c)
        if not cands:
            print("No endorsed candidate for", title)
            continue
        offices.append({"office": title, "note": "", "_code": office_code[title], "_sos": title,
                        "coverage": coverage(title), "candidates": cands})

    # Not-endorsed handling.
    for o in offices:
        unopposed = len(candidates_in(o["_sos"])) == 1
        kept = []
        for c in o["candidates"]:
            if c["name"] in NOT_ENDORSED:
                if not unopposed:
                    print("Removed:", c["name"], "-", o["office"])
                    continue
                c["endorsed"] = False
            kept.append(c)
        o["candidates"] = kept
    offices = [o for o in offices if o["candidates"]]

    offices.sort(key=lambda o: (o["_code"], o["office"]))
    for o in offices:
        o.pop("_code")
        o.pop("_sos")

    doc = {
        "district": "MPLS",
        "title": "Minneapolis Endorsements",
        "election": base["election"],
        "election_date": base["election_date"],
        "intro": INTRO,
        "voting_info": base.get("voting_info"),
        "not_endorsed_note": NOT_ENDORSED_NOTE,
        "ballot_lookup_url": base["ballot_lookup_url"],
        "offices": offices,
    }
    if len(all_names) < src["official_precinct_count"] and KNOWN_MISSING:
        missing = " and ".join(KNOWN_MISSING)
        doc["coverage_note"] = COVERAGE_NOTE.format(
            have=len(all_names), official=src["official_precinct_count"], missing=missing,
            verb="are" if len(KNOWN_MISSING) > 1 else "is")
    if not doc["voting_info"]:
        doc.pop("voting_info")

    out = os.path.join(DATA, "MPLS.json")
    json.dump(doc, open(out, "w"), indent=2, ensure_ascii=False)
    open(out, "a").write("\n")
    endorsed = sum(1 for o in offices if any(c.get("endorsed", True) for c in o["candidates"]))
    print(f"Wrote {os.path.relpath(out)}: {endorsed} endorsed races, {len(offices) - endorsed} for information, "
          f"{len(all_names)} precincts")


if __name__ == "__main__":
    main()
