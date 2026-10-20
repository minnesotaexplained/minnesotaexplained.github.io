"""Extract Title 20 tables from the Municode PDF into row/column form.

Aspose draws vertical rules as zero-length strokes with a huge line width, so we
rebuild them as real vertical edges before handing the page to pdfplumber.
"""
import json
import re
import sys

import pdfplumber


CAPTION = re.compile(r"^Table (5[2-6]\d-\d+[A-Z]?)\b\s*(.*)$")


def edges(page):
    v, h = [], []
    for l in page.lines:
        w = l["x1"] - l["x0"]
        lw = l.get("linewidth") or 0
        if w < 1.5 and lw > 2:  # fat dot = vertical rule
            x = (l["x0"] + l["x1"]) / 2
            v.append({"x0": x, "x1": x, "top": l["top"] - lw / 2, "bottom": l["top"] + lw / 2})
        elif w >= 1.5 and abs(l["bottom"] - l["top"]) < 1:
            h.append({"x0": l["x0"], "x1": l["x1"], "top": l["top"], "bottom": l["top"]})
    for r in page.rects:  # boxes (used by the use table)
        for x in (r["x0"], r["x1"]):
            v.append({"x0": x, "x1": x, "top": r["top"], "bottom": r["bottom"]})
        for y in (r["top"], r["bottom"]):
            h.append({"x0": r["x0"], "x1": r["x1"], "top": y, "bottom": y})
    def line(e):
        e.update(object_type="line", width=e["x1"] - e["x0"], height=e["bottom"] - e["top"])
        return e

    return [line(e) for e in v], [line(e) for e in h]


def norm(s):
    if s is None:
        return None
    s = s.replace(" ", " ")
    s = re.sub(r"[ \t]*\n[ \t]*", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def grid(table):
    """Return rows of strings; cells covered by a merged cell repeat the merged value
    downward (row spans) and are left empty sideways (column spans)."""
    cells = table.cells
    xs = sorted({round(c[0], 1) for c in cells} | {round(c[2], 1) for c in cells})
    ys = sorted({round(c[1], 1) for c in cells} | {round(c[3], 1) for c in cells})

    def merge(vals, tol=2):
        out = []
        for v in vals:
            if not out or v - out[-1] > tol:
                out.append(v)
        return out

    xs, ys = merge(xs), merge(ys)
    texts = {}
    page = table.page
    for c in cells:
        crop = page.crop(c, strict=False)
        txt = norm(crop.extract_text() or "")
        chars = [ch for ch in crop.chars if ch["text"].strip()]
        if chars and sum(not ch.get("upright", True) for ch in chars) > len(chars) / 2:
            txt = txt[::-1]  # vertical (rotated) header text comes out reversed
        texts[c] = txt
    rows = []
    for yi in range(len(ys) - 1):
        cy = (ys[yi] + ys[yi + 1]) / 2
        row = []
        for xi in range(len(xs) - 1):
            cx = (xs[xi] + xs[xi + 1]) / 2
            hit = next((c for c in cells if c[0] - 1 <= cx <= c[2] + 1 and c[1] - 1 <= cy <= c[3] + 1), None)
            if hit is None:
                row.append("")
            elif hit[0] < xs[xi] - 2:  # spans from the left
                row.append("")
            else:
                row.append(texts[hit])
        if any(row):
            rows.append(row)
    return rows


def page_items(page):
    """Tables on a page plus the caption lines found above them."""
    v, h = edges(page)
    if not v or not h:
        return []
    settings = {
        "vertical_strategy": "explicit",
        "horizontal_strategy": "explicit",
        "explicit_vertical_lines": v,
        "explicit_horizontal_lines": h,
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "intersection_tolerance": 4,
    }
    lines = page.extract_text_lines()
    items = []
    for t in page.find_tables(settings):
        x0, top, x1, bottom = t.bbox
        if bottom - top < 15 or len(t.cells) < 2:
            continue
        cap = None
        above = [l for l in lines if l["bottom"] <= top + 1 and top - l["bottom"] < 40]
        for k in range(len(above) - 1, -1, -1):
            m = CAPTION.match(above[k]["text"].strip())
            if m:
                # captions can wrap onto following lines before the table starts
                rest = " ".join(l["text"].strip() for l in above[k + 1:])
                cap = (m.group(1), norm(m.group(2) + " " + rest).rstrip("."))
                break
        items.append({"top": top, "bottom": bottom, "x0": x0, "x1": x1, "caption": cap, "rows": grid(t)})
    return items


def compact(rows):
    """Drop columns that are empty in every row of one page's piece of a table."""
    ncol = max((len(r) for r in rows), default=0)
    keep = [j for j in range(ncol) if any(j < len(r) and r[j] for r in rows)]
    return [[r[j] if j < len(r) else "" for j in keep] for r in rows]


SMALLCAPS = re.compile(r"^([A-Z]) ([A-Z]{2,}.*)$")


def tidy(rows):
    out = []
    for r in rows:
        r = list(r)
        m = SMALLCAPS.match(r[0]) if r else None
        if m:
            r[0] = m.group(1) + m.group(2)
        # group header rows (ALL CAPS label): clear stray single letters spilled from the label
        if r and r[0].isupper() and len(r[0]) > 3 and all(len(c) <= 1 for c in r[1:]):
            r = [r[0]] + [""] * (len(r) - 1)
        out.append(r)
    return out


def main(PDF, FIRST, LAST, OUT):
    pdf = pdfplumber.open(PDF)
    tables = []
    for i in range(FIRST, LAST + 1):
        page = pdf.pages[i]
        for it in page_items(page):
            if it["caption"]:
                tables.append({"table": it["caption"][0], "caption": it["caption"][1],
                               "pdf_page": i + 1, "segments": [it["rows"]]})
            elif tables and it["top"] < 140:  # continuation at the top of the next page
                tables[-1]["segments"].append(it["rows"])
                tables[-1].setdefault("continued_pages", []).append(i + 1)
            else:
                tables.append({"table": None, "caption": None, "pdf_page": i + 1, "segments": [it["rows"]]})

    for t in tables:
        segs = [compact(s) for s in t.pop("segments")]
        first = segs[0]
        header = first[:3]
        rows = list(first)
        for seg in segs[1:]:
            # drop header rows repeated at the top of the continuation page
            while seg and seg[0] in header:
                seg = seg[1:]
            rows.extend(seg)
        width = max((len(r) for r in rows), default=0)
        t["rows"] = tidy(compact([r + [""] * (width - len(r)) for r in rows]))

    json.dump(tables, open(OUT, "w"), ensure_ascii=False, indent=1)
    for t in tables:
        ncol = max((len(r) for r in t["rows"]), default=0)
        print(t["table"], t["pdf_page"], f"{len(t['rows'])}x{ncol}", (t["caption"] or "")[:50])


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
