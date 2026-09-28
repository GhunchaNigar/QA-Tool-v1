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


# Images this site serves when a company has NOT uploaded its own logo:
# JSON-LD "image" falls back to a shared category icon (e.g.
# /images/cat-jc-logo.png) and og:image to /images/og/default.png. Neither is
# the business's logo, so treating them as one reports "PRESENT" falsely.
_PLACEHOLDER_LOGO_RE = re.compile(
    r"(^|/)(cat-[\w-]*|default[\w-]*|placeholder[\w-]*|no[-_]?(image|logo)[\w-]*)\.(png|jpe?g|gif|webp|svg)$"
    r"|/images/og/",
    re.I,
)


def _is_placeholder_logo(src):
    path = urlparse(src or "").path
    return bool(_PLACEHOLDER_LOGO_RE.search(path))


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

    # ---- Country (rendered as <p class="country">; JSON-LD has no
    #      addressCountry on this template, so it is only a fallback) ----
    country_tag = soup.select_one("p.country")
    if country_tag:
        business["Country"] = clean(country_tag.get_text())
    if not business["Country"]:
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

    # ---- Logo: only the business's own uploaded logo counts. The JSON-LD
    #      "image" is used when it is not a shared placeholder; og:image on
    #      this template is always the directory's own default image, so it
    #      is NOT used as a fallback. ----
    jl_image = jsonld.get("image")
    if isinstance(jl_image, str) and jl_image and not _is_placeholder_logo(jl_image):
        business["Logo"] = urljoin(url, jl_image)

    return business