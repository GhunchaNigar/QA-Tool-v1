"""
Site parser: thebusinessminded.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_thebusinessminded(url, html):

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

    # ---- Address ----
    addr_div = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_div:
        direct_spans = addr_div.find_all("span", recursive=False)

        if len(direct_spans) >= 4:
            business["Street"] = clean(direct_spans[0].get_text())
            business["City"] = clean(direct_spans[1].get_text())
            business["State"] = clean(direct_spans[2].get_text())
            business["Zipcode"] = clean(direct_spans[3].get_text())
        elif len(direct_spans) == 3:
            # This template variant has no street span at all -- just
            # "<span>City</span>, <span>State</span>, <span>Zip</span>"
            # (e.g. "Plano", "Texas", "75023"). The old code only handled
            # the 4-span case and fell through to feeding the WHOLE
            # address blob -- including the trailing country text glued
            # on with no separating space -- into the comma-based street
            # splitter, producing garbage like State="75023United States".
            business["City"] = clean(direct_spans[0].get_text())
            business["State"] = clean(direct_spans[1].get_text())
            business["Zipcode"] = clean(direct_spans[2].get_text())
        else:
            raw_address = clean(addr_div.get_text())
            if raw_address:
                street, city, state, zipcode = _split_blinx_address(raw_address)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode

        # ---- Country ----
        # Sits as bare text directly after a <br> in this same container,
        # with no whitespace separating it from the zipcode in the raw
        # text (e.g. "...75023United States"). Replace <br> with a real
        # newline before extracting text so the split is reliable no
        # matter how many address spans preceded it.
        addr_copy = BeautifulSoup(str(addr_div), "lxml")
        for br in addr_copy.find_all("br"):
            br.replace_with("\n")
        lines = [clean(line) for line in addr_copy.get_text().split("\n")]
        lines = [line for line in lines if line]
        if lines and not re.search(r"\d", lines[-1]):
            business["Country"] = lines[-1]

    # ---- Website URL ----
    website_el = soup.select_one(".table-display-website a[href]")
    if website_el and website_el.get("href"):
        business["Website URL"] = website_el["href"]

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Phone + Description ----
    about_el = soup.select_one(".field-about_me")
    if about_el:
        paragraphs = [clean(p.get_text()) for p in about_el.find_all("p")]
        paragraphs = [p for p in paragraphs if p]

        desc_paragraphs = []
        i = 0
        while i < len(paragraphs):
            line = paragraphs[i]
            if re.match(r"^phone:?$", line, flags=re.I) and i + 1 < len(paragraphs):
                business["Phone"] = paragraphs[i + 1]
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


