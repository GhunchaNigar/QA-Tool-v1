"""
Site parser: vymaps.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _vymaps_jsonld(soup):
    """Return the first LocalBusiness JSON-LD object on the page, if any."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") == "LocalBusiness":
                return obj
    return {}


def parse_vymaps(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    jsonld = _vymaps_jsonld(soup)

    # ---- Business Name ----
    h1 = soup.select_one(".profile-cover-content h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and jsonld.get("name"):
        business["Business Name"] = clean(jsonld["name"])

    # ---- Address  ----
    addr_link = soup.select_one("a.listing-address[href]")
    if addr_link:
        addr_text = clean(addr_link.get_text())
        if is_meaningful(addr_text):
            street, city, state, zipcode = _split_blinx_address(addr_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode
        if _is_maps_link(addr_link["href"]):
            business["GBP Link"] = addr_link["href"]

    # ---- Country (JSON-LD only; never rendered as visible page text) ----
    addr_obj = jsonld.get("address")
    if isinstance(addr_obj, dict) and addr_obj.get("addressCountry"):
        business["Country"] = clean(addr_obj["addressCountry"])

    # ---- Phone ----
    tel = soup.select_one('a[href^="tel:"]')
    if tel and tel.get("href"):
        business["Phone"] = tel["href"].replace("tel:", "").strip()
    if not business["Phone"] and jsonld.get("telephone"):
        business["Phone"] = clean(jsonld["telephone"])

    # ---- Website URL ----
    site_link = soup.select_one('a[aria-label="Website"][href]')
    if site_link and site_link.get("href"):
        business["Website URL"] = site_link["href"]
    if not business["Website URL"] and jsonld.get("url"):
        business["Website URL"] = jsonld["url"]

    # ---- Business Email (Cloudflare-obfuscated) ----
    email = _find_cf_email(soup)
    if email:
        business["Business Email"] = email

    # ---- Description & Keywords ----
    about = soup.select_one("div.listing-title-bar")
    if about:
        paragraphs = about.find_all("p", recursive=False)
        for i, p in enumerate(paragraphs):
            text = clean(p.get_text())
            if not is_meaningful(text):
                continue
            tags_match = re.match(r"^Tags\s*:\s*(.*)$", text, flags=re.I)
            if tags_match:
                tags_text = tags_match.group(1).strip()
                if is_meaningful(tags_text):
                    business["Keywords"] = ", ".join(
                        tag.lstrip("#").strip()
                        for tag in tags_text.split()
                        if tag.lstrip("#").strip()
                    )
                continue
            if i == 0:
                continue
            if not business["Description"]:
                business["Description"] = text

    if not business["Description"] and jsonld.get("description"):
        desc_text = clean(jsonld["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Category (single hero badge, not a breadcrumb trail) ----
    cat_tag = soup.select_one("span.category-tag")
    if cat_tag:
        cat_text = clean(cat_tag.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Photos  ----
    photos = []
    for img in soup.select("ul.gallery-list img[src]"):
        if not img.get("src"):
            continue
        src = urljoin(url, img["src"])
        if src not in photos:
            photos.append(src)
    if photos:
        business["Photos"] = photos

    return business


