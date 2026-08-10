"""
Site parser: wireanium.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_wireanium(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Address (split across individual <span> elements: normally
    # street, city, state, zip -- but some listings have no street at all,
    # rendering only city/state/zip as 3 spans. The old code only handled
    # the 4-span case and fell back to dumping the ENTIRE container text
    # (including the trailing country) into Street for anything else, e.g.
    # Street="Plano, Texas, 75023United States" with City/State/Zipcode
    # left blank -- so branch on the actual span count instead.
    # A trailing plain-text country follows the final <br>. ----
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_container:
        addr_spans = addr_container.find_all("span", recursive=False)
        if len(addr_spans) >= 4:
            business["Street"] = clean(addr_spans[0].get_text())
            business["City"] = clean(addr_spans[1].get_text())
            business["State"] = clean(addr_spans[2].get_text())
            business["Zipcode"] = clean(addr_spans[3].get_text())
        elif len(addr_spans) == 3:
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

    # ---- Country/Phone fallback (LocalBusiness node inside the page's
    # JSON-LD "@graph" array) ----
    jsonld_local_business = {}
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
            if isinstance(node, dict) and node.get("@type") == "LocalBusiness":
                jsonld_local_business = node
                break
        if jsonld_local_business:
            break

    if not business["Country"]:
        country = clean(jsonld_local_business.get("address", {}).get("addressCountry", ""))
        if country and country.upper() != "N/A":
            business["Country"] = country

    # ---- Phone  ----
    phone_link = soup.select_one(".table-display-phone a[href^='tel:']")
    if phone_link and phone_link.get("href"):
        business["Phone"] = phone_link["href"].replace("tel:", "").strip()
    if not business["Phone"] and jsonld_local_business.get("telephone"):
        business["Phone"] = clean(jsonld_local_business["telephone"])

    # ---- Website URL (dedicated labeled row) ----
    website_el = soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Description (the About block on this skin holds only plain
    # paragraph text -- Phone/Website have their own dedicated rows
    # above) ----
    about_el = soup.select_one(".froala-data.field-about_me")
    if about_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in about_el.find_all("p") if clean(p.get_text())
        ]
        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Hours (opportunistic; not every listing on this source
    # publishes one) ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        hours_text = clean(hours_el.get_text())
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Social Media Links (opportunistic; not every listing on this
    # source publishes any) ----
    social_links = {}
    for a in soup.select(".table-display-social_media_links a[href]"):
        href = a.get("href", "")
        for domain, name in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                social_links[name] = href
    if social_links:
        business["Social Media Links"] = social_links

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

    # ---- GBP Link (scoped to the "Get Directions" anchor) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    return business


