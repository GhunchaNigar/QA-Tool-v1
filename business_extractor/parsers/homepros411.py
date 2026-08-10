
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


def _contact_info_block(soup):
    """Find the "Contact Information" section (the .table-view container
    holding the website/social-links/phone/address rows)."""
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


def _phone_value(soup):
    # Primary: the contact-row's hidden-but-populated phone span.
    phone_tag = soup.select_one(".phone_number")
    if phone_tag:
        value = clean(phone_tag.get_text())
        if is_meaningful(value):
            return value

    # Secondary: the profile-header's duplicate phone display.
    header_phone_tag = soup.select_one(".phone_number_header")
    if header_phone_tag:
        value = clean(header_phone_tag.get_text())
        if is_meaningful(value):
            return value

    # Last resort: a dedicated/generic contact row labelled "Phone".
    return _row_value(soup, ["phone"])


def _address_fields(soup):
    """Street/City/State/Zipcode/Country for this domain's span-per-field
    address markup (see module docstring). Falls back to
    _split_blinx_address() on the block's plain text if the expected four
    spans aren't present."""
    container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if not container:
        return "", "", "", "", ""

    spans = [clean(s.get_text()) for s in container.find_all("span")]
    spans = [s for s in spans if is_meaningful(s)]

    if len(spans) >= 4:
        street, city, state, zipcode = spans[0], spans[1], spans[2], spans[3]
    else:
        full_text = clean(container.get_text())
        street, city, state, zipcode = _split_blinx_address(full_text)

    # The country is bare text (not wrapped in a span) trailing the block,
    # e.g. "United States" after the final <br>. Site is U.S.-only
    # (breadcrumb / JSON-LD both fix addressCountry to "US"), so default
    # there regardless of the trailing text's exact wording.
    country = "US"

    return street, city, state, zipcode, country


def _about_container(soup):
    return soup.select_one(".table-display-about_me .froala-data")


def _labeled_field(about_container, label):
    """Extract the text following a <strong>Label:</strong> marker inside
    an about-me paragraph, up to the next <strong> label or end of that
    paragraph. This domain bundles multiple fields (Description, Number
    of Employees, Contact Email, etc.) into a single <p> tag delimited by
    bold labels rather than separate paragraphs -- walking the label's
    sibling nodes up to the next <strong> isolates just that field's
    value instead of running every bundled field together."""
    if not about_container:
        return ""

    label_norm = label.strip().lower().rstrip(":").strip()

    for p in about_container.find_all("p"):
        for strong in p.find_all("strong"):
            strong_text = clean(strong.get_text()).lower().rstrip(":").strip()
            if strong_text != label_norm:
                continue

            parts = []
            node = strong.next_sibling
            while node is not None:
                if getattr(node, "name", None) == "strong":
                    break
                if isinstance(node, str):
                    parts.append(node)
                else:
                    parts.append(node.get_text())
                node = node.next_sibling

            value = clean(" ".join(parts))
            if is_meaningful(value):
                return value

    return ""


def _description(about_container):
    """Prefer the labelled "Description:" field (see _labeled_field());
    fall back to the about-me paragraphs verbatim for listings on this
    domain that don't bundle fields with bold labels."""
    labelled = _labeled_field(about_container, "Description")
    if labelled:
        return labelled

    if not about_container:
        return ""
    paragraphs = [clean(p.get_text()) for p in about_container.find_all("p")]
    paragraphs = [p for p in paragraphs if is_meaningful(p)]
    return "\n\n".join(paragraphs)


def _hours_value(soup):
    return _row_value(soup, ["hour"])


def _social_links(soup):
    """Social media links belonging to the business itself, scoped to the
    Contact Information block -- this domain renders a real
    "Online Social Profiles" row here (unlike the sister sites, which
    mostly have none), and no sidebar page-share module exists to
    accidentally pick up instead."""
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


def parse_homepros411(url, html):
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

    street, city, state, zipcode, country = _address_fields(soup)
    if is_meaningful(street) or is_meaningful(city):
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode
        business["Country"] = country

    business["Phone"] = _phone_value(soup)

    about_container = _about_container(soup)
    description = _description(about_container)
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