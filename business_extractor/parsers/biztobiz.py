
from ..common import *


def _extract_ld_local_business(soup):
    """Find and parse the LocalBusiness object inside the page's JSON-LD
    @graph block. Returns {} if none is found or it fails to parse."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw or "LocalBusiness" not in raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue

        graph = data.get("@graph") if isinstance(data, dict) else None
        candidates = graph if isinstance(graph, list) else [data]
        for entry in candidates:
            if isinstance(entry, dict) and entry.get("@type") == "LocalBusiness":
                return entry
    return {}


def parse_biztobiz(url, html):
    business = empty_business()
    soup = BeautifulSoup(html, "html.parser")

    ld = _extract_ld_local_business(soup)
    ld_address = ld.get("address") or {}

    # ---- Business Name ----
    name_tag = soup.select_one(".header-member-name h1")
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())
    elif ld.get("name"):
        business["Business Name"] = clean(ld["name"])

    # ---- Street / City / State / Zipcode ----
    address_text = ""
    address_span = soup.select_one(".overview-tab-the-member-address .col-sm-8 span")
    if address_span:
        address_text = clean(address_span.get_text())
    elif ld_address.get("streetAddress"):
        address_text = clean(ld_address["streetAddress"])

    if address_text:
        street, city, state, zipcode = _split_blinx_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Country ----
    if ld_address.get("addressCountry"):
        business["Country"] = clean(ld_address["addressCountry"])

    # ---- Phone ----
    phone_tag = soup.select_one("span.phone_number")
    if phone_tag:
        business["Phone"] = clean(phone_tag.get_text())
    elif ld.get("telephone"):
        business["Phone"] = clean(ld["telephone"])

    # ---- Website URL ----
    website_tag = soup.select_one("a.weblink")
    if website_tag and website_tag.get("href"):
        business["Website URL"] = clean(website_tag["href"])

    # ---- Description ----
    desc_tag = soup.select_one(".froala-data.field-about_me")
    if desc_tag:
        value = clean_multiline(str(desc_tag)) if desc_tag.find("br") else clean(desc_tag.get_text())
        if is_meaningful(value):
            business["Description"] = value
    elif ld.get("description"):
        business["Description"] = clean(ld["description"])

    # ---- Category ----
    category_tag = soup.select_one(".profile-header-top-category")
    if category_tag:
        business["Category"] = clean(category_tag.get_text())
    elif ld.get("keywords"):
        business["Category"] = clean(ld["keywords"])

    # ---- Logo ----
    logo_tag = soup.select_one(".profile-image img")
    if logo_tag and logo_tag.get("src"):
        business["Logo"] = urljoin(url, logo_tag["src"])

    # Hours / Social Media Links: no source on this page template -- left
    # at empty_business() defaults ("" and {} respectively).

    return business