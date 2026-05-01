import json
from collections import defaultdict

INPUT_FILE = "council_voting_records_2026.json"
OUTPUT_FILE = "issues_by_vote_2026.json"


def main():
    with open(INPUT_FILE) as f:
        data = json.load(f)

    # Each issue is uniquely identified by FileNumber + MeetingDate + ItemDescription.
    # We accumulate each member's vote per issue.
    issues = {}

    for member_name, member_data in data.items():
        if "Error" in member_data:
            continue
        for record in member_data["VotingRecord"]:
            key = (
                record["FileNumber"],
                record["MeetingDate"],
                record["ItemDescription"],
            )
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

    # Sort by meeting date descending, then file number
    sorted_issues = sorted(
        issues.values(),
        key=lambda x: (x["MeetingDate"], x["FileNumber"], x["ItemDescription"]),
        reverse=True,
    )

    with open(OUTPUT_FILE, "w") as f:
        json.dump(sorted_issues, f, indent=2)

    print(f"Total unique vote items: {len(sorted_issues)}")
    print(f"Saved to {OUTPUT_FILE}")

    # Quick summary
    members = sorted({name for issue in sorted_issues for name in issue["Votes"]})
    print(f"\nMembers tracked ({len(members)}):")
    for m in members:
        print(f"  {m}")


if __name__ == "__main__":
    main()
