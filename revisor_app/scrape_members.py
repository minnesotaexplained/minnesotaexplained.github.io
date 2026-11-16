"""
Looks up party and district for every legislator who authored a bill, using
the member profile URLs already captured in the bill scrape.

  House:  the member list (house.mn.gov/members/list) shows "Name (31B, R)".
          Anyone not on the current list (e.g. resigned) gets their profile
          page fetched, which shows "(R) District: 31B".
  Senate: each member_bio page has "Senator Name (37, R)".

Run after build_data.py has produced data/legislators.json, then re-run
build_data.py to merge the result in:

    python scrape_members.py
    python build_data.py

Output:
    data/members.json   {member_key: {party, district, full_name}}
"""
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
LEGISLATORS = os.path.join(HERE, "data", "legislators.json")
OUT = os.path.join(HERE, "data", "members.json")
HOUSE_LIST = "https://www.house.mn.gov/members/list"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MNRevisorBillsScraper/1.0)"}
DELAY = 0.4


def fetch(session, url):
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    time.sleep(DELAY)
    return resp.text


def norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_house_list(html):
    """profile id -> (full_name, district, party)"""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.select("h5 a[href*='/members/profile/']"):
        m_id = re.search(r"/profile/(\d+)", a["href"])
        m = re.match(r"(.+?)\s*\((\w+),\s*([A-Za-z]+)\)", norm(a.get_text(" ")))
        if m_id and m:
            out[m_id.group(1)] = (m.group(1), m.group(2), m.group(3))
    return out


def parse_house_profile(html):
    text = norm(BeautifulSoup(html, "html.parser").get_text(" "))
    m = re.search(r"Rep\. (.+?) \(([A-Za-z]+)\) District: (\w+)", text)
    return (m.group(1), m.group(3), m.group(2)) if m else None


def parse_senate_bio(html):
    soup = BeautifulSoup(html, "html.parser")
    for h1 in soup.find_all("h1"):
        m = re.match(r"Senator (.+?)\s*\((\w+),\s*([A-Za-z]+)\)", norm(h1.get_text(" ")))
        if m:
            return (m.group(1), m.group(2), m.group(3))
    return None


LRL_URL = "https://www.lrl.mn.gov/legdb/fulldetail?ID={}"
# "Independent Republican" was the Minnesota GOP's name from 1975 to 1995
LRL_PARTIES = {"Democratic-Farmer-Labor": "DFL", "Republican": "R", "Independent Republican": "R"}


def parse_lrl(html, chamber):
    """Legislative Reference Library record; used for former members, whose
    chamber profile pages redirect there or to a JS-only page."""
    soup = BeautifulSoup(html, "html.parser")
    text = norm(soup.get_text(" "))
    m = re.search(r"Party when first elected: (.+?) Counties Served", text)
    if not m:
        return None
    party = LRL_PARTIES.get(m.group(1), m.group(1))
    body = "House" if chamber == "house" else "Senate"
    districts = re.findall(rf"{body} \d{{4}}-\S+ \(District (\w+)\)", text)
    h = soup.find("h1")
    name = norm(h.get_text(" ")) if h else None
    return (name, districts[-1] if districts else None, party)


def main():
    with open(LEGISLATORS) as f:
        legislators = json.load(f)

    session = requests.Session()
    session.headers.update(HEADERS)
    members = {}

    print("Fetching House member list ...", file=sys.stderr)
    house_list = parse_house_list(fetch(session, HOUSE_LIST))
    print(f"  {len(house_list)} current House members", file=sys.stderr)

    for leg in legislators:
        key, url = leg["key"], leg.get("url")
        info = None
        try:
            if leg["chamber"] == "house":
                ident = key[1:]
                info = house_list.get(ident)
                if not info and url:
                    info = parse_house_profile(fetch(session, url))
            elif url:
                info = parse_senate_bio(fetch(session, url))
            if not info:
                info = parse_lrl(fetch(session, LRL_URL.format(key[1:])), leg["chamber"])
                if info:
                    print(f"  {key} {leg['name']}: from LRL record ({info[2]})", file=sys.stderr)
        except Exception as e:
            print(f"  {key} {leg['name']}: fetch failed: {e}", file=sys.stderr)
        if info:
            full_name, district, party = info
            members[key] = {"full_name": full_name, "district": district, "party": party}
        else:
            print(f"  {key} {leg['name']}: party not found", file=sys.stderr)

    with open(OUT, "w") as f:
        json.dump(members, f, indent=1)
    print(f"Wrote {len(members)}/{len(legislators)} members to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
