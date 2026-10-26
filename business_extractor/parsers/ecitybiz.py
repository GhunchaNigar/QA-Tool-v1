"""
Site parser: ecitybiz.com/listing/<slug>.html  (eDirectory platform)

Data sources on the page:

  * <script type="application/ld+json"> LocalBusiness -> name, street, city,
    region (state), postal code, country, description
  * <meta property="business:contact_data:*">        -> same address, used as fallback
  * .details-categories a.link                       -> category
  * p.summary-description                            -> the listing text. Listings
    here are written as "Website <url> About Us <text>" separated by <br>, so the
    website URL and the real description are split out of it.

Phone is NOT in the HTML: eDirectory loads it on click through /listing/phone
(the .listingContactButton element). Listings without a visible phone control
simply return an empty Phone.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py

_URL_RE = re.compile(r"https?://\S+")


def _ld_business(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "{}")
        except (ValueError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and "LocalBusiness" in str(item.get("@type", "")):
                return item
    return {}


def _meta(soup, prop):
    tag = soup.find("meta", attrs={"property": prop})
    return clean(tag.get("content", "")) if tag else ""


def _split_summary(soup):
    """(website, about_text) from the 'Website <url> About Us <text>' description."""
    p = soup.select_one("p.summary-description")
    if not p:
        return "", ""
    lines = [clean(l) for l in p.get_text("\n").split("\n")]
    lines = [l for l in lines if l]

    website, about, mode = "", [], None
    for line in lines:
        key = line.rstrip(":").strip().lower()
        if key == "website":
            mode = "website"
        elif key in ("about us", "about"):
            mode = "about"
        elif mode == "website" and not website:
            m = _URL_RE.search(line)
            website = m.group(0) if m else ""
        elif mode == "about" or mode is None:
            about.append(line)
    return website, " ".join(about)


def parse_ecitybiz(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    ld = _ld_business(soup)
    addr = ld.get("address") or {}

    title = soup.select_one("h1#listing-title") or soup.find("h1")
    business["Business Name"] = clean(ld.get("name") or (title.get_text() if title else ""))

    business["Street"] = clean(addr.get("streetAddress") or _meta(soup, "business:contact_data:street_address"))
    business["City"] = clean(addr.get("addressLocality") or _meta(soup, "business:contact_data:locality"))
    business["State"] = clean(addr.get("addressRegion") or _meta(soup, "business:contact_data:region"))
    business["Zipcode"] = clean(addr.get("postalCode") or _meta(soup, "business:contact_data:postal_code"))
    business["Country"] = clean(addr.get("addressCountry") or _meta(soup, "business:contact_data:country_name"))

    website, about = _split_summary(soup)
    business["Website URL"] = website
    business["Description"] = about or clean(ld.get("description", ""))

    cats = soup.select_one(".details-categories")
    if cats:
        seen = []
        for a in cats.select("a.link"):
            text = clean(a.get_text())
            if text and text not in seen:
                seen.append(text)
        business["Category"] = ", ".join(seen)

    return filter_business_fields(business, url)