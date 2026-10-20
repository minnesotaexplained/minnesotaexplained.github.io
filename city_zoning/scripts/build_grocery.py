"""Build map_data/grocery.json for the grocery access simulator (sim-grocery.html).

Usage: python build_grocery.py <gis_dir> <grocery_dir> <out_dir>
  gis_dir      output of fetch_gis.py (parcels.json, Minneapolis_Neighborhoods.geojson)
  grocery_dir  output of fetch_grocery.py (inspections.json, osm.json)

Stores
  Inside Minneapolis: City food-inspection records (GROCERY / MEAT MARKET / MARKET licenses)
  inspected since OPEN_SINCE, so closed stores drop out. Each is sorted into
    full   a full-service supermarket (chains, co-ops, large independents), or
    small  a smaller grocer or ethnic market that sells fresh food (name-based, approximate).
  Convenience stores, gas stations, pharmacies, skyway kiosks, warehouse clubs, delivery
  depots and farmers markets are left out.
  Outside Minneapolis (within EDGE_MILES of the city line): OpenStreetMap supermarkets and
  Targets, since people near the border shop there.

People
  Homes per parcel (same rules as build_map.py) summed onto a GRID_FT square grid. Each cell
  keeps its home-weighted center so distances start where people actually live.
"""
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict

import shapely
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

GIS, GROC, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)

HENN = "ESRI:103734"  # Hennepin County coordinates, US feet (assessor X/Y)
to_ft = Transformer.from_crs("EPSG:4326", HENN, always_xy=True).transform
to_ll = Transformer.from_crs(HENN, "EPSG:4326", always_xy=True).transform

GRID_FT = 500
EDGE_MILES = 3
OPEN_SINCE = datetime.datetime(2024, 7, 1).timestamp() * 1000

# ---------------- city outline and neighborhoods ----------------
hoods_gj = json.load(open(os.path.join(GIS, "Minneapolis_Neighborhoods.geojson")))
hoods = []
for f in hoods_gj["features"]:
    p = f["properties"]
    name = p.get("BDNAME") or p.get("NAME") or p.get("name")
    hoods.append((name, transform(to_ft, shape(f["geometry"]))))
hood_names = sorted(h[0] for h in hoods)
hood_idx = {n: i for i, n in enumerate(hood_names)}
city_ft = unary_union([g for _, g in hoods]).buffer(0)
h_tree = STRtree([g for _, g in hoods])

# ---------------- stores inside the city ----------------
FULL = [
    (r"\bCUB FOODS\b", "Cub Foods"), (r"\bLUNDS\b", "Lunds & Byerlys"), (r"\bKOWALSKI", "Kowalski's"),
    (r"\bALDI\b", "Aldi"), (r"^TARGET (STORE|CORP)", "Target"), (r"\bWHOLE FOODS\b", "Whole Foods"),
    (r"\bTRADER JOE", "Trader Joe's"), (r"\bFRESH THYME\b", "Fresh Thyme"),
    (r"\bSEWARD (COMMUNITY )?CO-OP\b", "Seward Co-op"), (r"\bWEDGE COMMUNITY\b", "Wedge Co-op"),
    (r"\bLINDEN HILLS CO-OP\b", "Linden Hills Co-op"), (r"\bEASTSIDE FOOD\b", "Eastside Food Co-op"),
    (r"\bUNITED NOODLES\b", "United Noodles"), (r"\bSHUANG HUR\b", "Shuang Hur"),
    (r"\bHOLY LAND\b", "Holy Land"), (r"\bGOOD GROCER\b", "Good Grocer"),
]
SMALL = (r"MARKET|GROCER|SUPERMERCADO|SUPER MERCADO|SUPERMARKET|MERCADO|HALAL|HALAAL|CARNICERIA|"
         r"ORIENTAL|\bFOODS?\b|TIENDA|SUPERETTE|AFRICAN|\bMEATS?\b|SUPER MORE")
NOT_GROCERY = (r"FARMERS MARKET|MARKET SQUARE|^(THE )?FRESH MAR(KE)?T|CITY MARKET|DOWNTOWN MARKET|TARE MARKET|"
               r"FOOD GROUP|MOTOMART|PUMP|PETROLEUM|LIQUOR|GYM|COMPASS|ARAMARK|AMAZON|GOPUFF|DOORDASH|"
               r"COSTCO|WHOLESALE|HOTEL|INN\b|SUITES|SPICE|POPCORN|CHEESE|LUMBER|CAFE|EATERY|GRILL|"
               r"RESTAURANT|CAMPUS MARKET|MAXWELL|NUTRITION|EXPRESS|5TH AVE MARKET|"
               r"TOBACCO|SUPER USA|FOOD MART|KDIX|MARIGOLD|PRODUCE EXCHANGE|NEIGHBORHOOD ASSOCIATI|GARDEN")


def tidy(name):
    n = re.sub(r"\s+#\s*\w+$|\s+-\s+(GROCERY|MEAT MARKET)$|,?\s+(LLC|INC\.?|CORP\.?|INCORPORATED|CORPORATION)\b\.?",
               "", name.strip(), flags=re.I)
    n = re.sub(r"\s+(LLC|INC\.?)$", "", n, flags=re.I)
    words = []
    for w in n.title().split():
        words.append(w.replace("'S", "'s").replace("’S", "’s"))
    return " ".join(words)


def classify_city(name, cat):
    up = name.upper()
    for pat, brand in FULL:
        if re.search(pat, up):
            return "full", brand
    if up.startswith("TARGET EXPRESS"):
        return "small", "Target (small format)"
    if re.search(NOT_GROCERY, up):
        return None, None
    if cat == "MEAT MARKET" or re.search(SMALL, up):
        return "small", tidy(name)
    return None, None


# A few City records have no coordinates; looked up once with OpenStreetMap Nominatim.
NO_COORDS = {
    "4601 SNELLING AVE": (44.91894, -93.21591),
    "721 WASHINGTON AVE S": (44.97706, -93.25772),
    "4725 CEDAR AVE S": (44.91707, -93.24690),
    "3047 NICOLLET AVE": (44.94755, -93.27783),
}


def nice_addr(a):
    a = re.sub(r"\s*#.*$", "", a or "").title()
    a = re.sub(r"(\d)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), a)
    return re.sub(r"\b(Ne|Se|Nw|Sw)\b", lambda m: m.group(1).upper(), a)


def addr_key(a):
    return re.sub(r"\s*#.*$", "", (a or "").upper()).strip()


stores = {}
rank = {"full": 2, "small": 1}
for r in json.load(open(os.path.join(GROC, "inspections.json"))):
    if (r.get("last") or 0) < OPEN_SINCE or r["FacilityCategory"] == "MARKET":
        continue
    if not r.get("Latitude"):
        if addr_key(r["FullAddress"]) not in NO_COORDS:
            continue
        r["Latitude"], r["Longitude"] = NO_COORDS[addr_key(r["FullAddress"])]
    tier, label = classify_city(r["BusinessName"], r["FacilityCategory"])
    if not tier:
        continue
    key = addr_key(r["FullAddress"])
    cur = stores.get(key)
    if cur is None or rank[tier] > rank[cur["t"]]:
        stores[key] = {"name": label, "t": tier, "addr": nice_addr(r["FullAddress"]),
                       "lat": round(r["Latitude"], 5), "lng": round(r["Longitude"], 5), "in": 1,
                       "src": "City inspections"}
# The same store sometimes holds licenses under two addresses (e.g. a corner building).
city_stores = []
for st in sorted(stores.values(), key=lambda s: -rank[s["t"]]):
    x, y = to_ft(st["lng"], st["lat"])
    if any(o["name"] == st["name"] and abs(o["_x"] - x) + abs(o["_y"] - y) < 400 for o in city_stores):
        continue
    st["_x"], st["_y"] = x, y
    city_stores.append(st)
for st in city_stores:
    del st["_x"], st["_y"]

# ---------------- stores just outside the city ----------------
OSM_FULL = [
    (r"\bcub\b", "Cub Foods"), (r"lunds|byerly", "Lunds & Byerlys"), (r"kowalski", "Kowalski's"),
    (r"\baldi\b", "Aldi"), (r"target", "Target"), (r"whole foods", "Whole Foods"), (r"trader joe", "Trader Joe's"),
    (r"fresh thyme", "Fresh Thyme"), (r"hy-?vee", "Hy-Vee"), (r"walmart", "Walmart"), (r"jerry's", "Jerry's Foods"),
    (r"lakewinds", "Lakewinds Co-op"), (r"mississippi market", "Mississippi Market Co-op"),
    (r"hampden park", "Hampden Park Co-op"), (r"sprouts", "Sprouts"), (r"dragon star", "Dragon Star"),
    (r"shuang hur", "Shuang Hur"), (r"widmer", "Widmer's"), (r"korte", "Korte's"),
]
OSM_SKIP = r"thrift|fare for all|puris|premier food|wholesale|base exchange|treasures"
edge = city_ft.buffer(EDGE_MILES * 5280)
osm_stores = []
seen = set()
for e in json.load(open(os.path.join(GROC, "osm.json"))):
    t = e.get("tags", {})
    lat = e.get("lat") or e.get("center", {}).get("lat")
    lng = e.get("lon") or e.get("center", {}).get("lon")
    name = t.get("name") or ""
    if not lat or not name:
        continue
    x, y = to_ft(lng, lat)
    pt = shapely.Point(x, y)
    if city_ft.contains(pt) or not edge.contains(pt):
        continue
    low = (name + " " + t.get("brand", "")).lower()
    if re.search(OSM_SKIP, low):
        continue
    tier, label = None, name
    for pat, brand in OSM_FULL:
        if re.search(pat, low):
            tier, label = "full", brand
            break
    shop = t.get("shop")
    if tier is None:
        if shop == "department_store":
            continue
        tier = "small"
    if shop == "department_store" and label != "Target":
        continue
    key = (label, round(lat, 3), round(lng, 3))
    if key in seen:
        continue
    seen.add(key)
    city = t.get("addr:city", "")
    addr = " ".join(x for x in [t.get("addr:housenumber", ""), t.get("addr:street", "")] if x)
    osm_stores.append({"name": label, "t": tier, "addr": ", ".join(x for x in [addr, city] if x) or city,
                       "lat": round(lat, 5), "lng": round(lng, 5), "in": 0, "src": "OpenStreetMap"})

all_stores = sorted(city_stores + osm_stores, key=lambda s: (-s["in"], s["t"] != "full", s["name"]))
print("stores in city: full", sum(1 for s in city_stores if s["t"] == "full"),
      "small", sum(1 for s in city_stores if s["t"] == "small"),
      "| outside: full", sum(1 for s in osm_stores if s["t"] == "full"),
      "small", sum(1 for s in osm_stores if s["t"] == "small"))

# ---------------- homes on a grid ----------------
ONE_UNIT = ("RESIDENTIAL 1 UNIT", "MIXED USE - COMMERCIAL IND/RES 1 UNIT")
SHARED = ("RESIDENTIAL 1 UNIT - CONDOMINIUM", "RESIDENTIAL 1 UNIT - CO-OP")


def existing_units(p):
    t = p["PROPERTYTYPE"] or ""
    if t.startswith("RESIDENTIAL 2") or t.endswith("RES 2 UNITS"):
        return 2
    if t.startswith("RESIDENTIAL 3") or t.endswith("RES 3 UNITS"):
        return 3
    if t.startswith(ONE_UNIT) or t in SHARED:
        return 1
    if t in ("APARTMENT", "MIXED USE - COMMERCIAL IND/APARTMENT", "RESIDENTIAL CO-OP MASTER", "COMMERCIAL"):
        return p["TOTALUNITS"] or 0
    return 0


parcels = [p for p in json.load(open(os.path.join(GIS, "parcels.json"))) if p.get("X") and p.get("Y")]
cells = defaultdict(lambda: [0, 0.0, 0.0, Counter()])
pts = shapely.points([p["X"] for p in parcels], [p["Y"] for p in parcels])
idx_p, idx_h = h_tree.query(pts, predicate="intersects")
p_hood = {}
for i, j in zip(idx_p, idx_h):
    p_hood.setdefault(i, hoods[j][0])
total = 0
for i, p in enumerate(parcels):
    u = existing_units(p)
    if not u or i not in p_hood:
        continue
    c = cells[(int(p["X"] // GRID_FT), int(p["Y"] // GRID_FT))]
    c[0] += u
    c[1] += p["X"] * u
    c[2] += p["Y"] * u
    c[3][p_hood[i]] += u
    total += u

cols = {"lat": [], "lng": [], "glat": [], "glng": [], "h": [], "hood": []}
for (gx, gy), (u, sx, sy, hc) in sorted(cells.items()):
    lng, lat = to_ll(sx / u, sy / u)
    glng, glat = to_ll((gx + 0.5) * GRID_FT, (gy + 0.5) * GRID_FT)
    cols["lat"].append(round(lat, 5))
    cols["lng"].append(round(lng, 5))
    cols["glat"].append(round(glat, 5))
    cols["glng"].append(round(glng, 5))
    cols["h"].append(u)
    cols["hood"].append(hood_idx[hc.most_common(1)[0][0]])
print("cells", len(cols["h"]), "homes", total)

# ---------------- where a new grocery store could legally go ----------------
# Table 545-1: grocery store is P in CM2-CM4 and DT1-DT2; limited to 20,000 sq ft ("20P") in
# CM1 and RM1-RM3 (RM with use standards); 40,000 sq ft ("40P") in PR1-PR2; not allowed in UN.
GROCERY_OK = {"CM2": 0, "CM3": 0, "CM4": 0, "DT1": 0, "DT2": 0,
              "CM1": 20, "RM1": 20, "RM2": 20, "RM3": 20, "PR1": 40, "PR2": 40}
SITE_FT = 660  # one-eighth mile
zgj = json.load(open(os.path.join(GIS, "Planning_Primary_Zoning.geojson")))
zfeats = [(f["properties"].get("Land_Use_Code"), transform(to_ft, shape(f["geometry"])))
          for f in zgj["features"] if f.get("geometry")]
zfeats = [(c, g) for c, g in zfeats if c in GROCERY_OK]
z_tree = STRtree([g for _, g in zfeats])
minx, miny, maxx, maxy = city_ft.bounds
sx = [minx + SITE_FT * (i + 0.5) for i in range(int((maxx - minx) / SITE_FT) + 1)]
sy = [miny + SITE_FT * (j + 0.5) for j in range(int((maxy - miny) / SITE_FT) + 1)]
grid = [(x, y) for x in sx for y in sy]
gpts = shapely.points([x for x, _ in grid], [y for _, y in grid])
site_zone = {}
for i, j in zip(*z_tree.query(gpts, predicate="intersects")):
    site_zone.setdefault(i, zfeats[j][0])
sites = {"lat": [], "lng": [], "z": []}
for i in sorted(site_zone):
    lng, lat = to_ll(*grid[i])
    sites["lat"].append(round(lat, 5))
    sites["lng"].append(round(lng, 5))
    sites["z"].append(site_zone[i])
print("candidate sites", len(sites["z"]), Counter(sites["z"]).most_common())

out = {
    "built": datetime.date.today().isoformat(),
    "grid_ft": GRID_FT,
    "homes": total,
    "hoods": hood_names,
    "cells": cols,
    "stores": all_stores,
    "sites": sites,
    "grocery_ok": GROCERY_OK,
    "sources": {
        "stores_city": "City of Minneapolis Food_Inspections (GROCERY / MEAT MARKET licenses inspected since "
                       + datetime.date.fromtimestamp(OPEN_SINCE / 1000).isoformat() + ")",
        "stores_edge": f"OpenStreetMap supermarkets within {EDGE_MILES} miles of the city line",
        "zoning": "City of Minneapolis Planning_Primary_Zoning; grocery store row of Table 545-1",
        "homes": "Hennepin County / Minneapolis Assessing Department parcel data 2026",
    },
}
json.dump(out, open(os.path.join(OUT, "grocery.json"), "w"), separators=(",", ":"))
print("wrote", os.path.join(OUT, "grocery.json"), os.path.getsize(os.path.join(OUT, "grocery.json")) // 1024, "KB")
