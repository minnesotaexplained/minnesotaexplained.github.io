import os
import requests
import json
from collections import defaultdict

API_KEY = os.environ["LIMS_API_KEY"]
BASE_URL = "https://lims.minneapolismn.gov/api/v1"
HEADERS = {
    "Authorization": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

ISSUES_FILE = "issues_by_vote_2026.json"
AUTHORS_FILE = "file_authors_2026.json"
ENRICHED_FILE = "issues_by_vote_2026.json"  # overwrite in place


def fetch_by_year(year):
    resp = requests.post(
        f"{BASE_URL}/search/FileItemSearch",
        headers=HEADERS,
        json={"CalendarYear": year},
    )
    resp.raise_for_status()
    return resp.json()


def fetch_by_file_number(file_number):
    resp = requests.post(
        f"{BASE_URL}/search/FileItemSearch",
        headers=HEADERS,
        json={"FileNumber": file_number},
    )
    resp.raise_for_status()
    return resp.json()


def best_author(records):
    """Return the first non-zero author across a list of item records."""
    for r in records:
        if r.get("AuthorId") and r["AuthorId"] != 0:
            return {
                "AuthorId": r["AuthorId"],
                "AuthorLastName": r.get("Author"),
                "FileType": r.get("FileType"),
                "SubCategory": r.get("SubCategory"),
            }
    return {"AuthorId": None, "AuthorLastName": None, "FileType": None, "SubCategory": None}


def main():
    # Load member id -> full name
    resp = requests.get(f"{BASE_URL}/referenceList/CouncilMembers", headers=HEADERS)
    resp.raise_for_status()
    id_to_name = {m["CouncilMemberId"]: m["Name"] for m in resp.json()}

    # Load our voting issues to know which file numbers we need
    with open(ISSUES_FILE) as f:
        issues = json.load(f)
    our_file_numbers = set(i["FileNumber"] for i in issues)
    print(f"Need author info for {len(our_file_numbers)} unique file numbers")

    # Group by year prefix so we can bulk-fetch
    by_year = defaultdict(set)
    for fn in our_file_numbers:
        # AU2026-xxxxx files don't have a clean 4-digit year prefix
        if fn[:2] == "AU":
            by_year["AU"].add(fn)
        else:
            by_year[fn[:4]].add(fn)

    for yr, fns in sorted(by_year.items()):
        print(f"  Year prefix {yr}: {len(fns)} files")

    # Fetch all records grouped by year
    all_records = {}  # FileNumber -> list of item records

    for yr in sorted(k for k in by_year if k != "AU"):
        print(f"\nFetching CalendarYear={yr} ({len(by_year[yr])} files needed)...")
        records = fetch_by_year(int(yr))
        print(f"  API returned {len(records)} records across {len(set(r['FileNumber'] for r in records))} file numbers")
        for r in records:
            all_records.setdefault(r["FileNumber"], []).append(r)

    # Fetch AU files individually (no year-based query for them)
    if by_year["AU"]:
        print(f"\nFetching {len(by_year['AU'])} AU-prefixed files individually...")
        for fn in sorted(by_year["AU"]):
            try:
                records = fetch_by_file_number(fn)
                all_records.setdefault(fn, []).extend(records)
                author = best_author(records)
                print(f"  {fn}: {author['AuthorLastName'] or '—'}")
            except Exception as e:
                print(f"  {fn}: ERROR {e}")

    # Build author map for our file numbers
    authors = {}
    for fn in our_file_numbers:
        records = all_records.get(fn, [])
        info = best_author(records)
        authors[fn] = {
            "AuthorId": info["AuthorId"],
            "AuthorLastName": info["AuthorLastName"],
            "AuthorName": id_to_name.get(info["AuthorId"]) if info["AuthorId"] else None,
            "FileType": info["FileType"],
            "SubCategory": info["SubCategory"],
        }

    with open(AUTHORS_FILE, "w") as f:
        json.dump(authors, f, indent=2)

    authored = sum(1 for v in authors.values() if v["AuthorName"])
    print(f"\nAuthor map: {authored}/{len(authors)} files have a named author")

    from collections import Counter
    counts = Counter(v["AuthorName"] for v in authors.values() if v["AuthorName"])
    for name, c in counts.most_common():
        print(f"  {name}: {c}")

    # Enrich issues in place
    print(f"\nEnriching {len(issues)} vote records...")
    for issue in issues:
        info = authors.get(issue["FileNumber"], {})
        issue["AuthorId"]   = info.get("AuthorId")
        issue["AuthorName"] = info.get("AuthorName")
        issue["FileType"]   = info.get("FileType")
        issue["SubCategory"] = info.get("SubCategory")

    with open(ENRICHED_FILE, "w") as f:
        json.dump(issues, f, indent=2)
    print(f"Saved enriched issues to {ENRICHED_FILE}")


if __name__ == "__main__":
    main()
