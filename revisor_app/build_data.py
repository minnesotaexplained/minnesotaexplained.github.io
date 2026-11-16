#!/usr/bin/env python3
"""Build a lean search index plus per-bill detail files from bills_94.json,
so the web app doesn't have to load a 300MB blob up front.

Output:
  data/bills_index.json            - lean list for search/browse, incl. the
                                     pipeline stage of each House/Senate file
  data/legislators.json            - authorship activity per legislator (plus
                                     party/district if data/members.json exists;
                                     see scrape_members.py)
  data/bills/<revisor_number>.json - full per-bill record incl. bill text
"""
import json
import os
import re

SRC = os.path.join(os.path.dirname(__file__), "bills_94.json")
OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
BILLS_DIR = os.path.join(OUT_DIR, "bills")


# ---------------------------------------------------------------------------
# Pipeline stage classification
# ---------------------------------------------------------------------------
# Ordered from least to most advanced. "replaced" and "folded" are off-ramps:
# the file stopped moving because a companion or another bill carried it on.
STAGE_RANK = {
    "committee": 1,       # introduced / referred, not yet out of committee
    "floor": 2,           # reported out; second reading, on a calendar/orders
    "passed_origin": 3,   # passed its chamber of origin
    "second_chamber": 4,  # received in the other chamber
    "passed_second": 5,   # passed the other chamber
    "reconcile": 6,       # concurrence vote or conference committee
    "governor": 7,        # presented to the governor
    "law": 8,             # signed / chaptered
    "vetoed": 8,
}

RE_REFERRED = re.compile(r"referred to|re-refer", re.I)
RE_CMTE_REPORT = re.compile(r"^(Committee report|Comm report)", re.I)
RE_FLOOR = re.compile(
    r"Second reading|General Register|General Orders|Calendar for the Day|"
    r"Special Order|Consent Calendar|Fiscal Calendar",
    re.I,
)
RE_PASSED_HOUSE = re.compile(r"^Bill was (re)?passed", re.I)
RE_PASSED_SENATE = re.compile(r"Third reading (Re)?passed", re.I)
RE_RECONCILE = re.compile(r"conference|concur|accedes", re.I)
RE_GOVERNOR = re.compile(r"Presented to Governor|Presentment date", re.I)
RE_VETO = re.compile(r"veto", re.I)
RE_LAW = re.compile(
    r"Governor approval|Governor's action Approval|Chapter number|"
    r"Secretary of State|without signature",
    re.I,
)
RE_FOLDED = re.compile(r"^See\b")


def classify_file(chamber_rec, chamber_key):
    """Return (stage, committee_reported) for one House or Senate file."""
    num = chamber_rec.get("bill_number") or ""
    prefix = num[:2]  # HF / SF
    other_prefix = "SF" if prefix == "HF" else "HF"
    origin = "House" if chamber_key == "house" else "Senate"
    other = "Senate" if origin == "House" else "House"
    actions = chamber_rec.get("actions") or {}
    origin_actions = [a["text"] for a in actions.get(origin, [])]
    other_actions = [a["text"] for a in actions.get(other, [])]
    all_actions = origin_actions + other_actions

    passed_re_origin = RE_PASSED_HOUSE if origin == "House" else RE_PASSED_SENATE
    passed_re_other = RE_PASSED_SENATE if origin == "House" else RE_PASSED_HOUSE

    stage = "committee"
    cmte_reported = any(RE_CMTE_REPORT.search(t) for t in origin_actions)

    def bump(s):
        nonlocal stage
        if STAGE_RANK[s] > STAGE_RANK[stage]:
            stage = s

    if any(RE_FLOOR.search(t) for t in origin_actions):
        bump("floor")
    passed_origin = any(passed_re_origin.search(t) for t in origin_actions)
    if passed_origin:
        bump("passed_origin")
        if other_actions:
            bump("second_chamber")
        if any(passed_re_other.search(t) for t in other_actions) or any(
            t.startswith(f"Returned from {other}") for t in origin_actions
        ):
            bump("passed_second")
        if any(RE_RECONCILE.search(t) for t in all_actions):
            bump("reconcile")
    if any(RE_GOVERNOR.search(t) for t in all_actions):
        bump("governor")
    if any(RE_VETO.search(t) for t in all_actions):
        bump("vetoed")
    if any(RE_LAW.search(t) for t in all_actions):
        stage = "law"

    if STAGE_RANK[stage] < STAGE_RANK["governor"]:
        replaced = any(
            f"{prefix} indefinitely postponed" in t
            or re.search(rf"\b{other_prefix}\d*\s+substituted", t)
            for t in all_actions
        )
        folded = any(RE_FOLDED.search(t) for t in origin_actions)
        if replaced:
            stage = "replaced"
        elif folded:
            stage = "folded"

    return stage, cmte_reported


def row_stage(file_stages):
    """Most advanced stage across a bill's House and Senate files."""
    live = [s for s in file_stages if s in STAGE_RANK]
    if live:
        return max(live, key=lambda s: STAGE_RANK[s])
    if "folded" in file_stages:
        return "folded"
    if "replaced" in file_stages:
        return "replaced"
    return None


# ---------------------------------------------------------------------------
# Legislators
# ---------------------------------------------------------------------------
def member_key(author, chamber_key):
    """Stable key for a legislator: chamber + member id from their profile URL."""
    url = author.get("url") or ""
    m = re.search(r"(?:profile/|leg_id=)(\d+)", url)
    ident = m.group(1) if m else author["name"]
    return f"{chamber_key[0]}{ident}"


def authors_of(bill, chamber):
    c = bill.get(chamber)
    if not c:
        return []
    return [a["name"] for a in (c.get("authors") or [])]


def build():
    with open(SRC) as f:
        data = json.load(f)

    os.makedirs(BILLS_DIR, exist_ok=True)

    index = []
    legislators = {}
    seen = set()
    for b in data:
        rn = b["revisor_number"]
        if rn in seen:  # the source table lists a few rows twice
            continue
        seen.add(rn)

        files = []
        author_keys = set()
        for ch in ("house", "senate"):
            rec = b.get(ch)
            if not rec or not rec.get("bill_number"):
                continue
            num = rec["bill_number"]
            if num[:2] in ("HF", "SF"):
                stage, reported = classify_file(rec, ch)
            else:
                stage, reported = None, False  # resolutions follow a different path
            files.append({"n": num, "ch": ch, "st": stage, "cr": reported})
            for a in rec.get("authors") or []:
                author_keys.add(member_key(a, ch))

        rstage = row_stage([f["st"] for f in files])

        for f in files:
            rec = b[f["ch"]]
            for pos, a in enumerate(rec.get("authors") or []):
                key = member_key(a, f["ch"])
                leg = legislators.setdefault(key, {
                    "key": key,
                    "name": a["name"],
                    "chamber": f["ch"],
                    "url": a.get("url"),
                    "chief": 0,
                    "coauthor": 0,
                    "chief_passed": 0,
                    "chief_law": 0,
                    "co_law": 0,
                })
                is_law = rstage == "law"
                if pos == 0:
                    leg["chief"] += 1
                    if rstage and STAGE_RANK.get(rstage, 0) >= STAGE_RANK["passed_origin"]:
                        leg["chief_passed"] += 1
                    if is_law:
                        leg["chief_law"] += 1
                else:
                    leg["coauthor"] += 1
                    if is_law:
                        leg["co_law"] += 1

        index.append({
            "revisor_number": rn,
            "session": b["session"],
            "description": b["description"],
            "bill_numbers": b["bill_numbers"],
            "latest_action": b["latest_action"],
            "house_bill": b["house"]["bill_number"] if b.get("house") else None,
            "senate_bill": b["senate"]["bill_number"] if b.get("senate") else None,
            "authors": sorted(set(authors_of(b, "house") + authors_of(b, "senate"))),
            "author_keys": sorted(author_keys),
            "files": files,
            "stage": rstage,
        })

        # per-bill detail file (full record, includes bill text)
        safe_name = rn.replace("/", "_")
        with open(os.path.join(BILLS_DIR, f"{safe_name}.json"), "w") as f:
            json.dump(b, f)

    with open(os.path.join(OUT_DIR, "bills_index.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))

    # party / district / full name from scrape_members.py, when it has been run
    members_path = os.path.join(OUT_DIR, "members.json")
    if os.path.exists(members_path):
        with open(members_path) as f:
            members = json.load(f)
        for key, leg in legislators.items():
            leg.update(members.get(key, {}))

    legs = sorted(legislators.values(), key=lambda l: (-l["chief"], l["name"]))
    with open(os.path.join(OUT_DIR, "legislators.json"), "w") as f:
        json.dump(legs, f, separators=(",", ":"))

    print(f"wrote index with {len(index)} bills")
    print(f"wrote {len(index)} detail files to {BILLS_DIR}")
    print(f"wrote {len(legs)} legislators")


if __name__ == "__main__":
    build()
