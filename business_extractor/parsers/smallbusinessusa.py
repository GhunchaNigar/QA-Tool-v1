"""
Site parser: smallbusinessusa.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
    "puerto rico",
}

_US_STATE_ABBR = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi",
    "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc",
    "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut",
    "vt", "va", "wa", "wv", "wi", "wy", "dc", "pr",
}


def _looks_like_us_state(value):
    """True if `value` reads as a US state/territory name or its
    2-letter postal abbreviation (case-insensitive)."""
    text = clean(value).strip().lower()
    if not text:
        return False
    if text in _US_STATE_NAMES:
        return True
    if len(text) == 2 and text in _US_STATE_ABBR:
        return True
    return False


def _resolve_city_state(locality_val, region_val):
    region_is_state = _looks_like_us_state(region_val)
    locality_is_state = _looks_like_us_state(locality_val)

    if region_is_state and not locality_is_state:
        # Normal, non-swapped order: locality is city, region is state.
        return locality_val, region_val

    if locality_is_state and not region_is_state:
        # Swapped on this listing: region is city, locality is state.
        return region_val, locality_val

    return locality_val, region_val


def parse_smallbusinessusa(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- business:contact_data Open Graph extension (primary source) ----
    contact_meta = {}
    for meta in soup.find_all("meta", property=True):
        prop = meta["property"]
        if prop.startswith("business:contact_data:"):
            key = prop.split(":")[-1]
            contact_meta[key] = clean(meta.get("content", ""))

    business["Street"] = contact_meta.get("street_address", "")
    business["City"], business["State"] = _resolve_city_state(
        contact_meta.get("locality", ""), contact_meta.get("region", "")
    )
    business["Zipcode"] = contact_meta.get("postal_code", "")
    business["Country"] = contact_meta.get("country_name", "")
    business["Phone"] = contact_meta.get("phone_number", "")
    business["Website URL"] = contact_meta.get("website", "")

    # ---- JSON-LD (name/logo, backs up address/phone if missing) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:

            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            business["Business Name"] = obj.get("name", business["Business Name"])

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

            addr = obj.get("address", {})

            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            if not business["City"] and not business["State"]:
                business["City"], business["State"] = _resolve_city_state(
                    addr.get("addressLocality", ""), addr.get("addressRegion", "")
                )
            if not business["Zipcode"]:
                business["Zipcode"] = addr.get("postalCode", "")
            if not business["Country"]:
                business["Country"] = addr.get("addressCountry", "")

    # ---- Business Name fallback (visible <h1>) ----
    if not business["Business Name"]:
        h1 = soup.select_one("article.detail h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Website URL fallback ("Visit Website" button) ----
    if not business["Website URL"]:
        website_link = soup.select_one("#visit-website")
        if website_link and website_link.get("href"):
            business["Website URL"] = website_link["href"]

    # ---- Category (breadcrumb inside the listing article) ----
    category_links = soup.select("article.detail ul.breadcrumb a")
    categories = []
    for a in category_links:
        text = clean(a.get_text())
        if text and text not in categories:
            categories.append(text)
    if categories:
        business["Category"] = ", ".join(categories)

    return business


