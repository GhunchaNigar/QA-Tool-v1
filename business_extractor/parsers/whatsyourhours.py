"""
Site parser: whatsyourhours.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _whatsyourhours_field(soup, field_name):
    """Text of the value span inside a div.table-display-<field_name> row,
    or "" if that row isn't present on this listing."""
    el = soup.select_one(f".table-display-{field_name} .col-sm-8 span")
    return clean(el.get_text()) if el else ""


def parse_whatsyourhours(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"])

    # ---- Owner Name (First Name + Last Name rows combined) ----
    first_name = _whatsyourhours_field(soup, "first_name")
    last_name = _whatsyourhours_field(soup, "last_name")
    owner_name = " ".join(part for part in [first_name, last_name] if part)
    if owner_name:
        business["Owner Name"] = owner_name

    # ---- Address ----
    business["Street"] = _whatsyourhours_field(soup, "address1")
    business["City"] = _whatsyourhours_field(soup, "city")
    business["State"] = _whatsyourhours_field(soup, "state_ln")
    business["Zipcode"] = _whatsyourhours_field(soup, "zip_code")
    business["Country"] = _whatsyourhours_field(soup, "country_ln")

    # ---- Phone (visible phone-number row, falling back to the
    #      header's click-to-reveal phone span) ----
    phone_el = soup.select_one(".table-display-phone_number .phone")
    if phone_el:
        business["Phone"] = clean(phone_el.get_text())
    if not business["Phone"]:
        phone_header = soup.select_one(".phone_number_header")
        if phone_header:
            business["Phone"] = clean(phone_header.get_text())

    # ---- Business Email ----
    email_el = soup.select_one(".table-display-email .email")
    if email_el:
        business["Business Email"] = clean(email_el.get_text())

    # ---- Website URL ----
    website_el = soup.select_one(".table-display-website a[href]")
    if website_el:
        business["Website URL"] = website_el["href"]

    # ---- Description ("Write About You And Your Company" textarea,
    #      one paragraph per line) ----
    about_el = soup.select_one(".table-display-about_me .textarea")
    if about_el:
        paragraphs = [clean(p.get_text()) for p in about_el.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        if paragraphs:
            business["Description"] = "\n".join(paragraphs)

    # ---- Hours ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        business["Hours"] = clean(hours_el.get_text())

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Social Media Links ----
    social_links = {}
    for a in soup.select(".table-display-social_media_links a[href]"):
        href = a.get("href", "")
        for domain, name in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                social_links[name] = href
    if social_links:
        business["Social Media Links"] = social_links

   

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img")
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


