"""
countrypwr.py
Parser for countrypwr.com (Western Business Collective) business listing pages.

Strategy:
    1. Prefer the embedded schema.org JSON-LD (`LocalBusiness` / `ProfilePage`)
       block — it's present on every listing and is the most structured source.
    2. Fall back to direct DOM extraction for anything JSON-LD doesn't give us
       cleanly (e.g. this site sets addressLocality/addressRegion/postalCode
       to the literal string "N/A" and crams everything into streetAddress).
    3. Address parsing has a dedicated fallback because countrypwr.com does NOT
       put a comma between the street and the city — only before the state,
       e.g. "2244 Faraday Ave #206 Carlsbad, CA 92008". A naive comma split
       would incorrectly swallow the city into the street.

Returns "N/A" for any field that can't be found.
"""

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

NA = "N/A"

# Fields this parser is responsible for (per fields_config.SOURCE_FIELDS
# for "countrypwr.com"). "Hours" is included defensively even though this
# source doesn't actually list it in fields_config.py — the page has no
# hours markup, so it will always resolve to NA here.
FIELDS = [
    "Name", "Street", "City", "State", "Zipcode", "Country",
    "Phone", "Website URL", "Description", "Category", "Logo", "Hours",
]

# Street suffixes / unit markers used to split "STREET CITY" when there is
# no comma between them. Order matters: unit markers are checked first since
# they're the most reliable split point (e.g. "... #206 Carlsbad").
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


def _text(node):
    return node.get_text(strip=True) if node else ""


def _abs_url(base_url, maybe_relative):
    if not maybe_relative:
        return NA
    return urljoin(base_url, maybe_relative)


def _extract_json_ld(soup):
    """Return (local_business_dict, profile_page_dict) from the @graph, or (None, None)."""
    local_business, profile_page = None, None
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
            if node_type == "LocalBusiness" and local_business is None:
                local_business = node
            elif node_type == "ProfilePage" and profile_page is None:
                profile_page = node

    return local_business, profile_page


def _split_street_city(rest: str):
    """Split 'STREET CITY' (no comma) into (street, city) using suffix/unit heuristics."""
    rest = rest.strip()
    if not rest:
        return NA, NA

    m = _UNIT_MARKER_RE.match(rest)
    if not m:
        m = _STREET_SUFFIX_RE.match(rest)

    if m:
        street = m.group("street").strip().rstrip(",")
        city = m.group("city").strip()
        return (street or NA), (city or NA)

    # Last-resort fallback: assume the final word is the city, everything
    # else is the street. Better than returning nothing.
    parts = rest.rsplit(" ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return rest, NA


def _parse_address(raw_address: str):
    """
    Parse an address string of the form:
        "<street stuff><city>, ST ZIP"
    (no comma between street and city — see module docstring) into
    (street, city, state, zipcode).
    """
    if not raw_address or raw_address.strip().upper() == "N/A":
        return NA, NA, NA, NA

    m = _STATE_ZIP_RE.match(raw_address.strip())
    if not m:
        return NA, NA, NA, NA

    street, city = _split_street_city(m.group("rest"))
    state = m.group("state")
    zipcode = m.group("zip")
    return street, city, state, zipcode


def parse(html: str, url: str = "") -> dict:
    result = {field: NA for field in FIELDS}
    soup = BeautifulSoup(html, "html.parser")

    base_url = url or ""
    if not base_url:
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            base_url = canonical["href"]

    local_business, profile_page = _extract_json_ld(soup)

    # ---------- Name ----------
    name = NA
    if local_business and local_business.get("name"):
        name = local_business["name"].strip()
    if name == NA:
        h1 = soup.select_one(".header-member-name h1")
        name = _text(h1) or NA
    result["Name"] = name

    # ---------- Phone ----------
    phone = NA
    if local_business and local_business.get("telephone"):
        phone = local_business["telephone"].strip()
    if phone == NA:
        phone_el = soup.select_one(".profile-header-phone-number .author-phone")
        if phone_el:
            digits_and_dashes = re.sub(r"\s+", " ", phone_el.get_text(" ", strip=True))
            m = re.search(r"[\d\-\(\)\s\.]{7,}", digits_and_dashes)
            phone = m.group(0).strip() if m else NA
    result["Phone"] = phone

    # ---------- Website URL ----------
    website = NA
    weblink = soup.select_one("a.weblink[href]")
    if weblink:
        website = weblink["href"].strip()
    elif local_business and local_business.get("sameAs"):
        same_as = local_business["sameAs"]
        same_as = same_as if isinstance(same_as, list) else [same_as]
        host = urlparse(base_url).netloc.replace("www.", "") if base_url else "countrypwr.com"
        for link in same_as:
            if host not in link:
                website = link.strip()
                break
    result["Website URL"] = website

    # ---------- Address (Street / City / State / Zipcode) ----------
    raw_address = NA
    addr_span = soup.select_one(".overview-tab-the-member-address .col-sm-8 span")
    if addr_span:
        raw_address = addr_span.get_text(strip=True)
    elif local_business and isinstance(local_business.get("address"), dict):
        raw_address = local_business["address"].get("streetAddress", NA)

    street, city, state, zipcode = _parse_address(raw_address)
    result["Street"] = street
    result["City"] = city
    result["State"] = state
    result["Zipcode"] = zipcode

    # ---------- Country ----------
    country = NA
    if local_business and isinstance(local_business.get("address"), dict):
        country = local_business["address"].get("addressCountry", NA) or NA
    result["Country"] = country

    # ---------- Description ----------
    description = NA
    if local_business and local_business.get("description"):
        description = local_business["description"].strip()
    if description == NA:
        about = soup.select_one(".field-about_me")
        if about:
            description = about.get_text(" ", strip=True) or NA
    result["Description"] = description

    # ---------- Category ----------
    category = NA
    cat_span = soup.select_one(".profile-header-top-category")
    if cat_span:
        category = cat_span.get_text(strip=True) or NA
    elif profile_page and profile_page.get("about"):
        about_list = profile_page["about"]
        if isinstance(about_list, list) and about_list:
            category = about_list[0]
        elif isinstance(about_list, str):
            category = about_list
    result["Category"] = category

    # ---------- Logo ----------
    logo = NA
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        logo = og_image["content"].strip()
    else:
        profile_img = soup.select_one(".profile-image img")
        if profile_img and profile_img.get("src"):
            logo = _abs_url(base_url, profile_img["src"])
    if logo != NA and base_url:
        logo = _abs_url(base_url, logo)
    result["Logo"] = logo

    # ---------- Hours ----------
    # Not present anywhere on this listing type / not in this source's field
    # list — always NA. Left explicit (rather than omitted) for clarity.
    result["Hours"] = NA

    return result


if __name__ == "__main__":
    # Quick manual smoke test:
    #   python countrypwr.py path/to/saved_page.html "https://www.countrypwr.com/legal-services/..."
    import sys

    if len(sys.argv) >= 2:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            page_html = f.read()
        page_url = sys.argv[2] if len(sys.argv) >= 3 else ""
        for k, v in parse(page_html, page_url).items():
            print(f"{k:15s}: {v}")
