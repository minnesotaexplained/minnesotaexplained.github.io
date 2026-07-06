"""
Minneapolis City Council Resolution Vote Scraper (Playwright version)
----------------------------------------------------------------------
Uses a real Chromium browser to bypass 403s caused by JS-based bot detection.

Setup:
    pip install playwright
    playwright install chromium

Usage:
    python scrape_votes_playwright.py

Outputs:
    resolutions_with_votes.json
"""

import json
import re
import asyncio
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

BASE_URL = "https://lims.minneapolismn.gov"
INPUT_FILE = "cc_resolutions.json"
OUTPUT_FILE = "resolutions_with_votes.json"


async def get_voting_links(page: Page, file_number: str) -> list[str]:
    """Navigate to the file page and return all 'View Voting' hrefs."""
    url = f"{BASE_URL}/File/{file_number}"
    print(url)
    try:
        await page.goto(url, wait_until="networkidle", timeout=20_000)
    except PWTimeout:
        print(f"  ⚠  Timed out loading {url}")
        return []

    # Find all anchors whose text matches "View Voting"
    anchors = await page.query_selector_all("a")
    links = []
    for a in anchors:
        text = (await a.inner_text()).strip()
        if text == "View Voting":
            links.append(a)
    
    print(links)
    return links


async def scrape_votes(page: Page, voting_url: str) -> dict[str, str] | None:
    """
    Navigate to a voting page and parse the vote table.
    Returns a dict of { "Member Name": "Aye" | "Nay" | "Absent" | ... }
    
    try:
        await page.goto(voting_url, wait_until="networkidle", timeout=20_000)
    except PWTimeout:
        print(f"    ⚠  Timed out loading {voting_url}")
        return None
    """
    await voting_url.click()
    #await page.screenshot({ path: 'screenshot.png' });

    votes: dict[str, str] = {}

    # Try to find a table with voting rows
    rows = await page.locator(".vote-row").all()
    print(rows)

    for row in rows:
        cells = await row.locator(".vote-cell").all()
        texts = [(await c.inner_text()).strip() for c in cells]
        if len(texts) < 2:
            continue
        member, vote = texts[0], texts[1]
        # Skip header rows
        if not member or member.lower() in ("member", "name", "council member"):
            continue
        votes[member] = vote

    if votes:
        return votes

    return votes if votes else None


async def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        resolutions: list[dict] = json.load(f)

    total = len(resolutions)
    print(f"Launching browser — processing {total} resolutions...\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,  # Set to False to watch the browser — useful for debugging
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()

        # Cache by file number to avoid duplicate requests
        file_cache: dict[str, dict | None] = {}

        for i, res in enumerate(resolutions, start=1):
            file_number = res["fileNumber"]
            res_num = res["resolutionNumber"]
            print(f"[{i}/{total}] {res_num} — file {file_number}")

            if file_number in file_cache:
                res["voting"] = file_cache[file_number]
                print("  ↩  Reusing cached result (duplicate file number)")
                continue

            voting_links = await get_voting_links(page, file_number)

            if not voting_links:
                print("  ⚠  No 'View Voting' links found")
                res["voting"] = None
                file_cache[file_number] = None
                continue

            # Use the last (most recent) voting link
            latest = voting_links[-1]
            print(f"  → Voting URL: {latest}")

            votes = await scrape_votes(page, latest)

            if votes:
                print(f"  ✓  {len(votes)} votes: {list(votes.keys())}")
            else:
                print("  ⚠  Could not parse votes — check page structure")

            res["voting"] = votes
            file_cache[file_number] = votes

            # Small delay to be polite
            await asyncio.sleep(0.5)

        await browser.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(resolutions, f, indent=2, ensure_ascii=False)

    with_votes = sum(1 for r in resolutions if r.get("voting"))
    print(f"\n✅ Done! Written to '{OUTPUT_FILE}'")
    print(f"   {with_votes}/{total} resolutions have vote data")


if __name__ == "__main__":
    asyncio.run(main())