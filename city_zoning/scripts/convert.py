"""Convert the Municode Minneapolis ordinances spreadsheet into a nested JSON tree."""
import json
import re
import sys

import openpyxl

src, dst = sys.argv[1], sys.argv[2]
rows = list(openpyxl.load_workbook(src, read_only=True).active.iter_rows(values_only=True))
header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "Url")
rows = [r for r in rows[header_idx + 1:] if r and r[1]]

TOO_LARGE = "Content is too large for cell."
MARKER = re.compile(r"^(\(\w{1,5}\)|\w{1,4}\.|[a-z]\))$")  # (1), (a), (iv), 1., A., a)


def clean(text):
    if not text:
        return ""
    paras = []
    for block in re.split(r"\n\s*\n", text):
        p = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if p:
            paras.append(p)
    # attach lone list markers ("(1)") to the paragraph that follows
    out = []
    for p in paras:
        if out and MARKER.match(out[-1]) and not MARKER.match(p):
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return "\n\n".join(out)


def kind(node_id, title):
    t = (title or "").strip()
    u = t.upper()
    if "_" not in node_id:
        return "appendix" if u.startswith("APPENDIX") else "part"
    if u.startswith("TITLE"):
        return "title"
    if u.startswith("CHAPTER"):
        return "chapter"
    if u.startswith("ARTICLE"):
        return "article"
    if u.startswith("DIVISION"):
        return "division"
    if u.startswith("APPENDIX"):
        return "appendix"
    if u.startswith(("§", "SECTION", "PB")) or re.match(r"^\d", t):
        return "section"
    return "other"


def number(title):
    t = re.sub(r"^(TITLE|CHAPTER|ARTICLE|DIVISION|SECTION|APPENDIX|§)\s*", "", (title or "").strip(), flags=re.I)
    return t.rstrip(".").strip()


nodes, order = {}, []
for url, node_id, title, subtitle, content in rows[:]:
    node_id = str(node_id)
    title = (title or "").strip()
    subtitle = (subtitle or "").strip()
    n = {
        "id": node_id,
        "type": kind(node_id, title),
        "number": number(title),
        "heading": title,
        "title": subtitle.rstrip(".") if subtitle else "",
        "url": url,
    }
    if content == TOO_LARGE:
        n["text"] = ""
        n["text_missing"] = True  # Municode export dropped it; see url
    else:
        n["text"] = clean(content)
    n["children"] = []
    nodes[node_id] = n
    order.append(node_id)


def parent_of(node_id):
    parts = node_id.split("_")
    for i in range(len(parts) - 1, 0, -1):
        cand = "_".join(parts[:i])
        if cand in nodes:
            return cand
    # Titles 1-19 use a "COOR_" prefix while Title 20 uses "MICOOR_"; both belong to the code
    if node_id.startswith("COOR_"):
        return "MICOOR"
    return None


roots = []
for node_id in order:
    if node_id == "11490":  # "Code of Ordinances" wrapper with no content
        continue
    p = parent_of(node_id)
    (nodes[p]["children"] if p and p != "11490" else roots).append(nodes[node_id])


def prune(n):
    if not n["children"]:
        del n["children"]
    else:
        for c in n["children"]:
            prune(c)


for r in roots:
    prune(r)

counts = {}
for n in nodes.values():
    counts[n["type"]] = counts.get(n["type"], 0) + 1

out = {
    "source": "Minneapolis Code of Ordinances (Municode export)",
    "source_url": "https://library.municode.com/MN/Minneapolis/codes/Code_of_Ordinances",
    "node_counts": counts,
    "missing_text_ids": [i for i in order if nodes[i].get("text_missing")],
    "contents": roots,
}
with open(dst, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(counts, "roots:", len(roots))
