import re

from ..common import *


_GLUED_URL_TLD_RE = re.compile(
    r"^https?://[^\s/?#]+\.(?:com|net|org|us|biz|info|co|io|gov|edu|me|shop|store)",
    re.IGNORECASE,
)


def _clean_glued_website_url(url):
    """Strip a label glued directly onto a URL with no separator (seen
    on bizforgeusa.com's "Visit Website" link). Returns the URL
    unchanged if it already ends cleanly, has a real path/query/fragment
    after the domain, or if the TLD isn't recognized."""
    if not url:
        return ""
    url = url.strip()

    match = _GLUED_URL_TLD_RE.match(url)
    if not match:
        return url

    end = match.end()
    if end >= len(url):
        return url

    next_char = url[end]
    if next_char in ("/", "?", "#", ":"):
        return url

    # Anything else glued directly after the TLD (a letter, in the
    # bizforgeusa.com case) is the label-concatenation artifact.
    return url[:end]


def _extract_bigbizstuff_jsonld(soup):
    """Find the LocalBusiness node inside the page's JSON-LD @graph and
    pull out the fields this parser needs. Returns {} if no JSON-LD
    LocalBusiness block is present."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue

        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            items = data["@graph"]
        elif isinstance(data, dict):
            items = [data]
        else:
            continue

        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "LocalBusiness":
                continue

            address = item.get("address")
            street_address = ""
            if isinstance(address, dict):
                street_address = clean(address.get("streetAddress", ""))

            image = item.get("image")
            if isinstance(image, dict):
                image_url = image.get("url", "")
            elif isinstance(image, str):
                image_url = image
            else:
                image_url = ""

            same_as = item.get("sameAs")
            if not isinstance(same_as, list):
                same_as = []

            return {
                "name": clean(item.get("name", "")),
                "streetAddress": street_address,
                "telephone": clean(item.get("telephone", "")),
                "description": clean(item.get("description", "")),
                "image": image_url,
                "sameAs": same_as,
            }

    return {}


def parse_bigbizstuff(url, html):
    soup = BeautifulSoup(html, "html.parser")
    business = empty_business()

    jsonld = _extract_bigbizstuff_jsonld(soup)

    # ---- Name ----
    name = jsonld.get("name", "")
    if not is_meaningful(name):
        h1 = soup.select_one(".header-member-name h1")
        name = clean(h1.get_text()) if h1 else ""
    business["Business Name"] = name

    # ---- Address (Street / City / State / Zipcode) ----
    # JSON-LD addressLocality/addressRegion/postalCode are hardcoded "N/A"
    # on this template, so always split the combined streetAddress string
    # rather than trusting the discrete schema fields.
    address = jsonld.get("streetAddress", "")
    if not is_meaningful(address):
        addr_span = soup.select_one(".overview-tab-the-member-address .col-sm-8 span")
        address = clean(addr_span.get_text()) if addr_span else ""

    if is_meaningful(address):
        street, city, state, zipcode = _split_blinx_address(address)
    else:
        street, city, state, zipcode = "", "", "", ""

    business["Street"] = street
    business["City"] = city
    business["State"] = state
    business["Zipcode"] = zipcode
    business["Country"] = "US"

    # ---- Phone ----
    # Prefer the visible, human-formatted phone span over JSON-LD's
    # telephone (both matched exactly on the sampled page, but the
    # visible span is the more trustworthy source across this site
    # family in general).
    phone_span = soup.select_one(".table-display-phone .col-sm-8 span")
    phone = clean(phone_span.get_text()) if phone_span else ""
    if not is_meaningful(phone):
        phone = jsonld.get("telephone", "")
    business["Phone"] = phone

    # ---- Website URL ----
    website_link = soup.select_one(".table-display-website a.weblink")
    website = ""
    if website_link and website_link.has_attr("href"):
        website = website_link["href"].strip()
    if not is_meaningful(website):
        for same_as in jsonld.get("sameAs", []):
            same_as = (same_as or "").strip()
            if same_as and same_as.rstrip("/") != url.rstrip("/"):
                website = same_as
                break
    business["Website URL"] = _clean_glued_website_url(website)

    # ---- Description ----
    description = jsonld.get("description", "")
    if not is_meaningful(description):
        desc_p = soup.select_one(".field-about_me p")
        description = clean(desc_p.get_text()) if desc_p else ""
    business["Description"] = description

    # ---- Hours ----
    # No hours markup found on the sampled page -- left empty.
    business["Hours"] = ""

    # ---- Social Media Links ----
    social_links = {}
    for same_as in jsonld.get("sameAs", []):
        same_as = _clean_glued_website_url((same_as or "").strip())
        if not same_as:
            continue
        for domain_key, platform_name in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(same_as, domain_key):
                social_links[platform_name] = same_as
                break
    business["Social Media Links"] = social_links

    # ---- Category ----
    category_span = soup.select_one(".profile-header-top-category")
    business["Category"] = clean(category_span.get_text()) if category_span else ""

    # ---- Logo ----
    logo_img = soup.select_one(".profile-image img")
    logo_src = ""
    if logo_img and logo_img.has_attr("src"):
        logo_src = logo_img["src"].strip()
    if not is_meaningful(logo_src):
        logo_src = jsonld.get("image", "")
    business["Logo"] = urljoin(url, logo_src) if logo_src else ""

    return business