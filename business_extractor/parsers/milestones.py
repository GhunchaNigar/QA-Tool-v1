"""
Site parser: milestones.business
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_milestones(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    content = soup.select_one(".acadp-listing .col-md-8") or soup

    # ---- Business Name ----
    h1 = content.select_one("h1.acadp-no-margin") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Description  ----
    for p in content.find_all("p", recursive=False):
        if p.select_one("img"):
            continue
        text = clean(p.get_text())
        if is_meaningful(text):
            business["Description"] = text
            break

    # ---- Category  ----
    cat_link = content.select_one(".acadp-post-title a[href*='/listing-category/']")
    if cat_link:
        cat_text = clean(cat_link.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Address  ----
    # This theme renders the address as "Plano TX" in a span, with the
    # zipcode as a bare, un-wrapped text node elsewhere in the same <p>
    # (after the country/delimiter spans) -- it's never inside any
    # selectable element on its own. Grab that stray text node here and
    # stitch it back onto the "City ST" span text before splitting, so
    # we hand the comma-less splitter a normal "Plano TX 75023" string
    # instead of losing the zip entirely.
    addr_span = soup.select_one("span.acadp-street-address")
    if addr_span:
        addr_text = clean(addr_span.get_text())

        zip_text = ""
        addr_p = soup.select_one("p.acadp-address")
        if addr_p:
            for node in addr_p.contents:
                if isinstance(node, NavigableString):
                    candidate = clean(str(node))
                    if re.match(r"^\d{5}(?:-\d{4})?$", candidate):
                        zip_text = candidate
                        break

        if addr_text:
            combined = f"{addr_text} {zip_text}".strip()
            street, city, state, zipcode = _split_city_state_zip_address(combined)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode or zip_text

    # ---- Country ----
    country_span = soup.select_one("span.acadp-country-name")
    if country_span:
        country_text = clean(country_span.get_text())
        if is_meaningful(country_text):
            business["Country"] = country_text

    # ---- Phone  ----
    phone_span = soup.select_one("span.acadp-phone")
    if phone_span:
        phone_copy = BeautifulSoup(str(phone_span), "lxml")
        icon = phone_copy.find(class_=lambda c: c and "glyphicon" in c)
        if icon:
            icon.decompose()
        phone_text = clean(phone_copy.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Website URL ----
    site_link = soup.select_one("span.acadp-website a[href]")
    if site_link and site_link.get("href"):
        business["Website URL"] = site_link["href"]

    # ---- Logo  ----
    logo_img = content.select_one("p > img[src]")
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"]:
        meta_img = soup.select_one("[itemprop='image'] meta[itemprop='url']")
        if meta_img and meta_img.get("content"):
            business["Logo"] = urljoin(url, meta_img["content"])

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


