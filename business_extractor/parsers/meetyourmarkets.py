"""
Site parser: meetyourmarkets.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



# Matches "Street[, Suite/Unit ...], City, State Zip" -- this source renders
# the full address as a single unstructured string inside one <span> (e.g.
# "9716 Rea Rd Suite B #1101, Charlotte, NC 28277") instead of splitting it
# across separate elements, so it has to be pulled apart with a regex.
_MYM_ADDRESS_RE = re.compile(
    r"^(?P<street>.+),\s*(?P<city>[^,]+),\s*"
    r"(?P<state>[A-Za-z][A-Za-z .]*?)\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _meetyourmarkets_jsonld_local_business(soup):
    """Return the LocalBusiness node from this page's JSON-LD "@graph"
    array, or {} if none is present/parseable. There are usually two
    application/ld+json blocks on the page (one for this business, one
    sitewide for the Organization/WebSite) -- only the first has a
    LocalBusiness node."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue
        graph = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(graph, list):
            continue
        for node in graph:
            if isinstance(node, dict) and node.get("@type") == "LocalBusiness":
                return node
    return {}


def parse_meetyourmarkets(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _meetyourmarkets_jsonld_local_business(soup)
    addr_obj = ld_business.get("address", {}) if isinstance(ld_business, dict) else {}

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    # ---- Address (rendered as a single unsplit string, e.g.
    # "9716 Rea Rd Suite B #1101, Charlotte, NC 28277" -- the JSON-LD
    # address block on this theme fills city/state/zip with the literal
    # placeholder "N/A" rather than leaving them blank, so those can't be
    # trusted and the visible text has to be parsed instead) ----
    addr_el = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    addr_text = clean(addr_el.get_text()) if addr_el else ""
    match = _MYM_ADDRESS_RE.match(addr_text) if addr_text else None
    if match:
        business["Street"] = match.group("street").strip()
        business["City"] = match.group("city").strip()
        business["State"] = match.group("state").strip()
        business["Zipcode"] = match.group("zip").strip()
    elif addr_text:
        business["Street"] = addr_text

    # ---- Country (JSON-LD only; not rendered visibly on this theme) ----
    country = clean(addr_obj.get("addressCountry", "")) if isinstance(addr_obj, dict) else ""
    if country and country.upper() != "N/A":
        business["Country"] = country

    # ---- Phone (formatted text lives inside a nested <u>; the raw tel:
    # href is unformatted digits and only used as a last resort) ----
    tel_link = soup.select_one(".table-display-phone a[href^='tel:']")
    if tel_link:
        phone_u = tel_link.find("u")
        phone_text = clean(phone_u.get_text()) if phone_u else clean(tel_link.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text
    if not business["Phone"] and tel_link and tel_link.get("href"):
        business["Phone"] = tel_link["href"].replace("tel:", "").strip()
    if not business["Phone"] and ld_business.get("telephone"):
        business["Phone"] = clean(ld_business["telephone"])

    # ---- Website URL ----
    website_el = soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()
    if not business["Website URL"]:
        same_as = ld_business.get("sameAs")
        if isinstance(same_as, list) and same_as:
            business["Website URL"] = same_as[0]
        elif isinstance(same_as, str) and same_as:
            business["Website URL"] = same_as

    # ---- Description ----
    about_el = soup.select_one(".table-display-about_me .textarea-about_me")
    if about_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in about_el.find_all("p") if clean(p.get_text())
        ]
        desc_text = "\n".join(desc_paragraphs) if desc_paragraphs else clean(about_el.get_text())
        if is_meaningful(desc_text):
            business["Description"] = desc_text
    if not business["Description"] and ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Hours (opportunistic; not every listing on this source
    # publishes one) ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        hours_text = clean(hours_el.get_text())
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Social Media Links (opportunistic; not every listing on this
    # source publishes any -- scoped to the profile's own labeled row so
    # the sitewide footer's directory-owned Facebook link isn't picked up) ----
    social_links = {}
    for a in soup.select(".table-display-social_media_links a[href]"):
        href = a.get("href", "")
        for domain, name in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                social_links[name] = href
    if social_links:
        business["Social Media Links"] = social_links

    # ---- GBP Link (scoped to the "Get Directions" anchor) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


def parse_countrypwr(url, html):
    """countrypwr.com (Western Business Collective) runs the same
    "Bootstrap Theme" directory software as meetyourmarkets.com, so it
    shares the JSON-LD LocalBusiness lookup and the unsplit
    "Street, City, State Zip" address regex. Two markup differences from
    that source require their own handling here:
      - Phone is rendered as a plain <span> with no wrapping tel: link,
        so there's no formatted <a><u> text to prefer over JSON-LD.
      - The About block uses class "froala-data" (not "textarea-about_me").
    """

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _meetyourmarkets_jsonld_local_business(soup)
    addr_obj = ld_business.get("address", {}) if isinstance(ld_business, dict) else {}

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    # ---- Address (single unsplit string, e.g.
    # "300 Triple Diamond Blvd ,Nokomis ,FL 34275" -- JSON-LD address
    # block fills city/state/zip with the literal placeholder "N/A"
    # rather than leaving them blank, so those can't be trusted and the
    # visible text has to be parsed instead) ----
    addr_el = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    addr_text = clean(addr_el.get_text()) if addr_el else ""
    match = _MYM_ADDRESS_RE.match(addr_text) if addr_text else None
    if match:
        business["Street"] = match.group("street").strip()
        business["City"] = match.group("city").strip()
        business["State"] = match.group("state").strip()
        business["Zipcode"] = match.group("zip").strip()
    elif addr_text:
        business["Street"] = addr_text

    # ---- Country (JSON-LD only; not rendered visibly on this theme) ----
    country = clean(addr_obj.get("addressCountry", "")) if isinstance(addr_obj, dict) else ""
    if country and country.upper() != "N/A":
        business["Country"] = country

    # ---- Phone (plain text span here -- no tel: link/<u> wrapper like
    # meetyourmarkets, so the visible span is the primary source and the
    # unformatted JSON-LD telephone is only a last resort) ----
    phone_el = soup.select_one(".table-display-phone .col-sm-8")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text
    if not business["Phone"] and ld_business.get("telephone"):
        business["Phone"] = clean(ld_business["telephone"])

    # ---- Website URL ----
    website_el = soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()
    if not business["Website URL"]:
        same_as = ld_business.get("sameAs")
        if isinstance(same_as, list) and same_as:
            business["Website URL"] = same_as[0]
        elif isinstance(same_as, str) and same_as:
            business["Website URL"] = same_as

    # ---- Description (this theme instance uses "froala-data" for the
    # About block rather than "textarea-about_me") ----
    about_el = soup.select_one(".table-display-about_me .froala-data")
    if about_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in about_el.find_all("p") if clean(p.get_text())
        ]
        desc_text = "\n".join(desc_paragraphs) if desc_paragraphs else clean(about_el.get_text())
        if is_meaningful(desc_text):
            business["Description"] = desc_text
    if not business["Description"] and ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Hours (opportunistic; not every listing on this source
    # publishes one) ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        hours_text = clean(hours_el.get_text())
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


def parse_bizmakersamerica(url, html):
    """bizmakersamerica.org runs the same "Bootstrap Theme" directory
    software as meetyourmarkets.com/countrypwr.com, so it reuses the
    shared JSON-LD LocalBusiness lookup and the unsplit "Street, City,
    State Zip" address regex. Two markup differences from those sources
    require their own handling here:
      - Phone has no "table-display-phone" wrapper or tel: link at all --
        it's a bare ".phone_number" span hidden behind a "Show Phone
        Number" toggle, so the JSON-LD telephone is the reliable source.
      - The About block uses class "froala-data" (not "textarea-about_me"),
        same as countrypwr.com.
    """

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _meetyourmarkets_jsonld_local_business(soup)
    addr_obj = ld_business.get("address", {}) if isinstance(ld_business, dict) else {}

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        company_el = soup.select_one(".table-display-company .textbox-company")
        if company_el:
            business["Business Name"] = clean(company_el.get_text())
    if not business["Business Name"] and ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    # ---- Address (single unsplit string, e.g.
    # "8 The Green, Dover, Delaware 19901" -- JSON-LD address block fills
    # city/state/zip with the literal placeholder "N/A" rather than
    # leaving them blank, so those can't be trusted and the visible text
    # has to be parsed instead) ----
    addr_el = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    addr_text = clean(addr_el.get_text()) if addr_el else ""
    match = _MYM_ADDRESS_RE.match(addr_text) if addr_text else None
    if match:
        business["Street"] = match.group("street").strip()
        business["City"] = match.group("city").strip()
        business["State"] = match.group("state").strip()
        business["Zipcode"] = match.group("zip").strip()
    elif addr_text:
        business["Street"] = addr_text

    # ---- Country (JSON-LD only; not rendered visibly on this theme) ----
    country = clean(addr_obj.get("addressCountry", "")) if isinstance(addr_obj, dict) else ""
    if country and country.upper() != "N/A":
        business["Country"] = country

    # ---- Phone (bare hidden span, no tel: link/wrapper -- JSON-LD is the
    # reliable source since the visible span stays hidden until a JS
    # toggle click reveals it) ----
    phone_el = soup.select_one(".phone_number")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text
    if not business["Phone"] and ld_business.get("telephone"):
        business["Phone"] = clean(ld_business["telephone"])

    # ---- Website URL ----
    website_el = soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()
    if not business["Website URL"]:
        same_as = ld_business.get("sameAs")
        if isinstance(same_as, list) and same_as:
            business["Website URL"] = same_as[0]
        elif isinstance(same_as, str) and same_as:
            business["Website URL"] = same_as

    # ---- Description ----
    about_el = soup.select_one(".table-display-about_me .froala-data")
    if about_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in about_el.find_all("p") if clean(p.get_text())
        ]
        desc_text = "\n".join(desc_paragraphs) if desc_paragraphs else clean(about_el.get_text())
        if is_meaningful(desc_text):
            business["Description"] = desc_text
    if not business["Description"] and ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Hours (opportunistic; not every listing on this source
    # publishes one) ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        hours_text = clean(hours_el.get_text())
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


