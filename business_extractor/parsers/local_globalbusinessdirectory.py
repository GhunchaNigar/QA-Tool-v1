#!/usr/bin/env python3

import json
import re
import sys
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Known personal/general social platforms we treat as "social media" links
SOCIAL_DOMAINS = (
    "facebook.com", "x.com", "twitter.com", "instagram.com",
    "linkedin.com", "pinterest.com", "pin.it", "youtube.com",
    "tiktok.com", "threads.net", "snapchat.com",
)

# US state abbreviations, used to help split "City, ST ZIP" strings
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}


def fetch_html(source: str) -> str:
    """Load HTML from a URL or a local file path."""
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        resp = requests.get(source, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.text
    with open(source, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def clean(text):
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def extract_json_ld(soup):
    """Grab the first LocalBusiness JSON-LD block, if present."""
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and "LocalBusiness" in str(item.get("@type", "")):
                return item
    return None


def split_address(full_address: str):
    """
    Split a free-form US address string such as:
        '2244 Faraday Ave #206 Carlsbad, CA 92008'
    into street / city / state / zip.
    """
    street = city = state = zipcode = None
    if not full_address:
        return street, city, state, zipcode

    addr = clean(full_address)

    # Pattern: "<street>, <state?> <city>, <ST> <ZIP>" — the common
    # "<everything>, <City>, <ST> <ZIP[-XXXX]>" shape.
    m = re.search(
        r"^(?P<street>.*?),\s*(?P<city>[A-Za-z .'\-]+?),\s*"
        r"(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$",
        addr,
    )
    if m:
        street = clean(m.group("street"))
        city = clean(m.group("city"))
        state = m.group("state").upper()
        zipcode = m.group("zip")
        return street, city, state, zipcode

    # Fallback pattern seen on this theme, where there's no comma before
    # the city: "<street> <City>, <ST> <ZIP>"
    m = re.search(
        r"^(?P<rest>.*?)\s+(?P<city>[A-Za-z][A-Za-z .'\-]*?),\s*"
        r"(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$",
        addr,
    )
    if m and m.group("state").upper() in US_STATES:
        street = clean(m.group("rest"))
        city = clean(m.group("city"))
        state = m.group("state").upper()
        zipcode = m.group("zip")
        return street, city, state, zipcode

    # Last resort: just return the whole string as the street.
    street = addr
    return street, city, state, zipcode


def get_text_by_icon(details_block, icon_class):
    """Find the <span> text next to an <i class="mi {icon_class}"> icon."""
    if not details_block:
        return None
    icon = details_block.select_one(f"i.mi.{icon_class}, i.{icon_class}")
    if not icon:
        return None
    li = icon.find_parent("li")
    if not li:
        return None
    span = li.find("span")
    return clean(span.get_text()) if span else clean(li.get_text())


def extract_social_links(soup):
    """Collect distinct social-platform URLs found anywhere on the page."""
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("http"):
            continue
        host = urlparse(href).netloc.lower().replace("www.", "")
        if any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS):
            links.add(href)
    return sorted(links)


def parse_listing(html: str, source_url: str = None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    ld = extract_json_ld(soup) or {}

    # ---- Name -------------------------------------------------------
    name_tag = soup.select_one("h1.case27-primary-text, h1.entry-title")
    name = clean(name_tag.get_text()) if name_tag else clean(ld.get("name"))

    # ---- Owner name ---------------------------------------------------
    owner_tag = soup.select_one(
        ".block-type-author .host-name, .event-host .host-name"
    )
    owner_name = clean(owner_tag.get_text()) if owner_tag else None

    # ---- Description --------------------------------------------------
    desc_tag = soup.select_one(".block-type-text .pf-body")
    if desc_tag:
        description = clean(desc_tag.get_text(" "))
    else:
        description = clean(BeautifulSoup(ld.get("description", ""), "lxml").get_text(" "))

    # ---- Category -------------------------------------------------
    cat_tag = soup.select_one(".block-type-categories .category-name")
    category = clean(cat_tag.get_text()) if cat_tag else None

    # ---- Contact info block (email / phone / website) ------------------
    details_block = soup.select_one(".block-type-details")
    email = get_text_by_icon(details_block, "email") or clean(ld.get("email"))
    phone = get_text_by_icon(details_block, "phone") or clean(ld.get("telephone"))
    website = get_text_by_icon(details_block, "web") or clean(ld.get("url"))

    # ---- Country / region -------------------------------------------
    country_tag = soup.select_one(".block-type-terms .pf-body span")
    country = clean(country_tag.get_text()) if country_tag else None

    # ---- Address --------------------------------------------------
    addr_tag = soup.select_one(".map-block-address li p")
    full_address = clean(addr_tag.get_text()) if addr_tag else None
    if not full_address and isinstance(ld.get("address"), dict):
        full_address = clean(ld["address"].get("address"))
    street, city, state, zipcode = split_address(full_address)

    # ---- Social links ----------------------------------------------
    social_links = extract_social_links(soup)

    return {
        "Name": name,
        "Owner Name": owner_name,
        "Street": street,
        "City": city,
        "State": state,
        "Zipcode": zipcode,
        "Country": country,
        "Phone": phone,
        "Website URL": website,
        "Description": description,
        "Social Media Links": social_links,
        "Business Email": email,
        "Category": category,
        "Source URL": source_url,
    }


def scrape(source: str) -> dict:
    """Convenience wrapper: fetch (URL or local path) + parse in one call."""
    html = fetch_html(source)
    source_url = source if source.startswith("http") else None
    return parse_listing(html, source_url=source_url)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        sys.exit(1)

    results = []
    for source in argv[1:]:
        html = fetch_html(source)
        record = parse_listing(html, source_url=source if source.startswith("http") else None)
        results.append(record)

    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv)
