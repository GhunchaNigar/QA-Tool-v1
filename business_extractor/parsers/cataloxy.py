"""
Site parser: cataloxy.us
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def parse_cataloxy(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:

            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            business["Business Name"] = obj.get("name", business["Business Name"])

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

            addr = obj.get("address", {})
            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            if not business["City"]:
                business["City"] = addr.get("addressLocality", "")
            if not business["State"]:
                business["State"] = addr.get("addressRegion", "")
            if not business["Country"]:
                business["Country"] = addr.get("addressCountry", "")

    # ---- Business Name fallback (visible <h1 class="firms">) ----
    if not business["Business Name"]:
        h1 = soup.select_one("h1.firms")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Address microdata (primary source -- has the zip code) ----
    addr_block = soup.select_one('span[itemprop="address"]')
    if addr_block:
        street = addr_block.select_one('[itemprop="streetAddress"]')
        if street:
            business["Street"] = clean(street.get_text())
        zipcode = addr_block.select_one('[itemprop="postalCode"]')
        if zipcode:
            business["Zipcode"] = clean(zipcode.get_text())
        city = addr_block.select_one('[itemprop="addressLocality"]')
        if city:
            business["City"] = clean(city.get_text())
        state = addr_block.select_one('[itemprop="addressRegion"]')
        if state:
            business["State"] = clean(state.get_text())
        country = addr_block.select_one('[itemprop="addressCountry"]')
        if country:
            business["Country"] = country.get("content") or clean(country.get_text())

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Website URL  ----
    site_link = soup.select_one("a.firmDomain")
    if site_link:
        if site_link.get("title"):
            business["Website URL"] = site_link["title"]
        else:
            business["Website URL"] = clean(site_link.get_text())

    # ---- Business Email ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Description (itemprop="description" paragraph) ----
    desc_el = soup.select_one('[itemprop="description"]')
    if desc_el:
        desc = clean_multiline(desc_el.decode_contents())
        if is_meaningful(desc):
            business["Description"] = desc
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)
    if not business["Keywords"]:
        kw_links = [clean(a.get_text()) for a in soup.select('a[href*="/firms/kw/"]')]
        kw_links = [k for k in kw_links if k]
        if kw_links:
            business["Keywords"] = ", ".join(kw_links)

    # ---- Category ----
    crumb_names = [
        clean(span.get_text())
        for span in soup.select('#top_navigator span[itemprop="name"]')
    ]
    if crumb_names:
        business["Category"] = crumb_names[-1]

    # ---- Logo ----
    logo_el = soup.select_one('span[itemprop="logo"]')
    if logo_el and is_meaningful(logo_el.get_text()):
        business["Logo"] = urljoin(url, clean(logo_el.get_text()))
    if not business["Logo"]:
        logo_img = soup.select_one(".firm-top-panel__logo img[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        logo_img = soup.select_one("img.logo[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "js-native-share" in (a.get("class") or []):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


