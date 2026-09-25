"""
Site parser: bpublic.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _bpublic_field(soup, field_name):
    """Text of the value span/cell inside a div.table-display-<field_name>
    row, or "" if that row isn't present on this listing."""
    el = soup.select_one(f".table-display-{field_name} .col-sm-8 span") \
        or soup.select_one(f".table-display-{field_name} .col-sm-8")
    return clean(el.get_text()) if el else ""


def _bpublic_clean_value(text):
    text = clean(text)
    if not text or text.upper() == "N/A":
        return ""
    return text


def _bpublic_normalize_label(text):
    """Normalize an About-box label paragraph for matching: strip a
    trailing colon (some listings write "Address", others "Address:")
    and lowercase (some write "About Us:", others "About us:")."""
    return clean(text).rstrip(":").strip().lower()


_BPUBLIC_ADDRESS_LABEL = "address"
_BPUBLIC_PHONE_LABEL = "phone"
_BPUBLIC_WEBSITE_LABEL = "website"
_BPUBLIC_ABOUT_LABEL = "about us"
_BPUBLIC_ABOUT_LABELS = {
    _BPUBLIC_ADDRESS_LABEL, _BPUBLIC_PHONE_LABEL,
    _BPUBLIC_WEBSITE_LABEL, _BPUBLIC_ABOUT_LABEL,
}


def _parse_bpublic_about_block(business, about_el, url):
    """Some bPUBLIC listings (e.g. "Focal") don't fill in the structured
    address/phone/website rows at all -- instead everything is packed as
    "Label" / value paragraph pairs inside the free-text "About" box.
    Labels appear inconsistently across listings -- with or without a
    trailing colon, and with varying capitalization (e.g. "Address" vs
    "Address:", "About Us:" vs "About us:") -- so match them normalized.
    Pull Address/Phone/Website out of that pattern and treat any
    remaining paragraphs as the Description."""
    paragraphs = about_el.find_all("p")
    desc_parts = []
    i = 0
    while i < len(paragraphs):
        label = _bpublic_normalize_label(paragraphs[i].get_text())

        if label == _BPUBLIC_ADDRESS_LABEL and i + 1 < len(paragraphs):
            address_text = _bpublic_clean_value(paragraphs[i + 1].get_text())
            if address_text and not business["Street"]:
                zip_match = re.search(r"(\d{5}(?:-\d{4})?)\s*$", address_text)
                if zip_match and not business["Zipcode"]:
                    business["Zipcode"] = zip_match.group(1)
                street = address_text
                if business["City"]:
                    idx = address_text.lower().find(business["City"].lower())
                    # idx==0 means the city sits at the very start of the
                    # string (e.g. address_text is just "Plano TX 75023"
                    # with no street portion at all) -- slicing to idx
                    # still correctly yields "" in that case, so this must
                    # be >= 0, not > 0, or that case falls through and
                    # leaves the untouched "City State Zip" string as the
                    # Street value.
                    if idx >= 0:
                        street = address_text[:idx].rstrip(", ").strip()
                business["Street"] = street
            i += 2
            continue

        if label == _BPUBLIC_PHONE_LABEL and i + 1 < len(paragraphs):
            phone_text = _bpublic_clean_value(paragraphs[i + 1].get_text())
            if phone_text and not business["Phone"]:
                business["Phone"] = phone_text
            i += 2
            continue

        if label == _BPUBLIC_WEBSITE_LABEL and i + 1 < len(paragraphs):
            link = paragraphs[i + 1].find("a", href=True)
            website_text = link["href"].strip() if link else _bpublic_clean_value(paragraphs[i + 1].get_text())
            if website_text and not business["Website URL"]:
                business["Website URL"] = urljoin(url, website_text)
            i += 2
            continue

        if label == _BPUBLIC_ABOUT_LABEL:
            i += 1
            continue

        text = clean(paragraphs[i].get_text())
        if is_meaningful(text) and label not in _BPUBLIC_ABOUT_LABELS:
            desc_parts.append(text)
        i += 1

    if desc_parts and not business["Description"]:
        business["Description"] = "\n\n".join(desc_parts)


def parse_bpublic(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    company_el = soup.select_one(".table-display-company .textbox-company")
    if company_el:
        business["Business Name"] = clean(company_el.get_text())
    if not business["Business Name"]:
        h1 = soup.select_one(".header-member-name h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Category (badge under the name, e.g. "Professional Services") ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Structured address rows (when a listing fills them in) ----
    business["Street"] = _bpublic_field(soup, "address1") or _bpublic_field(soup, "street")
    business["City"] = _bpublic_field(soup, "city")
    business["State"] = _bpublic_field(soup, "state_ln") or _bpublic_field(soup, "state")
    business["Zipcode"] = _bpublic_field(soup, "zip_code") or _bpublic_field(soup, "zipcode")
    business["Country"] = _bpublic_field(soup, "country_ln") or _bpublic_field(soup, "country")

    # ---- Country / State / City / Category fallback: breadcrumb trail
    #      (Home > Country > State > City > Category) ----
    crumbs = [clean(s.get_text()) for s in soup.select(".breadcrumb span[itemprop='name']")]
    if crumbs and crumbs[0].lower() == "home":
        crumbs = crumbs[1:]
    if len(crumbs) >= 1 and not business["Country"]:
        business["Country"] = crumbs[0]
    if len(crumbs) >= 2 and not business["State"]:
        business["State"] = crumbs[1]
    if len(crumbs) >= 3 and not business["City"]:
        business["City"] = crumbs[2]
    if len(crumbs) >= 4 and not business["Category"]:
        business["Category"] = crumbs[3]

    # ---- Phone (structured row, tel: link, or reveal-on-click header) ----
    phone_el = soup.select_one(".table-display-phone_number .phone") \
        or soup.select_one(".table-display-phone .phone")
    if phone_el:
        business["Phone"] = clean(phone_el.get_text())
    if not business["Phone"]:
        phone_header = soup.select_one(".phone_number_header")
        if phone_header:
            business["Phone"] = clean(phone_header.get_text())
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = clean(tel["href"].replace("tel:", ""))

    # ---- Website URL (structured row) ----
    website_el = soup.select_one(".table-display-website a[href]") \
        or soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"]

    # ---- Hours (structured row, when a listing has one) ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        business["Hours"] = clean(hours_el.get_text())

    # ---- Description + Address/Phone/Website fallback: the "About" box.
    #      On many listings this is just free-text description, but on
    #      some (e.g. "Focal") it also carries Address/Phone/Website as
    #      label/value paragraph pairs when the structured rows above
    #      were left empty. ----
    about_el = soup.select_one(".table-display-about_me .froala-data") \
        or soup.select_one(".field-about_me")
    if about_el:
        paragraphs = [clean(p.get_text()) for p in about_el.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        has_labels = any(_bpublic_normalize_label(p) in _BPUBLIC_ABOUT_LABELS for p in paragraphs)

        if has_labels:
            _parse_bpublic_about_block(business, about_el, url)
        elif paragraphs:
            business["Description"] = "\n".join(paragraphs)

    if not business["Description"]:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            business["Description"] = clean(meta["content"])

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img")
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


