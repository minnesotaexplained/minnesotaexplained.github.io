#!/usr/bin/env bash
# Rebuild mpls-ordinances.json, title20.json and map_data/ (zoning map, housing and grocery simulators) from the Municode downloads
# and the City of Minneapolis GIS services.
#
#   mpls-ordinances.xlsx  Municode spreadsheet export of the whole code
#   ordinances.pdf        Municode PDF of the code (used for Title 20 tables and
#                         sections the spreadsheet dropped as "too large")
#
# Usage: scripts/build.sh            (run from city_zoning/)
# Needs: pip install -r scripts/requirements.txt, plus pdftotext (poppler-utils)
#
# Note: merge_t20.py and the table step assume Title 20 sits on PDF pages
# ~1800-2380 (Supplement No. 72). Check and adjust if a new PDF shifts pages.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

$PY -I scripts/convert.py mpls-ordinances.xlsx "$TMP/base.json"
$PY -I scripts/tables.py ordinances.pdf 1830 2345 "$TMP/t20_tables.json" > /dev/null
$PY -I scripts/merge_t20.py "$TMP/base.json" "$TMP/t20_tables.json" ordinances.pdf mpls-ordinances.json scripts
$PY -I scripts/extract_title20.py mpls-ordinances.json title20.json

# Zoning map data (downloads current City GIS layers; needs shapely and pyproj)
$PY -I scripts/fetch_gis.py "$TMP/gis"
$PY -I -W ignore scripts/build_map.py "$TMP/gis" title20.json map_data

# Grocery access simulator (City food inspections + OpenStreetMap stores; reuses the parcel and zoning downloads)
$PY -I scripts/fetch_grocery.py "$TMP/grocery"
$PY -I -W ignore scripts/build_grocery.py "$TMP/gis" "$TMP/grocery" map_data
