"""
Site parser: searchmypro.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_SEARCHMYPRO_ADDRESS_RE = re.compile(
    r"^(?P<street>.+),\s*(?P<city>[^,]+),\s*"
    r"(?P<state>[A-Za-z][A-Za-z .]*?)\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)

# Strips a trailing ", <Full State Name>" segment some listings append after
# the "State Zip" pair (e.g. "..., WI 54913, Wisconsin"), so the address
# regex -- which expects the string to end in a zip code -- can still match
# once that duplicate is removed.
_TRAILING_STATE_NAME_RE = re.compile(r",\s*[A-Za-z][A-Za-z .]*$")


def _searchmypro_jsonld_local_business(soup):
    """Return the LocalBusiness object from the page's JSON-LD (handles
    both a plain object/list and an @graph-wrapped block)."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue

        graph = data.get("@graph") if isinstance(data, dict) else None
        objects = graph if isinstance(graph, list) else (
            data if isinstance(data, list) else [data]
        )

        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "LocalBusiness":
                return obj

    return None


def _match_searchmypro_address(addr_text):
    """Match addr_text against _SEARCHMYPRO_ADDRESS_RE, retrying once with
    a trailing duplicated full-state-name segment stripped (see
    _TRAILING_STATE_NAME_RE) if the first pass doesn't match."""
    match = _SEARCHMYPRO_ADDRESS_RE.match(addr_text)
    if match:
        return match

    stripped = _TRAILING_STATE_NAME_RE.sub("", addr_text)
    if stripped != addr_text:
        return _SEARCHMYPRO_ADDRESS_RE.match(stripped)

    return None


def parse_searchmypro(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _searchmypro_jsonld_local_business(soup) or {}

    # ---- Business Name ----
    h1 = soup.select_one("h1.bold.inline-block")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        company = soup.select_one(".table-display-company .textbox-company")
        if company:
            business["Business Name"] = clean(company.get_text())

    if not business["Business Name"] and ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    # ---- Phone ----
    tel = soup.select_one('a[href^="tel:"]')
    if tel and tel.get("href"):
        business["Phone"] = tel["href"].replace("tel:", "").strip()

    if not business["Phone"] and ld_business.get("telephone"):
        business["Phone"] = clean(ld_business["telephone"])

    # ---- Website URL ----
    weblink = soup.select_one("a.weblink[href]")
    if weblink:
        business["Website URL"] = weblink["href"]

    # ---- Description (multi-paragraph About section) ----
    about = soup.select_one(".froala-data.field-about_me")
    if about:
        desc_text = clean_multiline(about.get_text(separator="\n"))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"] and ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Address ----
    # Three markup shapes seen on this template:
    #  (a) one <span> holding the full "Street, City, State Zip" string
    #      (e.g. the Focal listing)
    #  (b) four separate <span> elements -- street, city, state, zip --
    #      with a trailing plain-text country after the final <br>
    #      (e.g. the WrightWay Emergency Services listing)
    #  (c) one <span> holding "Street, City, State Zip[, Full State Name]"
    #      followed by a <br> and then the country as a second line inside
    #      the SAME span (e.g. the Valley Exteriors listing). Using
    #      span.get_text() here would glue the state name and country
    #      together with no separator at all ("WisconsinUnited States"),
    #      since get_text() inserts nothing across a <br>. Split on <br>
    #      first instead, so the address line and the country line stay
    #      separate.
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_container:
        addr_spans = addr_container.find_all("span", recursive=False)

        if len(addr_spans) >= 4:
            business["Street"] = clean(addr_spans[0].get_text())
            business["City"] = clean(addr_spans[1].get_text())
            business["State"] = clean(addr_spans[2].get_text())
            business["Zipcode"] = clean(addr_spans[3].get_text())
        elif len(addr_spans) == 1:
            span = addr_spans[0]
            span_lines = clean_multiline(span.decode_contents()).split("\n")
            addr_text = clean(span_lines[0]) if span_lines else ""
            # A second line inside the same span (after a <br>) is the
            # country for shape (c) -- stash it for the Country block below.
            span_country_line = clean(span_lines[-1]) if len(span_lines) > 1 else ""

            match = _match_searchmypro_address(addr_text) if addr_text else None
            if match:
                business["Street"] = clean(match.group("street"))
                business["City"] = clean(match.group("city"))
                business["State"] = clean(match.group("state"))
                business["Zipcode"] = match.group("zip")
            elif addr_text:
                # Fall back to storing the raw string as Street rather than
                # dropping the address entirely if it doesn't match the
                # expected "Street, City, State Zip" shape.
                business["Street"] = addr_text

            if span_country_line and is_meaningful(span_country_line):
                business["Country"] = span_country_line
        else:
            # Unexpected span count -- fall back to the raw container text
            # rather than dropping the address entirely.
            addr_text = clean(addr_container.get_text())
            if is_meaningful(addr_text):
                business["Street"] = addr_text

        # Country: trailing plain-text node directly under the container
        # (after the final <br>), not inside any of the address spans.
        # Only used when the address didn't already come from the
        # single-span shape (c) above, which sets Country itself.
        if not business["Country"]:
            trailing_text_nodes = [
                clean(node) for node in addr_container.contents
                if isinstance(node, NavigableString) and clean(node) and clean(node) != ","
            ]
            if trailing_text_nodes:
                country_text = trailing_text_nodes[-1]
                if country_text:
                    business["Country"] = country_text

    # ---- Country fallback (JSON-LD) ----
    if not business["Country"]:
        addr_obj = ld_business.get("address")
        if isinstance(addr_obj, dict):
            country = clean(addr_obj.get("addressCountry", ""))
            if country and country.upper() != "N/A":
                business["Country"] = country

    # ---- Hours (not every listing publishes one) ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        hours_text = clean(hours_el.get_text())
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category ----
    category_span = soup.select_one(".profile-header-top-category")
    if category_span:
        cat_text = clean(category_span.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    if not business["Category"]:
        crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        if len(crumbs) >= 2:
            business["Category"] = crumbs[-2]

    # ---- Social Media Links (dedicated widget row first, then JSON-LD
    #      sameAs as a fallback -- excluding the business's own website
    #      and this directory's own domain) ----
    for a in soup.select(".table-display-social_media_links a[href]"):
        href = a.get("href", "")
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href
                break

    if not business["Social Media Links"]:
        same_as = ld_business.get("sameAs")
        same_as = same_as if isinstance(same_as, list) else ([same_as] if same_as else [])
        for href in same_as:
            if not isinstance(href, str) or not href.startswith("http"):
                continue
            if "searchmypro.com" in href.lower():
                continue
            for domain, network in SOCIAL_DOMAINS.items():
                if _hostname_matches_social_domain(href, domain):
                    business["Social Media Links"][network] = href
                    break

    # ---- Logo ----
    logo_img = soup.select_one(".profile-image img[src]")
    if logo_img:
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"] and ld_business.get("image"):
        image = ld_business["image"]
        image_url = image.get("url") if isinstance(image, dict) else image
        if image_url:
            business["Logo"] = urljoin(url, image_url)

    return business
