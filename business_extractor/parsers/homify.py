from ..common import *


US_STATE_NAMES = frozenset({
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
})

# USPS 2-letter abbreviations for the 50 states + DC, used only to
# detect layout 3 (state abbreviation folded onto the end of
# streetAddress) -- see module docstring. Kept as its own set rather
# than deriving from US_STATE_NAMES since the two layouts key off
# different spellings (full name vs. abbreviation).
US_STATE_ABBREVIATIONS = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
    "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "DC",
})


def parse_homify(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    ld = _homify_local_business_ld(soup)

    business["Business Name"] = clean(ld.get("name", "")) or _homify_name_fallback(soup)

    address = ld.get("address") or {}
    street, city, state = _homify_split_address(address)
    business["Street"] = street
    business["City"] = city
    business["State"] = state
    business["Zipcode"] = clean(address.get("postalCode", ""))

    business["Phone"] = clean(ld.get("telephone", ""))
    business["Website URL"] = clean(ld.get("url", "")) or _homify_website_fallback(soup)

    business["Description"] = _homify_description(soup, ld)
    business["Keywords"] = _homify_keywords(soup, ld)
    business["Category"] = _homify_category(soup)
    business["Logo"] = clean(ld.get("image", ""))
    business["Social Media Links"] = _homify_social_links(soup)

    return filter_business_fields(business, url)


def _homify_local_business_ld(soup):
    """Find the schema.org LocalBusiness JSON-LD block. Homify emits
    several application/ld+json scripts on a profile page
    (Organization, BreadcrumbList, LocalBusiness) -- only the
    LocalBusiness one carries this profile's own data."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "LocalBusiness":
            return data
    return {}


def _homify_split_address(address):
    """Return (street, city, state), detecting which of Homify's three
    observed address layouts this particular page uses (see module
    docstring) rather than assuming one unconditionally.

    Signal 1: on the "city-in-street" layout, addressLocality holds a
    full US state name. On the other layouts it holds a city name,
    which will essentially never collide with a state name. So
    checking addressLocality against a state-name list is a reliable
    discriminator for that layout.

    Signal 2 (only checked once signal 1 has ruled out the first
    layout, and only when addressRegion is empty): on the
    "state-in-street" layout, streetAddress's final comma-separated
    segment is a bare 2-letter state abbreviation. Real street
    addresses essentially never end a comma segment with exactly a
    state abbreviation, so this is safe to treat as a positive match.
    """
    locality = clean(address.get("addressLocality", ""))
    street_address = address.get("streetAddress", "")
    region = clean(address.get("addressRegion", ""))

    if locality.strip().lower() in US_STATE_NAMES:
        # Layout 1 ("city-in-street"): city is folded into
        # streetAddress as its final comma-separated segment, and
        # addressLocality is actually the state.
        street, city = _homify_split_street_city(street_address)
        return street, city, locality

    # Not layout 1. addressLocality is trusted as the city.
    city = locality

    if not region:
        # addressRegion is missing -- check for layout 3
        # ("state-in-street") before giving up on ever finding a state.
        street, trailing_state = _homify_strip_trailing_state_abbr(street_address)
        if trailing_state:
            return street, city, trailing_state

    # Layout 2 (normal): street stands alone, state comes from
    # addressRegion if Homify provided one, otherwise it's genuinely
    # not on the page.
    return clean(street_address), city, region


def _homify_split_street_city(street_address):
    """Homify folds the city into streetAddress as the final
    comma-separated segment (e.g. "131 Continental Dr, Suite 305,
    Newark" -> street "131 Continental Dr, Suite 305", city "Newark").
    Only called once we've confirmed (via addressLocality being a
    state name) that this page uses layout 1.
    """
    parts = [p.strip() for p in (street_address or "").split(",") if p.strip()]
    if len(parts) >= 2:
        return ", ".join(parts[:-1]), parts[-1]
    if len(parts) == 1:
        return parts[0], ""
    return "", ""


def _homify_strip_trailing_state_abbr(street_address):
    """Return (street, state_abbr). If streetAddress's final
    comma-separated segment is a recognized 2-letter USPS state
    abbreviation (e.g. "1402 Park St, Ste G, CA"), split it out as the
    state and return the remaining segments rejoined as the street.
    Otherwise returns (cleaned street_address, "") unchanged.

    Only called once we've confirmed (via addressLocality NOT being a
    state name, and addressRegion being empty) that this page might be
    layout 3 -- see module docstring. Requires at least 2 segments so
    a bare "CA" with nothing else in streetAddress is left alone
    rather than being emptied out entirely.
    """
    parts = [p.strip() for p in (street_address or "").split(",") if p.strip()]
    if len(parts) >= 2 and parts[-1].upper() in US_STATE_ABBREVIATIONS:
        return ", ".join(parts[:-1]), parts[-1].upper()
    return clean(street_address), ""


def _homify_name_fallback(soup):
    name_el = soup.select_one(".user-header--public-name")
    return clean(name_el.get_text()) if name_el else ""


def _homify_website_fallback(soup):
    link = soup.select_one("a.contact--website")
    if link and link.get("href"):
        return link["href"].strip()
    return ""


def _homify_description(soup, ld):
    # Prefer the rendered description block: it's already split into
    # <p> tags, so we can rejoin with real paragraph breaks instead of
    # regex-stripping the HTML markup embedded in the JSON-LD string.
    desc_el = soup.select_one(".show-user--company-description")
    if desc_el:
        paragraphs = [clean(p.get_text()) for p in desc_el.find_all("p")]
        paragraphs = [p for p in paragraphs if is_meaningful(p)]
        if paragraphs:
            return "\n\n".join(paragraphs)

    ld_desc = ld.get("description", "")
    if ld_desc:
        return clean_multiline(re.sub(r"</p>\s*<p>", "\n\n", ld_desc))
    return ""


def _homify_keywords(soup, ld):
    """Homify has no dedicated keywords field. The closest analogue is
    the "Services" line in the details panel; fall back to the
    JSON-LD hasOfferCatalog names, which describe the same thing."""
    services_el = soup.select_one(".show-user--services")
    if services_el:
        value = clean(services_el.get_text())
        if is_meaningful(value):
            return value

    catalog = ld.get("hasOfferCatalog") or {}
    names = catalog.get("name")
    if isinstance(names, list) and names:
        joined = ", ".join(clean(n) for n in names if is_meaningful(n))
        if is_meaningful(joined):
            return joined
    if isinstance(names, str) and is_meaningful(names):
        return clean(names)
    return ""


def _homify_category(soup):
    cat_el = soup.select_one(".category-city--category")
    return clean(cat_el.get_text()) if cat_el else ""


def _homify_social_links(soup):
    """Restrict the search to the profile's own content area so
    homify's own footer social icons -- present on every page
    regardless of which business is being viewed -- never get
    attributed to the business being scraped."""
    links = {}
    scope = soup.select_one("#js-profile-content") or soup.select_one("#content")
    if not scope:
        return links

    for a in scope.find_all("a", href=True):
        href = a["href"]
        for domain, label in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                links.setdefault(label, href)
                break
    return links
