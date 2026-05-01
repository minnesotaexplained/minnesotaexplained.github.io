import os
import requests
import json
from datetime import datetime

API_KEY = os.environ["LIMS_API_KEY"]
BASE_URL = "https://lims.minneapolismn.gov/api/v1"
CURRENT_YEAR = datetime.now().year

HEADERS = {
    "Authorization": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def get_council_members():
    url = f"{BASE_URL}/referenceList/CouncilMembers"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    members = resp.json()
    return [m for m in members if m.get("IsCurrent")]


def get_voting_record(member_id, year):
    url = f"{BASE_URL}/search/CouncilMemberVotingRecord"
    body = {"MemberId": member_id, "CalendarYear": year}
    resp = requests.post(url, headers=HEADERS, json=body)
    resp.raise_for_status()
    return resp.json()


def main():
    print(f"Fetching current council members...")
    members = get_council_members()
    print(f"Found {len(members)} current members")

    results = {}
    for member in members:
        member_id = member["CouncilMemberId"]
        name = member["Name"]
        print(f"  Fetching {name} (id={member_id})...")
        try:
            votes = get_voting_record(member_id, CURRENT_YEAR)
            results[name] = {
                "CouncilMemberId": member_id,
                "Name": name,
                "Year": CURRENT_YEAR,
                "VotingRecord": votes,
            }
            print(f"    {len(votes)} votes")
        except Exception as e:
            print(f"    ERROR: {e}")
            results[name] = {
                "CouncilMemberId": member_id,
                "Name": name,
                "Year": CURRENT_YEAR,
                "Error": str(e),
            }

    output_file = f"council_voting_records_{CURRENT_YEAR}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {output_file}")


if __name__ == "__main__":
    main()
