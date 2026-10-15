"""
Parser for thelocal.directory listing pages
(e.g. https://thelocal.directory/business-directory/<slug>/).

The site is WordPress + the "Business Directory Plugin" (wpbdp). Every
listing field is rendered as

    <div class="wpbdp-field-display ... wpbdp-field-<field_key>
                wpbdp-field-association-<title|content|meta|category> ...">
        <div class="value">...</div>
    </div>

so fields are read by their stable `wpbdp-field-<key>` class rather than
by position.

Quirks confirmed against a real sample page (/business-directory/haqq-legal-ai/)
-- the site's own markup, not a parsing bug on our end:

  - The State is stored in a field whose key is `county`
    ("wpbdp-field-county" -> "Delaware"), and it is a FULL state name,
    not an abbreviation.
  - The listing's JSON-LD only carries streetAddress / postalCode /
    telephone / image (no city, state or country, no website), so it is
    only used as a fallback for those fields.
  - The only image on a listing is its thumbnail, which on this site is
    the business logo. The link around it points at the full-size file,
    which is preferred over the 350px thumbnail.
  - The site footer carries thelocal.directory's OWN social icons on
    every page; this parser doesn't read social links at all.
"""

from ..common import *


# Custom-field keys are derived from the field labels, so a few
# alternatives are tried per field in case a listing's label differs.
_TLD_KEYS = {
    "street": ("business_address", "street_address", "street", "address"),
    "city": ("town_or_city", "city", "town"),
    "state": ("county", "state", "state_or_province", "region"),
    "zipcode": ("post_code", "zip_code", "zipcode", "zip", "postal_code"),
    "country": ("country",),
    "phone": ("business_phone_number", "phone_number", "phone", "telephone"),
    "website": ("business_website_address", "website_address", "website", "website_url"),
}


def parse_thelocal_directory(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    if _looks_blocked(html_text):
        return business

    listing = (
        soup.select_one(".wpbdp-listing.wpbdp-single")
        or soup.select_one(".wpbdp-listing")
        or soup
    )
    ld = _tld_local_business_ld(soup)
    ld_address = ld.get("address") if isinstance(ld.get("address"), dict) else {}

    business["Business Name"] = _tld_name(listing, ld)

    business["Street"] = _tld_field_text(listing, _TLD_KEYS["street"]) or clean(
        ld_address.get("streetAddress", "")
    )
    business["City"] = _tld_field_text(listing, _TLD_KEYS["city"]) or clean(
        ld_address.get("addressLocality", "")
    )
    business["State"] = _tld_field_text(listing, _TLD_KEYS["state"]) or clean(
        ld_address.get("addressRegion", "")
    )
    business["Zipcode"] = _tld_field_text(listing, _TLD_KEYS["zipcode"]) or clean(
        ld_address.get("postalCode", "")
    )
    business["Country"] = _tld_field_text(listing, _TLD_KEYS["country"]) or clean(
        ld_address.get("addressCountry", "")
        if isinstance(ld_address.get("addressCountry"), str) else ""
    )

    _tld_split_combined_address(business)

    business["Phone"] = _tld_field_text(listing, _TLD_KEYS["phone"]) or clean(
        ld.get("telephone", "")
    )
    business["Website URL"] = _tld_website(listing)
    business["Description"] = _tld_description(listing)
    business["Category"] = _tld_category(listing)
    business["Logo"] = _tld_logo(listing, ld, url)

    return filter_business_fields(business, url)


# ---------------------------------------------------------------- helpers

def _tld_local_business_ld(soup):
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw, strict=False)
        except (ValueError, TypeError):
            continue
        nodes = data.get("@graph") if isinstance(data, dict) and isinstance(data.get("@graph"), list) \
            else (data if isinstance(data, list) else [data])
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") == "LocalBusiness":
                return node
    return {}


def _tld_field_value_el(listing, keys):
    """The `.value` element of the first wpbdp field whose key matches."""
    for key in keys:
        field = listing.select_one(f".wpbdp-field-display.wpbdp-field-{key}")
        if field:
            value = field.select_one(".value")
            if value:
                return value
    return None


def _tld_field_text(listing, keys):
    el = _tld_field_value_el(listing, keys)
    if not el:
        return ""
    # <br>-separated multi-line values (e.g. a textarea street) -> one line
    text = clean(el.get_text(" "))
    return text if is_meaningful(text) else ""


_TLD_TRAILING_ZIP_RE = re.compile(r"\b[\w-]*\d[\w-]*\s*$")


def _tld_split_combined_address(business):
    """Some listings put the WHOLE address into the single street field
    ("131 Continental Dr, Suite 305, Newark, Delaware 19713") and leave the
    town / state / post-code fields empty. Split it only in that case, so
    listings that fill the fields separately are never re-split. Needs at
    least "street, city, state zip" (3+ comma parts) so a plain street such
    as "8 The Green" is left alone. Only runs when City and State are both
    empty."""
    if business["City"] or business["State"]:
        return
    street = business["Street"]
    if street.count(",") < 2:
        return

    parts = [p.strip() for p in street.split(",") if p.strip()]
    # A trailing country segment ("..., United States") is already in Country.
    country = business["Country"].lower()
    if country and parts and parts[-1].lower() == country:
        parts.pop()
    if len(parts) < 3 or not _TLD_TRAILING_ZIP_RE.search(parts[-1]):
        return

    new_street, city, state, zipcode = _split_blinx_address(", ".join(parts))
    if new_street and city:
        business["Street"], business["City"] = new_street, city
        business["State"] = state
        # A zip already read from its own field / JSON-LD wins over the split one.
        business["Zipcode"] = business["Zipcode"] or zipcode


def _tld_name(listing, ld):
    el = listing.select_one(".wpbdp-field-association-title .value") \
        or listing.select_one(".listing-title h1")
    name = clean(el.get_text()) if el else ""
    return name or clean(ld.get("name", ""))


def _tld_website(listing):
    el = _tld_field_value_el(listing, _TLD_KEYS["website"])
    if not el:
        return ""
    for a in el.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith(("http://", "https://")) and "thelocal.directory" not in href.lower():
            return href
    return ""


def _tld_description(listing):
    el = listing.select_one(".wpbdp-field-association-content .value")
    if not el:
        return ""
    paragraphs = [clean(p.get_text(" ")) for p in el.find_all("p")]
    paragraphs = [p for p in paragraphs if is_meaningful(p)]
    if paragraphs:
        return "\n\n".join(paragraphs)
    text = clean(el.get_text(" "))
    return text if is_meaningful(text) else ""


def _tld_category(listing):
    el = listing.select_one(".wpbdp-field-association-category .value")
    if not el:
        return ""
    names = [clean(a.get_text()) for a in el.find_all("a")] or [clean(el.get_text())]
    names = [n for n in names if is_meaningful(n)]
    return ", ".join(dict.fromkeys(names))


def _tld_logo(listing, ld, base_url):
    # Prefer the full-size file the thumbnail links to, then the thumbnail.
    link = listing.select_one(".listing-thumbnail a[href]")
    if link and re.search(r"\.(?:jpe?g|png|gif|webp|svg)(?:\?|$)", link["href"], re.I):
        return urljoin(base_url, link["href"].strip())
    img = listing.select_one(".listing-thumbnail img[src]")
    if img:
        return urljoin(base_url, img["src"].strip())
    image = ld.get("image")
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url", "")
    return urljoin(base_url, image.strip()) if isinstance(image, str) and image.strip() else ""