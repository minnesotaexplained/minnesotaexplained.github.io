"""Fill Title 20 gaps in mpls-ordinances.json from the Municode PDF.

- Sections the spreadsheet export dropped ("too large for cell") get their text from the PDF.
- Every Title 20 table is attached to its section as rows/columns, and the flattened
  one-cell-per-line copy in the section text is replaced with a short pointer.
"""
import json
import re
import sys
from collections import Counter

import pdfplumber

sys.path.insert(0, sys.argv[5])
from tables import page_items  # noqa: E402

JSON_IN, TABLES, PDF, JSON_OUT = sys.argv[1:5]

data = json.load(open(JSON_IN))


def walk(n):
    yield n
    for c in n.get("children", []):
        yield from walk(c)


everything = [n for r in data["contents"] for n in walk(r)]
vocab = Counter(w.lower() for n in everything for w in re.findall(r"[A-Za-z]+", n["text"]))
KEEP_HYPHEN_BEFORE = {"and", "or", "to", "nor"}


def dehyphen(s):
    def fix(m):
        a, b = m.group(1), m.group(2)
        if b.lower() in KEEP_HYPHEN_BEFORE:
            return f"{a}- {b}"
        if vocab[(a + b).lower()] > 0:
            return a + b
        return f"{a}-{b}"
    return re.sub(r"([A-Za-z]+)- ([A-Za-z]+)", fix, s)


title20 = next(t for t in next(r for r in data["contents"] if r["id"] == "MICOOR")["children"]
               if t["number"] == "20")
sections = [n for n in walk(title20) if n["type"] == "section"]
by_number = {n["number"]: n for n in sections}

# ---------- tables ----------
tables = json.load(open(TABLES))
for t in tables:
    t["rows"] = [[dehyphen(c) for c in r] for r in t["rows"]]
    t["caption"] = dehyphen(t["caption"] or "")
    first = t["rows"][0][0] if t["rows"] and t["rows"][0] else None
    t["header_rows"] = max(1, sum(1 for r in t["rows"][:3] if first and r[0] == first))


def pointer(t):
    return f"[Table {t['table']}: {t['caption']} (see tables)]"


# ---------- missing section text from the PDF ----------
HEADING = re.compile(r"^(\d{3}\.\d+)\. ([A-Z])")
STOP = re.compile(r"^(\d{3}\.\d+\. [A-Z]|ARTICLE [IVXLC]+\.|CHAPTER \d+\.)")
RUNNING = re.compile(r"^(ZONING CODE \d|\d{3}\.\d+ MINNEAPOLIS CODE|MINNEAPOLIS CODE|Supp\. No\. \d+)")
MARKER = re.compile(r"^(\(\w{1,4}\)|[a-z0-9]{1,3}\.)\s")


def section_from_pdf(pdf, number, start_page):
    caps = {t["table"]: t for t in tables}
    lines_out, started = [], False
    for i in range(start_page, start_page + 25):
        page = pdf.pages[i]
        items = page_items(page)
        lines = [(l, l["text"].strip()) for l in page.extract_text_lines()]
        skip = set()
        events = []
        for it in items:
            if it["caption"]:
                events.append((it["top"] - 0.5, "table", it["caption"][0]))
            # caption line(s): from the "Table N" line down to the table's top edge
            cap_top = None
            for l, txt in lines:
                if it["top"] - 45 <= l["top"] < it["top"] and txt.startswith("Table "):
                    cap_top = l["top"] if cap_top is None else min(cap_top, l["top"])
            lo = cap_top if cap_top is not None else it["top"]
            for k, (l, txt) in enumerate(lines):
                if lo - 0.5 <= l["top"] <= it["bottom"] + 1:
                    skip.add(k)
        for k, (l, txt) in enumerate(lines):
            if k in skip or RUNNING.match(txt):
                continue
            events.append((l["top"], "line", (l, txt)))
        events.sort(key=lambda e: e[0])
        prev_bottom = None
        for _, kind, val in events:
            if kind == "table":
                if started:
                    lines_out.append(("table", caps[val]))
                    prev_bottom = None
                continue
            l, txt = val
            if not started:
                if txt.startswith(number + ". "):
                    started = True
                else:
                    continue
            elif STOP.match(txt):
                return lines_out
            gap = None if prev_bottom is None else l["top"] - prev_bottom
            lines_out.append(("line", txt, gap))
            prev_bottom = l["bottom"]
    return lines_out


def to_text(lines, number, title):
    paras, cur, attached = [], [], []
    for item in lines:
        if item[0] == "table":
            if cur:
                paras.append(cur)
                cur = []
            paras.append([pointer(item[1])])
            attached.append(item[1])
            continue
        _, txt, gap = item
        ends_clause = bool(cur) and re.search(r"[.:;]$", cur[-1])
        new_para = (not cur or (gap is not None and gap > 5) or txt.startswith("(Ord.")
                    or (MARKER.match(txt) and ends_clause))
        if new_para and cur:
            paras.append(cur)
            cur = []
        cur.append(txt)
    if cur:
        paras.append(cur)
    out = []
    for p in paras:
        s = ""
        for piece in p:
            if s.endswith("-"):
                s = s[:-1] + "- " + piece  # dehyphen() decides how to rejoin
            else:
                s = (s + " " + piece).strip()
        out.append(dehyphen(s))
    # drop the "540.630. Floor area ratio premiums." heading from the first paragraph
    out[0] = re.sub(r"^" + re.escape(number) + r"\.\s*" + re.escape(title) + r"\.?\s*", "", out[0]).strip()
    if not out[0]:
        out = out[1:]
    return "\n\n".join(out), attached


raw_pages = None
pdf = pdfplumber.open(PDF)
filled = []
for n in sections:
    if not n.get("text_missing"):
        continue
    if raw_pages is None:
        import subprocess
        raw = subprocess.run(["pdftotext", "-f", "1800", "-l", "2380", PDF, "-"],
                             capture_output=True, text=True).stdout
        raw_pages = raw.split("\f")
    start = next(1799 + k for k, p in enumerate(raw_pages)
                 if re.search(r"(^|\n)\s*" + re.escape(n["number"]) + r"\. [A-Z]", p))
    text, attached = to_text(section_from_pdf(pdf, n["number"], start), n["number"], n["title"])
    n["text"] = text
    n["text_source"] = "pdf"
    n["tables"] = [t for t in attached]
    del n["text_missing"]
    filled.append(n["number"])

# ---------- attach tables to sections whose spreadsheet text has them flattened ----------
def key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


placed = {t["table"] for n in sections for t in n.get("tables", [])}
replaced, attached_only = [], []
for t in tables:
    if t["table"] in placed:
        continue
    cap_re = re.compile(r"^Table " + re.escape(t["table"]) + r"(?![\d-])\s*[A-Z]")
    host, idx = None, None
    for n in sections:
        paras = n["text"].split("\n\n")
        for k, p in enumerate(paras):
            if cap_re.match(p) and len(p) < 250 and "," not in p[:len(t["table"]) + 7]:
                host, idx = n, k
                break
        if host:
            break
    if not host:
        print("no host section for table", t["table"])
        continue
    paras = host["text"].split("\n\n")
    cells_blob = key(" ".join(c for r in t["rows"] for c in r) + " " + t["caption"])
    j = idx + 1
    while j < len(paras):
        kp = key(paras[j])
        if kp == "nbsp" or not kp or kp in cells_blob:
            j += 1
            continue
        break
    consumed = j - idx - 1
    ncells = sum(1 for r in t["rows"] for c in r if c)
    if consumed >= max(2, ncells * 0.4):
        paras[idx:j] = [pointer(t)]
        host["text"] = "\n\n".join(paras)
        replaced.append(t["table"])
    else:
        attached_only.append((t["table"], consumed, ncells))
    host.setdefault("tables", []).append(t)
    placed.add(t["table"])

# final shape of each table
for n in sections:
    if "tables" in n:
        n["tables"] = [{
            "number": t["table"],
            "caption": t["caption"],
            "header_rows": t["header_rows"],
            "rows": t["rows"],
            "source": "pdf",
        } for t in n["tables"]]

data["missing_text_ids"] = [n["id"] for n in everything if n.get("text_missing")]
data["notes"] = {
    "tables": "Title 20 tables are in each section's 'tables' list as rows of cells; the first "
              "'header_rows' rows are headers. They come from Municode Supplement No. 72 (PDF), "
              "since the spreadsheet export flattened them.",
    "text_source_pdf": "Sections with text_source 'pdf' were missing from the spreadsheet export "
                       "and were filled from the Supplement No. 72 PDF.",
}
json.dump(data, open(JSON_OUT, "w"), ensure_ascii=False, indent=2)
print("filled from pdf:", filled)
print("tables with flattened text replaced:", len(replaced))
print("tables attached but text left as-is:", attached_only)
print("tables placed:", len(placed), "of", len(tables))
