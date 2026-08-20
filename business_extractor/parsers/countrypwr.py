import argparse
import json
import re
import sys
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

FIELDS = [
    "Business Name", "Street", "City", "State", "Zipcode", "Country",
    "Phone", "Website URL", "Description", "Category", "Logo", "Hours",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Street suffixes / unit markers used to split "STREET CITY" when there is
# no comma between them (this theme concatenates them directly, e.g.
# "2244 Faraday Ave #206 Carlsbad"). Unit markers are checked first since
# they're the most reliable split point.
_UNIT_MARKER_RE = re.compile(
    r"(?P<street>.*?(?:#|(?:suite|ste|unit|apt|bldg|floor|fl)\.?)\s*[\w-]+)"
    r"\s+(?P<city>[A-Za-z][A-Za-z .'-]*)$",
    re.IGNORECASE,
)

_STREET_SUFFIX_RE = re.compile(
    r"(?P<street>.*?\b(?:"
    r"st|street|ave|avenue|blvd|boulevard|dr|drive|rd|road|ln|lane|"
    r"way|ct|court|pl|place|cir|circle|ter|terrace|pkwy|parkway|"
    r"hwy|highway|sq|square|trl|trail|loop|xing|crossing"
    r")\.?)\s+(?P<city>[A-Za-z][A-Za-z .'-]*)$",
    re.IGNORECASE,
)

# "<street/city stuff>, ST ZIP[-ZIP4]"
_STATE_ZIP_RE = re.compile(
    r"^(?P<rest>.*?),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$"
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _abs_url(base_url: str, maybe_relative: str) -> str:
    if not maybe_relative:
        return ""
    return urljoin(base_url, maybe_relative) if base_url else maybe_relative


def _find_jsonld_nodes(soup: BeautifulSoup):
    """Return (local_business_dict, profile_page_dict) from any @graph block, or ({}, {})."""
    local_business, profile_page = {}, {}
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        graph = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(graph, list):
            continue

        for node in graph:
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type")
            if node_type == "LocalBusiness" and not local_business:
                local_business = node
            elif node_type == "ProfilePage" and not profile_page:
                profile_page = node

    return local_business, profile_page


def _split_street_city(rest: str):
    """Split 'STREET CITY' (no comma) into (street, city) using suffix/unit heuristics."""
    rest = rest.strip()
    if not rest:
        return "", ""

    m = _UNIT_MARKER_RE.match(rest)
    if not m:
        m = _STREET_SUFFIX_RE.match(rest)

    if m:
        street = m.group("street").strip().rstrip(",")
        city = m.group("city").strip()
        return street, city

    # Last-resort fallback: assume the final word is the city, everything
    # else is the street. Better than returning nothing.
    parts = rest.rsplit(" ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return rest, ""


def _parse_address(raw_address: str):
    """
    Parse an address string of the form "<street stuff><city>, ST ZIP"
    (no comma between street and city -- see _split_street_city) into
    (street, city, state, zipcode).
    """
    if not raw_address:
        return "", "", "", ""

    m = _STATE_ZIP_RE.match(raw_address.strip())
    if not m:
        return "", "", "", ""

    street, city = _split_street_city(m.group("rest"))
    state = m.group("state")
    zipcode = m.group("zip")
    return street, city, state, zipcode


def _extract_name(soup: BeautifulSoup, jsonld: dict) -> str:
    if jsonld.get("name"):
        return _clean(jsonld["name"])
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        return _clean(h1.get_text())
    return ""


def _extract_phone(soup: BeautifulSoup, jsonld: dict) -> str:
    if jsonld.get("telephone"):
        return _clean(jsonld["telephone"])
    phone_el = soup.select_one(".profile-header-phone-number .author-phone")
    if phone_el:
        text = _clean(phone_el.get_text(" "))
        m = re.search(r"[\d\-\(\)\s\.]{7,}", text)
        if m:
            return m.group(0).strip()
    return ""


def _extract_website(soup: BeautifulSoup, jsonld: dict, base_url: str) -> str:
    weblink = soup.select_one("a.weblink[href]")
    if weblink:
        return weblink["href"].strip()
    same_as = jsonld.get("sameAs")
    if same_as:
        same_as = same_as if isinstance(same_as, list) else [same_as]
        host = urlparse(base_url).netloc.replace("www.", "") if base_url else ""
        for link in same_as:
            if not host or host not in link:
                return link.strip()
    return ""


def _extract_address(soup: BeautifulSoup, jsonld: dict):
    addr_span = soup.select_one(".overview-tab-the-member-address .col-sm-8 span")
    if addr_span:
        raw_address = addr_span.get_text(strip=True)
    else:
        addr_obj = jsonld.get("address")
        raw_address = addr_obj.get("streetAddress", "") if isinstance(addr_obj, dict) else ""
    return _parse_address(raw_address)


def _extract_country(jsonld: dict) -> str:
    addr_obj = jsonld.get("address")
    if isinstance(addr_obj, dict):
        country = addr_obj.get("addressCountry", "")
        # This theme sometimes fills unknown address parts with the literal
        # string "N/A" rather than leaving them empty -- treat that the same
        # as missing, since it isn't a real country value.
        if country and country.strip().upper() != "N/A":
            return country.strip()
    return ""


def _extract_description(soup: BeautifulSoup, jsonld: dict) -> str:
    if jsonld.get("description"):
        return _clean(jsonld["description"])
    about = soup.select_one(".field-about_me")
    if about:
        return _clean(about.get_text(" "))
    return ""


def _extract_category(soup: BeautifulSoup, profile_page: dict) -> str:
    cat_span = soup.select_one(".profile-header-top-category")
    if cat_span:
        return _clean(cat_span.get_text())
    about_list = profile_page.get("about")
    if isinstance(about_list, list) and about_list:
        return _clean(about_list[0])
    if isinstance(about_list, str):
        return _clean(about_list)
    return ""


def _extract_logo(soup: BeautifulSoup, base_url: str) -> str:
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        return _abs_url(base_url, og_image["content"].strip())
    profile_img = soup.select_one(".profile-image img")
    if profile_img and profile_img.get("src"):
        return _abs_url(base_url, profile_img["src"])
    return ""


# --------------------------------------------------------------------------
# main parse function
# --------------------------------------------------------------------------
#
# NOTE: dispatch.py calls every "requests"-method parser as parser(url, html),
# and looks it up as parsers.countrypwr.parse_countrypwr. Both the name and
# the argument order below match that convention. (An earlier version of
# this file had the arguments reversed -- (html, url) -- which meant the
# dispatcher was silently feeding the page URL string into `html` and the
# real page HTML into `url`; BeautifulSoup then parsed a ~70-character URL
# as "HTML" and found nothing, so every field came back blank/"N/A".)

def parse_countrypwr(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    base_url = url
    if not base_url:
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            base_url = canonical["href"]

    local_business, profile_page = _find_jsonld_nodes(soup)

    name = _extract_name(soup, local_business)
    phone = _extract_phone(soup, local_business)
    website = _extract_website(soup, local_business, base_url)
    street, city, state, zipcode = _extract_address(soup, local_business)
    country = _extract_country(local_business)
    description = _extract_description(soup, local_business)
    category = _extract_category(soup, profile_page)
    logo = _extract_logo(soup, base_url)

    return {
        "Business Name": name,
        "Street": street,
        "City": city,
        "State": state,
        "Zipcode": zipcode,
        "Country": country,
        "Phone": phone,
        "Website URL": website,
        "Description": description,
        "Category": category,
        "Logo": logo,
        "Hours": "",  # not present anywhere on this listing type
        "Source URL": url,
    }


def fetch_and_parse(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return parse_countrypwr(url, resp.text)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Parse countrypwr.com listing pages")
    ap.add_argument("urls", nargs="+", help="Listing URL(s) to parse")
    ap.add_argument("--json", action="store_true", help="Print raw JSON instead of a table")
    args = ap.parse_args()

    results = []
    for url in args.urls:
        try:
            results.append(fetch_and_parse(url))
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Failed to parse {url}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for r in results:
            print(f"\n=== {r.get('Business Name') or r.get('Source URL')} ===")
            for field in FIELDS:
                print(f"{field:15}: {r.get(field, '')}")


if __name__ == "__main__":
    main()
