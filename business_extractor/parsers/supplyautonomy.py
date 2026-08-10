"""
Site parser: supplyautonomy.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_supplyautonomy(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    name_el = soup.select_one("[itemprop='name']")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Address (schema.org PostalAddress microdata block) ----
    addr_el = soup.select_one("[itemprop='address']")
    if addr_el:
        street_el = addr_el.select_one("[itemprop='streetAddress']")
        city_el = addr_el.select_one("[itemprop='addressLocality']")
        state_el = addr_el.select_one("[itemprop='addressRegion']")
        zip_el = addr_el.select_one("[itemprop='postalCode']")
        country_el = addr_el.select_one("[itemprop='addressCountry']")
        if street_el:
            business["Street"] = clean(street_el.get_text())
        if city_el:
            business["City"] = clean(city_el.get_text())
        if state_el:
            business["State"] = clean(state_el.get_text())
        if zip_el:
            business["Zipcode"] = clean(zip_el.get_text())
        if country_el:
            business["Country"] = clean(country_el.get_text())

    # ---- Phone ----
    phone_el = soup.select_one("[itemprop='telephone']")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Website URL ----
    website_el = soup.select_one("a[itemprop='url']")
    if website_el and website_el.get("href"):
        business["Website URL"] = website_el["href"]

    # ---- Description ----
    desc_el = soup.select_one("#companyDescription")
    if desc_el:
        desc_text = clean(desc_el.get_text())
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Logo (background-image URL embedded in a style attribute,
    # not an <img src="">) ----
    logo_el = soup.select_one("[itemprop='logo']")
    if logo_el and logo_el.get("style"):
        match = re.search(r"url\(([^)]+)\)", logo_el["style"])
        if match:
            business["Logo"] = urljoin(url, match.group(1).strip("'\""))

    # ---- Social Media Links (only icons that lack the "inactive" class,
    # since unset ones are dummy links to the bare platform homepage) ----
    for a in soup.select(".socialMediaLinks a[href]"):
        classes = a.get("class") or []
        if "inactive" in classes:
            continue
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


