"""
Scrapes standing committee rosters for the House and Senate.

  House:  house.mn.gov/committees lists each committee; /Committees/members/<id>
          has one card per member: optional role, "Rep. Name (PARTY) District: 12A".
  Senate: senate.mn/committees lists committee_bio.php?cmte_id=<id>&ls=94; each
          page has "Committee Leadership" and "Committee Members" cards with
          "Name, 14, DFL" and an optional role above the name.

Nearly every sitting legislator serves on a committee, so the union of the
rosters doubles as the seat roster for the hex map; the few seats left over (the
Speaker, for one) are filled from ../mn_legislature/election_2026.json, which
also supplies who won each seat in 2026. Members are matched to the
bill-authorship keys in data/legislators.json (built by build_data.py) so the
app can link a member to the bills they wrote.

    python scrape_committees.py

Output:
    data/committees.json
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
LEGISLATORS = os.path.join(HERE, "data", "legislators.json")
OUT = os.path.join(HERE, "data", "committees.json")
# Pre-election incumbents and 2026 winners; built by ../mn_legislature/scrape_election.py
ELECTION = os.path.join(HERE, "..", "mn_legislature", "election_2026.json")
TILES = os.path.join(HERE, "..", "mn_legislature", "district_tiles.json")
HOUSE_INDEX = "https://www.house.mn.gov/committees"
SENATE_INDEX = "https://www.senate.mn/committees"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MNRevisorBillsScraper/1.0)"}
DELAY = 0.4

# Sort order for roles; anything unrecognized sorts with the leads
ROLE_RANK = {
    "Chair": 0, "Co-Chair": 0,
    "Vice Chair": 1, "Co-Vice Chair": 1,
    "Ranking Minority Member": 2, "Republican Lead": 2, "DFL Lead": 2,
    "Alternate": 8,
    "Member": 9,
}
ROLE_ALIASES = {"Committee Chair": "Chair"}


def fetch(session, url):
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    time.sleep(DELAY)
    return resp.text


def norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def committee_kind(name):
    return "subcommittee" if re.search(r"subcommittee|division", name, re.I) else "committee"


# ---------------------------------------------------------------------------
# House
# ---------------------------------------------------------------------------
def house_committees(session):
    soup = BeautifulSoup(fetch(session, HOUSE_INDEX), "html.parser")
    out = {}
    for a in soup.find_all("a", href=re.compile(r"/Committees/Home/\d+$")):
        cid = re.search(r"(\d+)$", a["href"]).group(1)
        out.setdefault(cid, norm(a.get_text(" ")))
    return out


def parse_house_members(html):
    soup = BeautifulSoup(html, "html.parser")
    meets = None
    label = soup.find("b", string=re.compile(r"Meets"))
    if label:
        meets = norm(label.parent.get_text(" ")).removeprefix("Meets:").strip() or None
    members = []
    for card in soup.select("div.media"):
        body = card.find("div", class_="media-body")
        link = body and body.find("a", href=re.compile(r"/members/profile/\d+"))
        if not link:
            continue
        line = norm(link.find_parent("span").get_text(" "))
        m = re.search(r"Rep\. (.+?) \(([A-Za-z]+)\) District: (\w+)", line)
        if not m:
            continue
        role_el = body.find("u")
        members.append({
            "name": m.group(1),
            "party": m.group(2),
            "district": m.group(3),
            "role": norm(role_el.get_text(" ")) if role_el else "Member",
            "url": link["href"],
            "key": "h" + re.search(r"profile/(\d+)", link["href"]).group(1),
        })
    return meets, members


# ---------------------------------------------------------------------------
# Senate
# ---------------------------------------------------------------------------
def senate_committees(session):
    soup = BeautifulSoup(fetch(session, SENATE_INDEX), "html.parser")
    out = {}
    for a in soup.find_all("a", href=re.compile(r"committee_bio\.php\?cmte_id=\d+")):
        cid = re.search(r"cmte_id=(\d+)", a["href"]).group(1)
        out.setdefault(cid, norm(a.get_text(" ")))
    return out


def parse_senate_members(html, url):
    soup = BeautifulSoup(html, "html.parser")
    text = norm(soup.get_text(" "))
    m = re.search(r"Regular Meeting Schedule: (.+?) Committee (Leadership|Members)", text)
    meets = m.group(1) if m else None
    members = []
    # Only the leadership/member cards; the subcommittee summary further down is plain links
    for card in soup.select("div.media"):
        link = card.select_one("h5 a[href*='member_bio']")
        if not link:
            continue
        m = re.match(r"(.+?),\s*(\d+),\s*([A-Za-z]+)$", norm(link.get_text(" ")))
        if not m:
            continue
        role_el = card.select_one("div.media-body > b")
        members.append({
            "name": m.group(1),
            "party": m.group(3),
            "district": m.group(2).zfill(2),
            "role": norm(role_el.get_text(" ")) if role_el else "Member",
            "url": urljoin(url, link["href"]),
            "key": None,  # filled in from legislators.json below
        })
    return meets, members


def senate_key_lookup():
    """(district, last name) -> legislators.json key, for current senators."""
    if not os.path.exists(LEGISLATORS):
        return {}
    with open(LEGISLATORS) as f:
        legs = json.load(f)
    return {(l.get("district"), l["name"].split()[-1].lower()): l["key"]
            for l in legs if l["chamber"] == "senate" and l.get("district")}


def last_name(full):
    parts = [p for p in re.sub(r"[.,]", " ", full).split() if p.lower() not in ("jr", "sr", "ii", "iii")]
    return parts[-1].lower()


# ---------------------------------------------------------------------------
def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    committees = []

    print("House committees ...", file=sys.stderr)
    for cid, name in house_committees(session).items():
        url = f"https://www.house.mn.gov/Committees/members/{cid}"
        try:
            meets, members = parse_house_members(fetch(session, url))
        except Exception as e:
            print(f"  {cid} {name}: fetch failed: {e}", file=sys.stderr)
            continue
        print(f"  {name}: {len(members)} members", file=sys.stderr)
        committees.append({"id": f"h{cid}", "chamber": "house", "name": name, "kind": committee_kind(name),
                           "url": f"https://www.house.mn.gov/Committees/Home/{cid}", "meets": meets,
                           "members": members})

    print("Senate committees ...", file=sys.stderr)
    keys = senate_key_lookup()
    for cid, name in senate_committees(session).items():
        url = f"https://www.senate.mn/committees/committee_bio.php?cmte_id={cid}&ls=94"
        try:
            meets, members = parse_senate_members(fetch(session, url), url)
        except Exception as e:
            print(f"  {cid} {name}: fetch failed: {e}", file=sys.stderr)
            continue
        for mem in members:
            mem["key"] = keys.get((mem["district"], last_name(mem["name"])))
        print(f"  {name}: {len(members)} members", file=sys.stderr)
        committees.append({"id": f"s{cid}", "chamber": "senate", "name": name, "kind": committee_kind(name),
                           "url": url, "meets": meets, "members": members})

    for c in committees:
        for mem in c["members"]:
            mem["role"] = ROLE_ALIASES.get(mem["role"], mem["role"])
        c["members"].sort(key=lambda m: (ROLE_RANK.get(m["role"], 2), m["name"].split()[-1]))

    # Seat roster: one entry per district, from the union of committee rosters
    seats = {}
    for c in committees:
        for mem in c["members"]:
            seat = seats.setdefault(f"{c['chamber']}:{mem['district']}", {
                **{k: mem[k] for k in ("name", "party", "district", "url", "key")}, "committees": []})
            seat["committees"].append(c["id"])

    for ch, n in (("house", 134), ("senate", 67)):
        have = sum(1 for k in seats if k.startswith(ch + ":"))
        print(f"{ch}: {have}/{n} seats have committee assignments", file=sys.stderr)

    # Seats with no assignments (e.g. the Speaker): take the sitting member from
    # the 2026 election data's pre-election incumbent. Also record who won the
    # seat in 2026, for the incoming legislature.
    if os.path.exists(ELECTION):
        with open(ELECTION) as f:
            races = json.load(f)["races"]
        for r in races:
            sk = f"{r['chamber']}:{r['district']}"
            prev = r.get("previous")
            if sk not in seats and prev:
                print(f"  {sk}: no committees, using incumbent {prev['name']}", file=sys.stderr)
                seats[sk] = {"name": prev["name"], "party": prev["party"], "district": r["district"],
                             "url": None, "key": None, "committees": []}
            if sk in seats:
                seats[sk]["next"] = {"name": r["winner"]["name"], "party": r["winner"]["party"],
                                     "flip": bool(r.get("flip"))}
    else:
        print(f"  {ELECTION} not found; seats without committees are left out", file=sys.stderr)

    # Author keys for seats filled from election data, via the bill-author list
    if os.path.exists(LEGISLATORS):
        with open(LEGISLATORS) as f:
            legs = json.load(f)
        for sk, seat in seats.items():
            if seat["key"]:
                continue
            ch = sk.split(":")[0]
            hit = next((l for l in legs if l["chamber"] == ch and l.get("district") == seat["district"]
                        and last_name(l.get("full_name") or l["name"]) == last_name(seat["name"])), None)
            if hit:
                seat["key"], seat["url"] = hit["key"], hit.get("url")
    unmatched = [s["name"] for k, s in seats.items() if not s["key"]]
    if unmatched:
        print(f"no bill-author key for: {', '.join(unmatched)}", file=sys.stderr)

    # Hex layout for the seat map (one tile per district), built by ../mn_legislature/build_tiles.py
    tiles = None
    if os.path.exists(TILES):
        with open(TILES) as f:
            t = json.load(f)
        tiles = {"house": t["house"], "senate": t["senate"]}
    else:
        print(f"  {TILES} not found; the app will have no seat map", file=sys.stderr)

    with open(OUT, "w") as f:
        json.dump({
            "tiles": tiles,
            "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": {"house": HOUSE_INDEX, "senate": SENATE_INDEX},
            "committees": committees,
            "seats": seats,
        }, f, indent=1)
    print(f"Wrote {len(committees)} committees, {len(seats)} seats to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
