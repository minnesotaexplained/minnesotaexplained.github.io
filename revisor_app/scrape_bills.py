"""
Scrapes the Minnesota Revisor of Statutes bill list (revisor.mn.gov/bills/94/)
and, for every House File / Senate File / resolution listed in the table,
visits the bill's status page and its latest bill-text page to collect:

  - description
  - authors
  - actions (full history, both chambers where applicable) + latest action
  - bill text of the latest version, with additions/deletions marked

House and Senate companion bills are combined into a single JSON object per
row of the source table (the table already pairs companions together).

Usage:
    python scrape_bills.py                      # full run, resumable
    python scrape_bills.py --limit 20            # small test run
    python scrape_bills.py --resume false        # start over, ignore checkpoint
    python scrape_bills.py --workers 6 --delay 0.3

Output:
    bills_94.json               final compiled array (written at the end,
                                 and periodically re-compiled during the run)
    bills_94.progress.jsonl     one JSON line per completed row; used to
                                 resume an interrupted run
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests
from bs4 import BeautifulSoup, NavigableString

BASE = "https://www.revisor.mn.gov"
LIST_URL = f"{BASE}/bills/94/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MNRevisorBillsScraper/1.0)"}
TIMEOUT = 30


def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    adapter = requests.adapters.HTTPAdapter(
        max_retries=requests.adapters.Retry(
            total=4, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504]
        )
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def fetch(session, url, delay):
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    time.sleep(delay)
    return resp.text


# ---------------------------------------------------------------------------
# List page: table of all bills for the legislature, House/Senate paired
# ---------------------------------------------------------------------------
def get_all_rows(session, delay):
    html = fetch(session, LIST_URL, delay)
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="table-hover")
    rows = []
    for tr in table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) != 4:
            continue
        session_label = tds[0].get_text(strip=True)
        house_a = tds[1].find("a")
        senate_a = tds[2].find("a")
        revisor_number = tds[3].get_text(strip=True)
        house = {"bill_number": house_a.get_text(strip=True), "url": house_a["href"]} if house_a else None
        senate = {"bill_number": senate_a.get_text(strip=True), "url": senate_a["href"]} if senate_a else None
        if not house and not senate:
            continue
        rows.append(
            {
                "revisor_number": revisor_number,
                "session": session_label,
                "house": house,
                "senate": senate,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Bill status page: description, authors, actions, companion, text link
# ---------------------------------------------------------------------------
def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _label_and_href_after(soup, label_regex):
    node = soup.find(string=re.compile(label_regex))
    if not node:
        return None
    parent = node.find_parent("div")
    if not parent:
        return None
    a = parent.find("a")
    return a["href"] if a else None


def parse_bill_status(html, chamber_url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1", class_="card-title")
    bill_number = re.sub(r"\s+", "", h1.get_text(strip=True)) if h1 else None

    desc_h2 = soup.find("h2", string=lambda s: s and s.strip() == "Description")
    description = None
    if desc_h2:
        p = desc_h2.find_next_sibling("p")
        if p:
            description = _norm(p.get_text(" ", strip=True))

    authors = []
    author_div = soup.find("div", class_="author")
    if author_div:
        for a in author_div.select("ul li a"):
            authors.append({"name": a.get_text(strip=True), "url": a.get("href")})

    companion_url = _label_and_href_after(soup, r"^\s*Companion:")
    current_text_url = _label_and_href_after(soup, r"^\s*Current bill text:")
    if not current_text_url:
        current_text_url = chamber_url.rstrip("/") + "/versions/latest/"

    actions = {}
    sep = soup.find(id="separated-tab-pane")
    if sep:
        for chamber_name, css_class in (("House", "house"), ("Senate", "senate")):
            div = sep.find("div", class_=lambda c: c and css_class in c.split())
            if not div:
                continue
            chamber_actions = []
            for table in div.find_all("table", class_="actions"):
                for tr in table.find_all("tr"):
                    tds = tr.find_all("td", recursive=False)
                    if len(tds) != 2:
                        continue
                    date = _norm(tds[0].get_text(strip=True))
                    row_div = tds[1].find("div", class_="row")
                    text = ""
                    version_link = None
                    if row_div:
                        for col in row_div.find_all("div", recursive=False):
                            classes = col.get("class", [])
                            if "action_item" in classes:
                                a = col.find("a", href=re.compile(r"/versions/"))
                                if a:
                                    version_link = a["href"]
                            else:
                                text = _norm(col.get_text(" ", strip=True))
                    else:
                        text = _norm(tds[1].get_text(" ", strip=True))
                    if date or text:
                        chamber_actions.append({"date": date, "text": text, "version_link": version_link})
            if chamber_actions:
                actions[chamber_name] = chamber_actions

    latest_action = None
    for chamber_actions in actions.values():
        if chamber_actions:
            candidate = chamber_actions[-1]
            if latest_action is None or candidate["date"] >= latest_action["date"]:
                latest_action = candidate

    return {
        "bill_number": bill_number,
        "description": description,
        "authors": authors,
        "companion_url": companion_url,
        "current_text_url": current_text_url,
        "actions": actions,
        "latest_action": latest_action,
    }


# ---------------------------------------------------------------------------
# Bill text page: track-changes markup (additions/deletions)
# ---------------------------------------------------------------------------
def _walk(node, mode):
    """mode 'markup' -> [+added+] / [-removed-]; mode 'plain' -> current reading only"""
    if isinstance(node, NavigableString):
        return str(node)
    if node.name == "br":
        return " "
    classes = node.get("class", []) if node.has_attr("class") else []
    is_ins = node.name == "ins"
    is_del = "del" in classes

    if is_del and mode == "plain":
        return ""

    inner = "".join(_walk(c, mode) for c in node.children)

    if mode == "markup":
        if is_ins:
            return f"[+{inner}+]" if inner.strip() else inner
        if is_del:
            return f"[-{inner}-]" if inner.strip() else inner
    return inner


def _collect_changes(node):
    additions, deletions = [], []
    for ins in node.find_all("ins"):
        t = ins.get_text(" ", strip=True)
        if t:
            additions.append(t)
    for d in node.find_all(class_="del"):
        t = d.get_text(" ", strip=True)
        if t:
            deletions.append(t)
    return additions, deletions


def _clean_ws(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_bill_text(html):
    soup = BeautifulSoup(html, "html.parser")
    doc = soup.find(id="document")
    if not doc:
        return None

    for tag in doc.select("var.ln, span.pl, span.sr-only"):
        tag.decompose()

    additions, deletions = _collect_changes(doc)
    text_with_changes = _clean_ws(_walk(doc, "markup"))
    plain_text = _clean_ws(_walk(doc, "plain"))

    version_label = None
    posted_date = None
    header = soup.find("div", class_="card-header")
    if header:
        ps = header.find_all("p")
        if len(ps) >= 1:
            version_label = _norm(ps[0].get_text(strip=True)).split(" - ")[0].strip()
        if len(ps) >= 2:
            posted_date = _norm(ps[1].get_text(strip=True)).replace("Posted on ", "").strip()

    return {
        "version_label": version_label,
        "posted_date": posted_date,
        "text_with_changes": text_with_changes,
        "plain_text": plain_text,
        "additions": additions,
        "deletions": deletions,
    }


# ---------------------------------------------------------------------------
# Per-row processing
# ---------------------------------------------------------------------------
def process_chamber(session, delay, chamber_ref):
    url = chamber_ref["url"]
    try:
        status_html = fetch(session, url, delay)
        status = parse_bill_status(status_html, url)
    except Exception as e:
        return {"bill_number": chamber_ref["bill_number"], "url": url, "error": f"status fetch failed: {e}"}

    bill_text = None
    try:
        text_html = fetch(session, status["current_text_url"], delay)
        bill_text = parse_bill_text(text_html)
    except Exception as e:
        bill_text = {"error": f"text fetch failed: {e}"}

    return {
        "bill_number": status["bill_number"] or chamber_ref["bill_number"],
        "url": url,
        "description": status["description"],
        "authors": status["authors"],
        "companion_url": status["companion_url"],
        "actions": status["actions"],
        "latest_action": status["latest_action"],
        "bill_text": bill_text,
    }


def process_row(session, delay, row):
    result = {
        "revisor_number": row["revisor_number"],
        "session": row["session"],
        "house": None,
        "senate": None,
    }
    if row["house"]:
        result["house"] = process_chamber(session, delay, row["house"])
    if row["senate"]:
        result["senate"] = process_chamber(session, delay, row["senate"])

    description = None
    for chamber in (result["house"], result["senate"]):
        if chamber and chamber.get("description"):
            description = chamber["description"]
            break
    result["description"] = description

    bill_numbers = [c["bill_number"] for c in (result["house"], result["senate"]) if c and c.get("bill_number")]
    result["bill_numbers"] = bill_numbers

    latest = None
    for chamber in (result["house"], result["senate"]):
        if chamber and chamber.get("latest_action"):
            candidate = chamber["latest_action"]
            if latest is None or candidate["date"] >= latest["date"]:
                latest = candidate
    result["latest_action"] = latest

    return result


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------
def load_done_keys(progress_path):
    done = set()
    try:
        with open(progress_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    done.add(rec["revisor_number"])
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return done


def compile_output(progress_path, order, output_path):
    by_key = {}
    with open(progress_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            by_key[rec["revisor_number"]] = rec
    compiled = [by_key[k] for k in order if k in by_key]
    with open(output_path, "w") as f:
        json.dump(compiled, f, indent=2)
    return len(compiled)


def main():
    ap = argparse.ArgumentParser(description="Scrape MN Revisor bill list into JSON")
    ap.add_argument("--output", default="bills_94.json")
    ap.add_argument("--progress", default=None, help="defaults to <output>.progress.jsonl")
    ap.add_argument("--limit", type=int, default=None, help="only process first N rows (testing)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.4, help="seconds to sleep after each HTTP request")
    ap.add_argument("--resume", type=lambda v: v.lower() != "false", default=True)
    ap.add_argument("--compile-interval", type=int, default=100, help="recompile output every N completed rows")
    args = ap.parse_args()

    progress_path = args.progress or (args.output + ".progress.jsonl")

    session = make_session()
    print(f"Fetching bill list from {LIST_URL} ...", file=sys.stderr)
    rows = get_all_rows(session, args.delay)
    print(f"Found {len(rows)} rows in the bill table.", file=sys.stderr)

    if args.limit:
        rows = rows[: args.limit]

    order = [r["revisor_number"] for r in rows]

    done_keys = load_done_keys(progress_path) if args.resume else set()
    if not args.resume:
        open(progress_path, "w").close()
    todo = [r for r in rows if r["revisor_number"] not in done_keys]
    print(f"{len(done_keys)} already done, {len(todo)} remaining to scrape.", file=sys.stderr)

    write_lock = Lock()
    progress_f = open(progress_path, "a")
    completed = 0

    def worker(row):
        thread_session = make_session()
        return process_row(thread_session, args.delay, row)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, row): row for row in todo}
        for fut in as_completed(futures):
            row = futures[fut]
            try:
                result = fut.result()
            except Exception as e:
                result = {
                    "revisor_number": row["revisor_number"],
                    "session": row["session"],
                    "error": str(e),
                }
            with write_lock:
                progress_f.write(json.dumps(result) + "\n")
                progress_f.flush()
                completed += 1
            bill_label = "/".join(result.get("bill_numbers") or [row["revisor_number"]])
            print(f"[{completed}/{len(todo)}] {bill_label}", file=sys.stderr)
            if completed % args.compile_interval == 0:
                n = compile_output(progress_path, order, args.output)
                print(f"  -> recompiled {args.output} ({n} rows)", file=sys.stderr)

    progress_f.close()
    n = compile_output(progress_path, order, args.output)
    print(f"Done. Wrote {n} rows to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
