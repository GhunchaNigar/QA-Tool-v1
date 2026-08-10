"""
Site parser: yourbizlistings.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_YOURBIZLISTINGS_ADDRESS_RE = re.compile(
    r"^(?P<street>.+),\s*(?P<city>[^,]+),\s*(?P<state>[^,]+),\s*"
    r"(?P<zip>\d{5}(?:-\d{4})?),\s*(?P<country>.+)$"
)


def _split_yourbizlistings_address(text):
    """Splits the "Street, City, State, Zip, Country" line used in both
    the header and the Location/Contacts widget. Falls back to a plain
    positional comma-split if the trailing zip/country don't match the
    expected shape (some listings omit zip or country)."""
    match = _YOURBIZLISTINGS_ADDRESS_RE.match(text)
    if match:
        return (
            clean(match.group("street")),
            clean(match.group("city")),
            clean(match.group("state")),
            match.group("zip"),
            clean(match.group("country")),
        )

    parts = [clean(p) for p in text.split(",") if clean(p)]
    street = parts[0] if len(parts) > 0 else ""
    city = parts[1] if len(parts) > 1 else ""
    state = parts[2] if len(parts) > 2 else ""
    zipcode = parts[3] if len(parts) > 3 else ""
    country = parts[4] if len(parts) > 4 else ""
    return street, city, state, zipcode, country


def _yourbizlistings_jsonld_local_business(soup):
    """Return the LocalBusiness object from the page's JSON-LD @graph
    block (it's nested under a WebPage's mainEntity, not top-level)."""
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
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") == "LocalBusiness":
                return obj
            main_entity = obj.get("mainEntity")
            if isinstance(main_entity, dict) and main_entity.get("@type") == "LocalBusiness":
                return main_entity

    return None


def parse_yourbizlistings(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _yourbizlistings_jsonld_local_business(soup) or {}

    # ---- Business Name ----
    h1 = soup.select_one("h1.listing-title")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"] and ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    # ---- Street / City / State / Zipcode / Country / Phone / Website URL
    #      (Location / Contacts widget -- labels live in a <span>, values
    #      in the sibling <a>) ----
    for li in soup.select(".location-contact-section li"):
        label_el = li.find("span")
        label = clean(label_el.get_text()).lower() if label_el else ""
        value_el = li.find("a")
        value_text = clean(value_el.get_text()) if value_el else ""

        if "address" in label:
            if is_meaningful(value_text):
                (business["Street"], business["City"], business["State"],
                 business["Zipcode"], business["Country"]) = _split_yourbizlistings_address(value_text)
        elif label.startswith("phone"):
            if is_meaningful(value_text):
                business["Phone"] = value_text
        elif label.startswith("website"):
            if value_el and value_el.get("href"):
                business["Website URL"] = value_el["href"].strip()
            elif is_meaningful(value_text):
                business["Website URL"] = value_text

    # ---- Address fallback (JSON-LD) ----
    if not business["Street"]:
        addr_obj = ld_business.get("address")
        if isinstance(addr_obj, dict):
            business["Street"] = clean(addr_obj.get("streetAddress", ""))
            business["City"] = clean(addr_obj.get("addressLocality", ""))
            business["State"] = clean(addr_obj.get("addressRegion", ""))
            business["Zipcode"] = clean(addr_obj.get("postalCode", ""))
            business["Country"] = clean(addr_obj.get("addressCountry", ""))

    # ---- Phone fallback ----
    if not business["Phone"] and ld_business.get("telephone"):
        business["Phone"] = clean(ld_business["telephone"])

    # ---- Website URL fallback ----
    if not business["Website URL"] and ld_business.get("url"):
        business["Website URL"] = clean(ld_business["url"])

    # ---- Business Email (Cloudflare-obfuscated on this template) ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email
    elif ld_business.get("email"):
        business["Business Email"] = clean(ld_business["email"])

    # ---- Description ----
    for title_el in soup.select(".list-single-main-item-title h3"):
        if clean(title_el.get_text()).lower() != "description":
            continue
        item = title_el.find_parent(class_="list-single-main-item")
        if not item:
            continue
        p = item.select_one(".list-single-main-item_content p")
        if p:
            desc_text = clean(p.get_text())
            if is_meaningful(desc_text):
                business["Description"] = desc_text
        break

    if not business["Description"] and ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Hours (rendered twice -- desktop + mobile widgets -- so
    #      de-dupe after collecting) ----
    hours_lines = []
    for day_el in soup.select(".opening-hours-day"):
        li = day_el.find_parent("li")
        if not li:
            continue
        time_el = li.select_one(".opening-hours-time")
        if not time_el:
            continue
        day = clean(day_el.get_text())
        time_text = clean(time_el.get_text())
        if day and time_text:
            line = f"{day}: {time_text}"
            if line not in hours_lines:
                hours_lines.append(line)
    if hours_lines:
        business["Hours"] = "\n".join(hours_lines)

    # ---- Category ----
    category_span = soup.select_one(".listing-item-category-wrap span.text-start")
    if category_span:
        cat_text = clean(category_span.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    if not business["Category"]:
        cat_link = soup.select_one(".list-single-tags a")
        if cat_link:
            cat_text = clean(cat_link.get_text())
            if is_meaningful(cat_text):
                business["Category"] = cat_text

    # ---- Logo ----
    # Listings without an uploaded photo render a text-initials avatar
    # (div.location-logo) instead of an <img>, and og:image/JSON-LD
    # "image" both point at the directory's own generic placeholder
    # logo rather than the business's -- so only a real <img> inside
    # the banner counts as a Logo here.
    logo_img = soup.select_one(".banner-logo-wrapper img[src]")
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    return business


