"""Poll Craigslist SF apartments and push new matches to your phone via ntfy.

Env vars:
  NTFY_TOPIC  secret topic name you subscribe to in the ntfy app (required)
  PAGES_URL   base URL of your GitHub Pages site, e.g. https://you.github.io/housing-alerts
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- config
MAX_PRICE = 3500
SEARCH_URL = "https://sfbay.craigslist.org/search/sfc/apa"  # sfc = San Francisco

# Craigslist "location" is free text typed by the poster, so we match keywords
# against both the location field and the title.
NEIGHBORHOODS = {
    "Mission": r"(?<!outer )\bmission\b(?! bay)",
    "Potrero Hill": r"\b(potrero|portrero)\b",
    "Lower Haight": r"lower haight",
    "Dogpatch": r"dogpatch",
    "Bernal Heights": r"\bbernal\b",
    "Castro": r"\bcastro\b|eureka valley",
    "Noe Valley": r"noe valley",
}

SEEN_FILE = Path(__file__).parent / "seen.json"
MAX_SEEN = 3000
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
# ------------------------------------------------------------------------


def fetch_listings():
    resp = requests.get(
        SEARCH_URL,
        params={"max_price": MAX_PRICE, "sort": "date"},
        headers=HEADERS,
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(
            f"Craigslist returned HTTP {resp.status_code}. GitHub's IPs are "
            "sometimes blocked; see README notes about running elsewhere."
        )

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select("li.cl-static-search-result")
    if not items:
        sys.exit("Parsed 0 listings: the page markup changed or the request was blocked.")

    listings = []
    for li in items:
        link = li.find("a", href=True)
        if not link:
            continue
        title_el = li.select_one(".title")
        price_el = li.select_one(".price")
        loc_el = li.select_one(".location")

        title = (li.get("title") or (title_el.get_text(strip=True) if title_el else "")).strip()
        digits = re.sub(r"[^\d]", "", price_el.get_text()) if price_el else ""
        if not digits:
            continue  # no price listed
        listings.append(
            {
                "url": link["href"].split("?")[0],
                "title": title,
                "price": int(digits),
                "location": loc_el.get_text(strip=True) if loc_el else "",
            }
        )
    return listings


def match_neighborhood(listing):
    haystack = f"{listing['location']} {listing['title']}"
    for name, pattern in NEIGHBORHOODS.items():
        if re.search(pattern, haystack, re.I):
            return name
    return None


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return None  # first run


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen[-MAX_SEEN:], indent=0))


def notify(topic, pages_url, title, message, listing_url):
    reply_page = (
        f"{pages_url.rstrip('/')}/reply.html"
        f"?u={quote(listing_url, safe='')}&t={quote(message, safe='')}"
    )
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "tags": ["house"],
        "priority": 4,
        "click": reply_page,  # tap the notification -> reply page with saved messages
        "actions": [{"action": "view", "label": "Open listing", "url": listing_url}],
    }
    requests.post("https://ntfy.sh", json=payload, timeout=30).raise_for_status()


def main():
    topic = os.environ.get("NTFY_TOPIC")
    pages_url = os.environ.get("PAGES_URL", "")
    if not topic:
        sys.exit("Set the NTFY_TOPIC environment variable.")

    listings = fetch_listings()
    seen = load_seen()

    if seen is None:
        # First run: record everything silently so you aren't flooded.
        save_seen([l["url"] for l in listings])
        notify(topic, pages_url, "Housing alerts running", "Setup works. You'll be pinged for new matches.", pages_url or "https://ntfy.sh")
        print(f"Seeded {len(listings)} existing listings.")
        return

    seen_set = set(seen)
    new_matches = 0
    for l in reversed(listings):  # oldest first
        if l["url"] in seen_set:
            continue
        seen.append(l["url"])
        seen_set.add(l["url"])

        hood = match_neighborhood(l)
        if hood and l["price"] <= MAX_PRICE:
            new_matches += 1
            notify(
                topic,
                pages_url,
                f"{hood}: ${l['price']:,}",
                l["title"],
                l["url"],
            )

    save_seen(seen)
    print(f"Checked {len(listings)} listings, sent {new_matches} notifications.")


if __name__ == "__main__":
    main()
