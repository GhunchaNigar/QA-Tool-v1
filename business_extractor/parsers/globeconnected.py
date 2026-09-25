"""
Site parser: globeconnected.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _globeconnected_jsonld(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "LocalBusiness":
            return data
    return {}


def parse_globeconnected(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    jsonld = _globeconnected_jsonld(soup)

    # ---- Business Name ----
    h1 = soup.select_one(".result-content h1") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and jsonld.get("name"):
        business["Business Name"] = clean(jsonld["name"])

    # ---- Address  ----
    addr_tag = soup.select_one("p.address")
    addr_text = clean(addr_tag.get_text()) if addr_tag else ""
    if not addr_text:
        addr_obj = jsonld.get("address")
        if isinstance(addr_obj, dict) and addr_obj.get("streetAddress"):
            addr_text = clean(addr_obj["streetAddress"])

    if addr_text:
        street, city, state, zipcode = _split_blinx_address(addr_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Country (JSON-LD only; not rendered anywhere on the page) ----
    addr_obj = jsonld.get("address")
    if isinstance(addr_obj, dict) and addr_obj.get("addressCountry"):
        business["Country"] = clean(addr_obj["addressCountry"])

    # ---- Phone ----
    tel = soup.select_one("p.phone a[href^='tel:']")
    if tel and tel.get("href"):
        business["Phone"] = tel["href"].replace("tel:", "").strip()
    if not business["Phone"] and jsonld.get("telephone"):
        business["Phone"] = clean(jsonld["telephone"])

    # ---- Website URL (the business's own external site, not this
    #      directory listing) ----
    site_link = soup.select_one("a.web[href]")
    if site_link and site_link.get("href"):
        business["Website URL"] = site_link["href"]
    if not business["Website URL"] and jsonld.get("url"):
        business["Website URL"] = jsonld["url"]

    # ---- Business Email (Cloudflare-obfuscated on the page; plain in
    #      JSON-LD as a fallback) ----
    email = _find_cf_email(soup)
    if email:
        business["Business Email"] = email
    if not business["Business Email"] and jsonld.get("email"):
        business["Business Email"] = clean(jsonld["email"])

    # ---- Description ("About" section, heading stripped) ----
    desc_tag = soup.select_one("section.description")
    if desc_tag:
        desc_copy = BeautifulSoup(str(desc_tag), "lxml")
        heading = desc_copy.find("h5")
        if heading:
            heading.decompose()
        desc_text = clean(desc_copy.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"] and jsonld.get("description"):
        desc_text = clean(jsonld["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Category (p.cats link list) ----
    cat_links = [clean(a.get_text()) for a in soup.select("p.cats a")]
    cat_links = [c for c in cat_links if c]
    if cat_links:
        business["Category"] = ", ".join(cat_links)

    # ---- Logo (JSON-LD "image" is the business's own logo; og:image on
    #      this template is the directory site's own logo, so it's only
    #      used as a last-resort fallback) ----
    if jsonld.get("image"):
        business["Logo"] = urljoin(url, jsonld["image"])

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


