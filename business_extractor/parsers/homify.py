"""
Parser for homify.com professional/business profile pages
(e.g. https://www.homify.com/professionals/<id>/<slug>).

Homify embeds a schema.org LocalBusiness block in
<script type="application/ld+json"> right after the page header. That
block is the primary source for Street/City/State/Zipcode/Phone/
Website/Logo -- far more reliable than scraping the rendered "Address"
panel, whose <br>-separated lines have no reliable delimiters between
fields. Category and Keywords aren't in that JSON-LD block and are
pulled from the surrounding page instead.

Data-quality quirk -- NOT universal, must be detected per-page:

On some Homify listings (confirmed on /professionals/10005176/focal),
the LocalBusiness address block is malformed:

  - `address.streetAddress` is actually "STREET, CITY" (e.g.
    "131 Continental Dr, Suite 305, Newark") -- the city is appended
    onto the street as the final comma-separated segment rather than
    broken out on its own.
  - `address.addressLocality` is actually the STATE, not the city
    (e.g. "Delaware").

But on other listings (confirmed on /professionals/9936231/
valley-exteriors) the block is completely normal:

  - `address.streetAddress` is just the street ("1883 N Silverspring
    Dr"), with no city folded in and no comma to split on.
  - `address.addressLocality` is the actual CITY ("Appleton"), matching
    both the rendered address panel ("54913 Appleton") and the page's
    own category-city block ("in Appleton") / breadcrumb
    ("other-businesses-in-appleton").
  - There's no addressRegion field at all on this listing, so the
    state is genuinely absent from the page, not something to be
    reverse-engineered out of streetAddress/addressLocality.

Treating the quirky layout as the default (as an earlier version of
this parser did) silently corrupts the normal case: City comes out
empty and State gets the city's name instead. So we no longer assume
which layout we're looking at -- we detect it by checking whether
addressLocality is actually a US state name. Only then do we apply the
street/city split; otherwise addressLocality is trusted as the city
and State comes from addressRegion (blank if Homify didn't supply
one).
"""

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
    """Return (street, city, state), detecting which of Homify's two
    observed address layouts this particular page uses (see module
    docstring) rather than assuming one unconditionally.

    Signal: on the quirky layout, addressLocality holds a full US
    state name. On the normal layout it holds a city name, which will
    essentially never collide with a state name. So checking
    addressLocality against a state-name list is a reliable
    discriminator between the two layouts.
    """
    locality = clean(address.get("addressLocality", ""))
    street_address = address.get("streetAddress", "")
    region = clean(address.get("addressRegion", ""))

    if locality.strip().lower() in US_STATE_NAMES:
        # Quirky layout: city is folded into streetAddress as its
        # final comma-separated segment, and addressLocality is
        # actually the state.
        street, city = _homify_split_street_city(street_address)
        state = locality
    else:
        # Normal layout: addressLocality is the city as expected;
        # street stands alone. State comes from addressRegion if
        # Homify provided one, otherwise it's simply not on the page.
        street = clean(street_address)
        city = locality
        state = region

    return street, city, state


def _homify_split_street_city(street_address):
    """Homify folds the city into streetAddress as the final
    comma-separated segment (e.g. "131 Continental Dr, Suite 305,
    Newark" -> street "131 Continental Dr, Suite 305", city "Newark").
    Only called once we've confirmed (via addressLocality being a
    state name) that this page uses the quirky layout.
    """
    parts = [p.strip() for p in (street_address or "").split(",") if p.strip()]
    if len(parts) >= 2:
        return ", ".join(parts[:-1]), parts[-1]
    if len(parts) == 1:
        return parts[0], ""
    return "", ""


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
