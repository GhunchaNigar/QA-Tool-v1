
import re

from ..common import (
    BeautifulSoup,
    clean,
    clean_multiline,
    is_meaningful,
    empty_business,
    _split_blinx_address,
    _hostname_matches_social_domain,
    SOCIAL_DOMAINS,
    urljoin,
)

_PHONE_LABEL_RE = re.compile(r"^phone\s*:?$", re.I)
_PHONE_PATTERN_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def _contact_info_block(soup):
    """Find the "Contact Information" section (the .table-view container
    holding the website/phone/address rows)."""
    for h2 in soup.find_all("h2"):
        if "contact information" in clean(h2.get_text()).lower():
            block = h2.find_parent(class_="table-view")
            if block:
                return block
    return None


def _multiline_value(tag):
    """Text of a value cell, preserving <br>-separated lines without
    leaking the tag's own HTML -- clean_multiline() only regexes for
    literal "<br>" text, so feeding it str(tag) leaves the wrapping tag's
    opening/closing markup in the output. get_text(separator=...) does the
    actual HTML-to-text conversion; clean_multiline() then just collapses
    per-line whitespace and drops blank lines."""
    return clean_multiline(tag.get_text(separator="\n"))


def _row_value(soup, label_keywords):
    """Generic ".table-view-group" row reader: matches a row whose
    ".col-sm-4" label contains any of label_keywords and returns the
    cleaned text of its ".col-sm-8" value."""
    for group in soup.select(".table-view-group"):
        label_div = group.select_one(".col-sm-4")
        value_div = group.select_one(".col-sm-8")
        if not label_div or not value_div:
            continue
        label_text = clean(label_div.get_text()).lower()
        if any(kw in label_text for kw in label_keywords):
            value = _multiline_value(value_div)
            if is_meaningful(value):
                return value
    return ""


def _about_paragraphs(soup):
    about_container = soup.select_one(".table-display-about_me .froala-data")
    if not about_container:
        return []
    return [clean(p.get_text()) for p in about_container.find_all("p")]


def _phone_value(soup, paragraphs):
    # Primary: the dedicated "Phone Number" contact row -- unlike
    # bizbuildboom.com, the number here is plain text, not hidden behind a
    # "See Phone Number" reveal link.
    row_value = _row_value(soup, ["phone"])
    if row_value:
        return row_value

    # Secondary: the profile-header's inline phone display.
    header_phone_tag = soup.select_one(".author-phone")
    if header_phone_tag:
        value = clean(header_phone_tag.get_text())
        if is_meaningful(value):
            return value

    # Last resort: an About-paragraph "Phone:" label pair (bizbangboom.com
    # style), in case this listing follows that other pattern.
    for i, para in enumerate(paragraphs):
        if _PHONE_LABEL_RE.match(para):
            if i + 1 < len(paragraphs) and is_meaningful(paragraphs[i + 1]):
                return paragraphs[i + 1]

    about_text = " ".join(paragraphs)
    match = _PHONE_PATTERN_RE.search(about_text)
    return match.group(0) if match else ""


def _description_from_about(paragraphs):
    """Join the about paragraphs into the Description, defensively
    excluding any "Phone:" label/value pair (see bizbangboom.py) even
    though the sampled page for this domain didn't have one, and
    naturally dropping empty paragraphs like the trailing "<p><br></p>"
    left over from pasted Word content (is_meaningful() filters these)."""
    kept = []
    skip_next = False
    for para in paragraphs:
        if skip_next:
            skip_next = False
            continue
        if _PHONE_LABEL_RE.match(para):
            skip_next = True
            continue
        if is_meaningful(para):
            kept.append(para)
    return "\n\n".join(kept)


def _hours_value(soup):
    return _row_value(soup, ["hour"])


def _social_links(soup):
    """Social media links belonging to the business itself, scoped to the
    Contact Information block only. Unlike bizbuildboom.com's sidebar
    "Share This Page" module, this layout has no sidebar at all, but the
    scoping is kept anyway as a defensive measure in case another listing
    on this domain does render one."""
    links = {}
    contact_block = _contact_info_block(soup)
    if not contact_block:
        return links
    for a in contact_block.find_all("a", href=True):
        href = a["href"]
        for domain, label in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                links.setdefault(label, href)
                break
    return links


def parse_bizlinkbuilder(url, html):
    soup = BeautifulSoup(html, "html.parser")
    business = empty_business()

    name_tag = soup.select_one("h1.bold.inline-block")
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())

    category_tag = soup.select_one(".profile-header-top-category")
    if category_tag:
        business["Category"] = clean(category_tag.get_text())

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

    paragraphs = _about_paragraphs(soup)

    business["Phone"] = _phone_value(soup, paragraphs)

    description = _description_from_about(paragraphs)
    if is_meaningful(description):
        business["Description"] = description

    hours = _hours_value(soup)
    if hours:
        business["Hours"] = hours

    business["Social Media Links"] = _social_links(soup)

    logo_tag = soup.select_one(".profile-image img")
    if logo_tag and logo_tag.get("src"):
        business["Logo"] = urljoin(url, logo_tag["src"])

    return business