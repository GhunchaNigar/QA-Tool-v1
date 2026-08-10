"""
Site parser: yplocal.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_YPLOCAL_ADDRESS_RE = re.compile(
    r"^(?P<street>.+),\s*(?P<city>[^,]+),\s*"
    r"(?P<state>[A-Za-z][A-Za-z .]*?)\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _yplocal_jsonld_local_business(soup):
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


def parse_yplocal(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _yplocal_jsonld_local_business(soup) or {}

    # ---- Business Name ----
    if ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    if not business["Business Name"]:
        h1 = soup.select_one("h1.bold.inline-block")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        company = soup.select_one(".table-display-company .textbox-company")
        if company:
            business["Business Name"] = clean(company.get_text())

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

    # ---- Keywords (published under the "SERVICES" row) ----
    for row in soup.select(".table-view-group"):
        label = row.select_one(".col-sm-4")
        value = row.select_one(".col-sm-8")
        if label and value and clean(label.get_text()).lower() == "services":
            kw_text = clean(value.get_text())
            if is_meaningful(kw_text):
                business["Keywords"] = kw_text
            break

    # ---- Address (single unstructured string -> Street/City/State/Zip) ----
    # The address container holds multiple sibling <span> elements (e.g.
    # <span>Plano TX</span>, <span>75023</span>) -- selecting just the
    # first <span> drops everything after it (the zip code). Pull the
    # whole container's text instead.
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    addr_text = clean(addr_container.get_text()) if addr_container else ""

    match = _YPLOCAL_ADDRESS_RE.match(addr_text) if addr_text else None
    if match:
        business["Street"] = clean(match.group("street"))
        business["City"] = clean(match.group("city"))
        business["State"] = clean(match.group("state"))
        business["Zipcode"] = match.group("zip")
    else:
        # No street on file -- the address is just "City ST, Zipcode"
        # (e.g. "Plano TX, 75023"), so split the "City ST" chunk from
        # the trailing zip, then split city from the 2-letter state.
        city_state_zip = re.match(
            r"^(?P<city_state>.+?),\s*(?P<zip>\d{5}(?:-\d{4})?)$", addr_text
        ) if addr_text else None
        if city_state_zip:
            city_state = city_state_zip.group("city_state").strip()
            business["Zipcode"] = city_state_zip.group("zip")
            cs_match = re.match(r"^(.*?)\s+([A-Z]{2})$", city_state)
            if cs_match:
                business["City"] = cs_match.group(1).strip()
                business["State"] = cs_match.group(2)
            else:
                business["City"] = city_state
        elif addr_text:
            # Fall back to storing the raw string as Street rather than
            # dropping the address entirely if it doesn't match any of
            # the expected shapes.
            business["Street"] = addr_text

    # ---- Country  ----
    addr_obj = ld_business.get("address")
    if isinstance(addr_obj, dict):
        country = clean(addr_obj.get("addressCountry", ""))
        if country and country.upper() != "N/A":
            business["Country"] = country

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

    # ---- Logo ----
    logo_img = soup.select_one(".profile-image img[src]")
    if logo_img:
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"] and ld_business.get("image"):
        image = ld_business["image"]
        image_url = image.get("url") if isinstance(image, dict) else image
        if image_url:
            business["Logo"] = urljoin(url, image_url)

    # ---- Business Email (opportunistic; not every listing publishes one) ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email

    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Social Media Links / Website fallback (JSON-LD sameAs) ----
    same_as = ld_business.get("sameAs")
    same_as = same_as if isinstance(same_as, list) else ([same_as] if same_as else [])
    for href in same_as:
        if not isinstance(href, str) or not href.startswith("http"):
            continue
        if "yplocal.com" in href.lower():
            continue
        matched_social = False
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href
                matched_social = True
                break
        if not matched_social and not business["Website URL"]:
            business["Website URL"] = href

    # ---- GBP Link  ----
    directions = soup.select_one("a.member-directions[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    if not business["GBP Link"]:
        location = ld_business.get("location")
        if isinstance(location, dict) and location.get("hasMap"):
            business["GBP Link"] = location["hasMap"]

    return business


