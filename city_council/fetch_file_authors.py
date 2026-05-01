import os
import requests
import json
import time

API_KEY = os.environ["LIMS_API_KEY"]
BASE_URL = "https://lims.minneapolismn.gov/api/v1"
HEADERS = {
    "Authorization": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

ISSUES_FILE = "issues_by_vote_2026.json"
OUTPUT_FILE = "file_authors_2026.json"


def fetch_author(file_number):
    resp = requests.post(
        f"{BASE_URL}/search/FileItemSearch",
        headers=HEADERS,
        json={"FileNumber": file_number},
    )
    resp.raise_for_status()
    items = resp.json()
    # Take the first non-zero AuthorId across all sub-items
    for item in items:
        if item.get("AuthorId") and item["AuthorId"] != 0:
            return {
                "AuthorId": item["AuthorId"],
                "AuthorLastName": item.get("Author"),
                "FileType": item.get("FileType"),
                "SubCategory": item.get("SubCategory"),
            }
    return {"AuthorId": None, "AuthorLastName": None, "FileType": None, "SubCategory": None}


def main():
    with open(ISSUES_FILE) as f:
        issues = json.load(f)

    file_numbers = sorted(set(i["FileNumber"] for i in issues))
    print(f"Fetching authors for {len(file_numbers)} unique file numbers...")

    # Build member id -> full name lookup from the issues data
    member_lookup = {}
    for issue in issues:
        for name, vote in issue["Votes"].items():
            # We'll populate this from the council members API response
            pass

    # Fetch the member list to resolve AuthorId -> full name
    resp = requests.get(f"{BASE_URL}/referenceList/CouncilMembers", headers=HEADERS)
    resp.raise_for_status()
    members = resp.json()
    id_to_name = {m["CouncilMemberId"]: m["Name"] for m in members}

    # Load any previously fetched results so we only retry failures
    try:
        with open(OUTPUT_FILE) as f:
            results = json.load(f)
        print(f"Loaded {len(results)} existing results, retrying failures...")
    except FileNotFoundError:
        results = {}

    todo = [fn for fn in file_numbers if fn not in results or results[fn]["AuthorId"] is None and results[fn]["FileType"] is None]
    print(f"{len(todo)} file numbers to fetch (skipping already successful)...")

    for i, fn in enumerate(todo, 1):
        delay = 0.15
        for attempt in range(3):
            try:
                info = fetch_author(fn)
                author_id = info["AuthorId"]
                results[fn] = {
                    "AuthorId": author_id,
                    "AuthorLastName": info["AuthorLastName"],
                    "AuthorName": id_to_name.get(author_id) if author_id else None,
                    "FileType": info["FileType"],
                    "SubCategory": info["SubCategory"],
                }
                status = results[fn]["AuthorName"] or "—"
                print(f"  [{i}/{len(todo)}] {fn}: {status}")
                break
            except requests.HTTPError as e:
                if e.response.status_code == 429:
                    wait = int(e.response.headers.get("Retry-After", 5)) + 1
                    print(f"  [{i}/{len(todo)}] {fn}: rate limited, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  [{i}/{len(todo)}] {fn}: ERROR {e}")
                    results[fn] = {"AuthorId": None, "AuthorLastName": None, "AuthorName": None,
                                   "FileType": None, "SubCategory": None}
                    break
            except Exception as e:
                print(f"  [{i}/{len(todo)}] {fn}: ERROR {e}")
                results[fn] = {"AuthorId": None, "AuthorLastName": None, "AuthorName": None,
                               "FileType": None, "SubCategory": None}
                break
        time.sleep(delay)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    authored = sum(1 for v in results.values() if v["AuthorName"])
    print(f"\nDone. {authored}/{len(file_numbers)} files have a named author.")
    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
