"""Download the City of Minneapolis GIS layers used by build_map.py.

Usage: python fetch_gis.py <out_dir>
Writes the zoning, built form and neighborhood GeoJSON files plus parcels.json
(2026 assessor attributes only: neighborhood, zoning, lot area, coordinates, values,
year built, building size, property type, unit count, exempt flag; no owner names or addresses).
"""
import concurrent.futures as cf
import json
import os
import sys
import urllib.request

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
BASE = "https://services.arcgis.com/afSMGVsC7QlRK1kZ/ArcGIS/rest/services"

for layer in ["Planning_Primary_Zoning", "Planning_Zoning_Built_Form", "Minneapolis_Neighborhoods"]:
    url = f"{BASE}/{layer}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
    data = json.load(urllib.request.urlopen(url, timeout=300))
    if data.get("properties", {}).get("exceededTransferLimit"):
        sys.exit(f"{layer}: too many features for one request; add paging")
    json.dump(data, open(os.path.join(OUT, layer + ".geojson"), "w"))
    print(layer, len(data["features"]))

PARCELS = f"{BASE}/Assessing_Department_Parcel_Data_2026/FeatureServer/0/query"
FIELDS = ("NEIGHBORHOOD,ZONING,PARCELAREA,X,Y,PROPERTYTYPE,TOTALUNITS,BUILDINGUSE,ISEXEMPT,"
          "LANDVALUE,BUILDINGVALUE,TOTALVALUE,YEARBUILT,ABOVEGROUNDAREA,ObjectId")
count = json.load(urllib.request.urlopen(f"{PARCELS}?where=1%3D1&returnCountOnly=true&f=json"))["count"]


def page(offset):
    url = (f"{PARCELS}?where=1%3D1&outFields={FIELDS}&returnGeometry=false&orderByFields=ObjectId"
           f"&resultOffset={offset}&resultRecordCount=1000&f=json")
    for attempt in range(3):
        try:
            return [f["attributes"] for f in json.load(urllib.request.urlopen(url, timeout=120))["features"]]
        except Exception:
            if attempt == 2:
                raise


with cf.ThreadPoolExecutor(8) as ex:
    rows = [r for part in ex.map(page, range(0, count, 1000)) for r in part]
json.dump(rows, open(os.path.join(OUT, "parcels.json"), "w"))
print("parcels", len(rows), "of", count)
