"""
Site parser: bestdealfinder.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_bestdealfinder(url, html):

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
        company_el = soup.select_one(".table-display-company .textbox-company")
        if company_el:
            business["Business Name"] = clean(company_el.get_text())

    # ---- Address (split across individual <span> elements: street, city,
    # state, zip, with a trailing plain-text country after the final <br>,
    # same layout as findabusinesspro.com) ----
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_container:
        addr_spans = addr_container.find_all("span", recursive=False)
        if len(addr_spans) >= 4:
            business["Street"] = clean(addr_spans[0].get_text())
            business["City"] = clean(addr_spans[1].get_text())
            business["State"] = clean(addr_spans[2].get_text())
            business["Zipcode"] = clean(addr_spans[3].get_text())
        elif len(addr_spans) == 3:
            # Some listings (e.g. this one) render only City/State/Zip with
            # NO street span at all: <span>Plano</span>, <span>Texas</span>,
            # <span>75023</span>. Treating spans[0] as "Street" here (as the
            # >=4 branch does) would put "Plano" in Street and leave City
            # permanently blank -- shift the mapping down by one instead.
            business["City"] = clean(addr_spans[0].get_text())
            business["State"] = clean(addr_spans[1].get_text())
            business["Zipcode"] = clean(addr_spans[2].get_text())
        elif not business["Street"]:
            addr_text = clean(addr_container.get_text())
            if is_meaningful(addr_text):
                business["Street"] = addr_text

        trailing_text_nodes = [
            clean(node) for node in addr_container.contents
            if isinstance(node, NavigableString) and clean(node) and clean(node) != ","
        ]
        if trailing_text_nodes:
            country_text = trailing_text_nodes[-1]
            if country_text:
                business["Country"] = country_text

    # ---- Country fallback (LocalBusiness JSON-LD) ----
    if not business["Country"]:
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string, strict=False)
            except Exception:
                continue
            graph = data.get("@graph", [data]) if isinstance(data, dict) else data
            if not isinstance(graph, list):
                continue
            for node in graph:
                if not isinstance(node, dict) or node.get("@type") != "LocalBusiness":
                    continue
                country = clean(node.get("address", {}).get("addressCountry", ""))
                if country and country.upper() != "N/A":
                    business["Country"] = country
                break
            if business["Country"]:
                break

    # ---- Phone (dedicated labeled row, not embedded in the About block) ----
    phone_el = soup.select_one(".table-display-phone .col-sm-8 span") \
        or soup.select_one(".table-display-phone span")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Website URL (dedicated labeled row, not embedded in the About
    # block) ----
    website_el = soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Description (the About block on this skin holds only plain
    # paragraph text -- no "Phone:"/"Website:" label pairs to strip out,
    # since those fields have their own dedicated rows above) ----
    about_el = soup.select_one(".froala-data.field-about_me")
    if about_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in about_el.find_all("p") if clean(p.get_text())
        ]
        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email (opportunistic; not every listing publishes one) ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email
    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- GBP Link (scoped to the "Get Directions" anchor, not a page-wide
    # scan -- the footer on this template carries the directory's own
    # unrelated social/contact links) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    return business


