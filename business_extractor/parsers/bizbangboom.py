"""
Parser for bizbangboom.com business profile pages.

Example URL:
  https://www.bizbangboom.com/business-services/wrightway-emergency-services

bizbangboom.com runs the same directory-network theme as biz411.org (see
parsers/biz411.py -- same OptimizeCDN asset paths, same "table-view-group"
/ "col-sm-4" + "col-sm-8" row markup, same footer listing the sister sites:
America Small Biz, AutoPros411, BigBizStuff, Biz411, BizBangBoom, BizBuildBoom,
BizForgeUSA, etc.), but this sampled listing's markup differs from biz411's
sample in ways that matter for extraction:

  - There's a two-column layout (col-md-9 content / col-md-3 sidebar) instead
    of a single full-width column, with a "Send Message" contact form and a
    "Share This Page" module in the sidebar -- the sidebar's Facebook/
    LinkedIn/X share buttons share the page, not the business, so they must
    NOT be picked up as the business's own Social Media Links.
  - There is no dedicated "table-display-phone" contact row on this listing.
    The phone number instead appears as a labelled pair of paragraphs inside
    the Froala about-me block ("Phone:" / "(941) 379-8669"). We try the
    dedicated row first (in case other listings on this domain do have it,
    matching biz411's shape) and fall back to scanning the about paragraphs.
  - The JSON-LD LocalBusiness "description" field on this listing has the
    phone number appended directly onto the end of the description text
    with no separator ("...Fort Myers.Phone:(941) 379-8669"), so it is NOT
    used as a fallback source for Description here (unlike biz411.py) --
    it would need cleanup that's brittle to get right without a second
    sample to verify against. The About paragraphs (with the Phone: label
    pair stripped out) are the sole Description source.
  - No explicit Hours block was present on the sampled page. If a listing
    has one, it should appear as another ".table-view-group" row (matching
    the phone/website/address pattern), so _row_value() below looks for a
    row whose label contains "hour" -- unverified against a real sample,
    since none was available.
  - Social Media Links are scanned only inside the "Contact Information"
    block (the one containing the phone/website/address rows), never inside
    the sidebar's share module, to avoid misattributing the page-share
    buttons to the business itself.
"""

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
    holding the phone/website/address rows), as distinct from the sidebar's
    unrelated "Share This Page" module."""
    for h2 in soup.find_all("h2"):
        if "contact information" in clean(h2.get_text()).lower():
            block = h2.find_parent(class_="table-view")
            if block:
                return block
    return None


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
            value = (
                clean_multiline(str(value_div))
                if value_div.find("br")
                else clean(value_div.get_text())
            )
            if is_meaningful(value):
                return value
    return ""


def _about_paragraphs(soup):
    about_container = soup.select_one(".table-display-about_me .froala-data")
    if not about_container:
        return []
    return [clean(p.get_text()) for p in about_container.find_all("p")]


def _phone_from_about(paragraphs):
    """Fallback for listings (like this one) with no dedicated phone row:
    the about block carries "Phone:" as its own paragraph, immediately
    followed by a paragraph containing just the number."""
    for i, para in enumerate(paragraphs):
        if _PHONE_LABEL_RE.match(para):
            if i + 1 < len(paragraphs) and is_meaningful(paragraphs[i + 1]):
                return paragraphs[i + 1]
    return ""


def _description_from_about(paragraphs):
    """Join the about paragraphs into the Description, excluding the
    Phone: label and its value so the phone number isn't duplicated into
    the description text."""
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


def _social_links(soup):
    """Social media links belonging to the business itself, scoped to the
    Contact Information block only -- the sidebar's page-share buttons
    (Facebook/LinkedIn/X) live in a separate module and must be ignored."""
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


def parse_bizbangboom(url, html):
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

    phone_tag = soup.select_one(".table-display-phone .col-sm-8 span")
    if phone_tag and is_meaningful(clean(phone_tag.get_text())):
        business["Phone"] = clean(phone_tag.get_text())
    else:
        phone = _phone_from_about(paragraphs)
        if not phone:
            # Last resort: regex over the raw about text.
            about_text = " ".join(paragraphs)
            match = _PHONE_PATTERN_RE.search(about_text)
            if match:
                phone = match.group(0)
        business["Phone"] = phone

    description = _description_from_about(paragraphs)
    if is_meaningful(description):
        business["Description"] = description

    hours = _row_value(soup, ["hour"])
    if hours:
        business["Hours"] = hours

    business["Social Media Links"] = _social_links(soup)

    logo_tag = soup.select_one(".profile-image img")
    if logo_tag and logo_tag.get("src"):
        business["Logo"] = urljoin(url, logo_tag["src"])

    return business