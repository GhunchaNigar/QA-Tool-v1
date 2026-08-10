"""
Site parser: gravitysplash.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def _gravitysplash_sidebar_value(soup, li_class):

    li = soup.select_one(f"li.{li_class}")
    if not li:
        return None
    spans = li.find_all("span")
    if not spans:
        return None
    return clean(spans[-1].get_text())


def parse_gravitysplash(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".post-meta-left-box h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Category ----
    breadcrumb_links = soup.select(".breadcrumbs li a")
    if len(breadcrumb_links) >= 2:
        business["Category"] = clean(breadcrumb_links[1].get_text())

    # ---- Description (full write-up) ----
    desc_container = soup.select_one(".post-detail-content")
    if desc_container:
        desc_text = clean(desc_container.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Address ----
    address_text = _gravitysplash_sidebar_value(soup, "lp-details-address")
    if address_text:
        # gravitysplash renders a plain comma-free "City ST Zipcode" string
        # (e.g. "Plano TX 75023"), not "street, city, state zip". Feeding
        # that into _split_blinx_address() mis-splits it into
        # state="Plano TX", zipcode="75023", city="" -- use the helper
        # built for exactly this shape instead.
        street, city, state, zipcode = _split_city_state_zip_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone  ----
    phone_link = soup.select_one("li.lp-listing-phone a[href^='tel:']")
    if phone_link:
        business["Phone"] = phone_link["href"].replace("tel:", "").strip()
    else:
        phone_text = _gravitysplash_sidebar_value(soup, "lp-listing-phone")
        if phone_text:
            business["Phone"] = phone_text

    # ---- Website URL ----
    website_link = soup.select_one("li.lp-user-web a[href]")
    if website_link:
        business["Website URL"] = website_link["href"]

    # ---- Social Media Links ----
    contact_list = None
    for li_class in ("lp-user-web", "lp-listing-phone", "lp-details-address"):
        anchor_li = soup.select_one(f"li.{li_class}")
        if anchor_li:
            contact_list = anchor_li.find_parent("ul")
            if contact_list:
                break

    if contact_list:
        social_list = contact_list.find_next_sibling("ul")
        if social_list:
            for a in social_list.find_all("a", href=True):
                href = a["href"]
                for domain, network in SOCIAL_DOMAINS.items():
                    if domain in href.lower():
                        business["Social Media Links"][network] = href

    # ---- Fallbacks from the embedded LocalBusiness JSON-LD, only for
    #      whichever fields the sidebar didn't already fill in ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("@type") != "LocalBusiness":
            continue
        if not business["Business Name"] and data.get("name"):
            business["Business Name"] = data["name"]
        if not business["Phone"] and data.get("telephone"):
            business["Phone"] = data["telephone"]
        break

    return business


