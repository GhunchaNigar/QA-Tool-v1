"""
Site parser: dbesearch.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_DBESEARCH_CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<city>.+?),\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _split_dbesearch_address(address_text):
    """Splits the clean_multiline()'d contents of .business_address into
    Street / City / State / Zipcode. Expected shape (line 1 = street,
    line 2 = "City, ST 12345"), e.g.:
        300 Triple Diamond Blvd
        Nokomis, FL 34275
    Some listings have no street at all -- everything renders as a single
    line reading just "City, ST 12345" (e.g. "Plano , TX 75023") -- so
    that single line is checked against the city/state/zip pattern first
    rather than being assumed to be a street.
    """
    street, city, state, zipcode = "", "", "", ""

    lines = [clean(line) for line in address_text.split("\n") if clean(line)]
    if not lines:
        return street, city, state, zipcode

    if len(lines) == 1:
        match = _DBESEARCH_CITY_STATE_ZIP_RE.match(lines[0])
        if match:
            city = match.group("city").strip()
            state = match.group("state").strip()
            zipcode = match.group("zip").strip()
        else:
            street = lines[0]
        return street, city, state, zipcode

    street = lines[0]

    if len(lines) >= 2:
        match = _DBESEARCH_CITY_STATE_ZIP_RE.match(lines[1])
        if match:
            city = match.group("city").strip()
            state = match.group("state").strip()
            zipcode = match.group("zip").strip()
        else:
            city = lines[1]

    return street, city, state, zipcode


def parse_dbesearch(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    jumbotron = soup.select_one(".jumbotron")

    # ---- Name ----
    name_el = jumbotron.select_one("h1") if jumbotron else soup.select_one("h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Category (the <p><b>Category: </b>Other</p> block) ----
    if jumbotron:
        for p in jumbotron.find_all("p"):
            b = p.find("b")
            if b and "category" in clean(b.get_text()).lower():
                full_text = clean(p.get_text())
                label = clean(b.get_text())
                business["Category"] = full_text[len(label):].strip(" :")
                break

    # ---- Logo ----
    logo_el = soup.select_one('img[name^="logo_"]')
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])

    # ---- Website URL ----
    website_el = soup.select_one("a.business-web-link[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Street / City / State / Zipcode ----
    address_el = soup.select_one(".business_address")
    if address_el:
        # <br> splits the address into two separate text nodes (street,
        # then "City, ST Zip"); separator="\n" joins them one per line.
        address_text = address_el.get_text(separator="\n")
        street, city, state, zipcode = _split_dbesearch_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ----
    phone_el = soup.select_one('.business_contact_phone a[href^="tel:"]')
    if phone_el:
        business["Phone"] = clean(phone_el.get_text()) or phone_el["href"].replace("tel:", "").strip()

    return business


