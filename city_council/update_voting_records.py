"""
One-shot pipeline to refresh the Minneapolis City Council voting data used by index.html.

Combines what used to be three separate scripts (fetch_voting_records.py,
build_issues_index.py, fetch_all_file_items.py) into a single run:

  1. Fetch each current council member's voting record for the year -> council_voting_records_{YEAR}.json
  2. Collapse those per-member records into one row per issue      -> issues_by_vote_{YEAR}.json
  3. Look up the author of each file and merge it in               -> file_authors_{YEAR}.json
                                                                        (also re-saves issues_by_vote_{YEAR}.json)

Usage:
    python3 update_voting_records.py            # current calendar year
    python3 update_voting_records.py --year 2025

Requires LIMS_API_KEY, either exported in the environment or set in a .env file
next to this script (see .env.example).
"""
import argparse
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_URL = "https://lims.minneapolismn.gov/api/v1"


def load_dotenv(path):
    """Minimal .env loader so we don't need the python-dotenv dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv(SCRIPT_DIR / ".env")

try:
    API_KEY = os.environ["LIMS_API_KEY"]
except KeyError:
    raise SystemExit(
        "LIMS_API_KEY is not set. Add it to city_council/.env (see .env.example) "
        "or export it in your shell."
    )

HEADERS = {
    "Authorization": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

session = requests.Session()
session.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# Step 1: fetch each member's voting record for the year
# ---------------------------------------------------------------------------

def get_council_members():
    resp = session.get(f"{BASE_URL}/referenceList/CouncilMembers")
    resp.raise_for_status()
    return [m for m in resp.json() if m.get("IsCurrent")]


def get_voting_record(member_id, year):
    resp = session.post(
        f"{BASE_URL}/search/CouncilMemberVotingRecord",
        json={"MemberId": member_id, "CalendarYear": year},
    )
    resp.raise_for_status()
    return resp.json()


def fetch_voting_records(year):
    print(f"[1/3] Fetching current council members...")
    members = get_council_members()
    print(f"      Found {len(members)} current members")

    results = {}
    for member in members:
        member_id = member["CouncilMemberId"]
        name = member["Name"]
        print(f"      Fetching {name} (id={member_id})...")
        try:
            votes = get_voting_record(member_id, year)
            results[name] = {
                "CouncilMemberId": member_id,
                "Name": name,
                "Year": year,
                "VotingRecord": votes,
            }
            print(f"        {len(votes)} votes")
        except Exception as e:
            print(f"        ERROR: {e}")
            results[name] = {
                "CouncilMemberId": member_id,
                "Name": name,
                "Year": year,
                "Error": str(e),
            }

    out_file = SCRIPT_DIR / f"council_voting_records_{year}.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"      Saved to {out_file.name}")
    return results


# ---------------------------------------------------------------------------
# Step 2: collapse per-member records into one row per issue
# ---------------------------------------------------------------------------

def build_issues_index(records_by_member, year):
    print(f"\n[2/3] Building per-issue vote index...")
    issues = {}

    for member_name, member_data in records_by_member.items():
        if "Error" in member_data:
            continue
        for record in member_data["VotingRecord"]:
            key = (record["FileNumber"], record["MeetingDate"], record["ItemDescription"])
            if key not in issues:
                issues[key] = {
                    "FileNumber": record["FileNumber"],
                    "Subject": record["Subject"],
                    "ItemDescription": record["ItemDescription"],
                    "MeetingDate": record["MeetingDate"],
                    "MeetingBody": record["MeetingBody"],
                    "VoteResult": record["VoteResult"],
                    "FileURL": record["FileURL"],
                    "Votes": {},
                }
            issues[key]["Votes"][member_name] = record["Vote"]

    sorted_issues = sorted(
        issues.values(),
        key=lambda x: (x["MeetingDate"], x["FileNumber"], x["ItemDescription"]),
        reverse=True,
    )

    out_file = SCRIPT_DIR / f"issues_by_vote_{year}.json"
    with open(out_file, "w") as f:
        json.dump(sorted_issues, f, indent=2)

    print(f"      Total unique vote items: {len(sorted_issues)}")
    print(f"      Saved to {out_file.name}")
    return sorted_issues


# ---------------------------------------------------------------------------
# Step 3: look up each file's author and merge it into the issues index
# ---------------------------------------------------------------------------

def fetch_by_year(year):
    resp = session.post(f"{BASE_URL}/search/FileItemSearch", json={"CalendarYear": year})
    resp.raise_for_status()
    return resp.json()


def fetch_by_file_number(file_number, retries=3):
    delay = 0.15
    for attempt in range(retries):
        try:
            resp = session.post(
                f"{BASE_URL}/search/FileItemSearch", json={"FileNumber": file_number}
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if e.response.status_code == 429 and attempt < retries - 1:
                wait = int(e.response.headers.get("Retry-After", 5)) + 1
                print(f"        rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise
        finally:
            time.sleep(delay)
    return []


def best_author(records):
    """First non-zero author across a list of item records for one file number."""
    for r in records:
        if r.get("AuthorId") and r["AuthorId"] != 0:
            return {
                "AuthorId": r["AuthorId"],
                "AuthorLastName": r.get("Author"),
                "FileType": r.get("FileType"),
                "SubCategory": r.get("SubCategory"),
            }
    return {"AuthorId": None, "AuthorLastName": None, "FileType": None, "SubCategory": None}


def fetch_authors_and_enrich(issues, year):
    print(f"\n[3/3] Fetching file authors...")
    resp = session.get(f"{BASE_URL}/referenceList/CouncilMembers")
    resp.raise_for_status()
    id_to_name = {m["CouncilMemberId"]: m["Name"] for m in resp.json()}

    our_file_numbers = set(i["FileNumber"] for i in issues)
    print(f"      Need author info for {len(our_file_numbers)} unique file numbers")

    # Group by year prefix so most files can be bulk-fetched in one call per year.
    # AU-prefixed files (e.g. AU2026-xxxxx) don't have a clean 4-digit year prefix
    # and have to be looked up individually.
    by_year = defaultdict(set)
    for fn in our_file_numbers:
        by_year["AU" if fn[:2] == "AU" else fn[:4]].add(fn)

    all_records = {}
    for yr in sorted(k for k in by_year if k != "AU"):
        print(f"      Fetching CalendarYear={yr} ({len(by_year[yr])} files needed)...")
        for r in fetch_by_year(int(yr)):
            all_records.setdefault(r["FileNumber"], []).append(r)

    if by_year["AU"]:
        print(f"      Fetching {len(by_year['AU'])} AU-prefixed files individually...")
        for fn in sorted(by_year["AU"]):
            try:
                all_records.setdefault(fn, []).extend(fetch_by_file_number(fn))
            except Exception as e:
                print(f"        {fn}: ERROR {e}")

    authors = {}
    for fn in our_file_numbers:
        info = best_author(all_records.get(fn, []))
        authors[fn] = {
            "AuthorId": info["AuthorId"],
            "AuthorLastName": info["AuthorLastName"],
            "AuthorName": id_to_name.get(info["AuthorId"]) if info["AuthorId"] else None,
            "FileType": info["FileType"],
            "SubCategory": info["SubCategory"],
        }

    authors_file = SCRIPT_DIR / f"file_authors_{year}.json"
    with open(authors_file, "w") as f:
        json.dump(authors, f, indent=2)

    authored = sum(1 for v in authors.values() if v["AuthorName"])
    print(f"      Author map: {authored}/{len(authors)} files have a named author")
    for name, c in Counter(v["AuthorName"] for v in authors.values() if v["AuthorName"]).most_common():
        print(f"        {name}: {c}")

    for issue in issues:
        info = authors.get(issue["FileNumber"], {})
        issue["AuthorId"] = info.get("AuthorId")
        issue["AuthorName"] = info.get("AuthorName")
        issue["FileType"] = info.get("FileType")
        issue["SubCategory"] = info.get("SubCategory")

    issues_file = SCRIPT_DIR / f"issues_by_vote_{year}.json"
    with open(issues_file, "w") as f:
        json.dump(issues, f, indent=2)
    print(f"      Re-saved enriched issues to {issues_file.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=datetime.now().year,
                         help="Calendar year to fetch (default: current year)")
    args = parser.parse_args()

    records = fetch_voting_records(args.year)
    issues = build_issues_index(records, args.year)
    fetch_authors_and_enrich(issues, args.year)

    print(f"\nDone. index.html expects issues_by_vote_{args.year}.json in this directory.")


if __name__ == "__main__":
    main()
