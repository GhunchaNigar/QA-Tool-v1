"""
Site parser: b2bco.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_b2bco(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name ----
    name_el = soup.select_one("div.business.s-title h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Address (labeled "General Information" section) ----
    for addr_div in soup.select("div.Businessaddress"):
        text = clean(addr_div.get_text())
        if re.match(r"^Address:", text, flags=re.I):
            business["Street"] = re.sub(r"^Address:\s*", "", text, flags=re.I)
            break

    country_el = soup.select_one("div.Businesscountry a")
    if country_el:
        business["Country"] = clean(country_el.get_text())

    state_el = soup.select_one("div.countrypart a")
    if state_el:
        business["State"] = clean(state_el.get_text())

    city_el = soup.select_one("div.businesscity a")
    if city_el:
        business["City"] = clean(city_el.get_text())

    # ---- Phone (tel: link) ----
    phone_el = soup.select_one("div.Businessphone a[href^='tel:']")
    if phone_el:
        business["Phone"] = clean(phone_el.get_text())

    # ---- Website URL  ----
    website_el = soup.select_one("div.Businessweb a")
    if website_el:
        site_text = clean(website_el.get_text())
        if site_text:
            business["Website URL"] = site_text

    # ---- Description  ----
    desc_label = soup.find(string=re.compile(r"Business Summary", re.I))
    if desc_label:
        desc_block = desc_label.find_parent("div")
        if desc_block:
            summary_div = desc_block.find_next_sibling("div", class_="comtext")
            if summary_div:
                desc_text = clean(summary_div.get_text())
                if is_meaningful(desc_text):
                    business["Description"] = desc_text
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ----
    kw_label = soup.find(string=re.compile(r"Business Keywords", re.I))
    if kw_label:
        kw_block = kw_label.find_parent("div")
        if kw_block:
            kw_div = kw_block.find_next_sibling("div", class_="comtext")
            if kw_div:
                kw_text = clean(kw_div.get_text())
                if is_meaningful(kw_text):
                    business["Keywords"] = kw_text
    if not business["Keywords"]:
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw:
            kw_raw = clean(meta_kw.get("content", ""))
            if is_meaningful(kw_raw):
                business["Keywords"] = kw_raw

    # ---- Category  ----
    category_el = soup.select_one("ul.b-activities li a")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Logo (profile header logo image) ----
    logo_el = soup.select_one("div.business.s-title div.logo img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email (mailto: link, if present) ----
    email_el = soup.select_one('a[href^="mailto:"]')
    if email_el:
        business["Business Email"] = email_el["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Hours ----
    hours_label = soup.find(string=re.compile(r"Business Hours", re.I))
    if hours_label:
        hours_block = hours_label.find_parent("div")
        if hours_block:
            hours_div = hours_block.find_next_sibling("div", class_="comtext")
            if hours_div:
                hours_text = clean(hours_div.get_text())
                if is_meaningful(hours_text):
                    business["Hours"] = hours_text

    return business


