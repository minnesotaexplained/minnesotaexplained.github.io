"""Download grocery store locations used by build_grocery.py.

Usage: python fetch_grocery.py <out_dir>

Writes:
  inspections.json  one row per licensed food business in Minneapolis whose City food
                    inspection category is GROCERY, MEAT MARKET or MARKET, with its latest
                    inspection date (City of Minneapolis Food_Inspections, 2023 onward)
  osm.json          supermarkets, grocers and department stores around Minneapolis from
                    OpenStreetMap (Overpass API), used for stores just outside the city
"""
import json
import os
import sys
import urllib.parse
import urllib.request

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "minnesotaexplained city_zoning research"}

INSPECT = ("https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/"
           "Food_Inspections/FeatureServer/0/query")
params = {
    "where": "FacilityCategory IN ('GROCERY','MARKET','MEAT MARKET')",
    "groupByFieldsForStatistics": "HealthFacilityIDNumber,BusinessName,FacilityCategory,FullAddress,"
                                  "Neighborhood,Latitude,Longitude",
    "outStatistics": json.dumps([{"statisticType": "max", "onStatisticField": "DateOfInspection",
                                  "outStatisticFieldName": "last"}]),
    "f": "json",
}
data = json.load(urllib.request.urlopen(INSPECT + "?" + urllib.parse.urlencode(params), timeout=120))
if data.get("exceededTransferLimit"):
    sys.exit("Food_Inspections: too many rows for one request; add paging")
rows = [f["attributes"] for f in data["features"]]
json.dump(rows, open(os.path.join(OUT, "inspections.json"), "w"))
print("inspections", len(rows))

# Box reaching ~4 miles past the city limits on every side.
BBOX = (44.83, -93.42, 45.11, -93.12)
query = (f'[out:json][timeout:90];(nwr["shop"~"^(supermarket|grocery|greengrocer|department_store)$"]'
         f'{BBOX};);out center tags;')
req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                             data=urllib.parse.urlencode({"data": query}).encode(), headers=UA)
osm = json.load(urllib.request.urlopen(req, timeout=180))["elements"]
json.dump(osm, open(os.path.join(OUT, "osm.json"), "w"))
print("osm", len(osm))
