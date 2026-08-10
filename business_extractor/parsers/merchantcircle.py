"""
Site parser: merchantcircle.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py




def parse_merchantcircle(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name  ----
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        business["Business Name"] = clean(og_title["content"])

    if not business["Business Name"]:
        h1 = soup.select_one("h1.business-info-title")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Address  ----
    meta_street = soup.find("meta", property="business:contact_data:street_address")
    if meta_street and meta_street.get("content"):
        business["Street"] = clean(meta_street["content"])

    meta_city = soup.find("meta", property="business:contact_data:locality")
    if meta_city and meta_city.get("content"):
        business["City"] = clean(meta_city["content"])
    if not business["City"]:
        city_tag = soup.select_one('span[itemprop="addressLocality"]')
        if city_tag:
            business["City"] = clean(city_tag.get_text()).rstrip(",")

    state_tag = soup.select_one('span[itemprop="addressRegion"]')
    if state_tag:
        business["State"] = clean(state_tag.get_text())

    meta_zip = soup.find("meta", property="business:contact_data:postal_code")
    if meta_zip and meta_zip.get("content"):
        business["Zipcode"] = clean(meta_zip["content"])
    if not business["Zipcode"]:
        zip_tag = soup.select_one('span[itemprop="postalCode"]')
        if zip_tag:
            business["Zipcode"] = clean(zip_tag.get_text())

    meta_country = soup.find("meta", property="business:contact_data:country_name")
    if meta_country and meta_country.get("content"):
        business["Country"] = clean(meta_country["content"])

    # ---- Phone ----
    meta_phone = soup.find("meta", property="business:contact_data:phone_number")
    if meta_phone and meta_phone.get("content"):
        business["Phone"] = clean(meta_phone["content"])
    if not business["Phone"]:
        phone_tag = soup.select_one('span[itemprop="telephone"]')
        if phone_tag:
            business["Phone"] = clean(phone_tag.get_text())

    # ---- Website URL ----
    meta_website = soup.find("meta", property="business:contact_data:website")
    if meta_website and meta_website.get("content"):
        business["Website URL"] = clean(meta_website["content"])
    if not business["Website URL"]:
        site_link = soup.select_one(".bi-list-item a.bi-list-item-text[href]")
        if site_link:
            business["Website URL"] = site_link["href"]

    # ---- Description  ----
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        desc = clean(og_desc["content"])
        if is_meaningful(desc):
            business["Description"] = desc

    if not business["Description"]:
        desc_tag = soup.select_one("#business-description")
        if desc_tag:
            desc_copy = BeautifulSoup(str(desc_tag), "lxml")
            dots = desc_copy.find("span", class_="dots")
            if dots:
                dots.decompose()
            button = desc_copy.find("button")
            if button:
                button.decompose()
            desc_text = clean(desc_copy.get_text(separator=" "))
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    # ---- Hours  ----
    hours_container = soup.select_one(".listing-location-hours ul")
    if hours_container:
        pairs = []
        for li in hours_container.find_all("li"):
            spans = li.find_all("span")
            if len(spans) < 2:
                continue
            day = clean(spans[0].get_text())
            value = clean(spans[-1].get_text())
            if day:
                pairs.append(f"{day}: {value}")
        hours_text = "; ".join(pairs)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category  ----
    type_container = soup.select_one(".business-info-type")
    if type_container:
        full_text = type_container.get_text(separator=" ")
        if "\u2022" in full_text:
            full_text = full_text.split("\u2022", 1)[1]
        cats = [clean(c) for c in full_text.split(",")]
        cats = [c for c in cats if c]
        if cats:
            business["Category"] = ", ".join(cats)

    # ---- Logo  ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    if not business["Logo"]:
        avatar = soup.select_one(".business-info-avatar img[src]")
        if avatar:
            business["Logo"] = urljoin(url, avatar["src"])

    # ---- Social Media Links / GBP Link ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "merchantcircle.com" in href.lower():
            continue
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


