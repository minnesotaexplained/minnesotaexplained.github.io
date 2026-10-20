"""Build the data files for map.html.

Inputs (downloaded from the City of Minneapolis ArcGIS services, see fetch_gis.sh):
  gis/Planning_Primary_Zoning.geojson     primary zoning districts (UN1 ... TR1)
  gis/Planning_Zoning_Built_Form.geojson  built form overlay districts (Interior 1 ... Core 50)
  gis/Minneapolis_Neighborhoods.geojson   neighborhood boundaries
  gis/parcels.json                        2026 assessor parcel attributes (no owner names)
  title20.json                            zoning code text (for district descriptions)

Outputs (map_data/):
  zoning.geojson, builtform.geojson, neighborhoods.geojson   simplified shapes for the map
  stats.json                                                 per-neighborhood numbers + district rules

Usage: python build_map.py <gis_dir> <title20.json> <out_dir>
"""
import json
import math
import os
import sys
from collections import Counter, defaultdict

import shapely
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform
from shapely.strtree import STRtree

GIS, TITLE20, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)

# Hennepin County coordinate system (US feet); the assessor X/Y fields use it.
HENN = "ESRI:103734"
to_ft = Transformer.from_crs("EPSG:4326", HENN, always_xy=True).transform
to_ll = Transformer.from_crs(HENN, "EPSG:4326", always_xy=True).transform
SQFT_PER_SQMI = 5280 ** 2

AVG_UNIT_GSF = 1000  # assumed gross building sq ft per home (unit + share of halls/stairs)

# ---------------- zoning rules (Title 20) ----------------
# Table 545-1: which housing types each primary district allows (P, or C for conditional).
ALLOWS_1_3 = {"UN1", "UN2", "UN3", "RM1", "RM2", "CM1", "CM2"}
ALLOWS_MF = {"UN2": "P", "UN3": "P", "RM1": "P", "RM2": "P", "RM3": "P", "CM1": "P", "CM2": "P",
             "CM3": "P", "CM4": "P", "DT1": "P", "DT2": "P", "PR1": "C"}
UNRM = {"UN1", "UN2", "UN3", "RM1", "RM2", "RM3"}

# Built form rules. far13 / farMF = max FAR for 1-3 unit and 4+ unit buildings,
# for (UN/RM districts, all other districts). Table 540-2.
BF = {
    "BFI1": dict(name="Interior 1", far13=(0.5, 0.5), farMF=(0.5, 1.4), height="2.5 stories, 35 ft", cov=(45, 100), mf_lot=(9000, 50), front=20),
    "BFI2": dict(name="Interior 2", far13=(0.5, 0.5), farMF=(0.8, 1.4), height="2.5 stories, 35 ft", cov=(45, 100), mf_lot=(7500, 50), front=20),
    "BFI3": dict(name="Interior 3", far13=(0.7, 0.7), farMF=(1.4, 1.6), height="3 stories, 42 ft", cov=(60, 100), mf_lot=(5000, 40), front=20),
    "BFC3": dict(name="Corridor 3", far13=(1.5, 1.9), farMF=(1.5, 1.9), height="3 stories, 42 ft", cov=(60, 100), mf_lot=(5000, 40), front=15),
    "BFC4": dict(name="Corridor 4", far13=(2.0, 2.4), farMF=(2.0, 2.4), height="4 stories, 56 ft", cov=(70, 100), mf_lot=(5000, 40), front=15),
    "BFC6": dict(name="Corridor 6", far13=(3.0, 3.4), farMF=(3.0, 3.4), height="6 stories, 84 ft", cov=(70, 100), mf_lot=(5000, 40), front=15),
    "BFT10": dict(name="Transit 10", far13=(5.0, 5.4), farMF=(5.0, 5.4), height="10 stories, 140 ft", cov=(80, 100), mf_lot=(5000, 40), front=15),
    "BFT15": dict(name="Transit 15", far13=(6.0, 6.4), farMF=(6.0, 6.4), height="15 stories, 210 ft", cov=(80, 100), mf_lot=(5000, 40), front=15),
    "BFT20": dict(name="Transit 20", far13=(7.0, 7.4), farMF=(7.0, 7.4), height="20 stories, 280 ft", cov=(80, 100), mf_lot=(5000, 40), front=15),
    "BFT30A": dict(name="Transit 30A", far13=(10.0, 10.4), farMF=(10.0, 10.4), height="30 stories, 420 ft", cov=(80, 100), mf_lot=(5000, 40), front=15),
    "BFT30B": dict(name="Transit 30B", far13=(10.0, 10.4), farMF=(10.0, 10.4), height="30 stories, 420 ft", cov=(80, 100), mf_lot=(5000, 40), front=15),
    "BFC50": dict(name="Core 50", far13=(16.0, 16.0), farMF=(16.0, 16.0), height="No limit", cov=(100, 100), mf_lot=(5000, 40), front=None),
    "BFPA": dict(name="Parks", far13=(0.5, 2.0), farMF=(0.8, 2.0), height="2.5 stories, 35 ft", cov=(45, 45), mf_lot=(5000, 40), front=20),
    "BFPR": dict(name="Production", far13=(3.0, 3.0), farMF=(3.0, 3.0), height="10 stories, 140 ft", cov=(100, 100), mf_lot=(5000, 40), front=None),
}
# Interior 3 1-3 unit FAR differs by unit count (0.5 / 0.6 / 0.7); a triplex gets 0.7.

ZONE_NAMES = {}
title20 = json.load(open(TITLE20))


def walk(n):
    yield n
    for c in n.get("children", []):
        yield from walk(c)


sections = {n["number"]: n for n in walk(title20["title"]) if n["type"] == "section"}
tables = {t["number"]: t for n in sections.values() for t in n.get("tables", [])}

# District purposes: 530.110 "UN1 Urban Neighborhood District purpose" etc.
purposes = {}
for num, n in sections.items():
    if num.startswith("530.") and n["title"].endswith("purpose"):
        code = n["title"].split()[0]
        purposes[code] = (num, n["text"])
bf_desc = {}
for r in tables["540-1"]["rows"][1:]:
    code = r[0].split()[0]
    bf_desc[code] = r[1]

# ---------------- load shapes ----------------
def load(name, key):
    gj = json.load(open(os.path.join(GIS, name)))
    feats = []
    for f in gj["features"]:
        if not f.get("geometry"):
            continue
        g = shapely.make_valid(transform(to_ft, shape(f["geometry"])))
        feats.append((f["properties"][key], g, f["properties"]))
    return feats


zoning = load("Planning_Primary_Zoning.geojson", "Land_Use_Code")
builtform = load("Planning_Zoning_Built_Form.geojson", "Abbrv")
hoods = load("Minneapolis_Neighborhoods.geojson", "BDNAME")
for code, _, p in zoning:
    ZONE_NAMES[code] = p["Land_Use"]

z_tree = STRtree([g for _, g, _ in zoning])
b_tree = STRtree([g for _, g, _ in builtform])
h_tree = STRtree([g for _, g, _ in hoods])

# ---------------- parcels ----------------
parcels = json.load(open(os.path.join(GIS, "parcels.json")))
parcels = [p for p in parcels if p.get("X") and p.get("Y")]
pts = shapely.points([p["X"] for p in parcels], [p["Y"] for p in parcels])


def assign(tree, feats):
    idx_p, idx_f = tree.query(pts, predicate="intersects")
    out = [None] * len(parcels)
    for i, j in zip(idx_p, idx_f):
        if out[i] is None:
            out[i] = feats[j][0]
    return out


p_zone = assign(z_tree, zoning)
p_bf = assign(b_tree, builtform)
p_hood = assign(h_tree, hoods)

ONE_UNIT = ("RESIDENTIAL 1 UNIT", "MIXED USE - COMMERCIAL IND/RES 1 UNIT")
SKIP_LAND = {"COMMERCIAL RAILROAD", "UTILITY", "COMMON AREA", "RESIDENTIAL CONDOMINIUM GARAGE/MISC"}
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


def zoned_units(zone, bf, lot):
    """Most homes current zoning allows on one lot (simplified)."""
    if not zone or not bf or bf not in BF or not lot:
        return 0, None
    rules = BF[bf]
    k = 0 if zone in UNRM else 1
    best, kind = 0, None
    if zone in ALLOWS_1_3:
        best, kind = 3, "1-3"
    if zone in ALLOWS_MF:
        min_area, _ = rules["mf_lot"]
        if lot >= min_area:
            mf = math.floor(lot * rules["farMF"][k] / AVG_UNIT_GSF)
            if mf > best:
                best, kind = mf, "4+"
    return best, kind


# Condo / co-op unit parcels repeat the whole building lot's area, and some large lots
# appear as several parcel records. Treat records that share a neighborhood, zone,
# built form and lot area as one lot when they are condos/co-ops or 20,000+ sq ft.
GROUP_MIN_AREA = 20000
groups = {}
agg = defaultdict(lambda: Counter())
combo_agg = defaultdict(lambda: defaultdict(Counter))
for i, p in enumerate(parcels):
    hood = p_hood[i]
    if not hood:
        continue
    t = p["PROPERTYTYPE"] or ""
    zone, bf = p_zone[i], p_bf[i]
    ex = existing_units(p)
    combo = f"{zone}|{bf}"
    agg[hood]["existing"] += ex
    combo_agg[hood][combo]["existing"] += ex
    if t in SKIP_LAND:
        continue
    lot = p["PARCELAREA"] or 0
    exempt = p["ISEXEMPT"] == "Y"
    if t in SHARED or lot >= GROUP_MIN_AREA:
        key = (hood, zone, bf, round(lot), exempt)
    else:
        key = ("lot", i)
    g = groups.setdefault(key, {"hood": hood, "zone": zone, "bf": bf, "lot": lot, "units": 0,
                                "exempt": exempt, "types": set()})
    g["units"] += ex
    g["types"].add(t)

for g in groups.values():
    hood, combo = g["hood"], f"{g['zone']}|{g['bf']}"
    a, c = agg[hood], combo_agg[hood][combo]
    if g["exempt"]:
        a["capacity"] += g["units"]  # public, school, church, nonprofit land: assume it stays as is
        c["capacity"] += g["units"]
        a["exempt_lots"] += 1
        continue
    cap, kind = zoned_units(g["zone"], g["bf"], g["lot"])
    cap = max(cap, g["units"])  # existing buildings above today's limit stay
    a["capacity"] += cap
    a["lots"] += 1
    c["capacity"] += cap
    c["lots"] += 1
    if kind == "1-3":
        a["lots_cap3"] += 1
        c["lots_cap3"] += 1
    elif kind == "4+":
        a["lots_mf"] += 1
    if g["types"] == {"RESIDENTIAL 1 UNIT"} or g["types"] == {"RESIDENTIAL 1 UNIT  - LAKESHORE"}:
        a["single_family"] += 1

# ---------------- area mix per neighborhood ----------------
def mix(hood_geom, tree, feats):
    out = Counter()
    for j in tree.query(hood_geom, predicate="intersects"):
        inter = hood_geom.intersection(feats[j][1])
        if not inter.is_empty:
            out[feats[j][0]] += inter.area
    return out


stats = {}
for name, g, props in hoods:
    zm, bm = mix(g, z_tree, zoning), mix(g, b_tree, builtform)
    zt, bt = sum(zm.values()) or 1, sum(bm.values()) or 1
    a = agg[name]
    combos = []
    for combo, c in combo_agg[name].items():
        z, b = combo.split("|")
        if z == "None" or b == "None":
            continue
        combos.append({"zone": z, "bf": b, "lots": c["lots"], "lots_cap3": c["lots_cap3"],
                       "existing": c["existing"], "capacity": c["capacity"]})
    combos.sort(key=lambda c: -c["lots"])
    stats[name] = {
        "name": name,
        "area_sqmi": round(g.area / SQFT_PER_SQMI, 2),
        "zones": [{"code": k, "pct": round(100 * v / zt, 1)} for k, v in zm.most_common() if v / zt > 0.001],
        "builtform": [{"code": k, "pct": round(100 * v / bt, 1)} for k, v in bm.most_common() if v / bt > 0.001],
        "existing_units": a["existing"],
        "zoned_capacity": a["capacity"],
        "lots": a["lots"],
        "lots_cap3": a["lots_cap3"],
        "lots_mf": a["lots_mf"],
        "single_family": a["single_family"],
        "exempt_lots": a["exempt_lots"],
        "combos": combos[:12],
    }

rules = {
    "zones": {code: {"name": ZONE_NAMES.get(code, code),
                     "purpose": purposes.get(code, (None, ""))[1],
                     "section": purposes.get(code, (None, ""))[0],
                     "allows_1_3": code in ALLOWS_1_3,
                     "allows_mf": ALLOWS_MF.get(code)} for code in sorted(ZONE_NAMES)},
    "builtform": {code: dict(r, description=bf_desc.get(code, "")) for code, r in BF.items()},
    "assumptions": {"avg_unit_gsf": AVG_UNIT_GSF},
}

citywide = Counter()
for s in stats.values():
    for k in ("existing_units", "zoned_capacity", "lots", "lots_cap3", "lots_mf", "single_family"):
        citywide[k] += s[k]

json.dump({"neighborhoods": stats, "rules": rules, "citywide": citywide,
           "sources": {"zoning": "City of Minneapolis Planning_Primary_Zoning and Planning_Zoning_Built_Form (ArcGIS), edited 2026-10-16",
                       "parcels": "City of Minneapolis Assessing Department Parcel Data 2026",
                       "neighborhoods": "City of Minneapolis Minneapolis_Neighborhoods (ArcGIS)"}},
          open(os.path.join(OUT, "stats.json"), "w"), separators=(",", ":"))

# ---------------- simplified shapes for the web ----------------
def write(feats, key, fname, tol=6):
    out = []
    for code, g, props in feats:
        s = g.simplify(tol, preserve_topology=True)
        if s.is_empty:
            continue
        ll = transform(to_ll, s)
        geom = json.loads(json.dumps(mapping(ll)), parse_float=lambda x: round(float(x), 5))
        out.append({"type": "Feature", "properties": {key: code}, "geometry": geom})
    with open(os.path.join(OUT, fname), "w") as f:
        json.dump({"type": "FeatureCollection", "features": out}, f, separators=(",", ":"))


write(zoning, "z", "zoning.geojson")
write(builtform, "b", "builtform.geojson")
write(hoods, "n", "neighborhoods.geojson", tol=10)
print("citywide:", dict(citywide))
for f in sorted(os.listdir(OUT)):
    print(f, os.path.getsize(os.path.join(OUT, f)) // 1024, "KB")

# ---------------- candidate lots for the housing simulator (sim.html) ----------------
# Houses, duplexes and vacant residential lots on taxable land in districts that allow housing.
CAND_TYPES = {"RESIDENTIAL 1 UNIT": 0, "RESIDENTIAL 1 UNIT  - LAKESHORE": 0,
              "RESIDENTIAL 2 UNITS": 1, "VACANT LAND - RESIDENTIAL": 2}
zone_list = sorted(ZONE_NAMES)
bf_list = sorted(BF)
hood_list = sorted(stats)
zi = {c: i for i, c in enumerate(zone_list)}
bi = {c: i for i, c in enumerate(bf_list)}
hi = {c: i for i, c in enumerate(hood_list)}
cols = {k: [] for k in ("x", "y", "lot", "z", "b", "h", "t", "tv", "land", "yb", "sf")}
sel = []
for i, p in enumerate(parcels):
    t = CAND_TYPES.get(p["PROPERTYTYPE"])
    if t is None or p["ISEXEMPT"] == "Y":
        continue
    zone, bf, hood = p_zone[i], p_bf[i], p_hood[i]
    if zone not in ALLOWS_1_3 and zone not in ALLOWS_MF:
        continue
    if bf not in bi or hood not in hi or not p.get("PARCELAREA"):
        continue
    sel.append(i)
lng, lat = to_ll([parcels[i]["X"] for i in sel], [parcels[i]["Y"] for i in sel])
for k, i in enumerate(sel):
    p = parcels[i]
    tv = p.get("TOTALVALUE") or 0
    cols["x"].append(round(lng[k] * 1e5))
    cols["y"].append(round(lat[k] * 1e5))
    cols["lot"].append(int(p["PARCELAREA"]))
    cols["z"].append(zi[p_zone[i]])
    cols["b"].append(bi[p_bf[i]])
    cols["h"].append(hi[p_hood[i]])
    cols["t"].append(CAND_TYPES[p["PROPERTYTYPE"]])
    cols["tv"].append(round(tv / 1000))                                      # total value, $ thousands
    cols["land"].append(round(100 * (p.get("LANDVALUE") or 0) / tv) if tv else 100)  # land share of value, %
    cols["yb"].append(p.get("YEARBUILT") or 0)
    cols["sf"].append(p.get("ABOVEGROUNDAREA") or 0)
json.dump({"zones": zone_list, "builtform": bf_list, "hoods": hood_list, "n": len(sel), **cols},
          open(os.path.join(OUT, "candidates.json"), "w"), separators=(",", ":"))
print("candidates:", len(sel), os.path.getsize(os.path.join(OUT, "candidates.json")) // 1024, "KB")
