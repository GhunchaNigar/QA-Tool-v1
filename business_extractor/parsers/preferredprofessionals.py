"""
Site parser: preferredprofessionals.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


# Label spellings seen in the About block (normalised: lower-case, no
# trailing ":" / "*" / whitespace).
_PP_PHONE_LABELS = {"phone", "phone number", "business phone", "telephone", "tel"}
_PP_WEBSITE_LABELS = {"website", "website url", "url", "url/website", "website/url", "web site"}
_PP_DESC_LABELS = {"about us", "about", "description", "description long",
                   "long description", "description short", "short description"}


def _pp_norm_label(text):
    """'Business Phone *\xa0' -> 'business phone'"""
    return re.sub(r"[\s:*]+$", "", clean(text).replace("\xa0", " ")).strip().lower()


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
    # The pieces can also sit in separate <span>s split by <br> (e.g.
    # <span>Serving</span><br><span>Bowie, MD 20715</span>); get_text()
    # with no separator glued those together ("ServingBowie, MD 20715"),
    # so join the pieces with ", " before splitting.
    addr_div = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_div:
        addr_parts = [clean(t).strip(" ,") for t in addr_div.stripped_strings]
        raw_address = ", ".join(p for p in addr_parts if p)
        if raw_address:
            street, city, state, zipcode = _split_city_state_zip_address(raw_address)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Country (not on the visible page -- only in the LocalBusiness
    # JSON-LD block's address.addressCountry) ----
    ld_business = {}
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
            ld_business = node
            country = node.get("address", {}).get("addressCountry", "")
            if country and country.upper() != "N/A":
                business["Country"] = country
            break
        if ld_business:
            break

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Phone + Website URL + Description (label/value paragraph pairs
    # inside "span.textarea.textarea-about_me" -- this skin's equivalent
    # of cleansway's "div.froala-data.field-about_me"). Label wording
    # varies by listing ("Phone" / "Business Phone *", "Website" /
    # "URL/Website *", "About Us" / "Description Long"), so labels are
    # normalised (trailing ":" / "*" / nbsp removed) and matched against
    # alias lists. ----
    about_el = soup.select_one("span.textarea-about_me")
    if about_el:
        para_tags = [p for p in about_el.find_all("p") if clean(p.get_text())]

        desc_paragraphs = []
        i = 0
        while i < len(para_tags):
            line = clean(para_tags[i].get_text())
            label = _pp_norm_label(line)
            has_next = i + 1 < len(para_tags)
            if label in _PP_PHONE_LABELS and has_next:
                business["Phone"] = clean(para_tags[i + 1].get_text())
                i += 2
                continue
            if label in _PP_WEBSITE_LABELS and has_next:
                link = para_tags[i + 1].find("a", href=True)
                business["Website URL"] = link["href"] if link else clean(para_tags[i + 1].get_text())
                i += 2
                continue
            if label in _PP_DESC_LABELS:
                i += 1
                continue
            desc_paragraphs.append(line)
            i += 1

        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Phone fallbacks (tel: link, then JSON-LD telephone) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = clean(tel.get_text()).replace("Call:", "").strip() or tel["href"][4:]
    if not business["Phone"] and ld_business.get("telephone"):
        tel_text = clean(ld_business["telephone"])
        if tel_text.upper() != "N/A":
            business["Phone"] = tel_text

    # ---- Website fallbacks (a.weblink, then JSON-LD sameAs) ----
    if not business["Website URL"]:
        weblink = soup.select_one("a.weblink[href]")
        if weblink:
            business["Website URL"] = weblink["href"]
    if not business["Website URL"]:
        same_as = ld_business.get("sameAs") or []
        same_as = same_as if isinstance(same_as, list) else [same_as]
        for href in same_as:
            if (isinstance(href, str) and href.startswith("http")
                    and "preferredprofessionals.com" not in href.lower()
                    and not any(d in href.lower() for d in SOCIAL_DOMAINS)):
                business["Website URL"] = href
                break

    # ---- Hours ("Hours of Operation" row in Company Details) ----
    for row in soup.select(".table-view-group"):
        row_label = row.select_one(".col-sm-4")
        row_value = row.select_one(".col-sm-8")
        if row_label and row_value and "hours" in clean(row_label.get_text()).lower():
            hours_text = clean(row_value.get_text(" "))
            if hours_text:
                business["Hours"] = hours_text
                break

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img")
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business