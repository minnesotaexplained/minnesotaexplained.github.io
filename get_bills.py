import requests, re, json, time
from bs4 import BeautifulSoup

with open("senate.json") as f:
    reps = json.load(f)


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    bills = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        body = cells[0].get_text(strip=True)
        if body not in ("House", "Senate"):
            continue
        a = cells[1].find("a")
        if not a:
            continue
        bills.append(
            {
                "bill_number": a.text.strip(),
                "bill_url": "https://www.revisor.mn.gov" + a["href"]
                if a["href"].startswith("/")
                else a["href"],
                "official_actions": cells[2].get_text(strip=True),
                "last_action_date": cells[3].get_text(strip=True),
                "description": cells[7].get_text(strip=True) if len(cells) > 7 else "",
            }
        )
    return bills


for r in reps:
    try:
        resp = requests.get(
            r["bills_url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=20
        )
        r["bills"] = parse(resp.text)
        r["bills_count"] = len(r["bills"])
        print(r['name'])
        time.sleep(0.3)
    except Exception as e:
        r["bills"] = []

with open("senate_with_bills_complete.json", "w") as f:
    json.dump(reps, f, indent=2)
print("Done!")