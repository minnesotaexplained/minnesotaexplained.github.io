"""Build election_2026.json from the MN Secretary of State results files.

Uses SOS's bulk media files (semicolon-delimited) instead of scraping the
results pages, which sit behind bot protection. Compares each winner's party
with the current holder in reps.json / senate.json to find flips.

Re-run until results are certified; it overwrites election_2026.json.
"""
import json
import os
from datetime import datetime, timezone

import requests

ELECTION_ID = 201
ELECTION_DATE = "2026-11-03"
FILES_BASE = "https://electionresultsfiles.sos.mn.gov/20261103"
SOURCES = {
    "house": f"{FILES_BASE}/LegislativeByDistrict.txt",
    "senate": f"{FILES_BASE}/stsenate.txt",
}
PAGES = {
    "house": f"https://electionresults.sos.mn.gov/Results/Index?ersElectionId={ELECTION_ID}&scenario=StateRepresentative",
    "senate": f"https://electionresults.sos.mn.gov/Results/Index?ersElectionId={ELECTION_ID}&scenario=StateSenate",
}
MEMBER_FILES = {"house": "reps.json", "senate": "senate.json"}
PARTY_CODES = {"Republican": "R", "Democratic-Farmer-Labor": "DFL"}


def district_key(raw, chamber):
    """'1A' -> '01A' (house), '1' -> '01' (senate); matches districts.json."""
    raw = raw.strip()
    if chamber == "house":
        return raw[:-1].zfill(2) + raw[-1].upper()
    return raw.zfill(2)


def load_previous(chamber):
    with open(MEMBER_FILES[chamber]) as f:
        members = json.load(f)
    return {
        m["district_name"].replace("District ", ""): {
            "name": m["name"],
            "party": PARTY_CODES.get(m["party"], m["party"]),
        }
        for m in members
    }


def parse(text, chamber):
    """Group rows of the SOS media file into races keyed by district."""
    races = {}
    for line in text.splitlines():
        cols = line.split(";")
        if len(cols) < 16:
            continue
        district = district_key(cols[5], chamber)
        race = races.setdefault(district, {
            "chamber": chamber,
            "district": district,
            "precincts": {"reported": int(cols[11]), "total": int(cols[12])},
            "candidates": [],
            "total_votes": int(cols[15]),
        })
        race["candidates"].append({
            "name": cols[7].strip(),
            "party": cols[10].strip(),
            "votes": int(cols[13]),
            "pct": float(cols[14]),
        })
    return races


def finish_race(race, previous):
    cands = sorted(race["candidates"], key=lambda c: -c["votes"])
    race["candidates"] = cands
    winner, runner_up = cands[0], cands[1] if len(cands) > 1 else None
    race["winner"] = {"name": winner["name"], "party": winner["party"]}

    # Uncontested = no real opponent (write-ins don't count)
    race["uncontested"] = not any(c["party"] != "WI" for c in cands[1:])
    if runner_up:
        margin_votes = winner["votes"] - runner_up["votes"]
        margin_pct = round(100 * margin_votes / race["total_votes"], 2) if race["total_votes"] else 0
    else:
        margin_votes, margin_pct = winner["votes"], 100.0
    race["margin"] = {"votes": margin_votes, "pct": margin_pct}

    prev = previous.get(race["district"])
    race["previous"] = prev
    if prev and prev["party"] != winner["party"]:
        race["flip"] = f'{prev["party"]}->{winner["party"]}'
    else:
        race["flip"] = None
    return race


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))  # data files live next to this script
    all_races = []
    precincts = None
    for chamber in ("senate", "house"):
        resp = requests.get(SOURCES[chamber], timeout=30)
        resp.raise_for_status()
        races = parse(resp.text, chamber)
        previous = load_previous(chamber)
        missing = set(previous) - set(races)
        if missing:
            print(f"WARNING: {chamber} districts with no results: {sorted(missing)}")
        for district in sorted(races):
            all_races.append(finish_race(races[district], previous))
        print(f"{chamber}: {len(races)} races")

    reported = sum(r["precincts"]["reported"] for r in all_races)
    total = sum(r["precincts"]["total"] for r in all_races)
    out = {
        "election": {
            "id": ELECTION_ID,
            "date": ELECTION_DATE,
            "status": "unofficial",
            "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "all_precincts_reported": reported == total,
            "sources": {"data": SOURCES, "pages": PAGES},
        },
        "races": all_races,
    }
    with open("election_2026.json", "w") as f:
        json.dump(out, f, indent=1)
    print("wrote election_2026.json")


if __name__ == "__main__":
    main()
