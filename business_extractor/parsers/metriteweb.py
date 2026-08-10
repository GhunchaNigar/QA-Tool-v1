"""
Site parser: metriteweb.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_metriteweb(url, html):
    """metriteweb.com runs the WordPress "Classified Listing" (rtcl)
    plugin's default listing template. Every field lives under
    predictable rtcl-* / listingDetails-* classes."""

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Name ----
    name_el = soup.select_one(".listingDetails-header__heading")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Category ----
    cat_el = soup.select_one("a.listingDetails-header__tag")
    if cat_el:
        cat_text = clean(cat_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Description ----
    desc_el = soup.select_one(".listingDetails-block__des__text")
    if desc_el:
        text = clean(desc_el.get_text(separator=" "))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Street / City / State / Zipcode (the address is the first,
    #      link-less <li> in the "Posted By" info-list -- the other two
    #      <li>s are the phone and website links) ----
    addr_li = soup.select_one(".rtcl-listing-user-info .info-list li")
    if addr_li and not addr_li.find("a"):
        addr_text = clean(addr_li.get_text())
        if addr_text:
            street, city, state, zipcode = _split_address_allow_no_comma(addr_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Phone ----
    phone_link = soup.select_one("a.rtcl-phone-link")
    if phone_link:
        business["Phone"] = clean(phone_link.get_text())

    # ---- Website URL ----
    site_link = soup.select_one("a.rtcl-website-link")
    if site_link and site_link.get("href"):
        business["Website URL"] = urljoin(url, site_link["href"].strip())

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    return business


