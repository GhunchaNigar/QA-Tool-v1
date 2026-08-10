"""
Site parser: americansearch.info
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_americansearch(url, html):
    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name -- 
    h1 = soup.select_one("div.header-member-name h1.bold")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(re.sub(r"\s+on\s+AMERICAN SEARCH\s*$", "", og_title["content"], flags=re.I))

    # ---- Address --
    # Most listings on this site have no dedicated streetAddress element;
    # the address only appears as a PostalAddress microdata block with
    # addressLocality (e.g. "Plano TX") and postalCode as siblings, plus a
    # trailing country line. Try streetAddress first for the rare listing
    # that has one, then fall back to parsing the address block directly.
    addr_tag = soup.select_one('[itemprop="streetAddress"]')
    if addr_tag:
        street, city, state, zipcode = _split_blinx_address(clean(addr_tag.get_text()))
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode
    else:
        addr_block = soup.select_one('[itemprop="address"][itemtype*="PostalAddress"]')
        if addr_block:
            locality_tag = addr_block.select_one('[itemprop="addressLocality"]')
            zip_tag = addr_block.select_one('[itemprop="postalCode"]')
            if locality_tag:
                locality_text = clean(locality_tag.get_text())
                match = re.match(r"^(.*?)\s+([A-Z]{2})$", locality_text)
                if match:
                    business["City"] = match.group(1).strip()
                    business["State"] = match.group(2)
                else:
                    business["City"] = locality_text
            if zip_tag:
                business["Zipcode"] = clean(zip_tag.get_text())

    # ---- Country  ----
    crumbs = [clean(s.get_text()) for s in soup.select('ol.breadcrumb span[itemprop="name"]')]
    if len(crumbs) >= 3:
        business["Country"] = crumbs[1]

    # ---- Category  ----
    if len(crumbs) >= 3:
        business["Category"] = crumbs[2]
    if not business["Category"]:
        cat_tag = soup.select_one("span.profile-header-top-category")
        if cat_tag:
            business["Category"] = clean(cat_tag.get_text())

    # ---- Phone ----
    phone_tag = soup.select_one('[itemprop="telephone"]')
    if phone_tag:
        business["Phone"] = clean(phone_tag.get_text())

    # ---- Website URL ----
    site_link = soup.select_one('a.weblink[itemprop="url"]')
    if site_link and site_link.get("href"):
        business["Website URL"] = clean(site_link["href"])

    # ---- Description ("About my Business" free-text block) ----
    about_tag = soup.select_one("span.textarea.textarea-about_me")
    if about_tag:
        business["Description"] = clean(about_tag.get_text())

    # ---- Logo----
    logo_tag = soup.select_one("div.profile-image img.img-rounded")
    if logo_tag and logo_tag.get("src"):
        business["Logo"] = urljoin(url, logo_tag["src"])

    return business


