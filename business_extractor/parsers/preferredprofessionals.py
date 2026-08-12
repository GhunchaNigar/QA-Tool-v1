"""
Site parser: preferredprofessionals.com
"""
from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def parse_preferredprofessionals(url, html):
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

    # ---- Address (one combined "Street, City, State Zip" string in a
    # single <span>, same as cleansway.com) ----
    addr_div = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_div:
        raw_address = clean(addr_div.get_text())
        if raw_address:
            street, city, state, zipcode = _split_city_state_zip_address(raw_address)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Country (not on the visible page -- only in the LocalBusiness
    # JSON-LD block's address.addressCountry) ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        graph = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(graph, list):
            continue
        for node in graph:
            if not isinstance(node, dict) or node.get("@type") != "LocalBusiness":
                continue
            country = node.get("address", {}).get("addressCountry", "")
            if country and country.upper() != "N/A":
                business["Country"] = country
            break
        if business["Country"]:
            break

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Phone + Website URL + Description (label/value paragraph pairs
    # inside "span.textarea.textarea-about_me" -- this skin's equivalent
    # of cleansway's "div.froala-data.field-about_me")
    #
    # NOTE: this skin labels the site link "URL:" (not "Website:"), so the
    # website regex must accept both -- otherwise the label paragraph and
    # its following <a> paragraph both fall through into the description
    # and "Website URL" is left blank. ----
    about_el = soup.select_one("span.textarea-about_me")
    if about_el:
        para_tags = [p for p in about_el.find_all("p") if clean(p.get_text())]
        desc_paragraphs = []
        i = 0
        while i < len(para_tags):
            line = clean(para_tags[i].get_text())
            if re.match(r"^phone:?$", line, flags=re.I) and i + 1 < len(para_tags):
                business["Phone"] = clean(para_tags[i + 1].get_text())
                i += 2
                continue
            if re.match(r"^(website|url):?$", line, flags=re.I) and i + 1 < len(para_tags):
                link = para_tags[i + 1].find("a", href=True)
                business["Website URL"] = link["href"] if link else clean(para_tags[i + 1].get_text())
                i += 2
                continue
            if re.match(r"^about us:?$", line, flags=re.I):
                i += 1
                continue
            desc_paragraphs.append(line)
            i += 1
        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img")
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business
