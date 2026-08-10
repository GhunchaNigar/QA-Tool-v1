"""
Site parser: golocalezservices.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_GOLOCALEZ_ADDRESS_RE = re.compile(
    r"^(?P<street>.+),\s*(?P<city>[^,]+),\s*"
    r"(?P<state>[A-Za-z][A-Za-z .]*?)\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _golocalez_jsonld_local_business(soup):
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


def parse_golocalezservices(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _golocalez_jsonld_local_business(soup) or {}
    page_domain = urlparse(url).netloc.lower().replace("www.", "")

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

    # ---- Phone (hidden span, revealed by JS -- no tel: anchor here) ----
    phone_span = soup.select_one(".phone_number")
    if phone_span:
        phone_text = clean(phone_span.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    if not business["Phone"] and ld_business.get("telephone"):
        business["Phone"] = clean(ld_business["telephone"])

    # ---- About block (source of both Website URL and Description) ----
    about = soup.select_one(".textarea.textarea-about_me")

    # ---- Website URL ----
    if about:
        for anchor in about.select("a[href]"):
            href = anchor["href"].strip()
            if not href.lower().startswith(("http://", "https://")):
                continue
            if _hostname_matches_social_domain(href, page_domain):
                continue
            business["Website URL"] = href
            break

    # ---- Description  ----
    if about:
        desc_text = clean_multiline(about.get_text(separator="\n"))
        lines = [
            line for line in desc_text.split("\n")
            if line.strip().lower() not in ("website:", "about us:")
            and line.strip() != business["Website URL"]
        ]
        desc_text = "\n".join(lines).strip()
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"] and ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Address (single unstructured string -> Street/City/State/Zip) ----
    addr_span = soup.select_one(".overview-tab-the-member-address .col-sm-8 span")
    addr_text = clean(addr_span.get_text()) if addr_span else ""

    match = _GOLOCALEZ_ADDRESS_RE.match(addr_text) if addr_text else None
    if match:
        business["Street"] = clean(match.group("street"))
        business["City"] = clean(match.group("city"))
        business["State"] = clean(match.group("state"))
        business["Zipcode"] = match.group("zip")
    elif addr_text:
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

    # ---- GBP Link  ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    if not business["GBP Link"]:
        location = ld_business.get("location")
        if isinstance(location, dict) and location.get("hasMap"):
            business["GBP Link"] = location["hasMap"]

    return business


