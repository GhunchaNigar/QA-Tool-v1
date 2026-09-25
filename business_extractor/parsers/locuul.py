"""
Site parser: locuul.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Matches "Street[, Suite/Unit ...], City, State Zip" -- the unstructured
# single-string address shape used on listings like haqq-legal-ai and
# focal. Street is greedy so it absorbs any internal commas (e.g. a
# "Suite 305" segment); only the LAST two comma-separated segments are
# required to be City and "State Zip".
_LOCUUL_ADDRESS_RE = re.compile(
    r"^(?P<street>.+),\s*(?P<city>[^,]+),\s*"
    r"(?P<state>[A-Za-z][A-Za-z .]*?)\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)

# Matches "City State, Zip" -- the shorter shape used when a listing has no
# separate street line at all (e.g. Neera Truong Real Estate: the location
# row is just "Plano TX, 75023", two comma-separated segments instead of
# three). _LOCUUL_ADDRESS_RE requires a street segment and never matches
# this shape, which previously caused the whole raw string to fall through
# into Street while City/State/Zipcode stayed blank.
_LOCUUL_CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<city>.+?)\s+(?P<state>[A-Za-z]{2})\s*,\s*(?P<zip>\d{5}(?:-\d{4})?)$"
)


def parse_locuul(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one("h1.bold.inline-block")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        company = soup.select_one(".table-display-company .textbox-company")
        if company:
            business["Business Name"] = clean(company.get_text())

    # ---- Address  ----
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_container:
        addr_spans = addr_container.find_all("span", recursive=False)
        if len(addr_spans) >= 4:
            business["Street"] = clean(addr_spans[0].get_text())
            business["City"] = clean(addr_spans[1].get_text())
            business["State"] = clean(addr_spans[2].get_text())
            business["Zipcode"] = clean(addr_spans[3].get_text())
        elif not business["Street"]:
            addr_text = clean(addr_container.get_text())
            if is_meaningful(addr_text):
                match = _LOCUUL_ADDRESS_RE.match(addr_text)
                if match:
                    business["Street"] = clean(match.group("street"))
                    business["City"] = clean(match.group("city"))
                    business["State"] = clean(match.group("state"))
                    business["Zipcode"] = match.group("zip")
                else:
                    city_state_zip_match = _LOCUUL_CITY_STATE_ZIP_RE.match(addr_text)
                    if city_state_zip_match:
                        business["City"] = clean(city_state_zip_match.group("city"))
                        business["State"] = clean(city_state_zip_match.group("state"))
                        business["Zipcode"] = city_state_zip_match.group("zip")
                    else:
                        business["Street"] = addr_text

        trailing_text_nodes = [
            clean(node) for node in addr_container.contents
            if isinstance(node, NavigableString) and clean(node) and clean(node) != ","
        ]
        if trailing_text_nodes:
            country_text = trailing_text_nodes[-1]
            if country_text:
                business["Country"] = country_text

    # ---- Country/Phone fallback (LocalBusiness node inside the page's
    # JSON-LD "@graph" array) ----
    jsonld_local_business = {}
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
                jsonld_local_business = node
                break
        if jsonld_local_business:
            break

    if not business["Country"]:
        country = clean(jsonld_local_business.get("address", {}).get("addressCountry", ""))
        if country and country.upper() != "N/A":
            business["Country"] = country

    # ---- Phone  ----
    phone_el = soup.select_one(".table-display-phone .col-sm-8")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text
    if not business["Phone"]:
        phone_link = soup.select_one(".table-display-phone a[href^='tel:']")
        if phone_link and phone_link.get("href"):
            business["Phone"] = phone_link["href"].replace("tel:", "").strip()
    if not business["Phone"] and jsonld_local_business.get("telephone"):
        business["Phone"] = clean(jsonld_local_business["telephone"])

    # ---- Website URL (dedicated labeled row) ----
    website_el = soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Description  ----
    about_el = soup.select_one(".froala-data.field-about_me")
    about_text = ""
    if about_el:
        about_text = clean_multiline(about_el.get_text(separator="\n"))
        if is_meaningful(about_text):
            business["Description"] = about_text

    if not business["Description"] and jsonld_local_business.get("description"):
        desc_text = clean(jsonld_local_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Hours  ----
    for row in soup.select(".table-view-group"):
        label = row.select_one(".col-sm-4")
        value = row.select_one(".col-sm-8")
        if label and value and clean(label.get_text()).lower() == "hours of operation":
            hours_text = clean(value.get_text())
            if is_meaningful(hours_text):
                business["Hours"] = hours_text
            break

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    if not business["Category"]:
        crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        if len(crumbs) >= 2:
            business["Category"] = crumbs[-1]

    # ---- Social Media Links (opportunistic; not every listing on this
    # source publishes any) ----
    social_links = {}
    for a in soup.select(".table-display-social-links a[href]"):
        href = a.get("href", "")
        for domain, name in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                social_links[name] = href
    if social_links:
        business["Social Media Links"] = social_links

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email
    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()
    if not business["Business Email"] and about_text:
        email_match = _EMAIL_RE.search(about_text)
        if email_match:
            business["Business Email"] = email_match.group(0)

    # ---- GBP Link (scoped to the "Get Directions" anchor) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    return business


