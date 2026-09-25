"""
Site parser: earthmom.org
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_EARTHMOM_LABEL_MAP = {
    "phone": "Phone",
    "website": "Website URL",
}

_EARTHMOM_ABOUT_HEADINGS = {"about us", "about", "about company", "about the company"}


def _parse_earthmom_about_block(container):
    result = {}
    description_lines = []

    paragraphs = [clean(p.get_text(separator=" ")) for p in container.find_all("p")]
    n = len(paragraphs)

    i = 0
    while i < n:
        text = paragraphs[i]
        if not text:
            i += 1
            continue

        label_key = text.rstrip(":").strip().lower()

        if label_key in _EARTHMOM_LABEL_MAP and i + 1 < n and paragraphs[i + 1]:
            result[_EARTHMOM_LABEL_MAP[label_key]] = paragraphs[i + 1]
            i += 2
            continue

        if label_key in _EARTHMOM_ABOUT_HEADINGS:
            i += 1
            continue

        description_lines.append(text)
        i += 1

    if description_lines:
        result["Description"] = "\n".join(description_lines)

    return result


def parse_earthmom(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (visible <h1>, falls back to og:title split on
    #      " on " since the template renders it as "<Name> on Earth Mom") ----
    h1 = soup.select_one(".header-member-name h1") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"]).split(" on ")[0].strip()

    # ---- Category (short line directly under the name) ----
    category_tag = soup.select_one(".profile-header-top-category")
    if category_tag:
        business["Category"] = clean(category_tag.get_text())

    # ---- Address ----
    # This template usually gives a plain streetAddress itemprop, but on
    # profile pages like this one there's no street at all -- instead
    # addressLocality holds a merged "City ST" string (e.g. "Plano TX")
    # and postalCode is a separate sibling span. Fall back to combining
    # those into a single "City ST Zip" string the comma-less splitter
    # can parse correctly, the same fix applied for milestones.business.
    address_tag = soup.select_one('[itemprop="streetAddress"]')
    if address_tag:
        address_text = clean(address_tag.get_text(separator=" "))
        if address_text:
            street, city, state, zipcode = _split_blinx_address(address_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode
    else:
        locality_tag = soup.select_one('[itemprop="addressLocality"]')
        if locality_tag:
            locality_text = clean(locality_tag.get_text())
            zip_tag = soup.select_one('[itemprop="postalCode"]')
            zip_text = clean(zip_tag.get_text()) if zip_tag else ""
            combined = f"{locality_text} {zip_text}".strip()
            if combined:
                street, city, state, zipcode = _split_city_state_zip_address(combined)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode or zip_text

    # ---- Phone / Website / Business Email / Description ----
    about_container = soup.select_one(".overview-tab-about-me .textarea-about_me")
    if about_container:
        about_fields = _parse_earthmom_about_block(about_container)
        for field, value in about_fields.items():
            if is_meaningful(value):
                business[field] = value

    # ---- Description fallback (meta description, SEO-truncated) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Phone fallback (tel: link, if the free-form block didn't
    #      have one) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Country (same itemprop convention as the street address) ----
    country_tag = soup.select_one('[itemprop="addressCountry"]')
    if country_tag:
        business["Country"] = clean(country_tag.get_text())

    # ---- Country fallback: bare text node after the <br> ----
    # On pages like this one there's no addressCountry itemprop at all --
    # the country is just plain text sitting directly in the address
    # container after a <br>, following the addressLocality/postalCode
    # spans (e.g. "...75023<br />United States of America"). Pull the
    # last meaningful direct text node out of that container instead.
    if not business["Country"]:
        address_container = soup.select_one('[itemprop="address"]')
        if address_container:
            text_nodes = [
                clean(str(node)) for node in address_container.contents
                if isinstance(node, NavigableString)
            ]
            text_nodes = [t for t in text_nodes if is_meaningful(t)]
            if text_nodes:
                business["Country"] = text_nodes[-1]

    # ---- Hours ----
    hours_tag = soup.select_one('[itemprop="openingHours"]') or soup.select_one(".business-hours")
    if hours_tag:
        hours_text = clean(hours_tag.get_text(separator=" "))
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Social Media / GBP Link (external anchors, scanned like the
    #      other site parsers) ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "earthmom.org" in href.lower():
            continue
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    else:
        profile_img = soup.select_one(".profile-image img")
        if profile_img and profile_img.get("src"):
            business["Logo"] = urljoin(url, profile_img["src"])

    return business


