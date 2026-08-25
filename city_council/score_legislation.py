#!/usr/bin/env python3
"""
Score Minneapolis City Council legislative items on a progressive <-> conservative
scale using a local or remote Ollama model.

Reads issues_by_vote_2026.json, sends each distinct Subject/Description to Ollama's
/api/chat endpoint with a scoring rubric, and writes per-item score + supporting
keywords + rationale to a separate output file (issue_ideology_scores_2026.json by
default) so the source data file is never modified.

Usage:
    python3 score_legislation.py                      # local Ollama, llama3.1:8b
    python3 score_legislation.py --server remote  --model qwen2.5:7b-instruct     # remote Ollama (see REMOTE_URL)
    python3 score_legislation.py --ollama-url http://some-host:11434
    python3 score_legislation.py --model qwen2.5:14b --limit 20   # quick test run
    python3 score_legislation.py --force                # rescore everything

Safe to interrupt (Ctrl+C) and rerun — already-scored items are skipped.
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Ollama server endpoints ──────────────────────────────────────
# Mirrors DEFAULT_CONFIG.ollamaHost in ../../loonLlm/server.js. Edit these (or use
# --ollama-url) if your remote box's address changes.
LOCAL_URL = "http://localhost:11434"
REMOTE_URL = "http://192.168.68.66:11434"

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_INPUT = SCRIPT_DIR / "issues_by_vote_2026.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "issue_ideology_scores_2026.json"

REQUEST_TIMEOUT = 300  # seconds per item — large/slow models + the few-shot prompt need headroom
MAX_RETRIES = 3
RETRY_BACKOFF = 5  # seconds; multiplied by attempt number
FLUSH_EVERY = 10  # write progress to disk after this many newly-scored items

RUBRIC = """You are scoring items from the Minneapolis City Council legislative record \
on a political ideology scale, based ONLY on the substantive policy content of the \
item — never on which council member sponsored it.

Score on an integer scale from -5 to +5:

  -5 to -1  Conservative-leaning. Indicators: tax or fee cuts, deregulation, loosening \
zoning/land-use restrictions in favor of business or property owners, expanded police \
funding/authority or tougher criminal enforcement, restricting tenant protections or \
rent regulation, reduced city spending on social programs, business-friendly licensing \
changes.

   0       Neutral / procedural / no clear ideological direction. This is the DEFAULT \
for most items. Use it for: honorary or ceremonial resolutions, routine contracts, \
grant acceptances, permits, engineering/construction agreements, appointments, budget \
transfers with no policy stance, administrative housekeeping, or any item where the \
text does not clearly signal a progressive or conservative direction. Do not force a \
nonzero score onto administrative content just because a topic (e.g. "police," \
"housing") appears — the ACTION taken must have a discernible ideological direction.

  +1 to +5  Progressive-leaning. Indicators: expanded tenant protections or rent \
stabilization, police accountability/oversight or reduced police funding, labor \
protections (minimum wage, scheduling, union rights), expanded social services or \
affordable housing spending, environmental/climate regulation, equity-focused zoning \
(e.g. affordable housing mandates, inclusionary zoning), expanded civil rights \
protections, participatory/community oversight mechanisms.

Magnitude (1 vs 5) reflects how sweeping or clear-cut the policy shift is, not how \
important the topic sounds — a minor fee adjustment is a 1 or 2 even if the topic is \
politically charged; a sweeping policy change is a 4 or 5.

Respond with ONLY a single JSON object and nothing else — no markdown fences, no \
commentary before or after it:

{"score": <integer from -5 to 5>, "keywords": [<3 to 6 short phrases or terms copied \
or closely paraphrased from the item text that justify the score>], "rationale": \
"<one concise sentence explaining the score, written for a general audience>"}
"""

# Few-shot anchor examples fed as prior chat turns before the real item, so a small
# model has concrete reference points instead of only abstract rubric text. Most are
# real items from this dataset with hand-verified scores; a few are marked SYNTHETIC —
# invented, realistically-worded examples used only to calibrate the conservative end
# of the scale, which this (progressive-majority) council's real record has very few
# clean examples of.
#
# Several of these specifically correct a failure mode found in earlier runs: models
# defaulting to "restricts/revokes something commercial => conservative," which is
# backwards per this rubric (LOOSENING restrictions favors business/conservative;
# TIGHTENING them for a stated public-interest reason is neutral-to-progressive, not
# conservative). Marked FIX below.
FEWSHOT_EXAMPLES = [
    {
        "subject": "Immigration status of renters ordinance",
        "description": "Passage of Ordinance amending Title 12, Chapter 244 of the Minneapolis Code of "
                        "Ordinances relating to Housing: Maintenance Code, prohibiting inquiries into "
                        "immigration status during the rental screening process and creating protections for "
                        "tenants against retaliation based on immigration status.",
        "file_type": "Ordinance",
        "score": 5,
        "keywords": ["prohibiting inquiries into immigration status", "tenant protections", "retaliation protection"],
        "rationale": "Creates a sweeping new civil-rights protection for tenants, barring landlords from asking "
                      "about immigration status and protecting against retaliation.",
    },
    {
        "subject": "Tenant protections ordinance",
        "description": "Passage of Ordinance amending Title 12 of the Minneapolis Code of Ordinances relating "
                        "to Housing, creating a new Chapter 246 entitled Tenant Protections and adding thereto "
                        "provisions creating a right of first refusal and/or opportunity to purchase for tenants "
                        "and qualified developers to the sale of rental housing.",
        "file_type": "Ordinance",
        "score": 4,
        "keywords": ["right of first refusal", "opportunity to purchase", "tenant protections"],
        "rationale": "Creates a new structural right letting tenants (or partners) buy their building before "
                      "it's sold to a third party — a sweeping expansion of tenant power, not a minor tweak.",
    },
    {
        "subject": "Solar On Public Buildings Program Grant for Solar Array Installation on Fire Station #1",
        "description": "Accepting a grant from Minnesota Department of Commerce, in the amount of $52,504.32, "
                        "for installing an on-site solar array on Fire Station #1 to support the City's "
                        "renewable energy generation goals as outlined in the 2023 Climate Equity Plan.",
        "file_type": "Resolution",
        "score": 1,
        "keywords": ["renewable energy", "solar array", "Climate Equity Plan"],
        "rationale": "A real environmental/climate action, but small in scope — one grant-funded solar "
                      "installation on a single fire station, not a citywide policy change. Magnitude tracks "
                      "how sweeping the action is, not how appealing the topic sounds.",
    },
    {
        "subject": "Minnesota Department of Public Safety grant for bomb disposal equipment and training",
        "description": "Accepting a grant from the Minnesota Department of Public Safety, Homeland Security and "
                        "Emergency Management, in the amount of $183,959, for specialized bomb disposal services, "
                        "equipment, and training.",
        "file_type": "Resolution",
        "score": 0,
        "keywords": ["grant acceptance", "appropriation of funds", "equipment and training"],
        "rationale": "A routine grant acceptance and fund appropriation. It involves the Police Department, but "
                      "accepting external grant funding for equipment is standard administrative housekeeping, "
                      "not a policy stance on policing — topic alone doesn't justify a nonzero score.",
    },
    {
        "subject": "2026 Black and POC LGBTQ+ Pride honorary resolution",
        "description": "Passage of Resolution honoring Minnesota people of color LGBTQ+ Pride and Twin Cities "
                        "Black Pride 2026.",
        "file_type": "Resolution",
        "score": 0,
        "keywords": ["honoring", "resolution", "ceremonial"],
        "rationale": "A ceremonial recognition with no funding, regulatory, or enforcement action attached. The "
                      "topic reads as politically coded, but the ACTION taken is purely honorary — score 0 "
                      "regardless of subject matter.",
    },
    {
        # FIX: earlier runs scored this -1 with the reasoning "limits business development,
        # favoring property owners" — backwards, since restricting a use doesn't favor the
        # owner, it constrains them.
        "subject": "Moratorium on data center uses",
        "description": "Passage of Ordinance amending Title 21 of the Minneapolis Code of Ordinances relating to "
                        "Interim Ordinances, adding a new Chapter 597 providing for a moratorium on the "
                        "establishment, re-establishment, or expansion of data center uses.",
        "file_type": "Ordinance",
        "score": 0,
        "keywords": ["interim ordinance", "moratorium", "pending study"],
        "rationale": "An interim ordinance pausing new data center development while the city studies land-use "
                      "and environmental impacts. This restricts a commercial use, but restricting something for "
                      "a study/environmental reason is not deregulation favoring business — that would require "
                      "loosening restrictions, not tightening them. Score as a temporary procedural pause, not a "
                      "sweeping stance in either direction.",
    },
    {
        # FIX: earlier runs scored license revocations -1 as "restricts commercial activity."
        # A case-specific enforcement action against one business isn't a policy stance.
        "subject": "Exclusive Tobacco Dealer license revocation: University Tobacco and Vape, 2501 University "
                    "AVE NE, Minneapolis, MN, (Ward 1) submitted by Super Tobacco 1 LLC",
        "description": "University Tobacco and Vape, 2501 UNIVERSITY AVE NE Minneapolis, MN, (Ward 1) submitted "
                        "by Super Tobacco 1 LLC, BLGeneral, LIC407968",
        "file_type": None,
        "score": 0,
        "keywords": ["license revocation", "single business", "enforcement action"],
        "rationale": "A case-specific enforcement action against one named business — not a new ordinance or "
                      "policy change. Revoking one company's license for cause doesn't reflect a citywide "
                      "ideological stance on business or tobacco/vape regulation generally.",
    },
    {
        # FIX: a fee waiver sounds business-friendly, but this one applies an existing
        # program's standard terms rather than changing policy — still 0, not conservative.
        "subject": "Parkland Dedication Fee Waiver: Malcolm Yards Project",
        "description": "Passage of Resolution granting a waiver of park dedication fee requirements associated "
                        "with the Malcolm Yards Market LLC and Malcolm Yards Housing Project, LP development in "
                        "exchange for land interests in and in close proximity to the District property for "
                        "parkland located within the Grand Rounds Missing Link Regional Trail to satisfy all "
                        "Park Dedication Ordinance requirements for the entire district.",
        "file_type": "Resolution",
        "score": 0,
        "keywords": ["fee waiver", "land dedication", "existing ordinance terms"],
        "rationale": "Waives a cash park-dedication fee in exchange for land/easement dedication of equivalent "
                      "value — applying the existing Park Dedication Ordinance's standard fee-in-lieu-of-land "
                      "option, not a new policy favoring developers.",
    },
    {
        # SYNTHETIC — illustrative only, not an actual filed item. Calibrates the
        # conservative end of the scale, which this dataset has few real examples of.
        "subject": "Inclusionary zoning affordable-unit requirement reduction ordinance",
        "description": "Passage of an Ordinance amending the Unified Housing Policy to reduce the minimum share "
                        "of affordable units required in developments of 20 units or more from 20% to 10%, at "
                        "the request of area developers citing project feasibility concerns.",
        "file_type": "Ordinance",
        "score": -2,
        "keywords": ["reducing affordable-unit requirement", "developer feasibility", "Unified Housing Policy"],
        "rationale": "Rolls back an existing affordable-housing mandate on developers — loosening a land-use "
                      "requirement in favor of business, the core conservative-leaning pattern in this rubric.",
    },
    {
        # SYNTHETIC — illustrative only, not an actual filed item. Mirrors the +5 tenant
        # example above to calibrate a sweeping rollback at the negative end.
        "subject": "Rent stabilization ordinance repeal",
        "description": "Passage of an Ordinance repealing Chapter 244A of the Minneapolis Code of Ordinances in "
                        "its entirety, eliminating all citywide rent increase limits enacted in 2024.",
        "file_type": "Ordinance",
        "score": -5,
        "keywords": ["repealing rent stabilization", "eliminating rent increase limits"],
        "rationale": "Completely repeals an existing citywide rent-control law — a sweeping rollback of a "
                      "tenant protection, the mirror image of the +5 example above.",
    },
]


def server_url(args: argparse.Namespace) -> str:
    if args.ollama_url:
        return args.ollama_url.rstrip("/")
    return (REMOTE_URL if args.server == "remote" else LOCAL_URL).rstrip("/")


def check_server(base_url: str, model: str) -> None:
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=5)
        r.raise_for_status()
    except requests.RequestException as e:
        sys.exit(
            f"Cannot reach Ollama at {base_url} ({e}).\n"
            f"Is the server running? (loonLlm's remote default is {REMOTE_URL}; "
            f"use --server local or --ollama-url to point elsewhere.)"
        )
    names = {m.get("name") for m in r.json().get("models", [])}
    if model not in names:
        print(
            f"Warning: model '{model}' not found in `ollama list` at {base_url}.\n"
            f"Available models: {', '.join(sorted(names)) or '(none)'}\n"
            f"Continuing anyway — requests will fail per-item if this is wrong.",
            file=sys.stderr,
        )


def row_id(issue: dict) -> str:
    key = "|".join(
        str(issue.get(f, "") or "")
        for f in ("FileNumber", "MeetingDate", "MeetingBody", "Subject", "ItemDescription")
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def build_prompt(subject: str, description: str, file_type: str) -> str:
    parts = []
    if file_type:
        parts.append(f"Type: {file_type}")
    parts.append(f"Subject: {subject}")
    if description:
        parts.append(f"Description: {description}")
    return "\n".join(parts)


def extract_json(text: str) -> dict:
    text = text.strip()
    # tolerate stray markdown fences some models add despite instructions
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object found in response: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def normalize_result(parsed: dict) -> dict:
    score = parsed.get("score")
    if not isinstance(score, (int, float)):
        raise ValueError(f"missing/invalid 'score' field: {parsed!r}")
    score = max(-5, min(5, round(float(score))))

    keywords = parsed.get("keywords")
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(k).strip() for k in keywords if str(k).strip()][:8]

    rationale = str(parsed.get("rationale") or "").strip()

    return {"score": score, "keywords": keywords, "rationale": rationale}


def build_fewshot_messages() -> list:
    messages = []
    for ex in FEWSHOT_EXAMPLES:
        messages.append({"role": "user", "content": build_prompt(ex["subject"], ex["description"], ex["file_type"])})
        messages.append({"role": "assistant", "content": json.dumps({
            "score": ex["score"], "keywords": ex["keywords"], "rationale": ex["rationale"],
        })})
    return messages


def score_text(base_url: str, model: str, subject: str, description: str, file_type: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": RUBRIC},
            *build_fewshot_messages(),
            {"role": "user", "content": build_prompt(subject, description, file_type)},
        ],
        "format": "json",
        "stream": False,
        # disable extended chain-of-thought on reasoning-capable models (qwen3.5, glm) —
        # otherwise they can burn the whole num_predict budget on <think> tokens and
        # return empty content. No-op on non-thinking models (verified harmless).
        "think": False,
        "options": {"temperature": 0.1, "num_predict": 400},
    }

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(f"{base_url}/api/chat", json=payload, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            content = r.json()["message"]["content"]
            return normalize_result(extract_json(content))
        except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    raise RuntimeError(f"failed after {MAX_RETRIES} attempts: {last_err}")


def load_output(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            print(f"Warning: {path} is not valid JSON, starting fresh.", file=sys.stderr)
    return {}


def save_output(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", choices=["local", "remote"], default="local",
                     help=f"local={LOCAL_URL}  remote={REMOTE_URL}  (default: local)")
    ap.add_argument("--ollama-url", help="explicit Ollama base URL, overrides --server")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model tag (default: {DEFAULT_MODEL})")
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="source issues JSON")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="output scores JSON")
    ap.add_argument("--limit", type=int, default=None, help="only score the first N distinct items (testing)")
    ap.add_argument("--force", action="store_true", help="rescore items even if already present in output")
    args = ap.parse_args()

    base_url = server_url(args)
    print(f"Ollama server: {base_url}")
    print(f"Model:         {args.model}")
    check_server(base_url, args.model)

    issues = json.loads(args.input.read_text())
    print(f"Loaded {len(issues)} issues from {args.input}")

    # group rows by identical (Subject, ItemDescription) text so duplicates
    # (e.g. committee vote + full council vote on the same item) are scored once
    rows = []
    text_to_row_ids = {}
    for issue in issues:
        subject = issue.get("Subject") or ""
        description = issue.get("ItemDescription") or ""
        text_key = (subject, description)
        rid = row_id(issue)
        rows.append({
            "row_id": rid,
            "text_key": text_key,
            "FileNumber": issue.get("FileNumber"),
            "MeetingDate": issue.get("MeetingDate"),
            "MeetingBody": issue.get("MeetingBody"),
            "Subject": subject,
            "ItemDescription": description,
            "FileType": issue.get("FileType"),
        })
        text_to_row_ids.setdefault(text_key, []).append(rid)

    output = {} if args.force else load_output(args.output)

    # find one cached score per text_key, if any row sharing that text was already scored
    text_cache = {}
    for r in rows:
        if r["text_key"] not in text_cache and r["row_id"] in output:
            cached = output[r["row_id"]]
            text_cache[r["text_key"]] = {
                "score": cached["score"],
                "keywords": cached["keywords"],
                "rationale": cached["rationale"],
            }

    pending_texts = [t for t in text_to_row_ids if t not in text_cache]
    if args.limit is not None:
        pending_texts = pending_texts[: args.limit]

    total_rows = sum(len(text_to_row_ids[t]) for t in text_to_row_ids)
    already_rows = total_rows - sum(len(text_to_row_ids[t]) for t in pending_texts)
    print(f"{len(text_to_row_ids)} distinct items ({total_rows} rows total); "
          f"{len(text_cache)} already scored, {len(pending_texts)} to go.")

    scored_since_flush = 0
    failures = []
    try:
        for i, text_key in enumerate(pending_texts, 1):
            subject, description = text_key
            sample_row = next(r for r in rows if r["text_key"] == text_key)
            label = subject[:70] or "(no subject)"
            print(f"[{i}/{len(pending_texts)}] {label}", flush=True)
            try:
                result = score_text(base_url, args.model, subject, description, sample_row["FileType"])
            except RuntimeError as e:
                print(f"  SKIPPED: {e}", file=sys.stderr)
                failures.append(subject)
                continue

            text_cache[text_key] = result
            now = datetime.now(timezone.utc).isoformat()
            for rid in text_to_row_ids[text_key]:
                r = next(r for r in rows if r["row_id"] == rid)
                output[rid] = {
                    "FileNumber": r["FileNumber"],
                    "MeetingDate": r["MeetingDate"],
                    "MeetingBody": r["MeetingBody"],
                    "Subject": r["Subject"],
                    **result,
                    "model": args.model,
                    "scored_at": now,
                }
            scored_since_flush += 1
            if scored_since_flush >= FLUSH_EVERY:
                save_output(args.output, output)
                scored_since_flush = 0
    except KeyboardInterrupt:
        print("\nInterrupted — saving progress before exit.")
    finally:
        save_output(args.output, output)

    print(f"\nWrote {len(output)} row scores to {args.output}")
    if failures:
        print(f"{len(failures)} item(s) failed and were skipped (rerun the script to retry them):")
        for f in failures[:20]:
            print(f"  - {f}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")


if __name__ == "__main__":
    main()
