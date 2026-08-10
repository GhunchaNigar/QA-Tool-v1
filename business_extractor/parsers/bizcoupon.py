"""
Site parser: bizcoupon.directory
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _bizcoupon_jsonld_local_business(soup):
    """Return the LocalBusiness node from this page's JSON-LD "@graph"
    array, or {} if none is present/parseable. Mirrors the same
    two-block layout used on the meetyourmarkets.com theme (one
    LocalBusiness node, one sitewide Organization/WebSite node)."""
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


def parse_bizcoupon(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _bizcoupon_jsonld_local_business(soup)
    addr_obj = ld_business.get("address", {}) if isinstance(ld_business, dict) else {}

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    # ---- Address (this theme renders street/city/state/zip as separate
    # <span> siblings with no consistent whitespace between them, so the
    # visible DOM text can't be split reliably -- the JSON-LD address
    # block carries the same values cleanly split already) ----
    if isinstance(addr_obj, dict):
        street = clean(addr_obj.get("streetAddress", ""))
        city = clean(addr_obj.get("addressLocality", ""))
        state = clean(addr_obj.get("addressRegion", ""))
        zipcode = clean(addr_obj.get("postalCode", ""))
        country = clean(addr_obj.get("addressCountry", ""))
        if street:
            business["Street"] = street
        if city:
            business["City"] = city
        if state:
            business["State"] = state
        if zipcode:
            business["Zipcode"] = zipcode
        if country:
            business["Country"] = country

    # ---- Phone / Website URL / Description ----
    # This theme has no dedicated phone/website fields on the listing --
    # business owners enter "Phone:", "URL:" and "About us:" as labeled
    # paragraphs inside the free-text About Me block, so those have to be
    # picked out by label rather than by a fixed selector. The JSON-LD
    # "telephone" field is a literal "N/A" placeholder on this theme, so
    # it's never used as a phone source.
    about_el = soup.select_one(".table-display-about_me .field-about_me") \
        or soup.select_one(".field-about_me")
    if about_el:
        p_tags = about_el.find_all("p")
        idx = 0
        n = len(p_tags)
        found_labeled_section = False
        while idx < n:
            label_text = clean(p_tags[idx].get_text()).rstrip(":").strip().lower()
            if label_text == "phone" and idx + 1 < n:
                phone_text = clean(p_tags[idx + 1].get_text())
                if is_meaningful(phone_text):
                    business["Phone"] = phone_text
                found_labeled_section = True
                idx += 2
                continue
            if label_text == "url" and idx + 1 < n:
                link = p_tags[idx + 1].find("a", href=True)
                if link:
                    business["Website URL"] = link["href"].strip()
                else:
                    url_text = clean(p_tags[idx + 1].get_text())
                    if is_meaningful(url_text):
                        business["Website URL"] = url_text
                found_labeled_section = True
                idx += 2
                continue
            if label_text == "about us":
                desc_paragraphs = [
                    clean(p.get_text()) for p in p_tags[idx + 1:] if clean(p.get_text())
                ]
                desc_text = "\n".join(desc_paragraphs)
                if is_meaningful(desc_text):
                    business["Description"] = desc_text
                found_labeled_section = True
                break
            idx += 1

        # Fallback: no "Phone:"/"URL:"/"About us:" labels found -- treat
        # the whole About Me block as a plain description instead.
        if not found_labeled_section:
            desc_text = clean_multiline(str(about_el))
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    if not business["Website URL"]:
        same_as = ld_business.get("sameAs")
        if isinstance(same_as, list) and same_as:
            business["Website URL"] = same_as[0]
        elif isinstance(same_as, str) and same_as:
            business["Website URL"] = same_as

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


