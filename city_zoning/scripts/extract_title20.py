"""Write just Title 20 (Zoning Code) from the full ordinances JSON to its own file."""
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
data = json.load(open(src, encoding="utf-8"))

code = next(r for r in data["contents"] if r["id"] == "MICOOR")
title20 = next(t for t in code["children"] if t["type"] == "title" and t["number"] == "20")


def walk(n):
    yield n
    for c in n.get("children", []):
        yield from walk(c)


nodes = list(walk(title20))
counts = {}
for n in nodes:
    counts[n["type"]] = counts.get(n["type"], 0) + 1

out = {
    "source": data["source"],
    "source_url": data["source_url"],
    "node_counts": counts,
    "table_count": sum(len(n.get("tables", [])) for n in nodes),
    "missing_text_ids": [n["id"] for n in nodes if n.get("text_missing")],
    "notes": data.get("notes", {}),
    "title": title20,
}
with open(dst, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(counts, "tables:", out["table_count"])
