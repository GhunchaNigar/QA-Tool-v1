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

Data-quality quirks confirmed from a real sample page
(/professionals/10005176/focal) -- Homify's own markup, not a parsing
bug on our end:

  - `address.streetAddress` is actually "STREET, CITY" (e.g.
    "131 Continental Dr, Suite 305, Newark") -- the city is appended
    onto the street as the final comma-separated segment rather than
    broken out on its own.
  - `address.addressLocality` is actually the STATE, not the city
    (e.g. "Delaware"). Confirmed against the rendered address panel,
    which prints "07114 Delaware" (zip, then full state name) on one
    line, and against the page's own breadcrumb/category-city block
    ("professionals/other-businesses-in-delaware", "in Delaware").
"""

from ..common import *


def parse_homify(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    ld = _homify_local_business_ld(soup)

    business["Business Name"] = clean(ld.get("name", "")) or _homify_name_fallback(soup)

    address = ld.get("address") or {}
    street, city = _homify_split_street_city(address.get("streetAddress", ""))
    business["Street"] = street
    business["City"] = city
    business["State"] = clean(address.get("addressLocality", ""))
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


def _homify_split_street_city(street_address):
    """Homify folds the city into streetAddress as the final
    comma-separated segment (e.g. "131 Continental Dr, Suite 305,
    Newark" -> street "131 Continental Dr, Suite 305", city "Newark").
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