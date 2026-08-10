"""
Site parser: askmap.net
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _askmap_section_container(soup, header_text):
    for h3 in soup.find_all("h3"):
        if clean(h3.get_text()).lower() == header_text.strip().lower():
            return h3.parent
    return None


def parse_askmap(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (visible <h1>, falls back to og:title) ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"]).split("|")[0].strip()

    # ---- Category ("<b>Category</b>: <span>value</span>" --
    for b_tag in soup.find_all("b"):
        if clean(b_tag.get_text()).lower() == "category":
            value_tag = b_tag.find_next_sibling()
            if value_tag:
                business["Category"] = clean(value_tag.get_text())
            break

    # ---- Address details ----
    address_container = _askmap_section_container(soup, "Address details")
    if address_container:
        address_tag = address_container.find("address")
        if address_tag:
            address_text = clean(address_tag.get_text(separator=" "))
            if address_text:
                street, city, state, zipcode = _split_city_state_zip_address(address_text)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode

    # ---- Phone & WWW ----
    contact_container = _askmap_section_container(soup, "Phone & WWW")
    if contact_container:
        tel = contact_container.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()
        else:
            # Match starts with a digit OR an opening paren, so a
            # parenthesized area code like "(214) 566-1908" isn't
            # truncated to "214) 566-1908" -- the old [\d]-only start
            # skipped straight past the leading "(".
            phone_match = re.search(
                r"[\d(][\d\-.\s()]{6,}\d", clean(contact_container.get_text())
            )
            if phone_match:
                business["Phone"] = clean(phone_match.group())

        for a in contact_container.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            if "askmap.net" in href.lower():
                continue
            if any(domain in href.lower() for domain in SOCIAL_DOMAINS):
                continue
            business["Website URL"] = href
            break

    # ---- Business hours (own <div>; blank for many listings -- 
    hours_container = _askmap_section_container(soup, "Business hours")
    if hours_container:
        hours_copy = BeautifulSoup(str(hours_container), "lxml")
        heading = hours_copy.find("h3")
        if heading:
            heading.decompose()
        pieces = [clean(s) for s in hours_copy.find_all(string=True)]
        pieces = [p for p in pieces if p]
        hours_text = "; ".join(pieces)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Description ----
    info_container = _askmap_section_container(soup, "Info")
    if info_container:
        info_copy = BeautifulSoup(str(info_container), "lxml")
        heading = info_copy.find("h3")
        if heading:
            heading.decompose()
        desc_text = clean(info_copy.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords (meta keywords tag) ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)

    # ---- Logo (og:image -- matches the listing logo shown top-left) ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    return business


