"""
Site parser: fyple.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _fyple_label_value(soup, label_text):
    """fyple's Contact rows are a flat two-column layout:
        <div class="row">
            <div class="col-xs-12 col-sm-5"><strong>LABEL:</strong></div>
            <div class="col-xs-12 col-sm-7">VALUE</div>
        </div>
    The value div is a sibling of the LABEL div (which itself wraps
    the <strong>), not of the <strong> tag directly -- so this steps
    up to the label's parent before looking for the next sibling.
    """
    for strong in soup.find_all("strong"):
        if clean(strong.get_text()).rstrip(":").lower() == label_text.lower():
            label_cell = strong.parent
            value_cell = label_cell.find_next_sibling("div") if label_cell else None
            if value_cell:
                return clean(value_cell.get_text(separator=" "))
    return ""


def _fyple_section_heading(soup, heading_text):
    """Returns the <h3 class="comp_section_title"> tag whose text
    matches heading_text exactly (case-insensitive), or None."""
    for h3 in soup.find_all("h3"):
        if clean(h3.get_text()).lower() == heading_text.lower():
            return h3
    return None


def parse_fyple(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name  ----
    name_tag = soup.select_one('[itemtype*="LocalBusiness"] h1[itemprop="name"]')
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"]).split(" in ")[0].strip()

    # ---- Address  ----
    addr = soup.select_one('[itemprop="address"][itemtype*="PostalAddress"]')
    if addr:
        street = addr.find("span", itemprop="streetAddress")
        city = addr.find("span", itemprop="addressLocality")
        zipcode = addr.find("span", itemprop="postalCode")
        state = addr.find("span", itemprop="addressRegion")
        country = addr.find("span", itemprop="addressCountry")

        if street:
            business["Street"] = clean(street.get_text())
        if city:
            business["City"] = clean(city.get_text())
        if zipcode:
            business["Zipcode"] = clean(zipcode.get_text())
        if state:
            business["State"] = clean(state.get_text())
        if country:
            business["Country"] = clean(country.get_text())

    # ---- Phone number ("Phone number:" label/value row) ----
    phone = _fyple_label_value(soup, "Phone number")
    if phone:
        business["Phone"] = phone

    # ---- Hours ----
    hours_container = soup.find("div", id="OpenHoursCollapse")
    if hours_container:
        cells = [clean(c.get_text()) for c in hours_container.find_all("div", recursive=False)]
        cells = [c for c in cells if c]
        pairs = [f"{cells[i]}: {cells[i + 1]}" for i in range(0, len(cells) - 1, 2)]
        hours_text = "; ".join(pairs)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category ----
    cat_heading = _fyple_section_heading(soup, "Categories")
    if cat_heading:
        cat_container = cat_heading.find_next("div", class_="comp_wrap")
        if cat_container:
            cat_links = [clean(a.get_text()) for a in cat_container.find_all("a")]
            cat_links = [c for c in cat_links if c]
            if cat_links:
                business["Category"] = " > ".join(cat_links)

    # ---- Description  ----
    desc_heading = _fyple_section_heading(soup, "Company description")
    if desc_heading and desc_heading.parent:
        desc_copy = BeautifulSoup(str(desc_heading.parent), "lxml")
        heading_copy = desc_copy.find("h3")
        if heading_copy:
            heading_copy.decompose()
        desc_text = clean(desc_copy.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Photos + Logo ----
    photos = []
    logo_found = ""
    for a in soup.select('a[data-lightbox="images"][href]'):
        href = urljoin(url, a["href"])
        if not logo_found and re.search(r"logo", href, re.I):
            logo_found = href
        else:
            photos.append(href)

    if logo_found:
        business["Logo"] = logo_found
    business["Photos"] = photos

    # ---- Website URL / Business Email (same label/value row shape as Phone) ----
    website = _fyple_label_value(soup, "Website")
    if website:
        business["Website URL"] = website

    email = _fyple_label_value(soup, "Email address") or _fyple_label_value(soup, "Email")
    if email:
        business["Business Email"] = email

    # ---- Keywords (Tags section, same shape as Categories) ----
    kw_heading = _fyple_section_heading(soup, "Tags") or _fyple_section_heading(soup, "Keywords")
    if kw_heading:
        kw_container = kw_heading.find_next("div", class_="comp_wrap")
        if kw_container:
            kw_links = [clean(a.get_text()) for a in kw_container.find_all("a")]
            kw_links = [k for k in kw_links if k]
            if kw_links:
                business["Keywords"] = ", ".join(kw_links)

    # ---- Social Media Links / GBP Link ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "fyple.com" in href.lower():
            continue
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


