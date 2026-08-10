

from ..common import (
    BeautifulSoup,
    clean,
    clean_multiline,
    is_meaningful,
    empty_business,
    _split_blinx_address,
    urljoin,
    json,
)


def _multiline_value(tag):
    """Text of a value cell, preserving <br>-separated lines (e.g. a
    Mon-Fri / Sat hours block) without leaking the tag's own HTML --
    clean_multiline() only regexes for literal "<br>" text, so feeding it
    str(tag) (as opposed to a raw HTML string) leaves the wrapping tag's
    opening/closing markup in the output. get_text(separator=...) does the
    actual HTML-to-text conversion; clean_multiline() then just collapses
    per-line whitespace and drops blank lines."""
    return clean_multiline(tag.get_text(separator="\n"))


def _hours_value(soup):
    # Dedicated row first, in case some listings use the same
    # table-display-* naming convention as phone/website/address.
    dedicated = soup.select_one(".table-display-hours .col-sm-8")
    if dedicated:
        value = _multiline_value(dedicated)
        if is_meaningful(value):
            return value

    # Generic fallback: any ".table-view-group" row whose label mentions
    # "hour" (matches phone/website/address's shared row shape).
    for group in soup.select(".table-view-group"):
        label_div = group.select_one(".col-sm-4")
        value_div = group.select_one(".col-sm-8")
        if not label_div or not value_div:
            continue
        if "hour" in clean(label_div.get_text()).lower():
            value = _multiline_value(value_div)
            if is_meaningful(value):
                return value

    return ""


def _description_from_jsonld(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue

        graph = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(graph, list):
            continue

        for node in graph:
            if not isinstance(node, dict):
                continue
            if node.get("@type") in ("LocalBusiness", "Organization"):
                desc = clean(node.get("description") or "")
                if is_meaningful(desc):
                    return desc
    return ""


def parse_biz411(url, html):
    soup = BeautifulSoup(html, "html.parser")
    business = empty_business()

    name_tag = soup.select_one("h1.bold.inline-block")
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())

    category_tag = soup.select_one(".profile-header-top-category")
    if category_tag:
        business["Category"] = clean(category_tag.get_text())

    phone_tag = soup.select_one(".table-display-phone .col-sm-8 span")
    if phone_tag:
        business["Phone"] = clean(phone_tag.get_text())

    website_tag = soup.select_one("a.weblink[itemprop='url']")
    if website_tag and website_tag.get("href"):
        business["Website URL"] = website_tag["href"].strip()

    address_tag = soup.select_one(".overview-tab-the-member-address .col-sm-8 span")
    if address_tag:
        address = clean(address_tag.get_text())
        if is_meaningful(address):
            street, city, state, zipcode = _split_blinx_address(address)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode
            # Site is U.S.-only (breadcrumb / JSON-LD both fix
            # addressCountry to "US"); no per-listing country field exists
            # on the page to scrape instead.
            business["Country"] = "US"

    about_container = soup.select_one(".table-display-about_me .froala-data")
    if about_container:
        paragraphs = [clean(p.get_text()) for p in about_container.find_all("p")]
        paragraphs = [p for p in paragraphs if is_meaningful(p)]
        if paragraphs:
            business["Description"] = "\n\n".join(paragraphs)

    if not is_meaningful(business["Description"]):
        business["Description"] = _description_from_jsonld(soup)

    hours = _hours_value(soup)
    if hours:
        business["Hours"] = hours

    logo_tag = soup.select_one(".profile-image img")
    if logo_tag and logo_tag.get("src"):
        business["Logo"] = urljoin(url, logo_tag["src"])

    return business